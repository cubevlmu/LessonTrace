"""Frozen sampling and independent, staged human gold storage on EvidenceDB."""
import hashlib
import json
import random
import secrets
import uuid
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

import cv2

from src.gold_schema import GoldBlindReview,GoldTargetReview,GoldClaimReviews,GoldResolution,GoldClaimResolution,GoldInspection
from src.agent.review_claims import video_is_ready
from src.agent.review_claims import observable_parts

PRIORITY=['stand','walk','playphone','sleep','readbook','homework','chat','eatordrink']
DEVELOPMENT_ROWS={3,655,951,772}
DEFINITIONS={
    'stand':'站立姿态：在当前区域与时刻可辨别身体处于站立；不判断身份或学习状态。',
    'walk':'走动：短视频中可见有序位置移动及与行走相容的身体动作，不能只凭单帧站立判断。',
    'playphone':'原目标标签playphone：核验可观察的持手机、注视屏幕或手部交互；不据此认定游戏或不专注。',
    'sleep':'原目标标签sleep：真实睡眠不能仅由低头/伏桌证明；可记录head_down/伏桌等姿态，无法证实时选uncertain。',
    'readbook':'原目标标签readbook：书本/书页及相容的注视或翻阅可见；头低着本身不充分。',
    'homework':'原目标标签homework：记录书写等外显事实；没有任务语境时不能把书写自动等同于做作业。',
    'chat':'原目标标签chat：核验可观察交流证据；无声画面的朝向或手势不一定证明交谈。',
    'eatordrink':'进食/饮水：对象、口部及手部交互须可辨别；单独拿着杯子或袋子不充分。',
}


