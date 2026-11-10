"""Subject-scoped extraction using real 11A detections and the existing VLM/CESA."""
import base64
import hashlib
import json
import threading
import time
import subprocess
import os
from pathlib import Path
import cv2
import numpy as np
from filelock import FileLock,Timeout as LockTimeout
from src.agent.visual_reviewer import VisualReviewer,VLMConfig
from src.agent.visual_evidence_policy import decide,semantic_findings,DYNAMIC_ACTIONS
from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS
from src.schema import VisualObservations
from .schemas import ActorJudgment,ActorObservations
from .service import uid,now,bbox,overlap
from .transport import SubjectVLM

PROMPT='actor-scope-v1.4-normalized-target-contract'


def scaled(image,long_edge=512):
    h,w=image.shape[:2];factor=min(1,long_edge/max(h,w))
    return cv2.resize(image,(max(1,round(w*factor)),max(1,round(h*factor))))


def subject_gate(judgment,frames):
    """Model statements alone cannot prove ownership; independently check anchors."""
    known={f['frame_id']:f for f in frames};failures=[]
    if judgment.executor_relation not in {'target','multiple'}:failures.append('target_not_established_as_executor')
    target_facts=[f for f in judgment.support_facts if f.actor_role=='target' and f.subject_ref=='target' and f.relation_to_target=='supports']
    if not target_facts:failures.append('no_target_specific_support_fact')
    for fact in judgment.support_facts:
        if fact.relation_to_target=='supports' and (fact.actor_role!='target' or fact.subject_ref!='target'):failures.append('other_or_ambiguous_actor_used_as_support')
    for fact in target_facts:
        anchors={a.frame_id:a for a in fact.spatial_anchors}
        if not set(fact.evidence_refs)<=set(anchors):failures.append('target_anchor_missing')
        for ref in fact.evidence_refs:
            if ref not in known or ref not in anchors:continue
            f=known[ref];a=anchors[ref]
            b=a.box_xyxy_normalized;w,h=f['width'],f['height']
            if not all(0<=x<=1 for x in b) or b[0]>=b[2] or b[1]>=b[3] or a.subject_ref!='target':
                failures.append('invalid_target_anchor');continue
            box=[b[0]*w,b[1]*h,b[2]*w,b[3]*h];area=(box[2]-box[0])*(box[3]-box[1])
            if overlap(box,f['roi_xyxy'])/area<.8:failures.append('anchor_outside_target_detection')
            if any(overlap(box,other)/area>=.5 for other in f['other_person_boxes']):failures.append('anchor_has_competing_person_ownership')
    return {'passed':not failures,'failures':sorted(set(failures)),
            'scope':'tracked visible detections at sampled instants; identity continuity is not independently established'}


