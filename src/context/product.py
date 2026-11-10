"""Small product-facing class and roster API backed by the existing identity store."""
import json
from datetime import datetime, timezone, timedelta
from fastapi import Depends, File, HTTPException, UploadFile, Response
from pydantic import BaseModel,ConfigDict,Field
from src.identity.store import IdentityStore, now, uid, future
from src.identity.service import clean_image


def _expiry(days=365):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class ClassInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=160)
    grade:str=Field('',max_length=80)
    term:str=Field('',max_length=80)
    description:str=Field('',max_length=1000)

class StudentInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    display_name:str=Field(min_length=1,max_length=120)
    consent_record:str=Field(min_length=5,max_length=4000)
    expires_at:str
    authorized:bool
    allow_face_matching:bool=False
    reference_only:bool=False

class MatchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id:str
    consent_record:str=Field(min_length=5,max_length=4000)
    expires_at:str
    all_visible_people_authorized:bool

class NameInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    display_name:str=Field(min_length=1,max_length=120)

def mount(app, access: IdentityStore, account, context):
    from src.identity.product_matching import ClassroomMatcher
    from src.identity.schemas import Binding,Change
    matcher=ClassroomMatcher(access,context);app.state.product_matcher=matcher
    def owner_classroom(classroom_id, actor, role):
        with access.connection() as c:
            row = c.execute("SELECT * FROM classrooms WHERE classroom_id=? AND (owner_actor=? OR ?='admin')", (classroom_id, actor, role)).fetchone()
        if not row: raise HTTPException(404, 'classroom_not_found')
        return dict(row)

    def student_owner(sid,a,allow_reference=False):
        with access.connection() as c:
            row=c.execute("SELECT cs.classroom_id FROM classroom_students cs JOIN classrooms cl ON cl.classroom_id=cs.classroom_id WHERE cs.subject_id=? AND cs.status='active' AND (cl.owner_actor=? OR ?='admin')",(sid,a['actor'],a['role'])).fetchone()
        if not row:raise HTTPException(404,'student_not_found')
        subject=access.subject(sid,active=False)
        if not (allow_reference and subject['status']=='reference_only'):access.subject(sid)
        return row[0]

    @app.get('/api/context/classrooms')
    def classrooms(a=Depends(account)):
        with access.connection() as c:
            rows = [dict(r) for r in c.execute("SELECT c.*,COUNT(cs.subject_id) AS student_count FROM classrooms c LEFT JOIN classroom_students cs ON cs.classroom_id=c.classroom_id AND cs.status='active' WHERE c.owner_actor=? OR ?='admin' GROUP BY c.classroom_id ORDER BY c.updated_at DESC,c.created_at DESC", (a['actor'], a['role']))]
            for row in rows:
                row['student_count'] = int(row['student_count'] or 0)
        return rows

    @app.post('/api/context/classrooms', status_code=201)
    def create_classroom(body: ClassInput, a=Depends(account)):
        if a['role'] not in {'admin', 'teacher'}: raise HTTPException(403, 'teacher_write_required')
        cid = uid(); stamp = now()
        name = body.name
        with access.connection() as c:
            c.execute('INSERT INTO classrooms VALUES (?,?,?,?,?,?,?,?,?)', (cid, a['actor'], name, body.grade, body.term, body.description, 'active', stamp, stamp))
            access.audit(c, a['actor'], 'classroom_created', cid, {'name': name})
        return {'classroom_id': cid, 'owner_actor': a['actor'], 'name': name, 'status': 'active'}

    @app.put('/api/context/classrooms/{classroom_id}')
    def edit_class(classroom_id:str,body:ClassInput,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        owner_classroom(classroom_id,a['actor'],a['role'])
        with access.connection() as c:
            c.execute('UPDATE classrooms SET name=?,grade=?,term=?,description=?,updated_at=? WHERE classroom_id=?',(body.name,body.grade,body.term,body.description,now(),classroom_id))
            access.audit(c,a['actor'],'classroom_edited',classroom_id,{})
        return {'updated':True}

    @app.get('/api/context/classrooms/{classroom_id}')
    def classroom(classroom_id: str, a=Depends(account)):
        row = owner_classroom(classroom_id, a['actor'], a['role'])
        with access.connection() as c:
            people = []
            for r in c.execute("SELECT s.subject_id,s.display_name,s.status,s.expires_at,cs.status AS membership_status FROM registered_subjects s JOIN classroom_students cs ON cs.subject_id=s.subject_id WHERE cs.classroom_id=? AND cs.status='active' ORDER BY cs.created_at", (classroom_id,)):
                item = dict(r)
                item['display_name'] = access.unseal(item['display_name']) if item['display_name'] else ''
                photo=c.execute("SELECT reference_image_id FROM reference_images WHERE subject_id=? AND status='active' AND image IS NOT NULL ORDER BY created_at DESC LIMIT 1",(item['subject_id'],)).fetchone()
                item['photo_reference_id']=photo[0] if photo else None
                item['photo_count']=c.execute("SELECT COUNT(*) FROM reference_images WHERE subject_id=? AND status='active' AND image IS NOT NULL",(item['subject_id'],)).fetchone()[0]
                item['track_registration_count']=c.execute("SELECT COUNT(*) FROM identity_bindings b JOIN reference_images r ON r.reference_image_id=json_extract(b.evidence_refs,'$.reference_image_id') WHERE b.subject_id=? AND b.status='track_label' AND r.status='active'",(item['subject_id'],)).fetchone()[0]
                people.append(item)
        row['students'] = people
        return row

    @app.post('/api/context/classrooms/{classroom_id}/students', status_code=201)
    def add_student(classroom_id: str, body: StudentInput, a=Depends(account)):
        if a['role'] not in {'admin', 'teacher'}: raise HTTPException(403, 'teacher_write_required')
        owner_classroom(classroom_id, a['actor'], a['role'])
        if body.reference_only:
            if body.authorized or body.allow_face_matching:raise HTTPException(422,'reference_only_cannot_claim_identity_authorization')
            sid=uid();stamp=now()
            record={'record':body.consent_record,'purposes':['demo_reference_storage'],'authorization_status':'not_provided','recorded_by':a['actor'],'recorded_at':stamp}
            with access.connection() as c:
                c.execute('INSERT INTO registered_subjects VALUES (?,?,?,?,?,?)',(sid,access.seal(body.display_name),access.seal(record),'','reference_only',stamp))
                c.execute('INSERT INTO classroom_students VALUES (?,?,?,?)',(classroom_id,sid,'active',stamp))
                access.audit(c,a['actor'],'classroom_reference_added',sid,{'classroom_id':classroom_id,'identity_enrollment':False})
            return {'subject_id':sid,'classroom_id':classroom_id,'display_name':body.display_name,'status':'reference_only'}
        if not body.authorized:raise HTTPException(422,'explicit_authorization_required')
        display_name=body.display_name;consent=body.consent_record
        sid=uid();stamp=now();expires=future(body.expires_at)
        purposes=['manual_identity','reference_storage']+(['face_matching','video_reference_capture'] if body.allow_face_matching else [])
        with access.connection() as c:
            c.execute('INSERT INTO registered_subjects VALUES (?,?,?,?,?,?)', (sid, access.seal(display_name), access.seal({'record': consent, 'purposes': purposes, 'recorded_by': a['actor'], 'attested_at': stamp}), expires, 'active', stamp))
            c.execute('INSERT INTO classroom_students VALUES (?,?,?,?)', (classroom_id, sid, 'active', stamp))
            access.audit(c, a['actor'], 'classroom_student_added', sid, {'classroom_id': classroom_id})
        return {'subject_id': sid, 'classroom_id': classroom_id, 'display_name': display_name, 'status': 'active'}

    @app.put('/api/context/students/{subject_id}')
    def edit_student(subject_id:str,body:StudentInput,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        student_owner(subject_id,a,allow_reference=True)
        if not body.authorized:raise HTTPException(422,'explicit_authorization_required')
        if body.reference_only:raise HTTPException(422,'reference_only_cannot_claim_identity_authorization')
        future(body.expires_at)
        purposes=['manual_identity','reference_storage']+(['face_matching','video_reference_capture'] if body.allow_face_matching else [])
        with access.connection() as c:
            c.execute("UPDATE registered_subjects SET display_name=?,consent_record=?,expires_at=?,status='active' WHERE subject_id=?",(access.seal(body.display_name),access.seal({'record':body.consent_record,'purposes':purposes,'recorded_by':a['actor'],'attested_at':now()}),body.expires_at,subject_id))
            if not body.allow_face_matching:
                c.execute('UPDATE reference_images SET feature=NULL,model_id=NULL WHERE subject_id=?',(subject_id,))
            access.audit(c,a['actor'],'student_edited',subject_id,{'purposes':purposes})
        processed=[]
        if body.allow_face_matching:
            with access.connection() as c:photos=c.execute("SELECT reference_image_id,image FROM reference_images WHERE subject_id=? AND status='active' AND image IS NOT NULL ORDER BY created_at DESC LIMIT 10",(subject_id,)).fetchall()
            if photos and matcher.identity.backend.probe()['model_status']=='present_not_validated':
                import cv2,numpy as np
                for photo in photos:
                    status='needs_clear_single_face';value=None;model=None
                    try:
                        faces,_=matcher.identity.backend.faces(cv2.imdecode(np.frombuffer(access.unseal(photo['image'],True),np.uint8),cv2.IMREAD_COLOR))
                        if len(faces)==1 and faces[0]['feature'] is not None:value=access.seal(faces[0]['feature'].tolist());model=matcher.identity.backend.model_id;status='feature_extracted_not_identity_confirmed'
                    except (ValueError,cv2.error):status='extraction_failed'
                    with access.connection() as c:c.execute('UPDATE reference_images SET feature=?,model_id=? WHERE reference_image_id=?',(value,model,photo['reference_image_id']))
                    processed.append({'reference_image_id':photo['reference_image_id'],'status':status})
            elif photos:processed=[{'reference_image_id':p['reference_image_id'],'status':'local_face_model_unavailable'} for p in photos]
            with access.connection() as c:access.audit(c,a['actor'],'authorized_reference_processing',subject_id,{'results':processed})
        return {'updated':True,'reference_processing':processed,'identity_confirmation_created':False}

    @app.put('/api/context/students/{sid}/name')
    def edit_name(sid:str,body:NameInput,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        student_owner(sid,a,allow_reference=True)
        with access.connection() as c:
            c.execute('UPDATE registered_subjects SET display_name=? WHERE subject_id=?',(access.seal(body.display_name),sid))
            access.audit(c,a['actor'],'student_name_edited',sid,{})
        return {'updated':True}

    @app.post('/api/context/students/{subject_id}/photo', status_code=201)
    async def upload_student_photo(subject_id: str, photo: UploadFile = File(...), a=Depends(account)):
        if a['role'] not in {'admin', 'teacher'}: raise HTTPException(403, 'teacher_write_required')
        student_owner(subject_id,a,allow_reference=True)
        data = clean_image(await photo.read(8 * 1024 * 1024 + 1))
        feature_value=None;feature_model=None;feature_status='not_extracted'
        subject=access.subject(subject_id,active=False)
        if subject['status']=='active' and 'face_matching' in subject['consent_record']['purposes'] and matcher.identity.backend.probe()['model_status']=='present_not_validated':
            import cv2,numpy as np
            faces,_=matcher.identity.backend.faces(cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR))
            usable=[f for f in faces if f['feature'] is not None]
            if len(faces)==len(usable)==1:
                feature_value=access.seal(usable[0]['feature'].tolist());feature_model=matcher.identity.backend.model_id;feature_status='extracted_not_identity_confirmed'
            else:feature_status='reference_needs_review_single_clear_face_required'
        with access.connection() as c:
            row = c.execute("SELECT * FROM registered_subjects WHERE subject_id=? AND status IN ('active','reference_only')", (subject_id,)).fetchone()
            if not row: raise HTTPException(404, 'student_not_found')
            ref = uid(); provenance = {'kind': 'teacher_product_upload', 'actor': a['actor'], 'filename': (photo.filename or '')[:160], 'automatic_matching': 'not_validated'}
            c.execute('INSERT INTO reference_images VALUES (?,?,?,?,?,?,?,?)', (ref, subject_id, access.seal(data), feature_value, feature_model, json.dumps(provenance, ensure_ascii=False), 'active', now()))
            access.audit(c, a['actor'], 'student_photo_added', ref, {'subject_id': subject_id, 'automatic_matching': 'not_validated'})
        return {'reference_image_id': ref, 'subject_id': subject_id, 'feature_status': feature_status, 'matching_status': 'not_validated'}

    @app.get('/api/context/students/{subject_id}/references')
    def student_references(subject_id: str, a=Depends(account)):
        student_owner(subject_id,a,allow_reference=True)
        with access.connection() as c:
            row = c.execute('SELECT subject_id FROM registered_subjects WHERE subject_id=?', (subject_id,)).fetchone()
            if not row: raise HTTPException(404, 'student_not_found')
            return [dict(r) for r in c.execute('SELECT reference_image_id,subject_id,model_id,status,created_at FROM reference_images WHERE subject_id=? ORDER BY created_at DESC', (subject_id,))]

    @app.get('/api/context/students/{sid}/references/{ref}/image')
    def image(sid:str,ref:str,a=Depends(account)):
        student_owner(sid,a,allow_reference=True)
        with access.connection() as c:r=c.execute("SELECT image FROM reference_images WHERE reference_image_id=? AND subject_id=? AND status='active'",(ref,sid)).fetchone()
        if not r or r[0] is None:raise HTTPException(404,'reference_not_found')
        return Response(access.unseal(r[0],True),media_type='image/jpeg')

    @app.delete('/api/context/students/{sid}/references/{ref}')
    def delete_photo(sid:str,ref:str,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        student_owner(sid,a,allow_reference=True);access.purge(sid,a['actor'],ref)
        return {'deleted':True}

    @app.post('/api/context/students/{sid}/revoke')
    def revoke(sid:str,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        student_owner(sid,a,allow_reference=True);access.purge(sid,a['actor'])
        with access.connection() as c:c.execute("UPDATE classroom_students SET status='removed' WHERE subject_id=?",(sid,))
        return {'revoked':True}

    @app.post('/api/context/classrooms/{cid}/videos/{vid}')
    def link_video(cid:str,vid:str,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        owner_classroom(cid,a['actor'],a['role'])
        from src.behavior_discovery.repository import Repository
        video=Repository(context.repo).video(vid,a['actor'])
        with access.connection() as c:
            previous=c.execute('SELECT classroom_id FROM classroom_videos WHERE video_id=?',(vid,)).fetchone()
            if previous and previous[0]!=cid:raise HTTPException(409,'video_already_linked_to_other_classroom')
            c.execute('INSERT OR IGNORE INTO classroom_videos VALUES (?,?,?,?,?)',(cid,vid,None,now(),a['actor']))
            access.audit(c,a['actor'],'classroom_video_linked',vid,{'classroom_id':cid})
        return {'linked':True}

    @app.get('/api/context/classrooms/{cid}/videos')
    def class_videos(cid:str,a=Depends(account)):
        owner_classroom(cid,a['actor'],a['role'])
        with access.connection() as c:ids=[r[0] for r in c.execute('SELECT video_id FROM classroom_videos WHERE classroom_id=? ORDER BY created_at DESC',(cid,))]
        from src.behavior_discovery.repository import Repository
        result=[]
        for vid in ids:
            try:
                v=Repository(context.repo).video(vid,a['actor']);v['metadata']={k:x for k,x in v['metadata'].items() if not k.endswith('_path')};result.append(v)
            except PermissionError:continue
        return result

    def owned_run(rid,a):
        from src.behavior_discovery.repository import Repository
        _,clip=context.source.run(rid)
        Repository(context.repo).video(clip['video_id'],a['actor'])
        return clip

    @app.get('/api/context/identity/status')
    def matching_status(a=Depends(account)):
        return matcher.identity.backend.probe()

    @app.post('/api/context/classrooms/{cid}/videos/{vid}/identity-scope')
    def identity_scope(cid:str,vid:str,body:dict,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        owner_classroom(cid,a['actor'],a['role'])
        link_video(cid,vid,a)
        if body.get('all_visible_people_authorized') is not True:raise ValueError('video_identity_processing_authorization_required')
        consent=body.get('consent_record','');expiry=body.get('expires_at','')
        if not isinstance(consent,str) or not 5<=len(consent)<=4000:raise ValueError('authorization_basis_required')
        future(expiry)
        with access.connection() as c:
            c.execute('INSERT INTO classroom_video_identity_scopes VALUES (?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET consent_record=excluded.consent_record,expires_at=excluded.expires_at,status=excluded.status',(vid,cid,a['actor'],access.seal(consent),expiry,now(),'active'))
            access.audit(c,a['actor'],'video_identity_scope_authorized',vid,{'classroom_id':cid})
        return {'authorized':True,'scope':'this_video_only'}

    @app.post('/api/context/classrooms/{cid}/match')
    def match_classroom(cid:str,body:MatchInput,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        owner_classroom(cid,a['actor'],a['role']);owned_run(body.run_id,a)
        # Shared OS lock also covers the video pipeline; no additional GPU concurrency.
        from filelock import FileLock,Timeout
        try:
            with FileLock(str(context.repo.path.parent/'product_identity.lock'),timeout=0):
                return matcher.run_classroom(cid,body.run_id,a['actor'],body.consent_record,body.expires_at,body.all_visible_people_authorized)
        except Timeout:raise HTTPException(409,'identity_matching_busy')

    @app.get('/api/context/identity/results')
    def identity_results(run_id:str,a=Depends(account)):
        clip=owned_run(run_id,a);result=[]
        with access.connection() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM identity_candidates WHERE run_id=? ORDER BY created_at DESC',(run_id,))]
            for row in rows:
                row['display_name']=None;row['effective_status']=row['status']
                if row['subject_id']:
                    try:
                        student_owner(row['subject_id'],a);subject=access.subject(row['subject_id'],c)
                        matcher.identity.purpose(row['subject_id'],'face_matching',c)
                        ref=c.execute("SELECT 1 FROM reference_images WHERE reference_image_id=? AND status='active' AND feature IS NOT NULL",(row['reference_image_id'],)).fetchone()
                        if not ref:raise ValueError('reference_invalidated')
                        for fid in json.loads(row['face_observation_ids']):
                            f=c.execute("SELECT authorization_id FROM face_observations WHERE face_id=? AND status='usable' AND feature IS NOT NULL",(fid,)).fetchone()
                            if not f:raise ValueError('face_evidence_invalidated')
                            matcher.identity.authorization(f[0],run_id,c)
                        row['display_name']=subject['display_name']
                    except (HTTPException,ValueError):row['effective_status']='unknown';row['rejection_reason']='authorization_or_reference_inactive'
                result.append(row)
        bindings=[b for b in matcher.identity.bindings(run_id=run_id) if b['actor']==a['actor'] and b['source_kind'] not in {'teacher_named_track','demo_setup_track_label'}]
        return {'candidates':result,'bindings':bindings,'clip_id':clip['clip_id'],'track_id_is_identity':False}

    app.state.product_identity_results=identity_results

    @app.post('/api/context/identity/bindings',status_code=201)
    def bind_identity(body:Binding,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        student_owner(body.subject_id,a);owned_run(body.run_id,a)
        return matcher.identity.bind(body,a['actor'])

    @app.post('/api/context/identity/bindings/{bid}/revoke')
    def revoke_binding(bid:str,body:Change,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise HTTPException(403,'teacher_write_required')
        with access.connection() as c:row=c.execute('SELECT subject_id,run_id FROM identity_bindings WHERE binding_id=?',(bid,)).fetchone()
        if not row:raise HTTPException(404,'binding_not_found')
        student_owner(row[0],a);owned_run(row[1],a)
        return matcher.identity.change_binding(bid,body,a['actor'])

    @app.get('/api/context/students/{sid}/timeline')
    def student_timeline(sid:str,a=Depends(account)):
        student_owner(sid,a);subject=access.subject(sid)
        from src.behavior_discovery.repository import Repository
        repository=Repository(context.repo);intervals=[]
        for b in matcher.identity.bindings(sid):
            if b['effective_status']!='human_confirmed':continue
            try:
                owned_run(b['run_id'],a)
                matcher.tracks.interval(b['run_id'],b['track_id'],b['start'],b['end'])
            except (ValueError,PermissionError,HTTPException):continue
            observed=matcher.tracks.observations(b['run_id'],b['track_id'])
            visible=[o['global_timestamp'] for o in observed if b['start']<=o['global_timestamp']<=b['end']]
            events=[]
            for event in repository.events(b['run_id']):
                history=event['human_review_history'];h=history[-1] if history and history[-1]['status']!='revoked' else None
                if h:
                    d=h['data'];target=d.get('human_corrected_track_id',event['track_id'])
                    times=[t for t in d['reviewed_global_times'] if b['start']<=t<=b['end'] and any(abs(t-v)<.002 for v in visible)]
                    if target==b['track_id'] and times:
                        events.append({'event_id':event['event_id'],'action':d.get('human_corrected_category',event['action_category']),'status':'human_confirmed_behavior' if h['status']=='confirmed' else h['status'],
                            'times':times,'observation':d['observable_action'],'review_id':h['review_id'],'reviewer':h['actor'],'review_time':h['created_at'],'source_kind':'human_review','run_id':b['run_id'],
                            'href':'/app/review?global_time='+str(min(times))+'&run_id='+b['run_id']})
                elif event['track_id']==b['track_id']:
                    times=[t for t in event['observed_time_support'] if b['start']<=t<=b['end']]
                    if times:events.append({'event_id':event['event_id'],'action':event['action_category'],'status':'model_candidate_not_human_confirmed','times':times,'run_id':b['run_id']})
            intervals.append({'binding_id':b['binding_id'],'run_id':b['run_id'],'track_id':b['track_id'],'start':b['start'],'end':b['end'],'visible_times':visible,'events':events,
                'unobserved_scope':'Only these confirmed identity intervals and actual observations are included. Other times remain unknown.'})
        return {'subject_id':sid,'display_name':subject['display_name'],'identity_status':'human_confirmed_intervals_only' if intervals else 'not_confirmed',
            'intervals':intervals,'limitations':['AI candidates alone are never named behavior facts.','Unreviewed or revoked identity intervals are excluded.']}

    @app.post('/api/context/students/{sid}/report',status_code=201)
    def student_report(sid:str,body:dict,a=Depends(account)):
        student_owner(sid,a);owned_run(str(body.get('run_id','')),a)
        from src.agent.fusion.planning import ReviewRequest
        return app.state.fusion.ask(ReviewRequest(run_id=body['run_id'],subject_id=sid,question='生成该人物在有效身份区间内的可观察行为复盘报告，分离人工确认和模型候选。',use_llm=True),a['actor'],allow_identity=True)
