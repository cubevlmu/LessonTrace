"""Track-first behavior profiles. Roster labels never manufacture verified identities."""
import hashlib
import json
import math
from typing import Literal
from functools import lru_cache
from fastapi import Depends,Response
from pydantic import BaseModel,ConfigDict,Field,model_validator
from src.identity.store import uid,now,future
from src.identity.service import clean_image,jpeg
from src.behavior_discovery.repository import Repository
from src.agent.visual_evidence_policy import semantic_findings
from src.validation.repository import track_scope


class Registration(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)
    request_id:str=Field(min_length=8,max_length=80)
    classroom_id:str
    run_id:str
    track_id:int=Field(ge=1)
    frame_index:int=Field(ge=0)
    local_start:float=Field(ge=0)
    local_end:float=Field(ge=0)
    subject_id:str|None=None
    display_name:str=Field('',max_length=120)
    note:str=Field(min_length=5,max_length=2000)
    authorized:bool=False
    consent_record:str=Field('',max_length=4000)
    expires_at:str=''
    enable_face_matching:bool=False
    label_origin:Literal['operator','demo_setup']='operator'
    @model_validator(mode='after')
    def valid(self):
        if self.local_end<self.local_start:raise ValueError('invalid_profile_interval')
        if not self.subject_id and not self.display_name:raise ValueError('profile_name_required')
        if self.authorized:
            future(self.expires_at)
            if len(self.consent_record)<5:raise ValueError('actual_authorization_record_required')
        elif self.enable_face_matching:raise ValueError('face_matching_requires_explicit_authorization')
        if self.label_origin=='demo_setup' and (self.authorized or self.enable_face_matching):raise ValueError('demo_setup_cannot_attest_real_identity_authorization')
        return self


def labels(store,service,rid,clip,actor,role):
    with store.connection() as c:
        rows=c.execute("SELECT b.*,s.display_name,s.status AS subject_status,r.status AS photo_status FROM identity_bindings b JOIN registered_subjects s ON s.subject_id=b.subject_id JOIN classroom_students cs ON cs.subject_id=s.subject_id JOIN classrooms cl ON cl.classroom_id=cs.classroom_id LEFT JOIN reference_images r ON r.reference_image_id=json_extract(b.evidence_refs,'$.reference_image_id') WHERE b.run_id=? AND b.status='track_label' AND cs.status='active' AND cl.status='active' AND (cl.owner_actor=? OR ?='admin')",(rid,actor,role)).fetchall()
    result=[]
    run,_=service.source.verified_run(rid)
    for r in rows:
        evidence=json.loads(r['evidence_refs'])
        if r['subject_status'] not in {'active','reference_only'} or r['photo_status']!='active' or evidence['clip_source_sha256']!=run['metadata']['source_sha256']:continue
        if r['subject_status']=='active':
            try:store.subject(r['subject_id'])
            except ValueError:continue
        scope=track_scope(service.repo,rid,r['track_id'],r['start'],r['end'],run['metadata']['source_sha256'])
        if scope['problem_intervals']:continue
        result.append({'binding_id':r['binding_id'],'subject_id':r['subject_id'],'display_name':store.unseal(r['display_name']),'track_id':r['track_id'],
            'local_start':r['start']-clip['start'],'local_end':r['end']-clip['start'],'status':'track_label','source_kind':r['source_kind'],'reference_image_id':evidence['reference_image_id'],'frame_index':evidence['frame_index']})
    return result


