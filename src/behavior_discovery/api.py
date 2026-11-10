import json
from pathlib import Path
from fastapi import Depends,File,UploadFile,HTTPException
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from src.identity.store import uid,secure_directory
from src.agent.fusion.planning import ReviewRequest
from .schemas import Start,Control,ReportOptions
from .service import DiscoveryService
from .ingest import ingest
from .jobs import create,launch,control

ROOT=Path(__file__).resolve().parents[2]


def mount(app,context,agent,account,writer):
    svc=DiscoveryService(context);app.state.discovery=svc
    @app.get('/analyze')
    def page():return FileResponse(ROOT/'webui/analyze.html',media_type='text/html')
    @app.post('/api/context/analysis/videos',status_code=201)
    async def upload(file:UploadFile=File(...),a=Depends(writer)):
        folder=secure_directory(ROOT/'data/analysis_private/uploads');temp=folder/(uid()+'.partial');size=0
        try:
            with temp.open('xb') as out:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>512*1024*1024:raise ValueError('video_upload_limit_512_MiB')
                    out.write(chunk)
            return await run_in_threadpool(ingest,context,temp,file.filename or '',a['actor'])
        finally:temp.unlink(missing_ok=True)
    @app.get('/api/context/analysis/videos')
    def videos(a=Depends(account)):
        with context.repo.connection() as c:rows=[dict(r) for r in c.execute('SELECT * FROM analysis_videos WHERE owner=? ORDER BY created_at DESC',(a['actor'],))]
        for r in rows:
            r['metadata']={k:v for k,v in json.loads(r.pop('payload')).items() if not k.endswith('_path')}
        return rows
    @app.delete('/api/context/analysis/videos/{vid}')
    def delete(vid:str,a=Depends(writer)):return svc.delete(vid,a['actor'])
    @app.post('/api/context/analysis/jobs',status_code=201)
    def start(body:Start,a=Depends(writer)):
        import os
        if os.getenv('INTICLASS_PROFILE')=='product':
            full=body.config.analysis_mode=='full_timeline'
            body=body.model_copy(update={'config':body.config.model_copy(update={'device':os.getenv('INTICLASS_ANALYSIS_DEVICE','cpu'),'timeout':float(os.getenv('INTICLASS_VLM_TIMEOUT','240')),'frames':4 if full else 2,'input_long_edge':512 if full else 320})})
        j,fresh=create(context,body,a['actor'])
        if fresh:launch(j['job_id'])
        return {'job':j,'created':fresh}
    @app.get('/api/context/analysis/jobs/{jid}')
    def job(jid:str,a=Depends(account)):return svc.repo.job(jid,a['actor'])
    @app.get('/api/context/analysis/jobs')
    def jobs(a=Depends(account)):
        with context.repo.connection() as c:ids=[r[0] for r in c.execute('SELECT job_id FROM analysis_jobs WHERE owner=? ORDER BY created_at DESC',(a['actor'],))]
        return [svc.repo.job(j,a['actor']) for j in ids]
    @app.post('/api/context/analysis/jobs/{jid}/control')
    def act(jid:str,body:Control,a=Depends(writer)):return control(context,jid,body,a['actor'])
    @app.get('/api/context/analysis/jobs/{jid}/events')
    def events(jid:str,a=Depends(account)):
        j=svc.repo.job(jid,a['actor'])
        if not j['run_id']:return []
        with context.repo.connection() as c:observations={r['window_id']:json.loads(r['payload']).get('result',{}).get('observations',{}) for r in c.execute('SELECT * FROM discovery_windows WHERE job_id=?',(jid,))}
        from src.agent.visual_evidence_policy import semantic_findings
        result=[]
        for event in svc.repo.events(j['run_id']):
            if event['job_id']!=jid:continue
            e=svc.public_event(event)
            e['display_blind_observations']=[o for wid in e['window_ids'] for o in observations.get(wid,{}).get('observed_actions',[]) if not semantic_findings(o['action'])]
            result.append(e)
        return result
    @app.get('/api/context/analysis/jobs/{jid}/windows')
    def windows(jid:str,a=Depends(account)):
        svc.repo.job(jid,a['actor'])
        with context.repo.connection() as c:rows=[dict(r) for r in c.execute('SELECT * FROM discovery_windows WHERE job_id=? ORDER BY start,track_id',(jid,))]
        for r in rows:
            p=json.loads(r['payload']);result=p.get('result',{});r['summary']={'observations':result.get('observations'),'unknown_or_empty':result.get('unknown_or_empty'),'error':result.get('classification_error') if result else p.get('error'),
                'frames':[{k:v for k,v in f.items() if k!='files'} for f in result.get('frames',[])]};r.pop('payload')
        return rows
    @app.get('/api/context/analysis/windows/{wid}/frames/{fid}')
    def window_frame(wid:str,fid:str,a=Depends(account)):
        with context.repo.connection() as c:r=c.execute('SELECT job_id,payload FROM discovery_windows WHERE window_id=?',(wid,)).fetchone()
        if not r:raise ValueError('discovery_window_missing')
        job=svc.repo.job(r['job_id'],a['actor'])
        if svc.repo.video(job['video_id'],a['actor'])['status']!='active':raise ValueError('analysis_video_deleted')
        f=next((f for f in json.loads(r['payload']).get('result',{}).get('frames',[]) if f['frame_id']==fid),None)
        if not f:raise ValueError('window_evidence_frame_missing')
        from src.tracking.video import sha256
        path=Path(f['files']['input_combined']).resolve();root=(ROOT/'data/analysis_private/evidence').resolve()
        if root not in path.parents or not path.is_file() or sha256(path)!=f['sha256']['input_combined']:raise ValueError('evidence_frame_missing_or_modified')
        return FileResponse(path,media_type='image/jpeg')
    @app.get('/api/context/analysis/frames/{event_id}/{frame_id}')
    def frame(event_id:str,frame_id:str,a=Depends(account)):
        with context.repo.connection() as c:r=c.execute('SELECT payload FROM discovered_behavior_events WHERE event_id=?',(event_id,)).fetchone()
        if not r:raise ValueError('discovered_event_missing')
        e=json.loads(r[0])
        if svc.repo.video(e['video_id'],a['actor'])['status']!='active':raise ValueError('analysis_video_deleted')
        f=next((f for f in e['frames'] if f['frame_id']==frame_id),None)
        if not f:raise ValueError('evidence_frame_missing')
        path=Path(f['files']['input_combined']).resolve();allowed=(ROOT/'data/analysis_private/evidence').resolve()
        from src.tracking.video import sha256
        if allowed not in path.parents or not path.exists() or sha256(path)!=f['sha256']['input_combined']:raise ValueError('evidence_frame_missing_or_modified')
        return FileResponse(path,media_type='image/jpeg')
    @app.post('/api/context/analysis/jobs/{jid}/report',status_code=201)
    def report(jid:str,body:ReportOptions|None=None,a=Depends(writer)):
        j=svc.repo.job(jid,a['actor'])
        if not j['run_id']:raise ValueError('tracking_not_complete')
        return agent.ask_fusion(ReviewRequest(run_id=j['run_id'],question='生成这段课堂录像的复盘报告：概括动作记录、人物轨迹和教学资料，说明已分析范围。',document_ids=body.document_ids if body else [],use_llm=False),app.state.fusion,a['actor'],True)
