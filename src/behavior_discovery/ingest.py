"""Uploaded bytes only; local originals retained, validated H264 derivative registered."""
import hashlib
import json
import subprocess
import os
from pathlib import Path
from src.identity.store import secure_directory,now
from src.tracking.video import probe,sha256

ROOT=Path(__file__).resolve().parents[2]
MEDIA_ROOT=ROOT/'data/analysis_private/media'


def inspect(path):
    # decode_frames accepts all libav-supported containers; validates EVERY PTS.
    try:return probe(path,{'start':0.,'end':0.})
    except Exception as error:raise ValueError('video_decode_or_timestamp_validation_failed:'+type(error).__name__) from error


def ingest(service,temp_path,filename,actor):
    ext=Path(filename).suffix.lower()
    if ext not in {'.mp4','.avi','.mov','.mkv'}:raise ValueError('unsupported_video_extension')
    path=Path(temp_path);digest=sha256(path)
    owner=hashlib.sha256(actor.encode()).hexdigest()[:20];vid='uploaded_'+hashlib.sha256((actor+':'+digest).encode()).hexdigest()[:24]
    with service.repo.connection() as c:old=c.execute('SELECT status FROM analysis_videos WHERE owner=? AND original_sha256=?',(actor,digest)).fetchone()
    if old:
        if old['status']!='active':raise ValueError('same_video_was_deleted; explicit_restore_not_implemented')
        path.unlink();return {'video_id':vid,'deduplicated':True}
    timing=inspect(path)
    if timing['duration']>7200 or timing['width']*timing['height']>3840*2160:raise ValueError('video_exceeds_two_hours_or_4k_import_limit')
    folder=secure_directory(MEDIA_ROOT/owner);original=folder/(vid+ext+'.original');media=folder/(vid+'.mp4');partial=folder/(vid+'.partial.mp4')
    import shutil
    # Copy also supports a managed store on a different Windows volume.
    with original.open('xb') as output,path.open('rb') as uploaded:shutil.copyfileobj(uploaded,output)
    path.unlink()
    try:
        command=['ffmpeg','-v','error','-y','-i',str(original),'-map','0:v:0','-an','-vf','setpts=PTS-STARTPTS',
            '-fps_mode','passthrough','-c:v','libx264','-preset','ultrafast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(partial)]
        result=subprocess.run(command,capture_output=True,timeout=1800,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('video_transcode_failed:'+result.stderr.decode(errors='replace')[-300:])
        normalized=inspect(partial)
        if (timing['width'],timing['height'],len(timing['timestamps']))!=(normalized['width'],normalized['height'],len(normalized['timestamps'])):raise ValueError('media_derivative_dimensions_or_frame_count_changed')
        drift=max(abs(a-b) for a,b in zip(timing['timestamps'],normalized['timestamps']))
        if drift>.002:raise ValueError('media_derivative_pts_drift_exceeds_2ms')
        partial.replace(media)
        metadata={'filename':Path(filename).name,'original_sha256':digest,'processing_sha256':sha256(media),'original_path':str(original),
            'media_path':str(media),'duration':normalized['duration'],'width':normalized['width'],'height':normalized['height'],
            'fps':normalized['median_fps_from_pts'],'vfr':normalized['vfr_detected'],'max_pts_drift':drift,
            'frame_count':normalized['decoded_frame_count'],'source_timebase':'first_decoded_PTS_origin','source_kind':'uploaded_recording',
            'annotation_source':'none_required; legacy Excel is never queried by discovery'}
        clip={'clip_id':vid,'video_id':vid,'start':0.,'end':normalized['duration'],'duration':normalized['duration'],
            'width':normalized['width'],'height':normalized['height'],'path':str(media),'decode_status':'decoded',
            'source_kind':'uploaded_recording','owner':actor,'processing_sha256':metadata['processing_sha256'],'original_sha256':digest}
        # Registered video metadata only. NO region_annotations / fake Excel events.
        import sqlite3
        with sqlite3.connect(service.source.registry_db) as c:c.execute('INSERT INTO registered_clips(id,video_id,start,end,payload) VALUES (?,?,?,?,?)',(vid,vid,0.,clip['end'],json.dumps(clip)))
        with service.repo.connection() as c:c.execute('INSERT INTO analysis_videos VALUES (?,?,?,?,?,?,?,?)',(vid,actor,digest,vid,'recording_'+vid,'active',now(),json.dumps(metadata)))
        return {'video_id':vid,'deduplicated':False,'metadata':{k:v for k,v in metadata.items() if not k.endswith('_path')}}
    except Exception:
        partial.unlink(missing_ok=True)
        # Never remove the uploaded original on decode/registration failure.
        raise


def authorize_run(service,rid,actor,role='viewer'):
    _,clip=service.source.run(rid)
    if os.getenv('INTICLASS_PROFILE')=='product' and clip.get('source_kind')!='uploaded_recording':raise PermissionError('historical_experiment_not_a_product_resource')
    if clip.get('source_kind')=='uploaded_recording' and clip.get('owner')!=actor and role!='admin':raise PermissionError('uploaded_video_access_denied')


def safe_managed_path(clip,path):
    root=MEDIA_ROOT.resolve()
    return (clip.get('source_kind')=='uploaded_recording' and root in path.parents and path.name==clip['clip_id']+'.mp4' and path.suffix=='.mp4')