class ActorReviewer:
    def __init__(self,service,evidence_root,reviewer=None,temperature_checks=None):
        self.service=service;self.root=Path(evidence_root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.reviewer=reviewer or SubjectVLM(VLMConfig.from_env());self.lock=threading.Lock()
        self.process_lock=FileLock(str(self.root.parent/'actor_vlm.lock'))
        self.temperature_checks=temperature_checks

    def extract(self,d,observations,review_id,prefix):
        folder=self.root/review_id;folder.mkdir(exist_ok=True)
        frames=[]
        source=self.service.source
        batch=source.frames(d['run_id'],d['track_id'],[o['frame_index'] for o in observations]) if hasattr(source,'frames') else None
        for index,o in enumerate(observations):
            image,actual,_,_=batch[index] if batch is not None else source.frame(d['run_id'],d['track_id'],o['frame_index'])
            h,w=image.shape[:2];a,b,c,z=[int(x) for x in bbox(actual)]
            if not 0<=a<c<=w or not 0<=b<z<=h:raise ValueError('target_detection_out_of_bounds')
            other=[bbox(x) for x in self.service.source.rows('SELECT * FROM track_observations WHERE run_id=? AND frame_index=? AND track_id<>?',(d['run_id'],o['frame_index'],d['track_id']))]
            marked=image.copy()
            for bb in other:cv2.rectangle(marked,tuple(map(int,bb[:2])),tuple(map(int,bb[2:])),(160,160,160),1)
            cv2.rectangle(marked,(a,b),(c,z),(255,220,0),4)
            cv2.putText(marked,'TARGET',(a,max(20,b-8)),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,220,0),2)
            crop=image[b:z,a:c].copy();input_crop=scaled(crop)
            cv2.rectangle(input_crop,(0,0),(input_crop.shape[1]-1,input_crop.shape[0]-1),(255,220,0),3)
            scene=scaled(marked);panel=scaled(input_crop,256)
            combined=np.zeros((max(scene.shape[0],panel.shape[0]),scene.shape[1]+panel.shape[1],3),dtype=np.uint8)
            combined[:scene.shape[0],:scene.shape[1]]=scene;combined[:panel.shape[0],scene.shape[1]:]=panel
            combined=scaled(combined)
            fid=prefix+str(index).zfill(3);files={};hashes={}
            for view,pixels in [('full',image),('roi',crop),('input_full',scaled(marked)),('input_roi',input_crop),('input_combined',combined)]:
                path=folder/(fid+'_'+view+'.jpg')
                if not cv2.imwrite(str(path),pixels):raise ValueError('evidence_image_write_failed')
                files[view]=str(path);hashes[view]=hashlib.sha256(path.read_bytes()).hexdigest()
            frames.append({'frame_id':fid,'frame_index':o['frame_index'],'global_time':o['global_timestamp'],'local_time':o['local_timestamp'],
                           'width':w,'height':h,'roi_xyxy':bbox(actual),'other_person_boxes':other,'files':files,'sha256':hashes})
        return frames

    def content(self,frames):
        content=[]
        if len(frames)>2:raise ValueError('at_most_four_images_per_request')
        for f in frames:
            w,h=f['width'],f['height']
            normalized=[round(v/(w if i%2==0 else h),6) for i,v in enumerate(f['roi_xyxy'])]
            content.append({'type':'text','text':f"frame_id={f['frame_id']} global_seconds={f['global_time']:.6f} local_seconds={f['local_time']:.6f}; cyan TARGET is requested person; gray boxes are others. Full scene LEFT, TARGET crop RIGHT in the SAME evidence image. ORIGINAL frame_size={w}x{h}. target_detection_box_xyxy_normalized_0_to_1={normalized}. For spatial_anchors, independently localize the supporting action on TARGET using ORIGINAL full-frame normalized coordinates between 0 and 1, never pixel or composite-panel coordinates. The given detection box locates the requested crop only; it does not prove action ownership or identity continuity."})
            for view in ['input_combined']:
                content.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(Path(f['files'][view]).read_bytes()).decode()}})
        return content

    def run(self,request):
        if not self.lock.acquire(False):raise ValueError('actor_visual_review_busy')
        try:self.process_lock.acquire(timeout=0)
        except LockTimeout:
            self.lock.release();raise ValueError('actor_visual_review_busy_in_another_process')
        started=time.perf_counter();rid=uid();raw=[];rounds=[];all_frames=[];availability={};error=None;calls=0
        decision='error';raw_decision=None;reason='not_executed';override=[]
        try:
            d,e,run,clip=self.service.candidate(request.candidate_id)
            if not d['start']<=request.start<=request.end<=d['end']:raise ValueError('verification_window_outside_candidate')
            observations=[o for o in self.service.source.observations(d['run_id'],d['track_id']) if request.start<=o['global_timestamp']<=request.end]
            if not observations:raise ValueError('no_visible_target_observations_in_window')
            segments=[s for s in self.service.source.tracks(d['run_id']) if s['track_id']==d['track_id']][0]['segments']
            uninterrupted=any(s['global_start']<=request.start<=request.end<=s['global_end'] for s in segments)
            continuity_risks=self.service.source.continuity_risks(d['run_id'],d['track_id'],request.start,request.end)
            availability=self.reviewer.availability()
            if availability['status']!='available':raise ValueError('vlm_unavailable; no_rule_based_model_result')
            config=self.reviewer.config;deadline=started+config.max_elapsed_seconds

            def invoke(content,prompt,schema):
                nonlocal calls
                remaining=deadline-time.perf_counter()
                if calls>=config.max_calls or remaining<=0:raise TimeoutError('actor_review_budget_exhausted')
                try:
                    enabled=self.temperature_checks if self.temperature_checks is not None else os.getenv('INTICLASS_ACTOR_CHECK_TEMPERATURE','1')!='0'
                    if not enabled or self.reviewer.transport is not None:raise OSError('temperature_probe_disabled')
                    temp=float(subprocess.check_output(['nvidia-smi','--query-gpu=temperature.gpu','--format=csv,noheader,nounits'],text=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).splitlines()[0])
                    if temp>=83:raise RuntimeError('gpu_temperature_guard_aborted_before_next_call')
                except (OSError,ValueError,subprocess.SubprocessError):pass
                self.reviewer._context.timeout=min(config.timeout,remaining);calls+=1
                return self.reviewer.call(content,prompt,schema,raw)

            def review_batch(selected,prefix):
                frames=self.extract(d,selected,rid,prefix);all_frames.extend(frames);content=self.content(frames)
                observation=invoke(content,'Independently describe physical actions of cyan TARGET, separately from others. No target action label is provided. At most TWO observations, each under 150 characters; no repetition. Only cite supplied frames. No identity or mental-state inference.',ActorObservations)
                known={f['frame_id'] for f in frames}
                if any(not set(o.evidence_refs)<=known for o in observation.observed_actions):raise ValueError('unknown_observation_frame_reference')
                time_contract={f['frame_id']:[f['global_time'],f['global_time']] for f in frames}
                prompt=('Verify whether the cyan TARGET person specifically performs this target action: '+e['action']+'. '
                  'Echo target_claim exactly and target_ref="target". Distinguish executor_relation target, other, multiple, unknown, action_not_supported. '
                  'A behavior elsewhere in the scene is NOT target support. actor_role target means the cyan person. '
                  'Every supporting target fact must use subject_ref="target" and include ORIGINAL full-frame normalized spatial_anchors for each cited frame, even when view=roi. '
                  'For multiple actors, describe TARGET participation separately; do not assign every person the group action. '
                  'At most TWO support_facts, concise physical descriptions; unrelated actions do not prove the target label. '
                  'Global time pairs: '+json.dumps(time_contract)+'. Requirements: '+json.dumps(ACTION_REQUIREMENTS.get(e['action'],{}),ensure_ascii=False)+'. '
                  'Sparse absence cannot refute an interval event. Actual sleep cannot be confirmed from posture. '
                  'Reported initial observations (unverified): '+json.dumps(observation.model_dump(),ensure_ascii=False))
                judgment=invoke(content,prompt,ActorJudgment)
                refs=judgment.evidence_refs+[r for f in judgment.support_facts+judgment.counter_evidence for r in f.evidence_refs]
                if not set(refs)<=known:raise ValueError('unknown_judgment_frame_reference')
                violations=[]
                texts=[judgment.reason,*judgment.limitations,*[f.observed_action for f in judgment.support_facts],*[o.action for o in observation.observed_actions]]
                for text in texts:violations.extend(semantic_findings(text,'actor_review'))
                evidence={'requested_window':[request.start,request.end],'playable_window':[clip['start'],clip['end']],'sampling_exhaustive':False}
                outcome=decide(judgment,e['action'],evidence,frames,violations,{'frame_size':[frames[0]['width'],frames[0]['height']],
                    'input_views':['input_full','input_roi'],'observations':observation.model_dump()})
                gate=subject_gate(judgment,frames)
                if judgment.decision=='confirmed' and not gate['passed']:
                    outcome.update(decision='uncertain',final_decision='uncertain',decision_source='system_actor_evidence_alignment',safety_override=True)
                    outcome['override_reason'].append('subject_evidence_mismatch')
                if not uninterrupted:
                    outcome.update(decision='uncertain',final_decision='uncertain',decision_source='system_track_continuity',safety_override=True)
                    outcome['override_reason'].append('review_interval_crosses_tracking_gap; split_review_required')
                if (continuity_risks or 'track_quality_requires_review' in d['ambiguity_flags']) and judgment.decision=='confirmed':
                    outcome.update(decision='uncertain',final_decision='uncertain',decision_source='system_track_quality',safety_override=True)
                    outcome['override_reason'].append('possible_track_switch_not_independently_resolved')
                batch={'observations':observation.model_dump(),'judgment':judgment.model_dump(),'policy':outcome,'subject_checks':gate,'frame_ids':list(known)}
                rounds.append(batch);return batch

            initial=[observations[0]] if len(observations)==1 else [observations[0],observations[-1]]
            batch=review_batch(initial,'f')
            # One bounded extra round, targeted to actual new contiguous observations in one visible segment.
            if batch['policy']['decision']=='uncertain' and e['action'] in DYNAMIC_ACTIONS and uninterrupted and calls+2<=config.max_calls and deadline-time.perf_counter()>2:
                used={o['frame_index'] for o in initial};middle=observations[len(observations)//2]
                options=[o for o in observations if o['frame_index'] not in used and middle['global_timestamp']<=o['global_timestamp']<=middle['global_timestamp']+.5]
                if len(options)>=2:
                    a=options[0];b=min(options[1:],key=lambda o:abs(o['global_timestamp']-a['global_timestamp']-.4))
                    if .3<=b['global_timestamp']-a['global_timestamp']<=.5:batch=review_batch([a,b],'d')
            decision=batch['policy']['decision'];raw_decision=batch['judgment']['decision'];reason=batch['judgment']['reason'];override=batch['policy']['override_reason']
            if len(rounds)>1 and any(r['judgment']['executor_relation']!=batch['judgment']['executor_relation'] or r['judgment']['decision']=='rejected' for r in rounds[:-1]):
                decision='uncertain';override=override+['unresolved_actor_or_round_disagreement']
        except Exception as ex:
            error={'type':type(ex).__name__,'message':str(ex)[:1800]};reason=error['message'];decision='error'
            if rounds:
                raw_decision=rounds[-1]['judgment']['decision'];override=rounds[-1]['policy']['override_reason']+['execution_failed_after_partial_judgment']
        finally:
            self.reviewer._context.timeout=self.reviewer.config.timeout
            self.process_lock.release()
            self.lock.release()
        # Resolve candidate before persistence too; malformed requests cannot create fake event reviews.
        d,e,run,clip=self.service.candidate(request.candidate_id)
        record={'review_id':rid,'event_id':d['event_id'],'run_id':d['run_id'],'track_id':d['track_id'],'candidate_id':d['candidate_id'],
                'created_at':now(),'request':request.model_dump(),'target_action':e['action'],'source_kind':'visual_model_actor_review',
                'raw_model_decision':raw_decision,'final_decision':decision,'decision_source':'system_conservative_override' if override else ('system_error' if error else 'visual_model'),
                'override_reason':override,'reason':reason,'rounds':rounds,'frames':all_frames,
                'evidence_refs':rounds[-1]['judgment']['evidence_refs'] if rounds else [],'sampled_frame_refs':[f['frame_id'] for f in all_frames],
                'attempted_model_frame_refs':sorted({f for r in raw for f in r.get('frame_ids',[])}),'model_input_views':['input_combined'],
                'supporting_facts':rounds[-1]['judgment']['support_facts'] if rounds else [],'availability':availability,'raw_responses':raw,'error':error,
                'elapsed_seconds':time.perf_counter()-started,'api_calls':calls,'model_id':self.reviewer.config.model_id,'prompt_version':PROMPT,
                'input_images_across_calls':sum(r.get('input_images',0) for r in raw),'limitations':['Only sampled visible detections; anonymous track continuity is unverified.','Model confirmation is not human-confirmed attribution.','No student identity or cognitive state is established.']}
        with self.service.repo.connection() as c:
            c.execute('INSERT INTO event_track_reviews VALUES (?,?,?,?,?,?,?,?,?)',(rid,d['candidate_id'],d['event_id'],d['run_id'],d['track_id'],record['created_at'],raw_decision,decision,json.dumps(record)))
        return record
