"""Resumable window work; real model calls are bounded and single-concurrent."""
import json
import os
import subprocess
import time
from pathlib import Path
from filelock import FileLock,Timeout
from src.identity.store import now
from src.tracking.repository import TrackingRepository
from src.agent.fusion.verification import ResourceMonitor
from .repository import Repository
from .schemas import Config
from .windowing import plan_windows
from .event_merger import merge
from .visual_observer import Observer,model_info

ROOT=Path(__file__).resolve().parents[2]


def materialize(service,jid,video_id):
    with service.repo.connection() as c:
        proposals=[p for row in c.execute("SELECT payload FROM discovery_windows WHERE job_id=? AND status='completed' ORDER BY start,track_id",(jid,)) for p in json.loads(row[0]).get('result',{}).get('proposals',[])]
    events=merge(proposals)
    with service.repo.connection() as c:
        c.execute('DELETE FROM analysis_event_heads WHERE job_id=?',(jid,))
        for event in events:
            event.update(video_id=video_id,job_id=jid)
            c.execute('INSERT OR IGNORE INTO discovered_behavior_events VALUES (?,?,?,?,?,?,?,?,?)',(event['event_id'],jid,video_id,event['run_id'],event['track_id'],event['action_category'],event['local_start'],event['local_end'],json.dumps(event,ensure_ascii=False)))
            c.execute('INSERT INTO analysis_event_heads VALUES (?,?)',(jid,event['event_id']))
    return events


