"""Teacher-facing lesson setup, reusing registered videos, documents and queued analysis."""
import json
from fastapi import Depends
from pydantic import BaseModel,ConfigDict,Field
from src.identity.store import uid,now,IdentityStore
from src.behavior_discovery.repository import Repository
from src.behavior_discovery.schemas import Start,Config
from src.behavior_discovery.jobs import create,launch
from .material_library import set_scope
from .schemas import MaterialScope

class NewLesson(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    title:str=Field(min_length=1,max_length=200)
    classroom_id:str|None=None
class Attach(BaseModel):
    model_config=ConfigDict(extra='forbid')
    video_id:str|None=None
    document_ids:list[str]=Field(default_factory=list,max_length=30)
class Begin(BaseModel):
    model_config=ConfigDict(extra='forbid')
    preset:str='quick'

def bind_materials(service,video_id,rid,actor):
    with service.repo.connection() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lesson_sessions'").fetchone():return
        rows=c.execute('SELECT document_ids,classroom_id FROM lesson_sessions WHERE video_id=? AND owner=?',(video_id,actor)).fetchall()
    for row in rows:
        for did in json.loads(row['document_ids']):
            try:set_scope(service,did,actor,MaterialScope(run_id=rid,classroom_id=row['classroom_id']))
            except (ValueError,PermissionError):continue  # Deleted/revoked resources stay unavailable.

def mount(app,service,account,writer):
    with service.repo.connection() as c:c.execute('CREATE TABLE IF NOT EXISTS lesson_sessions(lesson_id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,classroom_id TEXT,video_id TEXT,document_ids TEXT NOT NULL,created_at TEXT NOT NULL)')
    def owned(lid,actor):
        with service.repo.connection() as c:r=c.execute('SELECT * FROM lesson_sessions WHERE lesson_id=? AND owner=?',(lid,actor)).fetchone()
        if not r:raise PermissionError('lesson_access_denied')
        return {**dict(r),'document_ids':json.loads(r['document_ids'])}
    @app.get('/api/context/lessons')
    def listing(a=Depends(account)):
        with service.repo.connection() as c:rows=c.execute('SELECT * FROM lesson_sessions WHERE owner=? ORDER BY created_at DESC',(a['actor'],)).fetchall()
        return [{**dict(r),'document_ids':json.loads(r['document_ids'])} for r in rows]
    @app.post('/api/context/lessons',status_code=201)
    def new(body:NewLesson,a=Depends(writer)):
        if body.classroom_id:
            with IdentityStore(service.identity_dir).connection() as c:r=c.execute("SELECT 1 FROM classrooms WHERE classroom_id=? AND owner_actor=? AND status='active'",(body.classroom_id,a['actor'])).fetchone()
            if not r:raise PermissionError('lesson_classroom_access_denied')
        lid=uid()
        with service.repo.connection() as c:c.execute('INSERT INTO lesson_sessions VALUES(?,?,?,?,?,?,?)',(lid,a['actor'],body.title.strip(),body.classroom_id,None,'[]',now()))
        return owned(lid,a['actor'])
    @app.put('/api/context/lessons/{lid}/resources')
    def attach(lid:str,body:Attach,a=Depends(writer)):
        lesson=owned(lid,a['actor']);vid=body.video_id or lesson['video_id']
        if vid:Repository(service.repo).video(vid,a['actor'])
        for did in body.document_ids:service.repo.document(did,a['actor'],True)
        if vid and lesson['classroom_id']:
            store=IdentityStore(service.identity_dir)
            with store.connection() as c:
                prior=c.execute('SELECT classroom_id FROM classroom_videos WHERE video_id=?',(vid,)).fetchone()
                if prior and prior[0]!=lesson['classroom_id']:raise ValueError('video_already_belongs_to_another_classroom')
                c.execute('INSERT OR IGNORE INTO classroom_videos VALUES(?,?,?,?,?)',(lesson['classroom_id'],vid,None,now(),a['actor']))
        documents=list(dict.fromkeys(lesson['document_ids']+body.document_ids))
        if len(documents)>30:raise ValueError('maximum_30_materials_per_lesson')
        with service.repo.connection() as c:c.execute('UPDATE lesson_sessions SET video_id=?,document_ids=? WHERE lesson_id=?',(vid,json.dumps(documents),lid))
        if vid:
            with service.repo.connection() as c:existing=c.execute("SELECT run_id FROM analysis_jobs WHERE video_id=? AND owner=? AND run_id IS NOT NULL AND stage!='tracking' ORDER BY created_at DESC LIMIT 1",(vid,a['actor'])).fetchone()
            if existing:bind_materials(service,vid,existing[0],a['actor'])
        return owned(lid,a['actor'])
    @app.post('/api/context/lessons/{lid}/start',status_code=201)
    def begin(lid:str,body:Begin,a=Depends(writer)):
        lesson=owned(lid,a['actor'])
        if not lesson['video_id']:raise ValueError('lesson_requires_video')
        repo=Repository(service.repo)
        with service.repo.connection() as c:ids=[r[0] for r in c.execute('SELECT job_id FROM analysis_jobs WHERE owner=? AND video_id=? ORDER BY created_at DESC,rowid DESC',(a['actor'],lesson['video_id']))]
        jobs=[repo.job(j,a['actor']) for j in ids]
        active=next((j for j in jobs if j['status'] in {'running','queued','pausing','cancelling'}),None)
        if active:return {'job':active,'created':False}
        if body.preset not in {'quick','standard','full_timeline'}:raise ValueError('unsupported_lesson_analysis_preset')
        cfg=Config(device='cpu',max_tracks=3 if body.preset=='standard' else 2,max_windows=6 if body.preset=='standard' else 2,max_calls=12 if body.preset=='standard' else 4,frames=2,input_long_edge=384,timeout=240,budget_seconds=900,prefer_clear_targets=True)
        if body.preset=='full_timeline':cfg=Config(analysis_mode='full_timeline',frames=4,input_long_edge=512,device='cpu',timeout=240,budget_seconds=900,overlap=0,prefer_clear_targets=False)
        reuse=next((j['run_id'] for j in jobs if j.get('run_id') and j['stage']!='tracking'),None)
        job,fresh=create(service,Start(video_id=lesson['video_id'],config=cfg,reuse_tracking_run_id=reuse),a['actor'])
        # Scope the selected lesson materials to the same actual run, when tracking already exists.
        for did in lesson['document_ids']:
            if reuse:set_scope(service,did,a['actor'],MaterialScope(run_id=reuse,classroom_id=lesson['classroom_id']))
            elif lesson['classroom_id']:set_scope(service,did,a['actor'],MaterialScope(classroom_id=lesson['classroom_id']))
        if fresh:launch(job['job_id'])
        return {'job':job,'created':fresh}
