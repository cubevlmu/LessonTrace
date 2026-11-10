import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path
from importlib.metadata import version as package_version
from .detector import PersonDetector
from .tracker import PersonTracker
from .video import registered_clip,safe_path,sha256,probe,decode_frames
from .schemas import ProcessingConfig,global_time
from .repository import TrackingRepository


def monitor_gpu(stop,samples):
    while not stop.is_set():
        try:
            text=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            mem,util=map(float,text.splitlines()[0].split(','));samples.append({'clock':time.monotonic(),'memory_mib':mem,'utilization_pct':util})
        except (OSError,ValueError,subprocess.SubprocessError):pass
        stop.wait(1)


def _run_tracking(registry_db,tracking_db,video_root,weights,config,*,clip_id=None,video_id=None,rerun=False,detector_factory=PersonDetector,tracker_factory=PersonTracker,control=None,execution_scope=None):
    config=ProcessingConfig.model_validate(config);repo=TrackingRepository(tracking_db)
    clip=registered_clip(registry_db,clip_id,video_id);path=safe_path(clip,video_root,require_exists=False)
    source_hash=sha256(path) if path.is_file() else 'missing';stat=path.stat() if path.is_file() else None
    identity={'clip_id':clip['clip_id'],'video_id':clip['video_id'],'source_sha256':source_hash,
        'weights_sha256':sha256(weights) if Path(weights).is_file() else 'missing',
        'global_offset':clip['start'],'registered_end':clip['end'],
        'detector_library_version':package_version('ultralytics') if detector_factory is PersonDetector else 'test_factory',
        'torch_library_version':package_version('torch') if detector_factory is PersonDetector else 'test_factory',
        'decoder_library_version':package_version('av'),
        'config':config.model_dump(),'execution_scope':execution_scope,'version':'tracking-v1.2-pts-no-temperature'}
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    rid,fresh=repo.start(clip['video_id'],clip['clip_id'],fingerprint,identity,rerun)
    if not fresh:return repo.run(rid)
    started=time.perf_counter();telemetry=[];stop=threading.Event();thread=threading.Thread(target=monitor_gpu,args=(stop,telemetry),daemon=True)
    if config.device!='cpu':thread.start()
    batch=[];processed=0;empty=0;det_count=0;inference_seconds=0;timing=None;detector=None;tracker=None
    status='completed';error=None;thermal_pauses=0
    try:
        if stat is None:raise ValueError('registered_video_missing')
        timing=probe(path,clip)
        if abs(timing['duration_delta'])>.15:raise ValueError('registered_duration_vs_decoded_timebase_mismatch')
        detector=detector_factory(weights,config);tracker=tracker_factory(config,timing['median_fps_from_pts'])
        for data in decode_frames(path):
            if data['frame_index']%config.stride:continue
            if config.max_frames is not None and processed>=config.max_frames:break
            if control and control() in {'pausing','cancelling','paused','cancelled'}:raise InterruptedError('operator_job_control')
            image=data.pop('frame').to_ndarray(format='bgr24');t=time.perf_counter()
            detections,boxes=detector.predict(image);matches=tracker.update(boxes,image,detections)
            inference_seconds+=time.perf_counter()-t
            batch.append({**{k:v for k,v in data.items() if k not in {'original_pts_seconds','origin_pts_seconds'}},
                'global_timestamp':global_time(data['local_timestamp'],clip['start']),'processed_index':processed,'detections':detections,'matches':matches})
            processed+=1;empty+=not detections;det_count+=len(detections)
            if len(batch)>=25:repo.save_batch(rid,batch);batch=[]
        if batch:repo.save_batch(rid,batch);batch=[]
        if path.stat().st_size!=stat.st_size or path.stat().st_mtime_ns!=stat.st_mtime_ns:raise ValueError('source_changed_during_processing')
    except InterruptedError:status='interrupted';error='operator_job_control; tracking restart requires fresh namespace'
    except KeyboardInterrupt:status='interrupted';error='operator_interrupt; restart uses new run and namespace, no fake tracker-state resume'
    except Exception as ex:status='failed';error=type(ex).__name__+': '+str(ex)[:1000]
    finally:
        # Commit only fully decoded and associated frames, even on failure.
        if batch:
            try:repo.save_batch(rid,batch)
            except Exception as ex:status='failed';error=(error or '')+'; last_batch_not_committed: '+str(ex)[:200]
        stop.set()
        if thread.is_alive():thread.join(timeout=6)
    tracks=repo.tracks(rid);elapsed=time.perf_counter()-started
    with repo.connection() as c:
        committed=c.execute('SELECT COUNT(*) FROM tracking_frames WHERE run_id=?',(rid,)).fetchone()[0]
        committed_empty=c.execute('SELECT COUNT(*) FROM tracking_frames WHERE run_id=? AND detection_count=0',(rid,)).fetchone()[0]
        committed_dets=c.execute('SELECT COUNT(*) FROM person_detections WHERE run_id=?',(rid,)).fetchone()[0]
    report={'processed_frames':committed,'attempted_inference_frames':processed,'frames_without_detections':committed_empty,'person_detections':committed_dets,
        'track_count':len(tracks),'elapsed_seconds':elapsed,'end_to_end_fps':committed/elapsed if elapsed else None,
        'detector_tracker_fps':processed/inference_seconds if inference_seconds else None,
        'gpu_memory_peak_mib':max((s['memory_mib'] for s in telemetry),default=None),
        'gpu_temperature_peak_c':None,
        'gpu_utilization_peak_pct':max((s['utilization_pct'] for s in telemetry),default=None),
        'thermal_pauses':thermal_pauses,'telemetry':telemetry,'source_timebase':{k:v for k,v in (timing or {}).items() if k!='timestamps'},
        'detector':detector.metadata if detector else None,'tracker_config':tracker.settings if tracker else None,
        'visibility_gaps':sum(len(t['gaps']) for t in tracks),'scope_limited':config.max_frames is not None,
        'identity_verified':False,'IDF1':None,'HOTA':None,'MOTA':None,
        'occlusion_semantics':'Unobserved intervals have no invented boxes; cause unknown. Reacquisition is not verified identity.'}
    repo.finish(rid,status,report,error)
    return repo.run(rid)


def run_tracking(registry_db,tracking_db,video_root,weights,config,*,clip_id=None,video_id=None,rerun=False,detector_factory=PersonDetector,tracker_factory=PersonTracker,control=None,execution_scope=None):
    from filelock import FileLock
    if Path(registry_db).resolve()==Path(tracking_db).resolve():raise ValueError('trajectory_db_must_differ_from_read_only_registry')
    target=Path(tracking_db).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    # A single worker per trajectory DB; query API remains read-only/concurrent.
    # OS-backed lock is released on process death; DB running status still needs
    # explicit abort, never pretend to resume a lost Kalman state.
    with FileLock(str(target)+'.worker.lock',timeout=0):
        return _run_tracking(registry_db,tracking_db,video_root,weights,config,clip_id=clip_id,video_id=video_id,
            rerun=rerun,detector_factory=detector_factory,tracker_factory=tracker_factory,control=control,execution_scope=execution_scope)
