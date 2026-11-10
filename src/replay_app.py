"""Local classroom evidence replay server. Bind loopback only."""
import argparse
import math
import subprocess
import hashlib
import threading
import shutil
import html
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import ValidationError
from src.evidence_db import EvidenceDB
from src.export_for_webui import WebUIBridge
from src.schema import HumanRegionReview, VisualReviewRequest, ReviewQuestionRequest
from src.agent.visual_reviewer import VisualReviewer
from src.agent.review_qa import EvidenceReviewAgent
from src.human_gold import GoldStore
from src.gold_schema import GoldLogin


def blind_session_blocked_response(request):
    detail='blind_gold_session_blocks_annotation_and_model_pages; log out of gold first'
    headers={'Cache-Control':'no-store'}
    if request.method=='GET' and not request.url.path.startswith('/api/') and 'text/html' in request.headers.get('accept',''):
        target=request.url.path+('?' + request.url.query if request.url.query else '')
        if target.startswith('//'):target='/'
        target=html.escape(target,quote=True)
        return HTMLResponse('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>退出盲标后打开演示 · IntiClass</title>
<style>body{font:18px system-ui;max-width:720px;margin:70px auto;padding:20px;color:#173d50}button,a{font:inherit;margin:12px 0;padding:12px;display:block}button{cursor:pointer}</style>
<main data-return-to="'''+target+'''"><h1>当前浏览器仍处于金标准盲标登录状态</h1>
<p>为避免模型答案影响独立标注，盲标登录期间不能打开演示、普通事件或模型页面。</p>
<p>同一浏览器访问127.0.0.1的不同端口会共享此登录状态。请先保存尚未提交的标注；退出不会删除已保存的人工结果。</p>
<button id="leaveGold">退出盲标并返回刚才页面</button><a href="/gold">返回盲标，继续标注</a>
<p>需要同时打开两种页面时，请用无痕窗口或另一个浏览器配置文件。</p><p id="logoutStatus" role="status"></p></main>
<script>document.getElementById('leaveGold').onclick=async function(){this.disabled=true;
try{const r=await fetch('/api/gold/session',{method:'DELETE'});if(!r.ok)throw Error('退出失败，请回到盲标页面点击退出');location.replace(document.querySelector('main').dataset.returnTo)}
catch(e){document.getElementById('logoutStatus').textContent=e.message;this.disabled=false}};</script></html>''',status_code=403,headers=headers)
    return JSONResponse({'detail':detail},status_code=403,headers=headers)


def create_app(db_path='data/evidence_replay.db',video_root='data/teacher_dataset/raw/spatial_action_clips',visual_reviewer=None,*,enable_tracking=False,tracking_db=None):
    db=EvidenceDB(db_path);root=Path(video_root).resolve()
    reviewer=visual_reviewer or VisualReviewer()
    visual_lock=threading.Lock()
    evidence_root=Path(db_path).resolve().parent/'visual_evidence'
    qa_agent=EvidenceReviewAgent(db,reviewer,root,evidence_root,visual_lock)
    gold=GoldStore(db,root)
    media_lock=threading.Lock()
    cache=Path(db_path).resolve().parent/'replay_media_cache'
    app=FastAPI(title='IntiClass 真实证据回放')
    def safe_clip(clip_id):
        clip=db.clip(clip_id);path=Path(clip['path']).resolve()
        if root not in path.parents or path.suffix.lower()!='.mp4':raise HTTPException(403,'registered_path_outside_video_root')
        if not path.is_file():raise HTTPException(404,'registered_video_missing')
        if clip['decode_status']!='ready':raise HTTPException(422,clip['decode_status'])
        return clip,path

    @app.exception_handler(KeyError)
    async def missing(_,error):return JSONResponse({'detail':str(error)},status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_,error):return JSONResponse({'detail':str(error)},status_code=422)

    @app.middleware('http')
    async def local_requests(request:Request,call_next):
        if request.cookies.get('inticlass_gold') and not (request.url.path=='/gold' or request.url.path.startswith('/api/gold/')):
            try:gold.authenticate(request.cookies['inticlass_gold'])
            except PermissionError:pass
            else:return blind_session_blocked_response(request)
        if request.method=='POST':
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'cross_origin_write_rejected'},status_code=403)
        response=await call_next(request)
        if request.url.path.startswith('/api/gold/'):response.headers['Cache-Control']='no-store'
        return response

    @app.get('/')
    def home():return FileResponse(Path(__file__).resolve().parents[1]/'webui/index.html')

    @app.get('/gold')
    def gold_home():return FileResponse(Path(__file__).resolve().parents[1]/'webui/gold.html')

    @app.exception_handler(PermissionError)
    async def denied_gold(_,error):return JSONResponse({'detail':str(error)},status_code=403)

    def gold_account(request):
        try:return gold.authenticate(request.cookies.get('inticlass_gold',''))
        except PermissionError as exc:raise HTTPException(401,str(exc))

    @app.post('/api/gold/session')
    def gold_login(body:GoldLogin):
        try:account=gold.authenticate(body.token)
        except PermissionError as exc:raise HTTPException(401,str(exc))
        response=JSONResponse(account)
        response.set_cookie('inticlass_gold',body.token,httponly=True,samesite='strict',path='/',max_age=86400)
        return response

    @app.delete('/api/gold/session')
    def gold_logout():
        response=JSONResponse({'status':'logged_out'});response.delete_cookie('inticlass_gold',path='/');return response

    @app.get('/api/gold/samples')
    def gold_samples(request:Request):
        account=gold_account(request)
        return {'account':account,'samples':gold.sample_list(account)}

    @app.get('/api/gold/samples/{sample_id}')
    def gold_sample(sample_id:str,request:Request):
        account=gold_account(request)
        return gold.coordinator_payload(sample_id,account) if account['role']=='coordinator' else gold.blind_payload(sample_id,account)

    @app.post('/api/gold/samples/{sample_id}/{stage}',status_code=201)
    def gold_submit(sample_id:str,stage:str,body:dict,request:Request):
        account=gold_account(request)
        try:
            if stage in {'resolve','resolve_claims'}:return gold.adjudicate(sample_id,body,account,'target' if stage=='resolve' else 'claims')
            return gold.submit(sample_id,stage,body,account)
        except PermissionError as exc:raise HTTPException(403,str(exc))

    @app.get('/api/gold/media/{sample_id}')
    def gold_media(sample_id:str,request:Request):
        sample=gold.sample(sample_id,gold_account(request))
        gold.verify_source(sample)
        response=media(sample['clip_id'])
        if 'x-original-clip-id' in response.headers:del response.headers['x-original-clip-id']
        response.headers['Content-Disposition']='inline; filename="sample_'+sample_id+'.mp4"'
        return response

    @app.get('/api/catalog')
    def catalog():
        events=db.events()
        return {'video_ids':sorted({e['video_id'] for e in events}),'actions':sorted({e['action'] for e in events}),'total_events':len(events)}

    @app.post('/api/agent-reviews',status_code=201)
    def agent_question(body:ReviewQuestionRequest):
        try:return qa_agent.ask(body)
        except RuntimeError as error:
            if str(error)=='agent_question_busy':raise HTTPException(409,str(error))
            raise

    @app.get('/api/agent-reviews/{review_id}')
    def agent_result(review_id:str):return db.agent_review(review_id)

    @app.get('/api/agent-reviews/{review_id}/export')
    def agent_export(review_id:str):
        return JSONResponse(db.agent_review(review_id),headers={'Content-Disposition':'attachment; filename="inticlass_agent_review.json"'})

    @app.get('/api/events')
    def events(video_id:str|None=None,action:str|None=None,start:float|None=None,end:float|None=None,todo:bool=False):
        if any(v is not None and not math.isfinite(v) for v in [start,end]):raise ValueError('nonfinite_time')
        return db.events(video_id,action,start,end,todo)

    @app.get('/api/events/{event_id}')
    def detail(event_id:str):return db.detail(event_id)

    @app.get('/api/events/{event_id}/videos')
    def videos(event_id:str,target:float|None=None):return db.candidates(event_id,target)

    @app.get('/api/events/{event_id}/reviews')
    def history(event_id:str):
        db.event(event_id);return db.history(event_id)

    @app.post('/api/events/{event_id}/reviews',status_code=201)
    def review(event_id:str,body:HumanRegionReview):
        safe_clip(body.clip_id)
        return db.review(event_id,body.model_dump())

    @app.get('/api/media/{clip_id}')
    def media(clip_id:str):
        clip,path=safe_clip(clip_id)
        # MPEG-4 Part 2/FMP4 is not widely playable in browsers. Preserve originals.
        if clip.get('codec','').lower() not in {'h264','avc1'}:
            if not shutil.which('ffmpeg'):raise HTTPException(422,'browser_compatible_copy_requires_local_ffmpeg; original codec='+str(clip.get('codec')))
            stat=path.stat()
            key=hashlib.sha256((clip_id+str(stat.st_mtime_ns)+str(stat.st_size)).encode()).hexdigest()
            cached=cache/(key+'.mp4')
            with media_lock:
                if not cached.exists():
                    cache.mkdir(parents=True,exist_ok=True)
                    temporary=cache/(key+'.partial.mp4')
                    command=['ffmpeg','-v','error','-y','-i',str(path),'-map','0:v:0','-map','0:a?','-c:v','libx264','-preset','ultrafast','-crf','23','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(temporary)]
                    try:
                        result=subprocess.run(command,capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        if result.returncode:raise HTTPException(422,'local_transcode_failed: '+result.stderr.decode(errors='replace')[-1000:])
                        temporary.replace(cached)
                    except subprocess.TimeoutExpired:
                        raise HTTPException(504,'local_transcode_timeout')
                    finally:
                        temporary.unlink(missing_ok=True)
            path=cached
        return FileResponse(path,media_type='video/mp4',headers={'X-Content-Type-Options':'nosniff','X-Original-Clip-ID':clip_id})


    @app.get('/api/vlm/status')
    def vlm_status():
        return {**reviewer.availability(),'model_id':reviewer.config.model_id,'frame_count':reviewer.config.frame_count,'max_calls':reviewer.config.max_calls,'budget_seconds':reviewer.config.max_elapsed_seconds,'dynamic_interval_seconds':reviewer.config.dynamic_interval}

    @app.post('/api/events/{event_id}/visual-reviews',status_code=201)
    def visual_review(event_id:str,body:VisualReviewRequest):
        # Extraction validates real source and path boundaries before any image call.
        if not visual_lock.acquire(blocking=False):raise HTTPException(409,'visual_review_busy')
        try:return reviewer.run(db,event_id,body.model_dump(),root,evidence_root)
        finally:visual_lock.release()

    @app.get('/api/events/{event_id}/visual-reviews')
    def visual_history(event_id:str):
        db.event(event_id);return db.visual_history(event_id)

    @app.get('/api/visual-reviews/{review_id}/frames/{frame_id}/{view}')
    def evidence_frame(review_id:str,frame_id:str,view:str):
        record=db.visual_review(review_id)
        evidence=record.get('evidence')
        if not evidence or view not in ['full','roi','input_full','input_roi']:raise HTTPException(404,'evidence_not_registered')
        frame=next((f for f in evidence['frames'] if f['frame_id']==frame_id),None)
        if frame is None:raise HTTPException(404,'frame_not_registered')
        path=Path(frame['files'][view]).resolve()
        if evidence_root.resolve() not in path.parents or not path.is_file():raise HTTPException(404,'evidence_file_missing')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=frame['sha256'][view]:raise HTTPException(409,'evidence_hash_changed')
        return FileResponse(path,media_type='image/jpeg',headers={'X-Content-Type-Options':'nosniff'})

    @app.get('/api/report')
    def report(video_id:str|None=None):
        return JSONResponse(WebUIBridge(str(db_path)).get_region_report(video_id),headers={'Content-Disposition':'attachment; filename="inticlass_evidence_report.json"'})
    if enable_tracking:
        from src.tracking.api import mount_tracking
        mount_tracking(app,db,root,tracking_db or Path(db_path).resolve().parent/'person_tracks.db')
    return app


if __name__=='__main__':
    import uvicorn
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default='data/evidence_replay.db')
    p.add_argument('--video-root',default='data/teacher_dataset/raw/spatial_action_clips')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--enable-tracking',action='store_true')
    p.add_argument('--tracking-db')
    a=p.parse_args();uvicorn.run(create_app(a.db,a.video_root,enable_tracking=a.enable_tracking,tracking_db=a.tracking_db),host='127.0.0.1',port=a.port)
