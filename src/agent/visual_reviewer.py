"""Two-pass, loopback-only OpenAI-compatible visual reviewer. No fake fallback."""
import base64
import ipaddress
import json
import os
import time
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from pydantic import ValidationError
from src.schema import VisualObservations,VisualJudgment,AlignedVisualJudgment,VisualReviewResult,VisualReviewRequest
from src.video_evidence import extract_evidence,EvidenceError,plan_dynamic_samples
from src.agent.visual_evidence_policy import DYNAMIC_ACTIONS,semantic_findings,temporal_coverage,decide
from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS

PROMPT_VERSION='blind-observe-then-check-v4.1-cesa-global-time'
SYSTEM=('You are an observable-video evidence reviewer. Return ONLY strict JSON matching the supplied schema. '
        'Use only supplied images. Never infer attention, emotion, cognition, learning outcomes or actual sleep from posture. '
        'Full frames and unverified fixed-region crops are paired in chronological order; regions may contain multiple people. '
        'Do not invent student identity. Rejecting a claim requires visible counterevidence; absence in sparse samples is uncertain. '
        'All observations and decisions must cite supplied frame_id values. Limit conclusions to sampled content. '
        'Be concise: at most 3 distinct observations, ONE short sentence per action (under 180 characters). '
        'Never repeat a description. Use at most 3 short limitations and a reason under 400 characters. '
        'Never equate absent observations with counterevidence. Region claims mean an action occurred somewhere during the interval. '
        'A fixed crop is not a tracked person. Actual sleep and mental states cannot be established or excluded from posture.')


@dataclass
class VLMConfig:
    base_url:str='http://127.0.0.1:1234/v1'
    model_id:str=''
    timeout:float=120
    max_tokens:int=1200
    frame_count:int=8
    max_calls:int=4
    max_elapsed_seconds:float=120
    dynamic_interval:float=.4

    @classmethod
    def from_env(cls):
        return cls(base_url=os.getenv('INTICLASS_VLM_URL','http://127.0.0.1:1234/v1'),model_id=os.getenv('INTICLASS_VLM_MODEL',''),timeout=float(os.getenv('INTICLASS_VLM_TIMEOUT','120')),frame_count=int(os.getenv('INTICLASS_VLM_FRAMES','8')),max_calls=int(os.getenv('INTICLASS_VLM_MAX_CALLS','4')),max_elapsed_seconds=float(os.getenv('INTICLASS_VLM_BUDGET_SECONDS','120')),dynamic_interval=float(os.getenv('INTICLASS_VLM_DYNAMIC_INTERVAL','0.4')))

    def validate(self):
        url=urlsplit(self.base_url)
        try:loopback=ipaddress.ip_address(url.hostname or '').is_loopback
        except ValueError:loopback=False
        if not loopback or url.scheme not in ['http','https'] or url.username or url.password or url.query or url.fragment:
            raise ValueError('Only literal loopback IP VLM endpoints are permitted; use 127.0.0.1 or [::1].')
        if not 1<=self.timeout<=600 or not 6<=self.frame_count<=12:raise ValueError('invalid_vlm_configuration')
        if not 2<=self.max_calls<=4 or not 1<=self.max_elapsed_seconds<=240 or not .3<=self.dynamic_interval<=.5:raise ValueError('invalid_evidence_budget')


