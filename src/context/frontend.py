"""Same-origin product shell. Existing technical routes remain separate."""
from pathlib import Path
import json
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from src.behavior_discovery.repository import Repository
from src.behavior_discovery.ingest import MEDIA_ROOT
from src.tracking.video import safe_path
DIST_ROOT=Path(__file__).resolve().parents[2]/'frontend/dist'


def mount(app,service,account):
    root=DIST_ROOT
    @app.get('/api/context/me')
    def me(a=Depends(account)):return a
    @app.get('/api/context/analysis/runtime')
    def runtime(a=Depends(account)):
        from src.inference.runtime import RuntimeHealth
        return RuntimeHealth.visual()
    @app.get('/api/context/analysis/jobs/{jid}/diagnostics')
    def diagnostics(jid:str,a=Depends(account)):
        Repository(service.repo).job(jid,a['actor'])
        with service.repo.connection() as c:rows=[dict(r) for r in c.execute("SELECT operation,created_at,payload FROM analysis_job_audit WHERE job_id=? AND operation IN ('failure','window_error','paused_or_budget_exhausted') ORDER BY created_at DESC LIMIT 5",(jid,))]
        for row in rows:
            raw=json.loads(row.pop('payload'));error=str(raw.get('error',raw.get('reason','')))
            row['detail']=error[:1000]
            row['message']='本地视觉服务未启动或连接中断，启动Ollama后重试。' if 'ConnectError' in error or '10061' in error else ('当前预算耗尽或用户请求暂停，可检查额度后恢复。' if row['operation']=='paused_or_budget_exhausted' else '部分处理未完成，请检查详情后重试。')
        return {'errors':rows}
    @app.get('/api/context/analysis/videos/{vid}/media')
    def preview(vid:str,a=Depends(account)):
        video=Repository(service.repo).video(vid,a['actor'])
        if video['status']!='active':raise ValueError('analysis_video_deleted')
        path=Path(video['metadata']['media_path']).resolve()
        if MEDIA_ROOT.resolve() not in path.parents:raise ValueError('managed_media_path_invalid')
        clip={'clip_id':vid,'path':str(path),'source_kind':'uploaded_recording'}
        safe_path(clip,MEDIA_ROOT)
        return FileResponse(path,media_type='video/mp4')
    @app.get('/app')
    @app.get('/app/{route:path}')
    def spa(route:str=''):
        if not (root/'index.html').is_file():raise HTTPException(503,'frontend_build_required')
        target=(root/route).resolve()
        if root.resolve() not in target.parents and target!=root.resolve():raise HTTPException(404,'asset_not_found')
        if route and target.is_file():return FileResponse(target)
        if route.startswith('assets/') or '.' in Path(route).name:raise HTTPException(404,'asset_not_found')
        return FileResponse(root/'index.html',media_type='text/html')
