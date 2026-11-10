"""Separate opt-in localhost service; existing replay and tracking remain unchanged."""
import json
import threading
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from .store import IdentityStore
from .tracks import TrackAccess
from .face_backend import PersonBackend
from .service import IdentityService, jpeg
from .schemas import Enrollment, VideoAuthorization, Capture, Binding, Change, Split, Match


def create_identity_app(private_dir,tracking_db,registry_db,video_root,*,enable_matching=False,model_root=None,backend=None):
    store=IdentityStore(private_dir)
    tracks=TrackAccess(tracking_db,registry_db,video_root)
    service=IdentityService(store,tracks,backend or PersonBackend(model_root,enabled=enable_matching))
    app=FastAPI(title='IntiClass local identity review',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.identity=service
    cookie='inticlass_identity_'+store.namespace[:12]
    busy=threading.Lock()

    @app.exception_handler(ValueError)
    async def invalid(request,error):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':str(error)},status_code=422)

    @app.middleware('http')
    async def boundaries(request,call_next):
        if request.url.hostname not in {'127.0.0.1','localhost','::1','testserver'}:
            return Response('local_host_required',403)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return Response('same_origin_required',403)
            if request.headers.get('sec-fetch-site')=='cross-site': return Response('same_origin_required',403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        return response

    def account(request:Request):
        try: return store.account(request.cookies.get(cookie,''))
        except PermissionError: raise HTTPException(401,'identity_login_required')

    def roles(*allowed):
        def guard(a=Depends(account)):
            if a['role'] not in allowed: raise HTTPException(403,'identity_role_not_authorized')
            return a
        return guard

    admin=roles('admin');review=roles('admin','reviewer')

    @app.get('/identity')
    @app.get('/')
    def page(): return FileResponse(Path(__file__).resolve().parents[2]/'webui/identity.html',media_type='text/html')

    @app.post('/api/identity/login')
    def login(request:Request, response:Response, token:str=Form(...)):
        try: a=store.account(token)
        except PermissionError: raise HTTPException(401,'invalid_identity_access_token')
        response.set_cookie(cookie,token,httponly=True,samesite='strict',path='/api/identity',max_age=8*3600)
        return a

    @app.post('/api/identity/logout')
    def logout(response:Response):
        response.delete_cookie(cookie,path='/api/identity')
        return {'logged_out':True}

    @app.get('/api/identity/status')
    def status(a=Depends(account)):
        return {'account':a,**service.backend.probe(),'tracking_continuity':'unverified','track_id_is_student_identity':False}

    @app.get('/api/identity/subjects')
    def subjects(a=Depends(account)):
        with store.connection() as c:
            ids=[r[0] for r in c.execute('SELECT subject_id FROM registered_subjects ORDER BY created_at')]
            result=[]
            for sid in ids:
                s=store.subject(sid,c,False)
                try: store.subject(sid,c)
                except ValueError: s['effective_status']='inactive'
                else: s['effective_status']='active'
                result.append(s)
            return result

    @app.post('/api/identity/subjects',status_code=201)
    def enroll(body:Enrollment,a=Depends(admin)): return service.enroll(body,a['actor'])

    @app.post('/api/identity/subjects/{sid}/revoke')
    def revoke_subject(sid:str,body:Change,a=Depends(admin)):
        store.purge(sid,a['actor'])
        return {'status':'revoked','biometrics_purged':True}

    @app.get('/api/identity/references')
    def references(subject_id:str,a=Depends(account)):
        store.subject(subject_id)
        with store.connection() as c:
            return [dict(r) for r in c.execute('SELECT reference_image_id,subject_id,provenance,status,model_id,created_at FROM reference_images WHERE subject_id=?',(subject_id,))]

    @app.post('/api/identity/references',status_code=201)
    async def upload(subject_id:str=Form(...),photo:UploadFile=File(...),a=Depends(admin)):
        return service.reference(subject_id,await photo.read(8*1024*1024+1),{'kind':'authorized_upload','independent_identity_validation':False},a['actor'])

    @app.get('/api/identity/references/{ref_id}/image')
    def reference_image(ref_id:str,a=Depends(account)):
        with store.connection() as c:
            r=c.execute('SELECT * FROM reference_images WHERE reference_image_id=?',(ref_id,)).fetchone()
            if not r or r['status']!='active' or r['image'] is None: raise HTTPException(404,'reference_deleted_or_missing')
            store.subject(r['subject_id'],c)
            return Response(store.unseal(r['image'],True),media_type='image/jpeg')

    @app.delete('/api/identity/references/{ref_id}')
    def delete_reference(ref_id:str,a=Depends(admin)):
        with store.connection() as c: r=c.execute('SELECT subject_id FROM reference_images WHERE reference_image_id=?',(ref_id,)).fetchone()
        if not r: raise HTTPException(404,'reference_not_found')
        store.purge(r[0],a['actor'],ref_id)
        return {'status':'deleted','features_purged':True}

    @app.post('/api/identity/video-authorizations',status_code=201)
    def authorize(body:VideoAuthorization,a=Depends(admin)): return service.authorize_video(body,a['actor'])

    @app.post('/api/identity/references/capture',status_code=201)
    def capture(body:Capture,a=Depends(admin)): return service.capture(body,a['actor'])

    @app.get('/api/identity/runs')
    def runs(a=Depends(account)): return tracks.runs()

    @app.get('/api/identity/tracks')
    def track_list(run_id:str,a=Depends(account)): return tracks.tracks(run_id)

    @app.get('/api/identity/observations')
    def observations(run_id:str,track_id:int,a=Depends(account)): return tracks.observations(run_id,track_id)

    @app.get('/api/identity/frame')
    def frame(run_id:str,track_id:int,frame_index:int,a=Depends(account)):
        import cv2
        image,o,_,_=tracks.frame(run_id,track_id,frame_index)
        x1,y1,x2,y2=[int(o[k]) for k in ('bbox_x1','bbox_y1','bbox_x2','bbox_y2')]
        cv2.rectangle(image,(x1,y1),(x2,y2),(0,220,240),2)
        return Response(jpeg(image),media_type='image/jpeg')

    @app.get('/api/identity/media')
    def media(run_id:str,a=Depends(account)):
        from src.tracking.video import safe_path,sha256
        run,clip=tracks.run(run_id)
        source=safe_path(clip,tracks.video_root)
        if sha256(source)!=run['metadata']['source_sha256']: raise ValueError('tracking_source_content_changed')
        # Only serve previously validated 11A derived media; no arbitrary-path media API.
        cache=tracks.tracking_db.parent/'tracking_media'
        key=run['metadata']['source_sha256']
        video=cache/(key+'.mp4');audit=cache/(key+'.json')
        if not video.is_file() or not audit.is_file(): raise HTTPException(422,'validated_11a_playback_copy_missing; open_the_run_on_tracking_page_first')
        m=json.loads(audit.read_text())
        if m['max_pts_error_seconds']>.002: raise ValueError('playback_timebase_not_validated')
        return FileResponse(video,media_type='video/mp4')

    @app.post('/api/identity/bindings',status_code=201)
    def bind(body:Binding,a=Depends(review)): return service.bind(body,a['actor'])

    @app.get('/api/identity/bindings')
    def bindings(subject_id:str|None=None,a=Depends(account)): return service.bindings(subject_id)

    @app.get('/api/identity/subjects/{sid}/confirmed-tracks')
    def confirmed_tracks(sid:str,a=Depends(account)):
        store.subject(sid)
        return [b for b in service.bindings(sid) if b['effective_status']=='human_confirmed']

    @app.post('/api/identity/bindings/{bid}/revoke')
    def revoke(bid:str,body:Change,a=Depends(review)): return service.change_binding(bid,body,a['actor'])

    @app.post('/api/identity/bindings/{bid}/split')
    def split(bid:str,body:Split,a=Depends(review)): return service.change_binding(bid,body,a['actor'],True)

    @app.get('/api/identity/history')
    def history(object_id:str,a=Depends(account)):
        with store.connection() as c:
            return [{**dict(r),'payload':store.unseal(r['payload'])} for r in c.execute('SELECT * FROM identity_review_history WHERE object_id=? ORDER BY created_at',(object_id,))]

    @app.post('/api/identity/match')
    def match(body:Match,a=Depends(admin)):
        if not busy.acquire(False): raise HTTPException(409,'identity_matching_busy')
        try: return service.match(body,a['actor'])
        finally: busy.release()

    @app.get('/api/identity/candidates')
    def candidates(run_id:str,track_id:int,a=Depends(account)):
        tracks.run(run_id)
        with store.connection() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM identity_candidates WHERE run_id=? AND track_id=? ORDER BY created_at',(run_id,track_id))]
            for r in rows:
                try:
                    if r['subject_id']: store.subject(r['subject_id'],c)
                    for fid in json.loads(r['face_observation_ids']):
                        f=c.execute('SELECT authorization_id,status FROM face_observations WHERE face_id=?',(fid,)).fetchone()
                        if not f or f['status']=='purged': raise ValueError('evidence_purged')
                        service.authorization(f['authorization_id'],run_id,c)
                except ValueError:
                    r['effective_status']='unknown';r['rejection_reason']='authorization_inactive_or_evidence_purged'
                else: r['effective_status']=r['status']
            return rows

    @app.get('/api/identity/faces/{fid}/image')
    def face_image(fid:str,a=Depends(account)):
        with store.connection() as c:
            r=c.execute('SELECT * FROM face_observations WHERE face_id=?',(fid,)).fetchone()
            if not r or r['image'] is None: raise HTTPException(404,'face_evidence_missing_or_purged')
            service.authorization(r['authorization_id'],r['run_id'],c)
            return Response(store.unseal(r['image'],True),media_type='image/jpeg')

    return app
