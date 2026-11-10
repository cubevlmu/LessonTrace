import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI,Depends,HTTPException,Request,Response,UploadFile,File,Form
from fastapi.responses import FileResponse,JSONResponse,RedirectResponse
from src.identity.store import IdentityStore,secure_directory,now
from src.attribution.source import EvidenceSource
from src.agent.review_qa import EvidenceReviewAgent
from .repository import ContextRepository
from .service import ContextService
from .retrieval import ReadOnlyAttribution,ReadOnlyEvidence,search
from .schemas import DocumentInput,ActivityInput,AlignmentInput,Reason,Share,Search,Question
from .temporal import explicit_suggestions
from .analysis import ContextAnalysis


def create_context_app(runtime,private_dir=None,identity_dir='data/identity_private'):
    from src.runtime_paths import data_path
    if private_dir is None:private_dir=data_path('context_private')
    folder=secure_directory(private_dir);access=IdentityStore(folder/'access')
    protected={Path(runtime[k]).resolve() for k in ['registry_db','tracking_db','attribution_db']}
    if (folder/'context.db').resolve() in protected:raise ValueError('context_database_must_be_independent')
    source=EvidenceSource(runtime['tracking_db'],runtime['registry_db'],runtime['video_root'])
    repo=ContextRepository(folder/'context.db');service=ContextService(repo,access,source)
    service.attribution_repo=ReadOnlyAttribution(runtime['attribution_db']);service.identity_dir=Path(identity_dir).resolve() if identity_dir else None
    agent=EvidenceReviewAgent(ReadOnlyEvidence(source.registry_db),None,source.video_root,folder/'evidence')
    workflow=ContextAnalysis(agent,service)
    app=FastAPI(title='IntiClass teacher context',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.context=service;app.state.access=access;app.state.agent=agent;app.state.workflow=workflow
    cookie='inticlass_context_'+access.namespace[:12]
    session_cookie='inticlass_session_'+access.namespace[:12]

    @app.exception_handler(ValueError)
    async def invalid(request,error):return JSONResponse({'detail':str(error)},422)
    @app.exception_handler(PermissionError)
    async def denied(request,error):return JSONResponse({'detail':str(error)},403)
    @app.middleware('http')
    async def boundary(request,call_next):
        product=os.getenv('INTICLASS_PROFILE')=='product'
        if product and (request.url.path in {'/context','/lesson-review','/analyze','/review','/validation','/tracking','/identity','/event-attribution','/person-review','/gold','/demo'} or request.url.path.startswith(('/api/context/validation/','/app/demo'))):return Response('not_found',404)
        public=os.getenv('INTICLASS_PUBLIC_ORIGIN','').rstrip('/')
        hosts={'127.0.0.1','localhost','::1','testserver'}
        if public:
            configured=urlsplit(public)
            if configured.scheme!='https' or configured.path not in {'','/'} or configured.username or configured.password:raise ValueError('invalid_public_https_origin')
            hosts.add(configured.hostname)
        if request.url.hostname not in hosts:return Response('configured_host_required',403)
        origin=request.headers.get('origin')
        if request.method not in {'GET','HEAD','OPTIONS'} and ((origin and origin not in {str(request.base_url).rstrip('/'),public}) or request.headers.get('sec-fetch-site')=='cross-site'):
            return Response('same_origin_required',403)
        r=await call_next(request);r.headers['Cache-Control']='no-store';r.headers['X-Content-Type-Options']='nosniff';r.headers['Referrer-Policy']='no-referrer';r.headers['X-Frame-Options']='DENY';return r
    async def account(request:Request):
        try:
            session=request.cookies.get(session_cookie,'')
            if session:a=access.session_account(session)
            elif os.getenv('INTICLASS_PROFILE')!='product':a=access.account(request.cookies.get(cookie,''))
            else:raise PermissionError('account_session_required')
        except PermissionError:raise HTTPException(401,'context_login_required')
        from src.behavior_discovery.ingest import authorize_run
        rid=request.query_params.get('run_id')
        cid=request.query_params.get('clip_id')
        if request.method in {'POST','PUT','PATCH'} and 'application/json' in request.headers.get('content-type',''):
            try:body=await request.json()
            except ValueError:body={}
            if isinstance(body,dict):rid=body.get('run_id',rid);cid=body.get('clip_id',cid)
        if rid:authorize_run(service,rid,a['actor'],a['role'])
        if cid:
            from src.tracking.video import registered_clip
            clip=registered_clip(source.registry_db,clip_id=cid)
            if os.getenv('INTICLASS_PROFILE')=='product' and clip.get('source_kind')!='uploaded_recording':raise PermissionError('historical_experiment_not_a_product_resource')
            if clip.get('source_kind')=='uploaded_recording' and clip.get('owner')!=a['actor'] and a['role']!='admin':raise PermissionError('uploaded_video_access_denied')
        return a
    def writer(a=Depends(account)):
        if a['role'] not in {'admin','teacher','reviewer'}:raise PermissionError('context_writer_required')
        return a
    @app.get('/api/context/health')
    def health():return {'status':'ok','build':'12c','profile':os.getenv('INTICLASS_PROFILE','development'),'scope':'HTTP liveness only; model readiness is separate'}
    @app.get('/context')
    @app.get('/lesson-review')
    @app.get('/')
    def page():
        if os.getenv('INTICLASS_PROFILE')=='product':return RedirectResponse('/app')
        return FileResponse(Path(__file__).resolve().parents[2]/'webui/context.html',media_type='text/html')
    @app.post('/api/context/login')
    def login(response:Response,username:str|None=Form(None,max_length=120),password:str|None=Form(None,max_length=256),token:str|None=Form(None,max_length=200)):
        try:
            if username is not None and password is not None:
                user=access.authenticate_password(username,password)
                session=access.create_session(user['actor'])
                response.set_cookie(session_cookie,session,httponly=True,samesite='strict',secure=os.getenv('INTICLASS_SECURE_COOKIE','1' if os.getenv('INTICLASS_PUBLIC_ORIGIN') else '0')=='1',path='/api/context',max_age=8*3600)
                return {'actor':user['actor'],'username':user['username'],'role':user['role'],'must_change_password':bool(user['must_change_password'])}
            if token and os.getenv('INTICLASS_PROFILE')!='product':
                a=access.account(token)
                response.set_cookie(cookie,token,httponly=True,samesite='strict',path='/api/context',max_age=8*3600);return a
        except PermissionError:raise HTTPException(401,'invalid_context_login')
        raise HTTPException(422,'username_password_or_legacy_token_required')
    @app.post('/api/context/logout')
    def logout(request:Request,response:Response):
        access.revoke_session(request.cookies.get(session_cookie,''));response.delete_cookie(cookie,path='/api/context');response.delete_cookie(session_cookie,path='/api/context');return {'logged_out':True}
    @app.post('/api/context/users',status_code=201)
    def create_user(body:dict,a=Depends(account)):
        if a['role']!='admin': raise HTTPException(403,'admin_required')
        return access.create_user(body.get('username',''),body.get('password',''),body.get('role','teacher'),a['actor'])
    @app.post('/api/context/password')
    def change_password(body:dict,request:Request,a=Depends(account)):
        current=str(body.get('current_password',''));new=str(body.get('new_password',''))
        if a.get('username') is None: raise HTTPException(409,'password_change_requires_account_login')
        access.authenticate_password(a['username'],current)
        with access.connection() as c:
            c.execute('UPDATE teacher_users SET password_hash=?,updated_at=?,must_change_password=0 WHERE actor=?',(access.password_hash(new),now(),a['actor']))
            current_hash=__import__('hashlib').sha256(request.cookies.get(session_cookie,'').encode()).hexdigest()
            c.execute('DELETE FROM teacher_sessions WHERE actor=? AND session_hash<>?',(a['actor'],current_hash))
            access.audit(c,a['actor'],'password_changed',a['actor'],{})
        return {'changed':True}
    @app.get('/api/context/documents')
    def documents(a=Depends(account)):return repo.documents(a['actor'])
    @app.post('/api/context/documents',status_code=201)
    def text(body:DocumentInput,a=Depends(writer)):
        return service.ingest('typed-note.txt',body.text.encode('utf-8'),body.title,body.source_type,a['actor'],body.is_example,body.scope)
    @app.post('/api/context/upload',status_code=201)
    async def upload(file:UploadFile=File(...),title:str=Form(...),source_type:str=Form(...),is_example:bool=Form(False),scope:str=Form('{}'),a=Depends(writer)):
        if len(title)>200 or not title.strip():raise ValueError('invalid_document_title')
        DocumentInput(title=title,source_type=source_type,text='validate',is_example=is_example)
        from .schemas import MaterialScope
        metadata=MaterialScope.model_validate_json(scope)
        return service.ingest(file.filename,await file.read(10*1024*1024+1),title,source_type,a['actor'],is_example,metadata)
    @app.get('/api/context/documents/{did}')
    def detail(did:str,a=Depends(account)):return service.detail(did,a['actor'])
    @app.get('/api/context/passages/{pid}')
    def passage(pid:str,a=Depends(account)):return repo.passage(pid,a['actor'])
    @app.get('/api/context/documents/{did}/original')
    def original(did:str,a=Depends(account)):
        d=repo.document(did,a['actor']);return Response(access.unseal(d['original'],True),media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="'+d['document_id']+d['file_type']+'"'})
    @app.delete('/api/context/documents/{did}')
    def delete(did:str,a=Depends(writer)):return service.delete(did,a['actor'])
    @app.post('/api/context/documents/{did}/share')
    def share(did:str,body:Share,a=Depends(writer)):
        with repo.connection() as c:
            repo.document(did,a['actor'],True,c)
            if body.allow:c.execute('INSERT OR IGNORE INTO document_grants VALUES (?,?)',(did,body.actor))
            else:c.execute('DELETE FROM document_grants WHERE document_id=? AND actor=?',(did,body.actor))
            repo.audit(c,did,a['actor'],'document_permission_changed',body.model_dump())
        return {'shared':body.allow,'permission':'read_only'}
    @app.get('/api/context/documents/{did}/suggestions')
    def suggestions(did:str,a=Depends(account)):
        d=service.detail(did,a['actor']);return [s for p in d['passages'] for s in explicit_suggestions(p)]
    @app.post('/api/context/activities',status_code=201)
    def activity(body:ActivityInput,a=Depends(writer)):return service.activity(body,a['actor'])
    @app.get('/api/context/activities')
    def activities(clip_id:str|None=None,lesson_id:str|None=None,a=Depends(account)):return service.activities(a['actor'],clip_id,lesson_id)
    @app.post('/api/context/activities/{aid}/revoke')
    def revoke_activity(aid:str,body:Reason,a=Depends(writer)):return service.revoke('activity',aid,a['actor'],body.reason)
    @app.post('/api/context/alignments',status_code=201)
    def alignment(body:AlignmentInput,a=Depends(writer)):return service.link(body,a['actor'])
    @app.get('/api/context/alignments')
    def alignments(a=Depends(account)):
        allowed={d['document_id'] for d in repo.documents(a['actor'])}
        with repo.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM context_video_links ORDER BY created_at') if r['document_id'] in allowed]
    @app.post('/api/context/alignments/{lid}/revoke')
    def revoke_link(lid:str,body:Reason,a=Depends(writer)):return service.revoke('link',lid,a['actor'],body.reason)
    @app.post('/api/context/search')
    def find(body:Search,a=Depends(account)):return search(service,body,a['actor'])
    @app.get('/api/context/knowledge')
    def knowledge(a=Depends(account)):
        with repo.connection() as c:return [json.loads(r[0]) for r in c.execute('SELECT payload FROM knowledge_rules ORDER BY category,rule_id')]
    @app.get('/api/context/runs')
    def runs(a=Depends(account)):
        from src.behavior_discovery.ingest import authorize_run
        result=[]
        for r in source.runs():
            if r['status']!='completed':continue
            try:authorize_run(service,r['run_id'],a['actor'],a['role'])
            except (PermissionError,ValueError):continue
            result.append(r)
        return result
    @app.get('/api/context/tracks')
    def tracks(run_id:str,a=Depends(account)):return source.tracks(run_id)
    @app.get('/api/context/media')
    def media(run_id:str,a=Depends(account)):
        from scripts.prepare_identity_playback import prepare
        run,_=source.verified_run(run_id);prepare(source,run_id)
        return FileResponse(source.tracking_db.parent/'tracking_media'/(run['metadata']['source_sha256']+'.mp4'),media_type='video/mp4')
    @app.post('/api/context/analyze',status_code=201)
    def analyze(body:Question,a=Depends(account)):
        if body.include_identity and a['role'] not in {'admin','reviewer'}:raise PermissionError('identity_display_requires_review_role')
        return agent.ask_context(body,service,a['actor'])
    @app.get('/api/context/analyses/{aid}')
    def saved(aid:str,a=Depends(account)):
        result=workflow.load(aid,a['actor'])
        if a['role'] not in {'admin','reviewer'} and any(c.get('identities') for c in result.get('claims',[])):raise PermissionError('identity_display_requires_review_role')
        return result
    @app.post('/api/context/analyses/{aid}/refresh')
    def refresh(aid:str,a=Depends(account)):
        with repo.connection() as c:r=c.execute('SELECT owner,request FROM context_analyses WHERE analysis_id=?',(aid,)).fetchone()
        if not r:raise ValueError('analysis_not_found')
        if r['owner']!=a['actor']:raise PermissionError('analysis_access_denied')
        if json.loads(r['request']).get('include_identity') and a['role'] not in {'admin','reviewer'}:raise PermissionError('identity_display_requires_review_role')
        return agent.ask_context(json.loads(r['request']),service,a['actor'])
    @app.get('/api/context/audit')
    def audit(object_id:str,a=Depends(account)):
        with repo.connection() as c:
            d=c.execute('SELECT document_id FROM context_documents WHERE document_id=?',(object_id,)).fetchone()
            if not d:
                for table,key in [('teaching_activities','activity_id'),('context_video_links','link_id')]:
                    d=c.execute('SELECT document_id FROM '+table+' WHERE '+key+'=?',(object_id,)).fetchone()
                    if d:break
            if not d:raise ValueError('audit_object_not_found')
            repo.document(d[0],a['actor'])
            return [dict(r) for r in c.execute('SELECT * FROM context_audit WHERE object_id=? ORDER BY created_at',(object_id,))]
    from src.agent.fusion.api import mount_fusion
    mount_fusion(app,service,agent,account,writer)
    if os.getenv('INTICLASS_PROFILE')!='product':
        from src.validation.api import mount as mount_validation
        mount_validation(app,service,account,writer)
    from src.behavior_discovery.api import mount as mount_discovery
    mount_discovery(app,service,agent,account,writer)
    from .frontend import mount as mount_frontend
    mount_frontend(app,service,account)
    from .product import mount as mount_product
    mount_product(app,IdentityStore(identity_dir),account,service)
    from src.behavior_discovery.corrections import mount as mount_corrections
    mount_corrections(app,service,account,writer)
    from .workbench import mount as mount_workbench
    mount_workbench(app,service,account)
    from .person_profiles import mount as mount_person_profiles
    mount_person_profiles(app,service,account)
    from .material_library import mount as mount_material_library
    mount_material_library(app,service,account,writer)
    from src.inference.settings import mount as mount_model_settings
    mount_model_settings(app,service,account)
    from .lessons import mount as mount_lessons
    mount_lessons(app,service,account,writer)
    return app