def execute(service,jid,observer_factory=Observer,model_provider=model_info):
    repo=Repository(service.repo);job=repo.job(jid);settings=job['config_and_runtime'];conf=Config.model_validate(settings['config'])
    folder=ROOT/'data/analysis_private';folder.mkdir(exist_ok=True,parents=True)
    joblock=FileLock(str(folder/(jid+'.lock')))
    try:joblock.acquire(timeout=0)
    except Timeout:return
    start=time.monotonic();deadline=start+conf.budget_seconds;gpu=FileLock(str(ROOT/'data/attribution_private/actor_vlm.lock'))
    telemetry=None
    try:
        if job['status'] not in {'queued','running'}:return
        video=repo.video(job['video_id'])
        if video['status']!='active':raise ValueError('video_deleted')
        repo.update(jid,status='running')
        rid=job['run_id']
        if not rid:
            # Recovery cannot restore a lost tracker state; only this job's old
            # incomplete namespace is abandoned, never an unrelated historical run.
            tracker_repo=TrackingRepository(service.source.tracking_db)
            for prior in tracker_repo.runs(video_id=job['video_id']):
                if prior['status']=='running' and prior['metadata'].get('execution_scope')==jid:
                    tracker_repo.finish(prior['run_id'],'interrupted',prior.get('report') or {},'abandoned job worker; new tracker namespace required')
            with gpu.acquire(timeout=0):
                import sys
                local_python=ROOT/('.venv-tracking/Scripts/python.exe' if os.name=='nt' else '.venv-tracking/bin/python')
                python=os.getenv('INTICLASS_TRACKING_PYTHON',str(local_python) if local_python.is_file() else sys.executable)
                process=subprocess.run([str(python),str(ROOT/'scripts/run_analysis_tracking.py'),'--job-id',jid],cwd=ROOT,capture_output=True,text=True,
                    timeout=conf.budget_seconds,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if process.returncode:raise ValueError('tracking_subprocess_failed: '+process.stderr[-800:])
            result=json.loads(process.stdout.strip().splitlines()[-1]);rid=result['run_id']
            if result['status']!='completed':
                repo.update(jid,status='cancelled' if repo.job(jid)['status']=='cancelling' else 'paused',stage='tracking')
                return
            repo.update(jid,run_id=rid,stage='windowing')
        from src.context.lessons import bind_materials
        bind_materials(service,job['video_id'],rid,job['owner'])
        windows,scope=plan_windows(service.source,rid,conf,jid);settings['coverage']=scope
        if conf.analysis_mode=='full_timeline' and not settings.get('full_call_budget_initialized'):
            repo.update(jid,call_limit=max(2,len(windows)*(1 if conf.processing_profile=='efficient' else 2)))
            settings['full_call_budget_initialized']=True
        with service.repo.connection() as c:
            for w in windows:c.execute('INSERT OR IGNORE INTO discovery_windows VALUES (?,?,?,?,?,?,?,?,?)',(w['window_id'],jid,rid,w['track_id'],w['start'],w['end'],w['segment_id'],'pending',json.dumps(w)))
        if not windows:
            settings.setdefault('identity_matching',{'status':'no_analysis_windows','anonymous_tracks_retained':True})
            repo.update(jid,status='completed',stage='report_ready',payload=json.dumps(settings));return
        installed=model_provider()
        if settings.get('model') and installed!=settings['model']:raise ValueError('model_version_changed; create_a_new_job')
        settings['model']=installed;repo.update(jid,stage='discovery',payload=json.dumps(settings))
        observer=observer_factory(service.source,service.repo,jid,folder/'evidence',conf,settings['model'],deadline)
        processed_in_slice=0
        with ResourceMonitor(True) as monitor:
            telemetry=monitor
            with service.repo.connection() as c:pending=[dict(r) for r in c.execute("SELECT * FROM discovery_windows WHERE job_id=? AND status!='completed' ORDER BY start,track_id,window_id",(jid,))]
            for row in pending:
                state=repo.job(jid)['status']
                remaining=deadline-time.monotonic()
                reserve=min(conf.budget_seconds*.9,conf.timeout*(1 if conf.processing_profile=='efficient' else 2)+30)
                slice_boundary=conf.analysis_mode=='full_timeline' and processed_in_slice>0 and remaining<reserve
                if state!='running' or remaining<=0 or slice_boundary:
                    # A full job continues in bounded time slices. User pauses,
                    # model failures and total call limits never auto-resume.
                    continued=state=='running' and conf.analysis_mode=='full_timeline'
                    repo.update(jid,status='queued' if continued else 'cancelled' if state=='cancelling' else 'paused')
                    if continued:repo.audit(jid,'full_timeline_next_time_slice',{'processed_windows':repo.job(jid)['processed_windows']})
                    break
                window=json.loads(row['payload']);window={k:v for k,v in window.items() if k not in {'result','error'}}
                with service.repo.connection() as c:c.execute("UPDATE discovery_windows SET status='running' WHERE window_id=?",(row['window_id'],))
                try:
                    with gpu.acquire(timeout=0):result=observer.observe(window)
                    with service.repo.connection() as c:c.execute("UPDATE discovery_windows SET status='completed',payload=? WHERE window_id=?",(json.dumps({**window,'result':result},ensure_ascii=False),row['window_id']))
                    processed_in_slice+=1
                    if conf.processing_profile!='efficient':materialize(service,jid,job['video_id'])
                except (TimeoutError,InterruptedError,Timeout) as error:
                    with service.repo.connection() as c:c.execute("UPDATE discovery_windows SET status='pending' WHERE window_id=?",(row['window_id'],))
                    state=repo.job(jid)['status']
                    continued=state=='running' and conf.analysis_mode=='full_timeline' and str(error)=='job_time_budget_exhausted'
                    repo.update(jid,status='queued' if continued else 'cancelled' if state=='cancelling' else 'paused')
                    repo.audit(jid,'paused_or_budget_exhausted',{'reason':str(error)});break
                except Exception as error:
                    with service.repo.connection() as c:c.execute("UPDATE discovery_windows SET status='error',payload=? WHERE window_id=?",(json.dumps({**window,'error':type(error).__name__+': '+str(error)}),row['window_id']))
                    repo.audit(jid,'window_error',{'window_id':row['window_id'],'error':str(error)[:500]})
                    if repo.job(jid)['failed_windows']>=3:repo.update(jid,status='paused');break
            else:repo.update(jid,status='completed_with_errors' if repo.job(jid)['failed_windows'] or repo.job(jid)['failed_calls'] else 'completed',stage='report_ready')
        if repo.job(jid)['status'] in {'completed','completed_with_errors'} and not settings.get('identity_matching'):
            from src.identity.auto_matching import execute as match_identity
            settings['identity_matching']=match_identity(service,job['video_id'],rid,job['owner'])
            settings['identity_matching_order']='after_track_behavior_observations'
            repo.update(jid,payload=json.dumps(settings))
        # No people is a valid outcome: do not fabricate scene/track actions.
        if not windows:repo.update(jid,status='completed',stage='report_ready')
    except Timeout as error:repo.update(jid,status='paused');repo.audit(jid,'gpu_busy',{'error':str(error)})
    except Exception as error:repo.update(jid,status='failed');repo.audit(jid,'failure',{'error':type(error).__name__+': '+str(error)[:1000]})
    finally:
        latest=repo.job(jid);runtime=latest['config_and_runtime'];runtime['elapsed_seconds']=runtime.get('elapsed_seconds',0)+time.monotonic()-start
        if telemetry:runtime['resources']=telemetry.summary()
        repo.update(jid,payload=json.dumps(runtime));joblock.release()
