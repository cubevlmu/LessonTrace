import hashlib
import json
from pathlib import Path
from fastapi import FastAPI,Depends,Request,Response,HTTPException,Form
from fastapi.responses import FileResponse,JSONResponse
from src.identity.store import IdentityStore,secure_directory
from src.identity.service import jpeg
from src.agent.visual_reviewer import VisualReviewer,VLMConfig
from .source import EvidenceSource
from .repository import AttributionRepository
from .service import AttributionService,bbox
from .visual import ActorReviewer
from .schemas import Generate,Verify,Human,Revoke


def public_review(r):
    value=json.loads(json.dumps(r))
    for f in value.get('frames',[]):f.pop('files',None)
    return value


def create_attribution_app(runtime,private_dir='data/attribution_private',identity_dir=None,reviewer=None):
    secure_directory(private_dir)
    protected={Path(runtime[k]).resolve() for k in ['tracking_db','registry_db']}
    if identity_dir:protected.add((Path(identity_dir)/'identity.db').resolve())
    if (Path(private_dir)/'attribution.db').resolve() in protected:raise ValueError('attribution_database_must_be_independent')
    access=IdentityStore(Path(private_dir)/'access')
    source=EvidenceSource(runtime['tracking_db'],runtime['registry_db'],runtime['video_root'])
    repo=AttributionRepository(Path(private_dir)/'attribution.db')
    service=AttributionService(source,repo,identity_dir)
    visual=ActorReviewer(service,Path(private_dir)/'evidence',reviewer)
    app=FastAPI(title='IntiClass event attribution',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.attribution=service;app.state.access=access;app.state.visual=visual
    cookie='inticlass_attribution_'+access.namespace[:12]

    @app.exception_handler(ValueError)
    async def invalid(request,error):return JSONResponse({'detail':str(error)},422)

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.url.hostname not in {'127.0.0.1','localhost','::1','testserver'}:return Response('loopback_host_required',403)
        origin=request.headers.get('origin')
        if request.method not in {'GET','HEAD','OPTIONS'} and ((origin and origin!=str(request.base_url).rstrip('/')) or request.headers.get('sec-fetch-site')=='cross-site'):
            return Response('same_origin_required',403)
        r=await call_next(request)
        r.headers['Cache-Control']='no-store';r.headers['X-Content-Type-Options']='nosniff';r.headers['Referrer-Policy']='no-referrer';r.headers['X-Frame-Options']='DENY'
        return r

    def account(request:Request):
        try:return access.account(request.cookies.get(cookie,''))
        except PermissionError:raise HTTPException(401,'attribution_login_required')

    def review_role(a=Depends(account)):
        if a['role'] not in {'admin','reviewer'}:raise HTTPException(403,'reviewer_role_required')
        return a

    @app.get('/event-attribution')
    @app.get('/person-review')
    @app.get('/')
    def page():return FileResponse(Path(__file__).resolve().parents[2]/'webui/event_attribution.html',media_type='text/html')

    @app.post('/api/attribution/login')
    def login(response:Response,token:str=Form(...)):
        try:a=access.account(token)
        except PermissionError:raise HTTPException(401,'invalid_attribution_token')
        response.set_cookie(cookie,token,httponly=True,samesite='strict',path='/api/attribution',max_age=8*3600)
        return a

    @app.post('/api/attribution/logout')
    def logout(response:Response):
        response.delete_cookie(cookie,path='/api/attribution');return {'logged_out':True}

    @app.get('/api/attribution/status')
    def status(a=Depends(account)):
        return {'account':a,'vlm':visual.reviewer.availability(),'model_id':visual.reviewer.config.model_id,'max_calls':visual.reviewer.config.max_calls,
                'budget_seconds':visual.reviewer.config.max_elapsed_seconds,'anonymous_tracks_supported':True}

    @app.get('/api/attribution/runs')
    def runs(a=Depends(account)):return source.runs()

    @app.get('/api/attribution/events')
    def events(run_id:str,a=Depends(account)):
        run,clip=source.run(run_id)
        return [{k:e[k] for k in ['event_id','video_id','action','t_start','t_end','region_xywh','frame_width','frame_height','source_kind','region_kind','excel_row']} for e in source.events(clip['video_id']) if e['t_end']>=clip['start'] and e['t_start']<=clip['end']]

    @app.post('/api/attribution/candidates')
    def generate(body:Generate,a=Depends(account)):return service.generate(body.event_id,body.run_id)

    @app.get('/api/attribution/observations')
    def observations(candidate_id:str,a=Depends(account)):
        d,_,_,_=service.candidate(candidate_id)
        return [o for o in source.observations(d['run_id'],d['track_id']) if d['start']<=o['global_timestamp']<=d['end']]

    @app.get('/api/attribution/tracks')
    def tracks(run_id:str,a=Depends(account)):return source.tracks(run_id)

    @app.get('/api/attribution/track-observations')
    def track_observations(run_id:str,track_id:int,a=Depends(account)):
        source.verified_run(run_id)
        return source.observations(run_id,track_id)

    @app.get('/api/attribution/frame')
    def frame(candidate_id:str,frame_index:int,a=Depends(account)):
        import cv2
        d,e,_,_=service.candidate(candidate_id)
        image,o,_,_=source.frame(d['run_id'],d['track_id'],frame_index)
        if not d['start']<=o['global_timestamp']<=d['end']:raise ValueError('frame_outside_event_scope')
        h,w=image.shape[:2];x,y,bw,bh=e['region_xywh']
        region=[x/e['frame_width']*w,y/e['frame_height']*h,(x+bw)/e['frame_width']*w,(y+bh)/e['frame_height']*h]
        cv2.rectangle(image,tuple(map(int,region[:2])),tuple(map(int,region[2:])),(0,150,255),3)
        for other in source.rows('SELECT * FROM track_observations WHERE run_id=? AND frame_index=?',(d['run_id'],frame_index)):
            b=bbox(other);selected=other['track_id']==d['track_id'];color=(255,220,0) if selected else (160,160,160)
            cv2.rectangle(image,tuple(map(int,b[:2])),tuple(map(int,b[2:])),color,4 if selected else 1)
            cv2.putText(image,'Track '+str(other['track_id']),(int(b[0]),max(18,int(b[1])-4)),cv2.FONT_HERSHEY_SIMPLEX,.55,color,2)
        return Response(jpeg(image),media_type='image/jpeg')

    @app.get('/api/attribution/media')
    def media(run_id:str,a=Depends(account)):
        from scripts.prepare_identity_playback import prepare
        run,_=source.verified_run(run_id);prepare(source,run_id)
        return FileResponse(source.tracking_db.parent/'tracking_media'/(run['metadata']['source_sha256']+'.mp4'),media_type='video/mp4')

    @app.post('/api/attribution/visual-reviews',status_code=201)
    def verify(body:Verify,a=Depends(review_role)):return public_review(visual.run(body))

    @app.get('/api/attribution/visual-reviews')
    def reviews(candidate_id:str,a=Depends(account)):
        service.candidate(candidate_id)
        with repo.connection() as c:return [public_review(json.loads(r[0])) for r in c.execute('SELECT payload FROM event_track_reviews WHERE candidate_id=? ORDER BY created_at',(candidate_id,))]

    @app.get('/api/attribution/reviews/{review_id}/frames/{frame_id}/{view}')
    def evidence(review_id:str,frame_id:str,view:str,a=Depends(account)):
        r=repo.payload('event_track_reviews','review_id',review_id);service.candidate(r['candidate_id'])
        f=next((f for f in r['frames'] if f['frame_id']==frame_id),None)
        if not f or view not in {'full','roi','input_full','input_roi','input_combined'}:raise HTTPException(404,'evidence_not_registered')
        p=Path(f['files'][view]).resolve()
        if visual.root not in p.parents or not p.is_file():raise HTTPException(404,'evidence_file_missing')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256'][view]:raise HTTPException(409,'evidence_hash_changed')
        return FileResponse(p,media_type='image/jpeg')

    @app.post('/api/attribution/human-reviews',status_code=201)
    def human(body:Human,a=Depends(review_role)):return service.human(body,a['actor'])

    @app.post('/api/attribution/bindings/{bid}/revoke')
    def revoke(bid:str,body:Revoke,a=Depends(review_role)):return service.revoke(bid,body.reason,a['actor'])

    @app.get('/api/attribution/timeline')
    def timeline(run_id:str,track_id:int,a=Depends(account)):
        r=service.timeline(run_id,track_id,include_identity=a['role'] in {'admin','reviewer'})
        r['visual_reviews']=[public_review(x) for x in r['visual_reviews']]
        return r

    @app.get('/api/attribution/history')
    def history(binding_id:str,a=Depends(account)):
        with repo.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM attribution_audit WHERE binding_id=? ORDER BY created_at',(binding_id,))]

    return app
