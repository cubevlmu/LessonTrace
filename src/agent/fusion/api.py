import json
from pathlib import Path
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse,Response
from .planning import ReviewRequest
from .orchestrator import FusionWorkflow
from . import reporting
from pydantic import BaseModel

class ConversationSelection(BaseModel):
    run_id:str
    last_report_id:str|None=None


def mount_fusion(app,service,agent,account,writer):
    workflow=FusionWorkflow(agent,service);app.state.fusion=workflow
    from src.context.lesson_reports import mount as mount_lesson_reports
    mount_lesson_reports(app,service,workflow,account,writer)
    def privileged(a):return a['role'] in {'admin','teacher','reviewer'}
    @app.get('/review')
    def page():return FileResponse(Path(__file__).resolve().parents[3]/'webui/review.html',media_type='text/html')
    @app.post('/api/context/fusion/ask',status_code=201)
    def ask(body:ReviewRequest,a=Depends(account)):
        return agent.ask_fusion(body,workflow,a['actor'],privileged(a))
    @app.get('/api/context/fusion/conversation')
    def conversation(run_id:str,last_report_id:str|None=None,a=Depends(account)):
        from .conversation import turns
        return turns(service,a['actor'],run_id,last_report_id,allow_identity=privileged(a),archive=True)
    @app.get('/api/context/fusion/conversations')
    def conversations(run_id:str,a=Depends(account)):
        from .conversation import threads
        return threads(service,a['actor'],run_id)
    @app.post('/api/context/fusion/conversation/select')
    def select_conversation(body:ConversationSelection,a=Depends(account)):
        from .conversation import select_head
        return select_head(service,a['actor'],body.run_id,body.last_report_id)
    @app.get('/api/context/fusion/reports/{rid}')
    def report(rid:str,a=Depends(account)):return reporting.load(service,rid,a['actor'],privileged(a))
    @app.get('/api/context/fusion/reports/{rid}/historical')
    def historical(rid:str,a=Depends(account)):return reporting.historical(service,rid,a['actor'],privileged(a))
    @app.post('/api/context/fusion/reports/{rid}/refresh',status_code=201)
    def refresh(rid:str,a=Depends(account)):
        with service.repo.connection() as c:r=c.execute('SELECT owner,request FROM fusion_reports WHERE report_id=?',(rid,)).fetchone()
        if not r:raise ValueError('fusion_report_not_found')
        if r['owner']!=a['actor']:raise PermissionError('fusion_report_access_denied')
        return agent.ask_fusion(json.loads(r['request']),workflow,a['actor'],privileged(a),rid)
    @app.get('/api/context/fusion/reports/{rid}/export/{format}')
    def export(rid:str,format:str,a=Depends(account)):
        r=reporting.load(service,rid,a['actor'],privileged(a))
        if r['status']!='current':raise HTTPException(409,'stale_report_recompute_required')
        if format=='json':data=json.dumps(r,ensure_ascii=False,indent=2);mime='application/json';suffix='.json'
        elif format=='markdown':data=reporting.markdown(r);mime='text/markdown';suffix='.md'
        else:raise ValueError('export_format_json_or_markdown')
        return Response(data,media_type=mime,headers={'Content-Disposition':'attachment; filename="inticlass_review_'+rid+suffix+'"'})
    @app.get('/api/context/fusion/reports/{rid}/history')
    def history(rid:str,a=Depends(account)):
        with service.repo.connection() as c:
            r=c.execute('SELECT owner FROM fusion_reports WHERE report_id=?',(rid,)).fetchone()
            if not r or r['owner']!=a['actor']:raise PermissionError('fusion_report_access_denied')
            return [dict(r) for r in c.execute('SELECT operation,created_at,payload FROM fusion_report_audit WHERE report_id=? ORDER BY created_at',(rid,))]
    @app.get('/api/context/fusion/scope')
    def scope(run_id:str,a=Depends(account)):
        run,clip=service.source.verified_run(run_id)
        return {k:clip[k] for k in ['clip_id','video_id','start','end','width','height']}
    @app.get('/api/context/fusion/observations')
    def observations(run_id:str,track_id:int,a=Depends(account)):
        service.source.verified_run(run_id)
        return service.source.observations(run_id,track_id)
    @app.get('/api/context/fusion/subjects')
    def subjects(run_id:str,a=Depends(account)):
        if not privileged(a):return []
        from src.attribution.source import IdentityReader
        run,clip=service.source.verified_run(run_id);result={}
        for t in service.source.tracks(run_id):
            for label in IdentityReader(service.identity_dir).intersect(run_id,t['track_id'],clip['start'],clip['end'],run['metadata']['source_sha256']):
                result[label['subject_id']]={'subject_id':label['subject_id'],'display_name':label['display_name'],'scope':'current_authorized_identity_interval_only'}
        return list(result.values())
