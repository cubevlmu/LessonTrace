import json
from pathlib import Path
from fastapi import Depends,File,Form,UploadFile
from fastapi.responses import FileResponse,Response
from src.agent.fusion import reporting
from src.identity.schemas import Enrollment,Binding,Change
from src.identity.service import jpeg
from src.identity.store import uid,now
from src.attribution.schemas import Generate,Revoke
from .schemas import TrackReview,QAScore,ActorAssessment,ChainRequest,QuestionApproval,IdentityAssessment
from .service import Workbench
from .evidence_chain import validate
from .repository import append_json,track_scope


def mount(app,context,account,writer):
    wb=Workbench(context);app.state.validation=wb
    def private(a=Depends(account)):
        if a['role'] not in {'admin','reviewer'}:raise PermissionError('validation_private_review_role_required')
        return a
    def admin(a=Depends(account)):
        if a['role']!='admin':raise PermissionError('validation_admin_required_for_identity_enrollment')
        return a
    @app.get('/validation')
    def page():return FileResponse(Path(__file__).resolve().parents[2]/'webui/validation.html',media_type='text/html')
    @app.get('/api/context/validation/environment')
    def environment(a=Depends(private)):return {'synthetic_fixture':bool(getattr(app.state,'synthetic_validation',False)),'automatic_human_grading':False}
    @app.get('/api/context/validation/tracking/tasks')
    def tasks(a=Depends(private)):return wb.track_tasks()
    @app.get('/api/context/validation/tracking/detail')
    def track(run_id:str,track_id:int,a=Depends(private)):return wb.track_detail(run_id,track_id)
    @app.post('/api/context/validation/tracking/reviews',status_code=201)
    def review(body:TrackReview,a=Depends(writer)):return wb.review_track(body,a['actor'])
    @app.get('/api/context/validation/frame')
    def frame(run_id:str,track_id:int,frame_index:int,a=Depends(private)):
        import cv2
        image,o,_,_=context.source.frame(run_id,track_id,frame_index)
        cv2.rectangle(image,(int(o['bbox_x1']),int(o['bbox_y1'])),(int(o['bbox_x2']),int(o['bbox_y2'])),(0,220,240),2)
        return Response(jpeg(image),media_type='image/jpeg')
    @app.get('/api/context/validation/identity')
    def identity(a=Depends(private)):
        if not context.identity_dir or not (context.identity_dir/'identity.db').exists():
            return {'status':'not_evaluable','subjects':[],'bindings':[],'automatic_matching':'not_validated_no_authorized_test_material'}
        native=wb.identity();subjects=[]
        with native.store.connection() as c:
            for row in c.execute('SELECT subject_id FROM registered_subjects ORDER BY created_at'):
                s=native.store.subject(row[0],c,False)
                try:native.store.subject(row[0],c);s['effective_status']='active'
                except ValueError:s['effective_status']='inactive'
                subjects.append(s)
        bindings=native.bindings()
        for b in bindings:
            try:
                run,_=context.source.verified_run(b['run_id']);b['continuity_review']=track_scope(context.repo,b['run_id'],b['track_id'],b['start'],b['end'],run['metadata']['source_sha256'])
                b['currently_usable_reviewed_intervals']=b['continuity_review']['verified_intervals'] if b['effective_status']=='human_confirmed' else []
            except (ValueError,OSError):b['currently_usable_reviewed_intervals']=[]
        return {'status':'available' if subjects else 'not_evaluable','subjects':subjects,'bindings':bindings,
            'automatic_matching':'not_validated','matching_enabled':False,'note':'Existing consent required. Similarity is not identity probability.'}
    @app.post('/api/context/validation/identity/subjects',status_code=201)
    def enroll(body:Enrollment,a=Depends(admin)):return wb.identity(True).enroll(body,a['actor'])
    @app.post('/api/context/validation/identity/subjects/{sid}/revoke')
    def purge(sid:str,body:Change,a=Depends(admin)):
        wb.identity(True).store.purge(sid,a['actor']);return {'status':'revoked','biometrics_purged':True}
    @app.post('/api/context/validation/identity/references',status_code=201)
    async def reference(subject_id:str=Form(...),photo:UploadFile=File(...),a=Depends(admin)):
        return wb.identity(True).reference(subject_id,await photo.read(8*1024*1024+1),{'kind':'authorized_upload','independent_identity_validation':False},a['actor'])
    @app.get('/api/context/validation/identity/references')
    def refs(subject_id:str,a=Depends(private)):
        native=wb.identity();native.store.subject(subject_id)
        with native.store.connection() as c:return [dict(r) for r in c.execute('SELECT reference_image_id,status,provenance FROM reference_images WHERE subject_id=?',(subject_id,))]
    @app.get('/api/context/validation/identity/references/{reference_id}/image')
    def reference_image(reference_id:str,a=Depends(private)):
        native=wb.identity()
        with native.store.connection() as c:r=c.execute('SELECT * FROM reference_images WHERE reference_image_id=?',(reference_id,)).fetchone()
        if not r or r['status']!='active' or r['image'] is None:raise ValueError('reference_image_missing_or_deleted')
        native.store.subject(r['subject_id']);return Response(native.store.unseal(r['image'],True),media_type='image/jpeg')
    @app.delete('/api/context/validation/identity/references/{reference_id}')
    def delete_ref(reference_id:str,a=Depends(admin)):
        native=wb.identity(True)
        with native.store.connection() as c:r=c.execute('SELECT subject_id FROM reference_images WHERE reference_image_id=?',(reference_id,)).fetchone()
        if not r:raise ValueError('reference_missing')
        native.store.purge(r[0],a['actor'],reference_id);return {'status':'deleted','features_purged':True}
    @app.get('/api/context/validation/identity/candidates')
    def candidates(run_id:str,track_id:int,a=Depends(private)):
        native=wb.identity();context.source.verified_run(run_id)
        with native.store.connection() as c:rows=[dict(r) for r in c.execute('SELECT * FROM identity_candidates WHERE run_id=? AND track_id=?',(run_id,track_id))]
        for r in rows:
            try:
                native.store.subject(r['subject_id'])
                with native.store.connection() as c:
                    ref=c.execute('SELECT status FROM reference_images WHERE reference_image_id=?',(r['reference_image_id'],)).fetchone()
                    if not ref or ref['status']!='active':raise ValueError('reference_revoked')
                    for fid in json.loads(r['face_observation_ids']):
                        face=c.execute('SELECT authorization_id,status FROM face_observations WHERE face_id=?',(fid,)).fetchone()
                        if not face or face['status']=='purged':raise ValueError('evidence_purged')
                        native.authorization(face['authorization_id'],run_id,c)
            except (ValueError,TypeError):r['status']='unknown'
        return {'candidates':rows,'automatic_matching':'not_validated','matching_enabled':False}
    @app.post('/api/context/validation/identity/bindings',status_code=201)
    def bind(body:Binding,a=Depends(writer)):
        run,_=context.source.verified_run(body.run_id)
        if body.decision=='human_confirmed' and track_scope(context.repo,body.run_id,body.track_id,body.start,body.end,run['metadata']['source_sha256'])['status']!='verified':
            raise ValueError('scoped_track_continuity_review_required_before_identity_confirmation')
        return wb.identity(True).bind(body,a['actor'])
    @app.post('/api/context/validation/identity/bindings/{bid}/revoke')
    def revoke_identity(bid:str,body:Change,a=Depends(writer)):return wb.identity(True).change_binding(bid,body,a['actor'])
    @app.get('/api/context/validation/identity/history')
    def identity_history(object_id:str,a=Depends(private)):
        native=wb.identity()
        with native.store.connection() as c:return [{**dict(r),'payload':native.store.unseal(r['payload'])} for r in c.execute('SELECT * FROM identity_review_history WHERE object_id=? ORDER BY created_at',(object_id,))]
    @app.post('/api/context/validation/identity/assessments',status_code=201)
    def assess_identity(body:IdentityAssessment,a=Depends(writer)):
        if not any(b['binding_id']==body.binding_id for b in wb.identity().bindings()):raise ValueError('identity_binding_missing')
        rid=uid();append_json(context.repo,'validation_identity_assessments',(rid,body.binding_id,a['actor'],now(),json.dumps(body.model_dump(),ensure_ascii=False)))
        return {'review_id':rid,'judgment':body.judgment}
    @app.get('/api/context/validation/attribution/candidates')
    def actor_candidates(run_id:str,a=Depends(private)):
        context.source.verified_run(run_id)
        with context.attribution_repo.connection() as c:result=[json.loads(r[0]) for r in c.execute('SELECT payload FROM event_track_candidates WHERE run_id=? ORDER BY start,event_id,track_id',(run_id,))]
        from src.behavior_discovery.repository import Repository
        for e in Repository(context.repo).events(run_id):
            result.append({'candidate_id':'discovered:'+e['event_id'],'event_id':e['event_id'],'run_id':run_id,'track_id':e['track_id'],
                'target_action':e['action_category'],'start':e['local_start'],'end':e['local_end'],'candidate_count':1,
                'ambiguity_flags':['model_discovered_not_human_confirmed']+e['subject_checks'].get('failures',[]),'source_kind':'model_discovered_event'})
        return result
    @app.get('/api/context/validation/events')
    def events(run_id:str,a=Depends(private)):
        _,clip=context.source.verified_run(run_id)
        return [e for e in context.source.events(clip['video_id']) if max(e['t_start'],clip['start'])<=min(e['t_end'],clip['end'])]
    @app.post('/api/context/validation/attribution/generate',status_code=201)
    def generate(body:Generate,a=Depends(writer)):return wb.attribution(True).generate(body.event_id,body.run_id)
    @app.get('/api/context/validation/attribution/timeline')
    def timeline(run_id:str,track_id:int,a=Depends(private)):return wb.attribution().timeline(run_id,track_id,True)
    @app.post('/api/context/validation/attribution/reviews',status_code=201)
    def assess(body:ActorAssessment,a=Depends(writer)):return wb.assess_actor(body,a['actor'])
    @app.post('/api/context/validation/attribution/bindings/{bid}/revoke')
    def revoke_actor(bid:str,body:Revoke,a=Depends(writer)):
        if bid.startswith('discovered:'):
            from src.behavior_discovery.service import DiscoveryService
            return DiscoveryService(context).revoke_review(bid.split(':',1)[1],a['actor'],body.reason)
        return wb.attribution(True).revoke(bid,body.reason,a['actor'])
    @app.get('/api/context/validation/attribution/history')
    def actor_history(candidate_id:str,a=Depends(private)):
        if candidate_id.startswith('discovered:'):
            from src.behavior_discovery.repository import Repository
            with context.repo.connection() as c:r=c.execute('SELECT video_id FROM discovered_behavior_events WHERE event_id=?',(candidate_id.split(':',1)[1],)).fetchone()
            if not r:raise ValueError('discovered_event_missing')
            Repository(context.repo).video(r[0],a['actor'])
            with context.repo.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM discovered_behavior_reviews WHERE event_id=? ORDER BY created_at',(candidate_id.split(':',1)[1],))]
        with context.repo.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM validation_actor_assessments WHERE candidate_id=? ORDER BY created_at',(candidate_id,))]
    @app.get('/api/context/validation/qa/pairs')
    def pairs(a=Depends(private)):return wb.pairs(a['actor'])
    @app.get('/api/context/validation/qa/reports/{rid}')
    def saved_report(rid:str,a=Depends(private)):return reporting.historical(context,rid,a['actor'],True)
    @app.post('/api/context/validation/qa/scores',status_code=201)
    def score(body:QAScore,a=Depends(writer)):return wb.score(body,a['actor'])
    @app.get('/api/context/validation/qa/scores')
    def scores(report_id:str,a=Depends(private)):
        reporting.historical(context,report_id,a['actor'],True)
        with context.repo.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM validation_qa_scores WHERE report_id=? ORDER BY created_at',(report_id,))]
    @app.post('/api/context/validation/chain')
    def chain(body:ChainRequest,a=Depends(private)):return validate(context,body,a['actor'])
    @app.get('/api/context/validation/questions')
    def questions(a=Depends(private)):
        path=Path(__file__).resolve().parents[2]/'outputs/evidence_chain_validation/new_questions.json'
        data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status':'pending','cases':[]}
        with context.repo.connection() as c:
            for q in data['cases']:
                row=c.execute('SELECT * FROM validation_question_approvals WHERE case_id=? ORDER BY created_at DESC,review_id DESC LIMIT 1',(q['case_id'],)).fetchone()
                q['latest_human_review']={**dict(row),'payload':json.loads(row['payload'])} if row else None
        return data
    @app.post('/api/context/validation/questions/reviews',status_code=201)
    def approve(body:QuestionApproval,a=Depends(writer)):
        path=Path(__file__).resolve().parents[2]/'outputs/evidence_chain_validation/new_questions.json'
        if not path.exists() or body.case_id not in {q['case_id'] for q in json.loads(path.read_text(encoding='utf-8'))['cases']}:raise ValueError('unknown_validation_question')
        rid=uid();append_json(context.repo,'validation_question_approvals',(rid,body.case_id,a['actor'],now(),json.dumps(body.model_dump(),ensure_ascii=False)))
        return {'review_id':rid,'status':'human_review_recorded'}
    @app.get('/api/context/validation/metrics')
    def metrics(a=Depends(private)):
        from .metrics import evaluate
        return evaluate(wb,a['actor'])