def profiles(service,rid):
    run,clip=service.source.verified_run(rid);result=[]
    with service.repo.connection() as c:windows=c.execute("SELECT * FROM discovery_windows WHERE run_id=? AND status='completed' ORDER BY start,window_id",(rid,)).fetchall()
    groups={}
    for w in windows:
        raw=json.loads(w['payload']).get('result',{});frames={f['frame_id']:f for f in raw.get('frames',[])}
        for action in (raw.get('observations') or {}).get('observed_actions',[]):
            if semantic_findings(action['action']):continue
            refs=action['evidence_refs']
            if not refs or any(ref not in frames for ref in refs):continue
            key=(w['track_id'],w['segment_id'],action['action'].strip().casefold())
            item=groups.setdefault(key,{'observed_action':action['action'],'segment_id':w['segment_id'],'group_id':hashlib.sha256(json.dumps([rid,*key]).encode()).hexdigest()[:24],'observed_times':[],'evidence_refs':[],'source_job_ids':[],'source_kind':'vlm_observation','verification_status':'unreviewed'})
            item['observed_times'].extend(frames[r]['local_time'] for r in refs)
            item['evidence_refs'].extend({'frame_id':r,'local_time':frames[r]['local_time'],'window_id':w['window_id'],'frame_index':frames[r]['frame_index']} for r in refs)
            item['source_job_ids'].append(w['job_id'])
    for (tid,_,_),item in groups.items():
        item['observed_times']=sorted(set(item['observed_times']));item['source_job_ids']=sorted(set(item['source_job_ids']))
        item['evidence_refs']=list({(r['frame_id'],r['local_time']):r for r in item['evidence_refs']}.values())
    events=Repository(service.repo).events(rid)
    for track in service.source.tracks(rid):
        obs=[o for o in service.source.observations(rid,track['track_id']) if o['tracker_state']=='matched_visible_detection']
        if not obs:continue
        representative=max(obs,key=lambda o:(o['bbox_x2']-o['bbox_x1'])*(o['bbox_y2']-o['bbox_y1'])*o['confidence'])
        corrections=[]
        for event in events:
            history=event['human_review_history'];h=history[-1] if history and history[-1]['status']!='revoked' else None
            if h and h['data'].get('human_corrected_track_id',event['track_id'])==track['track_id']:corrections.append({'event_id':event['event_id'],'status':h['status'],'data':h['data']})
        scope=track_scope(service.repo,rid,track['track_id'],obs[0]['global_timestamp'],obs[-1]['global_timestamp'],run['metadata']['source_sha256'])
        result.append({**track,'profile_id':f'{rid}:{track["track_id"]}','continuity_status':scope['status'],'problem_intervals':scope['problem_intervals'],'action_groups':[g for (tid,_,_),g in groups.items() if tid==track['track_id']],
            'human_corrections':corrections,'representative_frame':representative['frame_index'],'representative_time':representative['local_timestamp'],
            'portrait_url':f'/api/context/workbench/people/{track["track_id"]}/image?run_id={rid}&frame_index={representative["frame_index"]}'})
    return {'run_id':rid,'global_offset':clip['start'],'people':result,'truncated_windows':False,'grouping_basis':'same_run_and_anonymous_track; not_cross_clip_identity'}


