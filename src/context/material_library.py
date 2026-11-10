"""Owner-scoped teaching/work library and explicit teacher classroom memory."""
import json
from fastapi import Depends
from pydantic import Field
from .schemas import Input,MaterialScope,SourceType
from .repository import tokens
from src.identity.store import IdentityStore,uid


def validate_scope(service,actor,scope):
    if scope is None:return
    scope=MaterialScope.model_validate(scope)
    if not any(scope.model_dump().values()):return
    store=IdentityStore(service.identity_dir)
    cid=scope.classroom_id
    if scope.run_id:
        _,clip=service.source.verified_run(scope.run_id)
        from src.behavior_discovery.repository import Repository
        Repository(service.repo).video(clip['video_id'],actor)
        if scope.track_id and scope.track_id not in {t['track_id'] for t in service.source.tracks(scope.run_id)}:raise ValueError('material_track_not_in_video')
        with store.connection() as c:
            linked=c.execute('SELECT classroom_id FROM classroom_videos WHERE video_id=?',(clip['video_id'],)).fetchone()
        if cid and (not linked or linked[0]!=cid):raise ValueError('material_classroom_video_mismatch')
        cid=cid or (linked[0] if linked else None)
    if scope.track_id and not scope.run_id:raise ValueError('material_track_requires_run')
    with store.connection() as c:
        if cid and not c.execute("SELECT 1 FROM classrooms WHERE classroom_id=? AND owner_actor=? AND status='active'",(cid,actor)).fetchone():raise PermissionError('material_classroom_access_denied')
        if scope.subject_id:
            row=c.execute("SELECT cs.classroom_id FROM classroom_students cs JOIN classrooms cl ON cl.classroom_id=cs.classroom_id JOIN registered_subjects s ON s.subject_id=cs.subject_id WHERE cs.subject_id=? AND cs.status='active' AND cl.owner_actor=? AND s.status IN ('active','reference_only')",(scope.subject_id,actor)).fetchone()
            if not row or cid and row[0]!=cid:raise PermissionError('material_student_scope_denied')


def set_scope(service,did,actor,scope):
    validate_scope(service,actor,scope);scope=MaterialScope.model_validate(scope)
    with service.repo.connection() as c:
        service.repo.document(did,actor,True,c)
        c.execute('INSERT OR REPLACE INTO context_document_scope VALUES (?,?,?,?,?)',(did,scope.run_id,scope.classroom_id,scope.subject_id,scope.track_id))
        c.execute('UPDATE context_documents SET version=version+1 WHERE document_id=?',(did,))
        service.repo.audit(c,did,actor,'material_scope_updated',scope.model_dump())


def scope_rows(service):
    with service.repo.connection() as c:return {r['document_id']:{k:r[k] for k in r.keys() if k!='document_id'} for r in c.execute('SELECT * FROM context_document_scope')}


def matches(scope,run,subject=None,track=None,classroom=None):
    if not scope:return True
    if scope.get('run_id') and scope['run_id']!=run:return False
    if scope.get('classroom_id') and scope['classroom_id']!=classroom:return False
    if scope.get('subject_id') and subject and scope['subject_id']!=subject:return False
    if scope.get('track_id') and track and scope['track_id']!=track:return False
    return True


class Edit(Input):
    source_type:SourceType
    title:str=Field(min_length=1,max_length=200)
    scope:MaterialScope

class Transcript(Input):
    text:str=Field(min_length=1,max_length=500000)

class Memory(Input):
    run_id:str
    text:str=Field(min_length=1,max_length=10000)
    subject_id:str|None=None
    track_id:int|None=Field(None,ge=1)
    supersedes:str|None=None


def mount(app,service,account,writer):
    @app.get('/api/context/documents/{did}/preview')
    def preview(did:str,a=Depends(account)):
        from fastapi import Response
        d=service.repo.document(did,a['actor'])
        types={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}
        if d['file_type'] not in types:raise ValueError('only_registered_image_documents_have_previews')
        return Response(service.access.unseal(d['original'],True),media_type=types[d['file_type']],headers={'Cache-Control':'private, no-store'})
    @app.put('/api/context/documents/{did}/metadata')
    def edit(did:str,body:Edit,a=Depends(writer)):
        validate_scope(service,a['actor'],body.scope)
        with service.repo.connection() as c:
            service.repo.document(did,a['actor'],True,c)
            c.execute('UPDATE context_documents SET title=?,source_type=?,version=version+1 WHERE document_id=?',(body.title,body.source_type,did))
            service.repo.audit(c,did,a['actor'],'material_classified',body.model_dump())
        set_scope(service,did,a['actor'],body.scope);return service.detail(did,a['actor'])

    @app.post('/api/context/documents/{did}/transcript')
    def transcript(did:str,body:Transcript,a=Depends(writer)):
        with service.repo.connection() as c:
            document=service.repo.document(did,a['actor'],True,c)
            if document['status'] not in {'requires_transcription','requires_ocr','partial_requires_ocr'}:raise ValueError('transcription_only_for_unread_or_partial_sources')
            pid=uid();provenance={'parser_version':'teacher-transcript-v1','file_hash':document['file_hash'],'offset_kind':'teacher_supplied_transcription_not_ocr','independent_verification':False}
            c.execute('INSERT INTO context_passages VALUES (?,?,?,?,?,?,?,?)',(pid,did,body.text,None,0,len(body.text),json.dumps(provenance),'teacher_transcription'))
            c.execute('INSERT INTO context_fts VALUES (?,?)',(pid,' '.join(tokens(body.text))))
            c.execute("UPDATE context_documents SET status='parsed_teacher_transcription',version=version+1 WHERE document_id=?",(did,))
            service.repo.audit(c,did,a['actor'],'teacher_transcription_added',{'passage_id':pid})
        return service.detail(did,a['actor'])

    @app.post('/api/context/classroom-memory',status_code=201)
    def remember(body:Memory,a=Depends(writer)):
        scope=MaterialScope(run_id=body.run_id,subject_id=body.subject_id,track_id=body.track_id)
        validate_scope(service,a['actor'],scope)
        if body.supersedes:
            prior=service.repo.document(body.supersedes,a['actor'],True)
            if prior['source_type']!='classroom_memory' or scope_rows(service).get(body.supersedes,{}).get('run_id')!=body.run_id:raise ValueError('memory_update_scope_mismatch')
        result=service.ingest('teacher-memory.txt',body.text.encode('utf-8'),'教师课堂补充','classroom_memory',a['actor'],scope=scope)
        if body.supersedes:
            with service.repo.connection() as c:
                c.execute("UPDATE context_documents SET status='superseded',version=version+1 WHERE document_id=?",(body.supersedes,))
                service.repo.audit(c,body.supersedes,a['actor'],'teacher_memory_superseded',{'replacement_document_id':result['document_id']})
        return {**result,'source_kind':'teacher_report','not_model_training':True,'not_independent_visual_truth':True}

    @app.get('/api/context/classroom-memory')
    def memories(run_id:str,a=Depends(account)):
        validate_scope(service,a['actor'],MaterialScope(run_id=run_id));scopes=scope_rows(service)
        return [service.detail(d['document_id'],a['actor']) for d in service.repo.documents(a['actor']) if d['source_type']=='classroom_memory' and scopes.get(d['document_id'],{}).get('run_id')==run_id]
