import json
import subprocess
import sys
import os
from pathlib import Path
from filelock import FileLock,Timeout
from src.agent.fusion.evidence import version
from src.identity.store import uid,now
from .repository import Repository
from .schemas import VERSION

ROOT=Path(__file__).resolve().parents[2]


def create(service,body,actor):
    if os.getenv('INTICLASS_PROFILE')=='product' and 'processing_profile' not in body.config.model_fields_set:
        body=body.model_copy(update={'config':body.config.model_copy(update={'processing_profile':'efficient'})})
    repo=Repository(service.repo);video=repo.video(body.video_id,actor)
    from src.inference.runtime import ModelRegistry
    registry=ModelRegistry.from_env()
    if video['status']!='active':raise ValueError('video_not_active')
    reuse=body.reuse_tracking_run_id
    if reuse:
        run,clip=service.source.verified_run(reuse)
        if run['video_id']!=body.video_id or clip['clip_id']!=video['clip_id']:raise ValueError('tracking_reuse_video_mismatch')
        actual={t['track_id'] for t in service.source.tracks(reuse)}
        if set(body.config.track_ids)-actual:raise ValueError('selected_track_not_in_reused_run')
    if body.config.analysis_mode=='full_timeline' and not body.force_new:
        # A frontend retry or software update must not enqueue a second day-long
        # identical full job. Keep the durable run and its completed windows.
        with service.repo.connection() as c:
            rows=c.execute("SELECT job_id,payload FROM analysis_jobs WHERE owner=? AND video_id=? AND run_id IS ? AND status IN ('queued','running','pausing','paused','cancelling') ORDER BY created_at DESC",(actor,body.video_id,reuse)).fetchall()
        for row in rows:
            if json.loads(row['payload']).get('config')==body.config.model_dump():return repo.job(row['job_id'],actor),False
    key=version({'video':body.video_id,'config':body.config.model_dump(),'reused_tracking_run_id':reuse,'pipeline_version':VERSION,
        'model_configuration':{'url':registry.visual_url,'model':registry.visual_model,'context':registry.context_tokens,'max_tokens':os.getenv('INTICLASS_VLM_MAX_TOKENS','1600')},
        'code':{p.name:p.read_text(encoding='utf-8') for p in Path(__file__).parent.glob('*.py')},'new_namespace':uid() if body.force_new else None})
    with service.repo.connection() as c:
        old=c.execute('SELECT job_id FROM analysis_jobs WHERE fingerprint=?',(key,)).fetchone()
        if old:return repo.job(old[0],actor),False
        jid=uid();payload={'config':body.config.model_dump(),'pipeline_version':VERSION,'elapsed_seconds':0.,'product_queue':os.getenv('INTICLASS_EXTERNAL_WORKER')=='1'}
        if reuse:payload.update(reused_tracking_run_id=reuse,tracking_config_source='existing_verified_run; no_detector_rerun')
        c.execute('INSERT INTO analysis_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(jid,body.video_id,actor,key,'queued','windowing' if reuse else 'tracking',reuse,body.config.max_calls,0,0,0,now(),now(),json.dumps(payload)))
    repo.audit(jid,'created',{'actor':actor,'config':body.config.model_dump()})
    return repo.job(jid,actor),True


def launch(jid):
    if os.getenv('INTICLASS_EXTERNAL_WORKER')=='1':return
    folder=ROOT/'data/analysis_private/logs';folder.mkdir(exist_ok=True,parents=True)
    with (folder/(jid+'.log')).open('ab') as log:
        subprocess.Popen([sys.executable,str(ROOT/'scripts/run_automatic_analysis.py'),'--job-id',jid],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def control(service,jid,body,actor):
    repo=Repository(service.repo);job=repo.job(jid,actor)
    if job['status']=='completed':raise ValueError('completed_job_is_immutable; use_a_new_configuration')
    if body.action in {'pause','cancel'}:
        if job['status']=='running':repo.update(jid,status='pausing' if body.action=='pause' else 'cancelling')
        else:repo.update(jid,status='paused' if body.action=='pause' else 'cancelled')
    else:
        # An active worker holds the OS job lock. Never run a second copy.
        lock=FileLock(str(ROOT/'data/analysis_private'/(jid+'.lock')))
        try:lock.acquire(timeout=0)
        except Timeout:raise ValueError('worker_active; wait_for_pause_or_cancel_completion')
        else:lock.release()
        if body.additional_calls:
            ceiling=20000 if job['config_and_runtime']['config'].get('analysis_mode')=='full_timeline' else 2000
            repo.update(jid,call_limit=min(ceiling,job['call_limit']+body.additional_calls))
        with service.repo.connection() as c:c.execute("UPDATE discovery_windows SET status='pending' WHERE job_id=? AND status IN ('running','error')",(jid,))
        repo.update(jid,status='queued');launch(jid)
    repo.audit(jid,body.action,{'actor':actor,'additional_calls':body.additional_calls})
    return repo.job(jid,actor)