def mount(app,service,account):
    store=app.state.product_matcher.store
    def source(rid,a):
        run,clip=service.source.verified_run(rid);Repository(service.repo).video(clip['video_id'],a['actor']);return run,clip
    def classroom(cid,a):
        with store.connection() as c:r=c.execute("SELECT * FROM classrooms WHERE classroom_id=? AND status='active' AND (owner_actor=? OR ?='admin')",(cid,a['actor'],a['role'])).fetchone()
        if not r:raise PermissionError('classroom_not_owned')
    @lru_cache(maxsize=128)
    def cropped(rid,tid,index,source_hash):
        frame,o,run,clip=service.source.frame(rid,tid,index)
        x1,y1,x2,y2=[int(o[k]) for k in ['bbox_x1','bbox_y1','bbox_x2','bbox_y2']]
        crop=frame[max(0,y1):min(frame.shape[0],y2),max(0,x1):min(frame.shape[1],x2)]
        if not crop.size:raise ValueError('empty_person_crop')
        return clean_image(jpeg(crop)),o,run,clip
    def image(rid,tid,index,a):
        run,_=source(rid,a)
        return cropped(rid,tid,index,run['metadata']['source_sha256'])

    @app.get('/api/context/workbench/people')
    def people(run_id:str,a=Depends(account)):
        source(run_id,a);return profiles(service,run_id)

    @app.get('/api/context/workbench/people/{track_id}/capture')
    def capture(track_id:int,run_id:str,local_time:float,a=Depends(account)):
        source(run_id,a)
        if not math.isfinite(local_time) or local_time<0:raise ValueError('invalid_capture_time')
        rows=service.source.observations(run_id,track_id)
        o=min(rows,key=lambda o:abs(o['local_timestamp']-local_time)) if rows else None
        if not o or abs(o['local_timestamp']-local_time)>.2 or o['tracker_state']!='matched_visible_detection':raise ValueError('target_not_observed_at_requested_time')
        return {'frame_index':o['frame_index'],'local_time':o['local_timestamp'],'portrait_url':f'/api/context/workbench/people/{track_id}/image?run_id={run_id}&frame_index={o["frame_index"]}'}

    @app.get('/api/context/workbench/people/{track_id}/image')
    def portrait(track_id:int,run_id:str,frame_index:int,a=Depends(account)):
        data,*_=image(run_id,track_id,frame_index,a);return Response(data,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})

    @app.post('/api/context/workbench/registrations',status_code=201)
    def register(body:Registration,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise PermissionError('teacher_write_required')
        run,clip=source(body.run_id,a);classroom(body.classroom_id,a)
        start,end=body.local_start+clip['start'],body.local_end+clip['start']
        service.source.interval(body.run_id,body.track_id,start,end)
        if track_scope(service.repo,body.run_id,body.track_id,start,end,run['metadata']['source_sha256'])['problem_intervals']:raise ValueError('known_track_switch; select_a_trustworthy_subinterval')
        fingerprint=hashlib.sha256(body.model_dump_json(exclude={'request_id'}).encode()).hexdigest()
        bid='profile_'+hashlib.sha256((a['actor']+body.request_id).encode()).hexdigest()[:32]
        def existing(c):
            row=c.execute('SELECT * FROM identity_bindings WHERE binding_id=?',(bid,)).fetchone()
            if row:
                e=json.loads(row['evidence_refs'])
                if e.get('registration_fingerprint')!=fingerprint or row['status']!='track_label':raise ValueError('registration_key_conflict_or_revoked')
                photo=c.execute("SELECT 1 FROM reference_images r JOIN registered_subjects s ON s.subject_id=r.subject_id WHERE r.reference_image_id=? AND r.status='active' AND s.status IN ('active','reference_only')",(e['reference_image_id'],)).fetchone()
                if not photo:raise ValueError('registration_reference_or_subject_inactive')
                return {'binding_id':bid,'subject_id':row['subject_id'],'reference_image_id':e['reference_image_id'],'status':'track_label','identity_confirmed':False,'created':False}
        with store.connection() as c:old=existing(c)
        if old:return old
        photo,o,_,_=image(body.run_id,body.track_id,body.frame_index,a)
        if not start-.002<=o['global_timestamp']<=end+.002:raise ValueError('registration_frame_outside_selected_interval')
        sid=body.subject_id or uid();ref=uid();stamp=now();feature=None;model=None;feature_status='not_requested'
        if body.subject_id:
            with store.connection() as c:
                if not c.execute("SELECT 1 FROM classroom_students WHERE classroom_id=? AND subject_id=? AND status='active'",(body.classroom_id,sid)).fetchone():raise PermissionError('student_not_in_classroom')
                if store.subject(sid,c,False)['status'] not in {'active','reference_only'}:raise ValueError('student_not_active')
        if body.enable_face_matching:
            import cv2,numpy as np
            backend=app.state.product_matcher.identity.backend
            if backend.probe()['model_status']!='present_not_validated':feature_status='model_unavailable'
            else:
                try:
                    faces,_=backend.faces(cv2.imdecode(np.frombuffer(photo,np.uint8),cv2.IMREAD_COLOR))
                    if len(faces)==1 and faces[0]['feature'] is not None:feature=store.seal(faces[0]['feature'].tolist());model=backend.model_id;feature_status='extracted_not_identity_confirmed'
                    else:feature_status='needs_clear_single_face'
                except (ValueError,RuntimeError,cv2.error):feature_status='face_extraction_failed; track_registration_only'
        evidence={'clip_source_sha256':run['metadata']['source_sha256'],'frame_index':o['frame_index'],'local_time':o['local_timestamp'],'reference_image_id':ref,'reason':body.note,'registration_fingerprint':fingerprint,'identity_confirmed':False,'label_origin':body.label_origin}
        with store.connection() as c:
            c.execute('BEGIN IMMEDIATE');old=existing(c)
            if old:return old
            if c.execute("SELECT 1 FROM identity_bindings WHERE run_id=? AND track_id=? AND status='track_label' AND start<=? AND end>=?",(body.run_id,body.track_id,end,start)).fetchone():raise ValueError('track_interval_already_registered; revoke_previous_label_first')
            if c.execute("SELECT 1 FROM identity_bindings WHERE subject_id=? AND clip_id=? AND status IN ('track_label','human_confirmed') AND start<=? AND end>=? AND (run_id<>? OR track_id<>?)",(sid,clip['clip_id'],end,start,body.run_id,body.track_id)).fetchone():raise ValueError('conflicting_simultaneous_tracks_for_roster_person')
            linked=c.execute('SELECT classroom_id FROM classroom_videos WHERE video_id=?',(clip['video_id'],)).fetchone()
            if linked and linked[0]!=body.classroom_id:raise ValueError('video_already_in_another_classroom')
            if not body.subject_id:
                c.execute('INSERT INTO registered_subjects VALUES (?,?,?,?,?,?)',(sid,store.seal(body.display_name),store.seal({'record':body.consent_record if body.authorized else body.note,'purposes':['manual_identity','reference_storage']+(['face_matching','video_reference_capture'] if body.enable_face_matching else []) if body.authorized else ['demo_reference_storage'],'authorization_status':'attested_by_teacher' if body.authorized else 'not_provided','recorded_by':a['actor']}),body.expires_at if body.authorized else '', 'active' if body.authorized else 'reference_only',stamp))
                c.execute('INSERT INTO classroom_students VALUES (?,?,?,?)',(body.classroom_id,sid,'active',stamp))
            elif body.authorized:
                c.execute("UPDATE registered_subjects SET status='active',expires_at=?,consent_record=? WHERE subject_id=?",(body.expires_at,store.seal({'record':body.consent_record,'purposes':['manual_identity','reference_storage']+(['face_matching','video_reference_capture'] if body.enable_face_matching else []),'recorded_by':a['actor']}),sid))
                if not body.enable_face_matching:c.execute('UPDATE reference_images SET feature=NULL,model_id=NULL WHERE subject_id=?',(sid,))
            c.execute('INSERT INTO reference_images VALUES (?,?,?,?,?,?,?,?)',(ref,sid,store.seal(photo),feature,model,json.dumps({'kind':'teacher_track_capture','run_id':body.run_id,'track_id':body.track_id,'frame_index':body.frame_index,'local_time':o['local_timestamp'],'self_reference_not_independent_identity_evidence':True}),'active',stamp))
            c.execute('INSERT INTO identity_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(bid,sid,body.run_id,body.track_id,clip['clip_id'],start,end,'track_label','demo_setup_track_label' if body.label_origin=='demo_setup' else 'teacher_named_track',json.dumps(evidence),a['actor'],stamp,'unverified_anonymous_track_registration'))
            c.execute('INSERT OR IGNORE INTO classroom_videos VALUES (?,?,?,?,?)',(body.classroom_id,clip['video_id'],None,stamp,a['actor']))
            store.audit(c,a['actor'],'track_registered_to_roster',bid,{'subject_id':sid,'run_id':body.run_id,'track_id':body.track_id,'local_scope':[body.local_start,body.local_end],'feature_status':feature_status,'not_independent_identity_confirmation':True})
        return {'binding_id':bid,'subject_id':sid,'reference_image_id':ref,'status':'track_label','identity_confirmed':False,'feature_status':feature_status,'created':True}

    @app.post('/api/context/workbench/registrations/{binding_id}/revoke')
    def revoke(binding_id:str,body:dict,a=Depends(account)):
        if a['role'] not in {'admin','teacher'}:raise PermissionError('teacher_write_required')
        if len(str(body.get('reason','')).strip())<5:raise ValueError('revocation_reason_required')
        with store.connection() as c:row=c.execute('SELECT * FROM identity_bindings WHERE binding_id=?',(binding_id,)).fetchone()
        if not row or row['status']!='track_label':raise ValueError('track_registration_not_active')
        source(row['run_id'],a)
        with store.connection() as c:member=c.execute("SELECT cs.classroom_id FROM classroom_students cs JOIN classrooms cl ON cl.classroom_id=cs.classroom_id WHERE cs.subject_id=? AND cs.status='active' AND (cl.owner_actor=? OR ?='admin')",(row['subject_id'],a['actor'],a['role'])).fetchone()
        if not member:raise PermissionError('classroom_not_owned')
        with store.connection() as c:
            c.execute("UPDATE identity_bindings SET status='revoked' WHERE binding_id=?",(binding_id,));store.audit(c,a['actor'],'track_registration_revoked',binding_id,body)
        return {'status':'revoked'}

    @app.get('/api/context/workbench/students/{subject_id}/profiles')
    def roster_profiles(subject_id:str,a=Depends(account)):
        with store.connection() as c:
            row=c.execute("SELECT s.* FROM registered_subjects s JOIN classroom_students cs ON cs.subject_id=s.subject_id JOIN classrooms cl ON cl.classroom_id=cs.classroom_id WHERE s.subject_id=? AND cs.status='active' AND (cl.owner_actor=? OR ?='admin')",(subject_id,a['actor'],a['role'])).fetchone()
            runs=[r[0] for r in c.execute("SELECT DISTINCT run_id FROM identity_bindings WHERE subject_id=? AND status='track_label'",(subject_id,))]
        if not row or row['status'] not in {'active','reference_only'}:raise PermissionError('student_profile_not_accessible')
        items=[]
        for rid in runs:
            try:
                _,clip=source(rid,a);bindings=[b for b in labels(store,service,rid,clip,a['actor'],a['role']) if b['subject_id']==subject_id]
                groups=profiles(service,rid)
                for b in bindings:
                    p=next(p for p in groups['people'] if p['track_id']==b['track_id'])
                    actions=[]
                    for g in p['action_groups']:
                        times=[t for t in g['observed_times'] if b['local_start']<=t<=b['local_end']]
                        if times:actions.append({**g,'observed_times':times,'evidence_refs':[r for r in g['evidence_refs'] if r['local_time'] in times]})
                    items.append({**b,'run_id':rid,'portrait_url':f'/api/context/workbench/people/{b["track_id"]}/image?run_id={rid}&frame_index={b["frame_index"]}','action_groups':actions,'continuity_status':p['continuity_status']})
            except (ValueError,PermissionError):continue
        return {'subject_id':subject_id,'display_name':store.unseal(row['display_name']),'profiles':items,'identity_confirmed':False}