class VisualReviewer:
    def __init__(self,config=None,transport=None):
        self.config=config or VLMConfig.from_env();self.config.validate();self.transport=transport
        self._context=threading.local()

    def client(self):
        return httpx.Client(timeout=getattr(self._context,'timeout',self.config.timeout),trust_env=False,follow_redirects=False,transport=self.transport)

    def availability(self):
        if not self.config.model_id:return {'status':'unavailable','reason':'未配置 INTICLASS_VLM_MODEL；未发送图像。'}
        try:
            with self.client() as client:r=client.get(self.config.base_url.rstrip('/')+'/models',timeout=min(3,self.config.timeout))
            r.raise_for_status();models=r.json().get('data') or []
            if not any(m.get('id')==self.config.model_id for m in models):return {'status':'unavailable','reason':'configured_model_not_listed'}
            return {'status':'available','reason':'Model listed; vision capability requires a successful image call.'}
        except (httpx.HTTPError,ValueError,TypeError) as e:return {'status':'unavailable','reason':type(e).__name__+': local model listing failed'}

    def content(self,evidence):
        items=[]
        views=evidence.get('input_views',['input_full','input_roi'])
        if len(views)*len(evidence['frames'])>4:raise ValueError('model_request_image_budget_exceeded')
        for f in evidence['frames']:
            geometry=f"; frame_size={evidence.get('frame_width','unknown')}x{evidence.get('frame_height','unknown')}; original_roi_xyxy={f.get('roi_xyxy',[])}"
            items.append({'type':'text','text':f"frame_id={f['frame_id']} global_seconds={f['global_time']:.6f} local_seconds={f['local_time']:.6f}; views={','.join(views)}{geometry}; unverified fixed region, not a tracked person"})
            for view in views:
                payload=base64.b64encode(Path(f['files'][view]).read_bytes()).decode()
                items.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+payload}})
        return items

    def call(self,content,prompt,schema,raw):
        frame_ids=[i['text'].split()[0].split('=')[1] for i in content if i['type']=='text']
        response_schema=schema.model_json_schema()
        def restrict_refs(node):
            if isinstance(node,dict):
                for key,value in node.items():
                    if key=='evidence_refs' and isinstance(value,dict):value['items']={'type':'string','enum':frame_ids}
                    else:restrict_refs(value)
            elif isinstance(node,list):
                for value in node:restrict_refs(value)
        restrict_refs(response_schema)
        body={'model':self.config.model_id,'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':[{'type':'text','text':prompt+' Allowed frame IDs: '+', '.join(frame_ids)+'.\nJSON schema: '+json.dumps(response_schema)},*content]}], 'temperature':0,'max_tokens':self.config.max_tokens,'response_format':{'type':'json_schema','json_schema':{'name':schema.__name__,'strict':True,'schema':response_schema}}}
        started=time.perf_counter()
        entry={'input_images':sum(i['type']=='image_url' for i in content),'frame_ids':[i['text'].split()[0].split('=')[1] for i in content if i['type']=='text'],'timeout_seconds':getattr(self._context,'timeout',self.config.timeout)}
        try:
            with self.client() as client:
                response=client.post(self.config.base_url.rstrip('/')+'/chat/completions',json=body)
            entry.update(http_status=response.status_code,body=response.text)
        except httpx.HTTPError as exc:
            entry.update(http_status=None,body=None,transport_error=type(exc).__name__)
            raise
        finally:
            entry['elapsed_seconds']=time.perf_counter()-started;raw.append(entry)
        response.raise_for_status()
        text=response.json()['choices'][0]['message']['content']
        try:return schema.model_validate_json(text)
        except ValidationError:
            # Accept the exact old contract for read compatibility, never as sufficient
            # positive evidence: CESA will reject a confirmation with missing facts.
            payload=json.loads(text)
            if schema is AlignedVisualJudgment and isinstance(payload,dict) and not {'target_claim','support_facts'}.intersection(payload):
                return VisualJudgment.model_validate(payload)
            raise

    def run(self,db,event_id,request,video_root,evidence_root):
        request=VisualReviewRequest.model_validate(request)
        event=db.event(event_id);review_id=uuid.uuid4().hex
        raw=[];evidence=None;available='not_checked';sent=[];rounds=[];violations=[]
        started=time.perf_counter();deadline=started+self.config.max_elapsed_seconds
        phase='extraction';error=None;call_count=0
        result=dict(review_id=review_id,event_id=event_id,clip_id=request.clip_id,model_id=self.config.model_id,prompt_version=PROMPT_VERSION,target_claim=event['action'],decision='error',final_decision='error',reason='',availability=available,decision_source='system_error')
        sampling={'initial':'uniform_endpoints_full_and_roi','additional':None,'trigger_reasons':[],
                  'max_calls':self.config.max_calls,'max_elapsed_seconds':self.config.max_elapsed_seconds,
                  'dynamic_interval_seconds':self.config.dynamic_interval,'actual_calls':0,'budget_exhausted':False}

        def invoke(batch,prompt,schema):
            nonlocal call_count,phase
            remaining=deadline-time.perf_counter()
            if call_count>=self.config.max_calls or remaining<=0:raise TimeoutError('evidence_budget_exhausted')
            self._context.timeout=min(self.config.timeout,remaining)
            phase='blind_observation' if schema is VisualObservations else 'target_verification'
            call_count+=1
            content=self.content(batch)
            # Sent frames are an audit of attempted requests, not proof of successful review.
            sent.extend(batch['frames'])
            value=self.call(content,prompt,schema,raw)
            known={f['frame_id'] for f in batch['frames']}
            refs=[r for a in value.observed_actions for r in a.evidence_refs] if schema is VisualObservations else value.evidence_refs+[r for c in value.counter_evidence for r in c.evidence_refs]
            if not set(refs)<=known:raise ValueError('unknown_model_evidence_ref')
            return value

        def review_batch(batch):
            observation=invoke(batch,'Independently describe only visible physical actions and changes in these chronological images. No target label is provided. Never assign identity across fixed crops.',VisualObservations)
            safe=[]
            for action in observation.observed_actions:
                findings=semantic_findings(action.action,'observation')
                violations.extend(findings)
                if not findings:safe.append(action.model_dump())
            for limitation in observation.limitations:violations.extend(semantic_findings(limitation,'observation_limitation'))
            result.setdefault('observed_actions',[]).extend(safe)
            time_contract=json.dumps({f['frame_id']:[f['global_time'],f['global_time']] for f in batch['frames']})
            judgment=invoke(batch,'Check this region-level claim that an action occurred during the event: '+event['action']+'. Echo this exact target_claim. Describe specific support_facts, not just a generic reason. Each fact must state the actual observed physical action, local subject description (not an identity), source view, actual referenced global time window, and relation to this target. global_time_window is [GLOBAL start, GLOBAL end] in seconds, NEVER [global, local]. For a single-frame fact copy the corresponding exact pair: '+time_contract+'. For multiple cited frames use [minimum cited GLOBAL seconds, maximum cited GLOBAL seconds]. Do not use clip-local seconds in this field. A claimed supports relation is not proof. Full-view facts need normalized original-frame spatial anchors inside the registered ROI; ROI facts cannot establish a tracked identity. Never merge objects/actions belonging to different people. For a local motion sequence, provide consistent local subject anchors in each cited frame; no global identity is verified. Unknown or unrelated facts cannot justify confirmed. Definition/evidence requirements: '+json.dumps(ACTION_REQUIREMENTS.get(event['action'],{}),ensure_ascii=False)+'. No verified external teaching/audio context is supplied. Concrete counter_evidence must cite frames; absence in samples is uncertain, never rejected. Actual sleep cannot be established from posture. Independent safe observations: '+json.dumps(safe,ensure_ascii=False),AlignedVisualJudgment)
            violations.extend(semantic_findings(judgment.reason,'reason'))
            for fact in judgment.support_facts:violations.extend(semantic_findings(fact.observed_action,'support_fact'))
            for fact in judgment.counter_evidence:violations.extend(semantic_findings(fact.fact,'counter_evidence'))
            for limitation in judgment.limitations:violations.extend(semantic_findings(limitation,'judgment_limitation'))
            outcome=decide(judgment,event['action'],evidence,batch['frames'],violations,
                           {'observations':observation.model_dump(),'frame_size':[event['frame_width'],event['frame_height']],
                            'input_views':batch.get('input_views',['input_full','input_roi'])})
            assessment=outcome.pop('assessment')
            if assessment:result.setdefault('evidence_sufficiency',[]).append(assessment)
            rounds.append({'frame_ids':[f['frame_id'] for f in batch['frames']],
                           'observations':observation.model_dump(),'model_judgment':judgment.model_dump(),
                           'policy_outcome':outcome.copy()})
            result.update(outcome)
            result['reason']=judgment.reason if not outcome['safety_override'] else 'System conservative decision: '+', '.join(outcome['override_reason'])+'. Current evidence is insufficient; inspect the referenced clip.'
            result['evidence_refs']=judgment.evidence_refs
            result.setdefault('limitations',[]).extend(v for v in observation.limitations+judgment.limitations if not semantic_findings(v,'limitation'))
            return judgment

        try:
            evidence=extract_evidence(db,event_id,request.clip_id,Path(evidence_root)/review_id,video_root,request.frame_count)
            evidence.update(frame_width=event['frame_width'],frame_height=event['frame_height'])
            result['limitations']=list(evidence['limitations'])
            phase='availability';health=self.availability();available=health['status']
            if available!='available':raise RuntimeError('unavailable: '+health['reason'])
            batch={**evidence,'frames':[evidence['frames'][0],evidence['frames'][-1]]}
            judgment=review_batch(batch)
            dynamic=event['action'] in DYNAMIC_ACTIONS
            if result['decision']=='uncertain':sampling['trigger_reasons'].append('insufficient_initial_evidence')
            if dynamic and judgment.decision=='rejected':sampling['trigger_reasons'].append('static_samples_cannot_refute_dynamic_interval_claim')
            # Even a first-round temporal assertion across endpoints has a large time gap.
            if dynamic and result['decision']!='confirmed':sampling['trigger_reasons'].append('dynamic_action_requires_close_temporal_sequence')
            needs_extra=bool(sampling['trigger_reasons'])
            alignment=result.get('alignment_checks') or {}
            if judgment.decision=='confirmed' and alignment and not alignment.get('passed'):
                sampling['trigger_reasons'].append('unsupported_confirmation')
                needs_extra=bool(alignment.get('additional_video_evidence_may_help'))
                sampling['targeted_evidence_goal']={'target_claim':event['action'],
                    'missing_features':sorted({feature for c in alignment.get('checks',[]) for feature in c['missing_features']}),
                    'scope':'same registered ROI, new time points; never search for a desired answer'}
                if not needs_extra:sampling['not_requested_reason']='Semantic mismatch or missing nonvisual context is not repaired by repeatedly asking the same model.'
            if event['action']=='sleep' or (violations and not dynamic):
                needs_extra=False
                sampling['not_requested_reason']='More posture samples cannot resolve an unobservable state or repair semantic overreach.'
            result['additional_evidence_requested']=needs_extra
            if needs_extra:
                remaining=deadline-time.perf_counter()
                if call_count+2>self.config.max_calls or remaining<=.1:
                    sampling['budget_exhausted']=True
                    result.setdefault('limitations',[]).append('Additional evidence deferred: insufficient remaining call/time budget.')
                else:
                    phase='additional_extraction'
                    if dynamic:
                        try:plan=plan_dynamic_samples(evidence,self.config.dynamic_interval)
                        except EvidenceError as ex:
                            if str(ex)!='insufficient_new_contiguous_frames':raise
                            sampling['additional']={'strategy':'contiguous_roi_sequence','status':'insufficient_new_contiguous_frames','global_times':[]}
                            result.setdefault('limitations',[]).append('The playable window has insufficient distinct new frames for the requested contiguous sequence.')
                            plan=None
                        if plan:
                            extra=extract_evidence(db,event_id,request.clip_id,Path(evidence_root)/review_id/'dynamic',video_root,request.frame_count,sample_times=plan['global_times'],frame_prefix='d')
                            sampling['additional']=plan
                            evidence['frames'].extend(extra['frames'])
                            extra['input_views']=['input_roi']
                        else:extra=None
                    else:
                        extra={**evidence,'frames':[evidence['frames'][len(evidence['frames'])//3],evidence['frames'][2*len(evidence['frames'])//3]]}
                        sampling['additional']={'strategy':'uniform_internal_full_and_roi','global_times':[f['global_time'] for f in extra['frames']]}
                        if 'unsupported_confirmation' in sampling['trigger_reasons']:
                            extra['input_views']=['input_roi']
                            sampling['additional']['strategy']='targeted_internal_roi_semantic_check'
                            sampling['additional']['goal']=sampling['targeted_evidence_goal']
                    if extra:review_batch(extra)
            # Conflicting rounds cannot be resolved merely by overwriting the first.
            decisions={r['policy_outcome']['decision'] for r in rounds}
            if 'confirmed' in decisions and any(r['model_judgment']['decision']=='rejected' for r in rounds):
                result.update(decision='uncertain',final_decision='uncertain',decision_source='system_evidence_policy',safety_override=True,reason='System conservative decision: unresolved disagreement between evidence batches.')
                result.setdefault('override_reason',[]).append('unresolved_batch_disagreement')
        except TimeoutError as ex:
            if str(ex)!='evidence_budget_exhausted':raise
            sampling['budget_exhausted']=True
            result.update(decision='uncertain',final_decision='uncertain',decision_source='system_budget',safety_override=True,override_reason=['evidence_budget_exhausted'],reason='Evidence budget exhausted; no further conclusion is justified.')
        except Exception as ex:
            error={'phase':phase,'type':type(ex).__name__,'message':str(ex)[:2000]}
            result.update(decision='error',final_decision='error',decision_source='system_error',reason=f"{phase}: {type(ex).__name__}: {str(ex)[:1000]}")
        finally:
            self._context.timeout=self.config.timeout
        result.update(availability=available,sampling_strategy=sampling,semantic_guardrail_violations=violations)
        sampling['actual_calls']=call_count
        if evidence:
            coverage=temporal_coverage(evidence,sent)
            result['temporal_coverage']=coverage
            result['sampled_global_times']=coverage['sampled_global_times']
            evidence['frames'].sort(key=lambda f:f['global_time'])
        result.setdefault('limitations',[]).append('Only request batch frames were submitted. No person identity, exhaustive interval observation, or cognitive state is established.')
        result['limitations']=list(dict.fromkeys(result['limitations']))
        validated=VisualReviewResult.model_validate(result).model_dump()
        record={'review_id':review_id,'event_id':event_id,'clip_id':request.clip_id,'created_at':datetime.now(timezone.utc).isoformat(),'request':request.model_dump(),'configuration':{'endpoint':self.config.base_url,'model_id':self.config.model_id,'prompt_version':PROMPT_VERSION,'timeout':self.config.timeout,'max_calls':self.config.max_calls,'max_elapsed_seconds':self.config.max_elapsed_seconds,'dynamic_interval':self.config.dynamic_interval},'result':validated,'evidence':evidence,'raw_responses':raw,'review_rounds':rounds,'error':error,'elapsed_seconds':time.perf_counter()-started,'resources':{'input_images':len(evidence['frames'])*2 if evidence else 0,'image_bytes':sum(Path(f['files'][v]).stat().st_size for f in evidence['frames'] for v in ['input_full','input_roi']) if evidence else 0,'sent_images_across_calls':sum(r.get('input_images',0) for r in raw),'gpu_memory':None,'gpu_note':'GPU usage is sampled separately during acceptance; no measurement is invented here.'}}
        db.save_visual_review(record)
        return record