def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def build_sampling_manifest(db,video_root,seed=1729,per_class=5,window_seconds=8.,model_snapshot=None):
    from src.agent.visual_reviewer import PROMPT_VERSION
    root=Path(video_root).resolve();rng=random.Random(seed)
    events=sorted(db.events(),key=lambda e:e['event_id'])
    histories={e['event_id']:db.visual_history(e['event_id']) for e in events}
    blocked={e['video_id'] for e in events if histories[e['event_id']] or e['excel_row'] in DEVELOPMENT_ROWS}
    used_ids=set();used_assets=set();samples=[];hashes={};notes=[]
    def add(event,target,kind,split,reason):
        if event['video_id'] in used_ids and split!='development':return False
        candidates=db.candidates(event['event_id'])['candidates']
        for clip in candidates:
            if not video_is_ready(clip,root):continue
            a=max(event['t_start'],clip['start']);b=min(event['t_end'],clip['end'],clip['start']+clip['duration'])
            if b-a<1:continue
            width=(b-a) if split=='development' else min(window_seconds,b-a);start=(a+b-width)/2;end=start+width
            if kind=='unlabelled_target_window' and any(e['video_id']==event['video_id'] and e['action']==target and e['t_start']<end and e['t_end']>start for e in events):continue
            sha=hashes.setdefault(clip['clip_id'],None)
            if sha is None:sha=file_hash(clip['path']);hashes[clip['clip_id']]=sha
            if sha in used_assets and split!='development':continue
            review_states=[v['result'].get('final_decision') or v['result']['decision'] for v in histories[event['event_id']]]
            identity={'seed':seed,'event_id':event['event_id'],'clip_sha256':sha,'window':[start,end],'target':target,'split':split}
            sample={'sample_id':'g_'+digest(identity)[:24],'event_id':event['event_id'],'clip_id':clip['clip_id'],
                    'video_id':event['video_id'],'global_window':[start,end],'clip_start':clip['start'],
                    'source_path':clip['path'],'source_sha256':sha,'source_size':Path(clip['path']).stat().st_size,
                    'target_action':target,'target_definition':DEFINITIONS.get(target,'保留原类别含义；仅依据当前视频的可观察证据，不足时选uncertain。'),
                    'source_excel_row':event['excel_row'],'source_excel_action':event['action'],
                    'region_xywh':event['region_xywh'],'frame_width':event['frame_width'],'frame_height':event['frame_height'],
                    'roi_fraction':event['region_xywh'][2]*event['region_xywh'][3]/(event['frame_width']*event['frame_height']),
                    'sampling_kind':kind,'selection_reason':reason,'split':split,
                    'recording_source_group':'unknown','classroom_group':'unknown','asset_group':sha,
                    'dataset_id_contamination_block':event['video_id'],'prior_vlm_states':review_states,
                    'difficulty_human_label':'pending','occlusion_human_label':'pending','ground_truth':None,
                    'target_not_annotated_in_excel':kind=='unlabelled_target_window'}
            cap=cv2.VideoCapture(clip['path'])
            try:
                cap.set(cv2.CAP_PROP_POS_MSEC,((start+end)/2-clip['start'])*1000);ok,frame=cap.read()
                sample['sharpness_proxy']=float(cv2.Laplacian(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()) if ok else None
            finally:cap.release()
            samples.append(sample);used_ids.add(event['video_id']);used_assets.add(sha);return True
        return False
    for row in sorted(DEVELOPMENT_ROWS):
        event=next((e for e in events if e['excel_row']==row),None)
        if event and not add(event,event['action'],'historical_debug','development','Required historical development case; never independent test.'):
            notes.append(f'development row {row}: no playable clip')
    for action in PRIORITY:
        pool=[e for e in events if e['action']==action and e['video_id'] not in blocked]
        rng.shuffle(pool)
        by_area=sorted(pool,key=lambda e:e['region_xywh'][2]*e['region_xywh'][3]/(e['frame_width']*e['frame_height']))
        order=([by_area[-1],by_area[0]] if by_area else [])+pool
        count=0;seen=set()
        for event in order:
            if event['event_id'] in seen:continue
            seen.add(event['event_id'])
            if add(event,action,'dataset_candidate','independent_test','Area extremes then seeded random, distinct dataset IDs/assets; clarity requires human judgment.'):
                count+=1
            if count>=per_class:break
        if count<per_class:notes.append(f'{action}: only {count}/{per_class} playable distinct-ID/asset candidates')
    for target in PRIORITY:
        pool=[e for e in events if e['video_id'] not in blocked and e['action']!=target];rng.shuffle(pool)
        if not any(add(e,target,'unlabelled_target_window','independent_test','No overlapping Excel target label; NOT a negative until independently annotated.') for e in pool):
            notes.append(f'{target}: no eligible unlabelled-target window')
    rare={}
    for action in sorted({e['action'] for e in events}-set(PRIORITY)):
        pool=[e for e in events if e['action']==action and e['video_id'] not in blocked];rng.shuffle(pool)
        rare[action]={'events':sum(e['action']==action for e in events),'sampled':0}
        if action in {'discuss','hubbub','call','chase'}:
            rare[action]['sampled']=int(any(add(e,action,'rare_candidate','independent_test','At most one rare-class candidate; no forced quota.') for e in pool))
    payload={'protocol_version':'human-gold-v1','seed':seed,'per_priority_class':per_class,'window_seconds':window_seconds,
             'experiment_protocol':{'model_id':'qwen3-vl:4b-instruct','model_snapshot':model_snapshot,
                                    'definition_version':'original_13_labels_with_explicit_observable_limits_v1',
                                    'B1_prompt_version':'gold-single-target-v2-cesa-v1','Ours_prompt_version':PROMPT_VERSION,
                                    'B1_model_calls':1,'Ours_max_model_calls':4,'max_images_per_request':4,'timeout_seconds':120,
                                    'event_iou_threshold':.5,'B2_status':'deferred_minimal_B1_vs_Ours'},
             'samples':samples,'rare_classes':rare,'notes':notes,'data_snapshot_sha256':digest([(e['event_id'],e['action'],e['t_start'],e['t_end']) for e in events]),
             'grouping_note':'Unknown classroom/recording identity stays unknown. Dataset IDs are conservative contamination blocks, not inferred lessons. Asset SHA deduplicates exact files only.',
             'difficulty_note':'Large-area, historical model-state and sharpness proxies guide sampling; true clarity/occlusion/negative labels await humans.',
             'counts':dict(Counter(s['split'] for s in samples)),'target_counts':dict(Counter(s['target_action'] for s in samples))}
    campaign_id='gold_'+digest(payload)[:20]
    for sample in samples:
        sample['sample_id']='g_'+digest([campaign_id,sample['sample_id']])[:24];sample['campaign_id']=campaign_id
    payload['manifest_sha256']=digest(payload);payload['campaign_id']=campaign_id
    return payload


class GoldStore:
    def __init__(self,db,video_root=None):
        self.db=db;self.video_root=Path(video_root).resolve() if video_root else None;self._hash_cache={}
        with db._get_connection() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS gold_campaigns(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gold_samples(id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gold_access(id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,role TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS gold_reviews(id TEXT PRIMARY KEY,sample_id TEXT NOT NULL,annotator_id TEXT NOT NULL,stage TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gold_resolutions(id TEXT PRIMARY KEY,sample_id TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gold_experiments(id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,sample_id TEXT NOT NULL,method TEXT NOT NULL,payload TEXT NOT NULL);
            ''')

    def create_campaign(self,manifest):
        cid=manifest['campaign_id']
        content={k:v for k,v in manifest.items() if k not in {'manifest_sha256','campaign_id'}}
        if digest(content)!=manifest['manifest_sha256'] or any(s.get('ground_truth') is not None for s in manifest['samples']):raise ValueError('invalid_or_prelabelled_campaign')
        with self.db._get_connection() as c:
            existing=c.execute('SELECT payload FROM gold_campaigns WHERE id=?',(cid,)).fetchone()
            if existing:
                if json.loads(existing[0])!=manifest:raise ValueError('campaign_hash_collision')
                return None
            c.execute('INSERT INTO gold_campaigns VALUES (?,?)',(cid,json.dumps(manifest,ensure_ascii=False)))
            for sample in manifest['samples']:c.execute('INSERT INTO gold_samples VALUES (?,?,?)',(sample['sample_id'],cid,json.dumps(sample,ensure_ascii=False)))
            credentials={'campaign_id':cid,'accounts':[]}
            for name,role in [('annotator_a','annotator'),('annotator_b','annotator'),('coordinator','coordinator')]:
                token=secrets.token_urlsafe(32);identity=cid+':'+name
                c.execute('INSERT INTO gold_access VALUES (?,?,?,?)',(identity,cid,role,hashlib.sha256(token.encode()).hexdigest()))
                credentials['accounts'].append({'id':identity,'role':role,'token':token})
        return credentials

    def authenticate(self,token):
        with self.db._get_connection() as c:row=c.execute('SELECT id,campaign_id,role FROM gold_access WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None:raise PermissionError('invalid_gold_access_token')
        return dict(zip(['id','campaign_id','role'],row))

    def campaign(self,cid):
        with self.db._get_connection() as c:row=c.execute('SELECT payload FROM gold_campaigns WHERE id=?',(cid,)).fetchone()
        if row is None:raise KeyError('gold_campaign_not_found')
        return json.loads(row[0])

    def sample(self,sid,account=None):
        with self.db._get_connection() as c:row=c.execute('SELECT campaign_id,payload FROM gold_samples WHERE id=?',(sid,)).fetchone()
        if row is None:raise KeyError('gold_sample_not_found')
        if account and account['campaign_id']!=row[0]:raise PermissionError('sample_outside_campaign')
        return json.loads(row[1])

    def reviews(self,sid,stage=None):
        sql='SELECT payload FROM gold_reviews WHERE sample_id=?';params=[sid]
        if stage:sql+=' AND stage=?';params.append(stage)
        with self.db._get_connection() as c:rows=c.execute(sql+' ORDER BY created_at,rowid',params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def latest(self,sid,stage,annotator):
        return next((r for r in reversed(self.reviews(sid,stage)) if r['annotator_id']==annotator),None)

    def resolution(self,sid,kind='target'):
        with self.db._get_connection() as c:row=c.execute('SELECT payload FROM gold_resolutions WHERE sample_id=? AND kind=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(sid,kind)).fetchone()
        return json.loads(row[0]) if row else None

    def _within(self,sample,t):
        if not sample['global_window'][0]<=t<=sample['global_window'][1]:raise ValueError('annotation_time_outside_sample')

    def verify_source(self,sample):
        clip=self.db.clip(sample['clip_id']);path=Path(clip['path']).resolve()
        if self.video_root and self.video_root not in path.parents:raise ValueError('gold_source_outside_video_root')
        if not path.is_file() or clip['start']!=sample['clip_start'] or clip['video_id']!=sample['video_id']:raise ValueError('gold_source_missing_or_mapping_changed')
        stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns)
        if key not in self._hash_cache:self._hash_cache[key]=file_hash(path)
        if self._hash_cache[key]!=sample['source_sha256']:raise ValueError('gold_source_hash_changed')
        return clip

    def submit(self,sid,stage,body,account):
        sample=self.sample(sid,account)
        self.verify_source(sample)
        if account['role']!='annotator':raise PermissionError('independent_annotator_role_required')
        aid=account['id'];blind=self.latest(sid,'blind',aid);target=self.latest(sid,'target',aid)
        inspection=self.latest(sid,'inspection',aid)
        if stage=='blind':
            if target:raise ValueError('blind_stage_locked_after_target_submission')
            value=GoldBlindReview.model_validate(body).model_dump()
            for observation in value['observations']:self._within(sample,observation['evidence_time'])
        elif stage=='inspection':
            if not blind:raise ValueError('blind_observation_required_first')
            if target:raise ValueError('target_locked_use_coordinator_for_corrections')
            value=GoldInspection.model_validate(body).model_dump()
        elif stage=='target':
            if not blind:raise ValueError('blind_observation_required_first')
            if target:raise ValueError('target_locked_use_coordinator_for_corrections')
            value=GoldTargetReview.model_validate(body).model_dump()
            if value['watched_full_window'] and not (blind['data']['watched_full_window'] or (inspection and inspection.get('blind_review_id')==blind['review_id'])):raise ValueError('full_inspection_not_attested_in_blind_stage')
            if value['decision']=='rejected' and blind['data']['occlusion']!='none':raise ValueError('occluded_region_cannot_be_certified_negative')
            for interval in value['target_intervals']:
                self._within(sample,interval['start']);self._within(sample,interval['end'])
        elif stage=='claims':
            if not target:raise ValueError('submit_target_before_model_claim_audit')
            value=GoldClaimReviews.model_validate(body).model_dump();known={x['claim_id'] for x in self.claim_items(sample)}
            if any(r['claim_id'] not in known for r in value['ratings']):raise ValueError('unknown_claim_to_audit')
            if len({r['claim_id'] for r in value['ratings']})!=len(value['ratings']):raise ValueError('duplicate_claim_rating')
            for rating in value['ratings']:
                if rating['actual_time'] is not None:self._within(sample,rating['actual_time'])
        else:raise ValueError('invalid_gold_stage')
        record={'review_id':uuid.uuid4().hex,'sample_id':sid,'annotator_id':aid,'stage':stage,'created_at':now(),
                'source_kind':'independent_gold_annotation','data':value,
                'blind_review_id':blind['review_id'] if blind else None,'target_review_id':target['review_id'] if target else None}
        if stage=='inspection':record['observation_scope']='after_target_hypothesis_revealed'
        if stage=='target':record['inspection_review_id']=inspection['review_id'] if inspection and inspection.get('blind_review_id')==blind['review_id'] else None
        with self.db._get_connection() as c:c.execute('INSERT INTO gold_reviews VALUES (?,?,?,?,?,?)',(record['review_id'],sid,aid,stage,record['created_at'],json.dumps(record,ensure_ascii=False)))
        return record

    def claim_items(self,sample):
        items=[]
        for review in self.db.visual_history(sample['event_id'])[-1:]:
            if review['clip_id']!=sample['clip_id']:continue
            evidence=review.get('evidence') or {};frames={f['frame_id']:f for f in evidence.get('frames',[])}
            for i,obs in enumerate(review['result'].get('observed_actions',[])):
                refs=[frames[r] for r in obs['evidence_refs'] if r in frames and sample['global_window'][0]<=frames[r]['global_time']<=sample['global_window'][1]]
                if not refs:continue
                for f in refs:
                    for part in observable_parts(obs['action']):
                        items.append({'claim_id':'gc_'+digest([review['review_id'],i,part,f['frame_id']])[:24],
                                      'description':part,'subject_context':obs['action'],
                                      'visual_review_id':review['review_id'],'observation_index':i,
                                      'evidence':[{'global_time':f['global_time'],'local_time':f['local_time'],'frame_id':f['frame_id']}]})
        # Future real experiment records expose full source observations, not fragments.
        for run in self.experiments(sample.get('campaign_id'),sample['sample_id']):
            for item in run.get('claim_items',[]):items.append(item)
        return sorted(items,key=lambda item:digest([sample['sample_id'],item['claim_id']]))

    def blind_payload(self,sid,account):
        sample=self.sample(sid,account);aid=account['id']
        blind=self.latest(sid,'blind',aid);target=self.latest(sid,'target',aid)
        inspection=self.latest(sid,'inspection',aid)
        if inspection and (not blind or inspection.get('blind_review_id')!=blind['review_id']):inspection=None
        payload={'sample_id':sid,'split':sample['split'],'global_window':sample['global_window'],
                 'local_window':[t-sample['clip_start'] for t in sample['global_window']],
                 'region_xywh':sample['region_xywh'],'frame_width':sample['frame_width'],'frame_height':sample['frame_height'],
                 'media_url':'/api/gold/media/'+sid,'own_blind_review':blind,'own_target_review':target,'own_inspection_review':inspection,
                 'phase':'blind_observation' if not blind else ('target_judgment' if not target else 'claim_audit')}
        if blind:payload['target']={'action':sample['target_action'],'definition':sample['target_definition'].replace('原目标标签','待判类别'),'note':'Evaluation hypothesis, not the original Excel answer; could be absent.'}
        if target:payload['claim_items']=self.claim_items(sample)
        return payload

    def sample_list(self,account):
        samples=self.campaign(account['campaign_id'])['samples']
        ordered=sorted(samples,key=lambda s:digest([account['id'],s['sample_id']]))
        return [{'sample_id':s['sample_id'],'split':s['split'],
                 'blind_done':bool(self.latest(s['sample_id'],'blind',account['id'])),
                 'target_done':bool(self.latest(s['sample_id'],'target',account['id'])),
                 'received_target_count':len(self.independent_targets(s['sample_id'])) if account['role']=='coordinator' else None,
                 'adjudicated':bool(self.resolution(s['sample_id']))} for s in ordered]

    def independent_targets(self,sid):
        result={}
        for r in self.reviews(sid,'target'):result[r['annotator_id']]=r
        return list(result.values())

    def adjudicate(self,sid,body,account,kind='target'):
        sample=self.sample(sid,account)
        self.verify_source(sample)
        if account['role']!='coordinator':raise PermissionError('coordinator_role_required')
        targets=self.independent_targets(sid)
        if len(targets)<2:raise ValueError('two_independent_target_reviews_required')
        if kind=='target':
            value=GoldResolution.model_validate(body).model_dump()
            if value['method']=='agreement_confirmation' and (len({r['data']['decision'] for r in targets})!=1 or value['review']['decision']!=targets[0]['data']['decision']):raise ValueError('disagreement_requires_discussion_or_third_person')
            if value['recording_source_group'] and not value['grouping_evidence'].strip():raise ValueError('source_group_requires_external_evidence')
            for interval in value['review']['target_intervals']:
                self._within(sample,interval['start']);self._within(sample,interval['end'])
        else:
            value=GoldClaimResolution.model_validate(body).model_dump();reviews=self.reviews(sid,'claims')
            if len({r['claim_id'] for r in value['ratings']})!=len(value['ratings']):raise ValueError('duplicate_claim_resolution')
            audit_refs=set()
            for rating in value['ratings']:
                votes={r['annotator_id']:x for r in reviews for x in r['data']['ratings'] if x['claim_id']==rating['claim_id']}
                if len(votes)<2:raise ValueError('two_independent_claim_audits_required')
                fields=['correctness','unsupported','evidence_consistency','reference_accessible','timestamp_correct','actual_time']
                if value['method']=='agreement_confirmation' and any(any(v[k]!=rating[k] for k in fields) for v in votes.values()):raise ValueError('claim_disagreement_requires_discussion')
                if rating['actual_time'] is not None:self._within(sample,rating['actual_time'])
                audit_refs.update(r['review_id'] for r in reviews if any(x['claim_id']==rating['claim_id'] for x in r['data']['ratings']))
        result={'resolution_id':uuid.uuid4().hex,'sample_id':sid,'kind':kind,'created_at':now(),'coordinator_id':account['id'],
                'source_kind':'independent_gold_adjudication','annotation_refs':[r['review_id'] for r in targets],'data':value}
        if kind=='claims':result['claim_annotation_refs']=sorted(audit_refs)
        with self.db._get_connection() as c:c.execute('INSERT INTO gold_resolutions VALUES (?,?,?,?,?)',(result['resolution_id'],sid,kind,result['created_at'],json.dumps(result,ensure_ascii=False)))
        return result

    def coordinator_payload(self,sid,account):
        sample=self.sample(sid,account)
        if account['role']!='coordinator':raise PermissionError('coordinator_role_required')
        targets=self.independent_targets(sid)
        return {'sample_id':sid,'global_window':sample['global_window'],'target':{'action':sample['target_action'],'definition':sample['target_definition'].replace('原目标标签','待判类别')},
                'region_xywh':sample['region_xywh'],'frame_width':sample['frame_width'],'frame_height':sample['frame_height'],
                'blind_reviews':self.reviews(sid,'blind') if len(targets)>=2 else [],
                'target_reviews':targets if len(targets)>=2 else [],'received_target_count':len(targets),
                'inspection_reviews':self.reviews(sid,'inspection') if len(targets)>=2 else [],
                'claim_reviews':self.reviews(sid,'claims') if self.resolution(sid) else [],
                'claim_items':self.claim_items(sample) if self.resolution(sid) else [],
                'resolution':self.resolution(sid),'claim_resolution':self.resolution(sid,'claims'),
                'media_url':'/api/gold/media/'+sid,'local_window':[t-sample['clip_start'] for t in sample['global_window']]}

    def experiments(self,cid=None,sid=None):
        query='SELECT payload FROM gold_experiments WHERE 1=1';params=[]
        if cid:query+=' AND campaign_id=?';params.append(cid)
        if sid:query+=' AND sample_id=?';params.append(sid)
        with self.db._get_connection() as c:rows=c.execute(query+' ORDER BY rowid',params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_experiment(self,record):
        self.sample(record['sample_id'])
        with self.db._get_connection() as c:c.execute('INSERT INTO gold_experiments VALUES (?,?,?,?,?)',(record['run_id'],record['campaign_id'],record['sample_id'],record['method'],json.dumps(record,ensure_ascii=False,allow_nan=False)))
