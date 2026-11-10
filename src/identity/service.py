import json
import math
import cv2
import numpy as np
from .store import uid, now, future
from .face_backend import cosine, owner, choose_match


def jpeg(image):
    ok,encoded=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,90])
    if not ok: raise ValueError('image_encoding_failed')
    return encoded.tobytes()


def clean_image(data):
    if len(data)>8*1024*1024: raise ValueError('reference_image_too_large')
    # Inspect dimensions before native decoder allocation; re-encode removes EXIF.
    from PIL import Image
    import io
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width*im.height>16_000_000 or min(im.size)<32: raise ValueError('invalid_image_dimensions')
            im.verify()
    except (OSError,Image.DecompressionBombError) as error:
        raise ValueError('reference_image_decode_failed') from error
    image=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
    if image is None: raise ValueError('reference_image_decode_failed')
    return jpeg(image)


class IdentityService:
    def __init__(self,store,tracks,backend):
        self.store,self.tracks,self.backend=store,tracks,backend

    def enroll(self,body,actor):
        future(body.expires_at)
        if 'manual_identity' not in body.purposes: raise ValueError('manual_identity_purpose_required')
        sid=uid()
        with self.store.connection() as c:
            consent={'record':body.consent_record,'purposes':body.purposes,'recorded_by':actor,'attested_at':now()}
            c.execute('INSERT INTO registered_subjects VALUES (?,?,?,?,?,?)',(sid,self.store.seal(body.display_name),self.store.seal(consent),body.expires_at,'active',now()))
            self.store.audit(c,actor,'enrollment',sid,{'consent_attested':True})
        return {'subject_id':sid,'status':'active'}

    def purpose(self,sid,purpose,c=None):
        s=self.store.subject(sid,c)
        if purpose not in s['consent_record']['purposes']: raise ValueError('authorization_purpose_missing:'+purpose)
        return s

    def reference(self,sid,data,provenance,actor):
        data=clean_image(data)
        ref=uid()
        with self.store.connection() as c:
            self.purpose(sid,'reference_storage',c)
            c.execute('INSERT INTO reference_images VALUES (?,?,?,?,?,?,?,?)',(ref,sid,self.store.seal(data),None,None,json.dumps(provenance),'active',now()))
            self.store.audit(c,actor,'reference_added',ref,{'subject_id':sid,'provenance':provenance})
        return {'reference_image_id':ref,'feature_status':'not_extracted','provenance':provenance}

    def authorize_video(self,body,actor):
        self.purpose(body.subject_id,'video_reference_capture')
        future(body.expires_at)
        run,_=self.tracks.run(body.run_id)
        authorization=uid()
        with self.store.connection() as c:
            self.purpose(body.subject_id,'video_reference_capture',c)
            c.execute('INSERT INTO video_authorizations VALUES (?,?,?,?,?,?,?)',(authorization,body.run_id,body.subject_id,run['metadata']['source_sha256'],self.store.seal({'record':body.consent_record,'all_visible_people_authorized':True}),body.expires_at,'active'))
            self.store.audit(c,actor,'video_scope_authorized',authorization,{'run_id':body.run_id,'all_visible_people_attestation':True})
        return {'authorization_id':authorization}

    def authorization(self,aid,rid,c=None):
        if c is None:
            with self.store.connection() as cx: return self.authorization(aid,rid,cx)
        row=c.execute('SELECT * FROM video_authorizations WHERE authorization_id=?',(aid,)).fetchone()
        if not row or row['status']!='active' or row['run_id']!=rid: raise ValueError('video_authorization_missing_or_revoked')
        future(row['expires_at'])
        self.store.subject(row['subject_id'],c)
        run,_=self.tracks.run(rid)
        if row['source_hash']!=run['metadata']['source_sha256']: raise ValueError('authorized_video_source_changed')
        return dict(row)

    def capture(self,body,actor):
        auth=self.authorization(body.authorization_id,body.run_id)
        if auth['subject_id']!=body.subject_id: raise ValueError('video_authorization_subject_mismatch')
        self.purpose(body.subject_id,'video_reference_capture')
        image,o,run,clip=self.tracks.frame(body.run_id,body.track_id,body.frame_index)
        h,w=image.shape[:2]
        a,b,c,d=[int(o[k]) for k in ('bbox_x1','bbox_y1','bbox_x2','bbox_y2')]
        if not 0<=a<c<=w or not 0<=b<d<=h: raise ValueError('person_crop_out_of_bounds')
        with self.store.connection() as cx: self.authorization(body.authorization_id,body.run_id,cx)
        return self.reference(body.subject_id,jpeg(image[b:d,a:c]),{'kind':'video_capture','run_id':body.run_id,'track_id':body.track_id,
            'clip_id':clip['clip_id'],'frame_index':body.frame_index,'local_time':o['local_timestamp'],'global_time':o['global_timestamp'],
            'source_sha256':run['metadata']['source_sha256'],'independent_identity_validation':False},actor)

    def bind(self,body,actor):
        run,clip=self.tracks.interval(body.run_id,body.track_id,body.start,body.end)
        bid=uid()
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            self.purpose(body.subject_id,'manual_identity',c)
            existing=c.execute("SELECT * FROM identity_bindings WHERE status='human_confirmed'").fetchall()
            if body.decision=='human_confirmed':
                switches=c.execute("SELECT start,end FROM identity_candidates WHERE run_id=? AND track_id=? AND rejection_reason='possible_identity_switch'",(body.run_id,body.track_id)).fetchall()
                if any(body.start<=s['start'] and body.end>=s['end'] for s in switches):
                    raise ValueError('possible_identity_switch_spans_interval; split_and_independently_review')
                for old in existing:
                    # No assumption that clocks across clips are synchronized.
                    overlap=max(old['start'],body.start)<=min(old['end'],body.end)
                    if overlap and old['run_id']==body.run_id and old['track_id']==body.track_id:
                        raise ValueError('track_interval_already_bound; revoke_or_split_first')
                    if overlap and old['clip_id']==clip['clip_id'] and old['subject_id']==body.subject_id and (old['run_id'],old['track_id'])!=(body.run_id,body.track_id):
                        raise ValueError('simultaneous_mutually_exclusive_tracks_for_subject')
                    if old['subject_id']==body.subject_id and old['clip_id']!=clip['clip_id'] and len(body.cross_clip_evidence.strip())<5:
                        raise ValueError('independent_cross_clip_evidence_required')
            evidence={'source_ref':body.evidence_ref,'reason':body.reason,'cross_clip_evidence':body.cross_clip_evidence,
                      'clip_source_sha256':run['metadata']['source_sha256'],'interval_reviewed':True,'global_offset':clip['start']}
            c.execute('INSERT INTO identity_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(bid,body.subject_id,body.run_id,body.track_id,clip['clip_id'],body.start,body.end,body.decision,body.source_kind,json.dumps(evidence),actor,now(),'human_reviewed_interval_only; full_track_unverified'))
            self.store.audit(c,actor,'identity_'+body.decision,bid,body.model_dump())
        return {'binding_id':bid,'status':body.decision,'scope':'reviewed_interval_only'}

    def bindings(self,sid=None,run_id=None):
        with self.store.connection() as c:
            result=[]
            conditions=[];params=[]
            if sid:conditions.append('subject_id=?');params.append(sid)
            if run_id:conditions.append('run_id=?');params.append(run_id)
            for row in c.execute('SELECT * FROM identity_bindings'+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY created_at',params):
                d=dict(row)
                try: self.store.subject(d['subject_id'],c)
                except ValueError:
                    d['effective_status']='authorization_inactive'
                else: d['effective_status']=d['status']
                d['evidence_refs']=json.loads(d['evidence_refs'])
                _,clip=self.tracks.run(d['run_id'])
                d['local_start']=d['start']-clip['start']
                d['replay_url']='/tracking?run_id='+d['run_id']+'&track_id='+str(d['track_id'])+'&local_time='+str(d['local_start'])
                result.append(d)
        return result

    def change_binding(self,bid,body,actor,split=False):
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM identity_bindings WHERE binding_id=?',(bid,)).fetchone()
            if not old: raise ValueError('binding_not_found')
            if old['status']!='human_confirmed': raise ValueError('only_active_confirmed_binding_can_be_revoked_or_split')
            if split and not old['start']<body.split_at<old['end']: raise ValueError('split_outside_binding')
            c.execute("UPDATE identity_bindings SET status='revoked' WHERE binding_id=?",(bid,))
            children=[]
            if split:
                for a,b in ((old['start'],body.split_at),(body.split_at,old['end'])):
                    child=uid();children.append(child)
                    c.execute('INSERT INTO identity_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(child,old['subject_id'],old['run_id'],old['track_id'],old['clip_id'],a,b,'needs_review',old['source_kind'],old['evidence_refs'],actor,now(),'split_requires_new_human_review'))
            self.store.audit(c,actor,'binding_split' if split else 'binding_revoked',bid,{'reason':body.reason,'children':children})
        return {'status':'revoked','children_requiring_review':children}

    def match(self,body,actor,subject_ids=None):
        if not self.backend.enabled: raise ValueError('automatic_identity_matching_disabled')
        self.authorization(body.authorization_id,body.run_id)
        if len(set(body.frame_indices))!=len(body.frame_indices): raise ValueError('duplicate_frame_indices')
        # Use only authorized, single-subject references, no library self-update.
        with self.store.connection() as c:
            refs=[dict(r) for r in c.execute("SELECT * FROM reference_images WHERE status='active'")]
        if subject_ids is not None:refs=[r for r in refs if r['subject_id'] in subject_ids]
        usable=[]
        elapsed=0.
        for r in refs:
            try: self.purpose(r['subject_id'],'face_matching')
            except ValueError: continue
            if r['feature'] is None or (self.backend.model_id and r['model_id']!=self.backend.model_id):
                faces,t=self.backend.faces(cv2.imdecode(np.frombuffer(self.store.unseal(r['image'],True),np.uint8),cv2.IMREAD_COLOR));elapsed+=t
                faces=[f for f in faces if f['feature'] is not None]
                if len(faces)!=1: continue
                r['feature']=self.store.seal(faces[0]['feature'].tolist());r['model_id']=self.backend.model_id
                with self.store.connection() as c:
                    self.purpose(r['subject_id'],'face_matching',c)
                    c.execute('UPDATE reference_images SET feature=?,model_id=? WHERE reference_image_id=? AND status=\'active\'',(r['feature'],r['model_id'],r['reference_image_id']))
            usable.append(r)
        if not usable: raise ValueError('no_authorized_usable_reference')
        acquired=[];scores=[];diagnostics=[]
        for idx in sorted(body.frame_indices):
            image,o,run,clip=self.tracks.frame(body.run_id,body.track_id,idx)
            self.authorization(body.authorization_id,body.run_id)
            faces,t=self.backend.faces(image);elapsed+=t
            observations=self.tracks.rows('SELECT * FROM track_observations WHERE run_id=? AND frame_index=?',(body.run_id,idx))
            selected=[f for f in faces if owner(f['bbox'],observations)==body.track_id and f['feature'] is not None]
            if len(selected)!=1:
                diagnostics.append({'frame_index':idx,'status':'unknown','reason':'face_missing_low_quality_or_ambiguous_owner'})
                continue
            f=selected[0];fid=uid();acquired.append((fid,o,f['feature']))
            with self.store.connection() as c:
                self.authorization(body.authorization_id,body.run_id,c)
                c.execute('INSERT INTO face_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,body.authorization_id,body.run_id,body.track_id,idx,o['global_timestamp'],o['local_timestamp'],json.dumps(f['bbox']),self.store.seal(jpeg(f['crop'])),self.store.seal(f['feature'].tolist()),self.backend.model_id,json.dumps({'status':f['quality'],'thresholds_validated':False}),'usable'))
            for r in usable:
                p=json.loads(r['provenance'])
                if p.get('kind')=='video_capture' and p.get('source_sha256')==run['metadata']['source_sha256']:
                    diagnostics.append({'status':'self_reference_excluded','reference_image_id':r['reference_image_id']})
                    continue
                if r['model_id']!=self.backend.model_id: continue
                scores.append({'subject_id':r['subject_id'],'reference_image_id':r['reference_image_id'],'score':cosine(f['feature'],self.store.unseal(r['feature'])),'face_id':fid})
        if subject_ids is not None and scores:
            import statistics
            aggregated=[]
            for sid in sorted({s['subject_id'] for s in scores}):
                subject_scores=[s for s in scores if s['subject_id']==sid]
                by_frame={fid:max(s['score'] for s in subject_scores if s['face_id']==fid) for fid in {s['face_id'] for s in subject_scores}}
                representative=max(subject_scores,key=lambda s:s['score'])
                aggregated.append({**representative,'score':statistics.median(by_frame.values())})
            status,best,reason=choose_match(aggregated)
        else:status,best,reason=choose_match(scores)
        switches=any(cosine(a[2],b[2])<.25 for i,a in enumerate(acquired) for b in acquired[i+1:])
        if switches: status,reason='needs_review','possible_identity_switch'
        elif status=='candidate' and len(acquired)<2: status,reason='needs_review','single_frame_only'
        elif best:
            supported={s['face_id'] for s in scores if s['subject_id']==best['subject_id'] and s['score']>=.55}
            if status=='candidate' and len(supported)<2: status,reason='needs_review','insufficient_independent_frames'
        times=[a[1]['global_timestamp'] for a in acquired]
        if not times:
            obs=self.tracks.observations(body.run_id,body.track_id)
            selected=[o['global_timestamp'] for o in obs if o['frame_index'] in body.frame_indices]
            if not selected: raise ValueError('no_visible_frames_selected')
            times=selected
        cid=uid()
        with self.store.connection() as c:
            self.authorization(body.authorization_id,body.run_id,c)
            if best:
                self.purpose(best['subject_id'],'face_matching',c)
                active=c.execute("SELECT 1 FROM reference_images WHERE reference_image_id=? AND status='active' AND feature IS NOT NULL",(best['reference_image_id'],)).fetchone()
                if not active: raise ValueError('reference_withdrawn_during_matching')
            c.execute('INSERT INTO identity_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,best['subject_id'] if best else None,body.run_id,body.track_id,min(times),max(times),best['score'] if best else None,self.backend.model_id,best['reference_image_id'] if best else None,json.dumps([a[0] for a in acquired]),status,reason,now()))
            self.store.audit(c,actor,'candidate_matching',cid,{'status':status,'reason':reason,'diagnostics':diagnostics,'elapsed_seconds':elapsed})
        return {'candidate_id':cid,'status':status,'reason':reason,'raw_similarity':best['score'] if best else None,
                'similarity_is_probability':False,'face_observation_ids':[a[0] for a in acquired],
                'elapsed_seconds':elapsed,'diagnostics':diagnostics,'model_id':self.backend.model_id}
