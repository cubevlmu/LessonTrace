"""Explicit, post-gold-only B1/Ours runner using the existing local VLM tools."""
import json
import random
import subprocess
import threading
import time
import uuid
import hashlib
from dataclasses import replace
from pathlib import Path

from src.agent_evaluation import gold_state
from src.agent.visual_reviewer import VisualReviewer,VLMConfig,PROMPT_VERSION
from src.agent.visual_evidence_policy import semantic_findings,decide
from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS
from src.schema import VisualJudgment,VisualObservation
from pydantic import Field
from src.video_evidence import extract_evidence
from src.human_gold import digest
from src.agent.review_claims import observable_parts


class GoldSinglePass(VisualJudgment):
    observed_actions:list[VisualObservation]=Field(default_factory=list)


class SampleEvidenceView:
    """Adapt a frozen evaluation hypothesis; never mutate source annotations."""
    def __init__(self,db,sample):self.db=db;self.sample=sample;self.record=None
    def event(self,eid):
        if eid!=self.sample['sample_id']:raise KeyError('evaluation_sample_mismatch')
        s=self.sample
        return {'event_id':eid,'video_id':s['video_id'],'action':s['target_action'],
                't_start':s['global_window'][0],'t_end':s['global_window'][1],
                'region_xywh':s['region_xywh'],'frame_width':s['frame_width'],'frame_height':s['frame_height'],
                'source_kind':'evaluation_hypothesis_not_dataset_truth'}
    def clip(self,cid):return self.db.clip(cid)
    def save_visual_review(self,record):self.record=record


def gpu_monitor(stop,samples):
    while not stop.is_set():
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=memory.used,temperature.gpu,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode==0:
                values=[float(v.strip()) for v in result.stdout.strip().splitlines()[0].split(',')]
                samples.append({'elapsed_clock':time.monotonic(),'memory_mib':values[0],'temperature_c':values[1],'gpu_utilization_percent':values[2]})
                if values[1]>=86:
                    # This runner owns this local model during the batch; stop its generation on overheating.
                    subprocess.run(['ollama','stop','qwen3-vl:4b-instruct'],capture_output=True,timeout=10,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    return
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
        stop.wait(1)


def gpu_values(samples,key):
    return [s[key] for s in samples if isinstance(s,dict) and key in s]+([s for s in samples if isinstance(s,(int,float))] if key=='memory_mib' else [])


def install_request_audit(reviewer,gpu,deadline=None):
    original=reviewer.call
    def guarded(content,prompt,schema,raw):
        cooling_started=time.perf_counter()
        while gpu_values(gpu,'temperature_c') and gpu_values(gpu,'temperature_c')[-1]>65:
            if max(gpu_values(gpu,'temperature_c'))>=86:raise RuntimeError('batch_safety_gpu_temperature_86C')
            if deadline is not None and time.perf_counter()>=deadline:raise TimeoutError('evidence_budget_exhausted')
            if time.perf_counter()-cooling_started>110:raise RuntimeError('batch_safety_cooldown_timeout')
            time.sleep(1)
        cooldown=time.perf_counter()-cooling_started
        temperatures=gpu_values(gpu,'temperature_c')
        if temperatures and max(temperatures)>=86:raise RuntimeError('batch_safety_gpu_temperature_86C')
        if deadline is not None:
            remaining=deadline-time.perf_counter()
            if remaining<=0:raise TimeoutError('evidence_budget_exhausted')
            reviewer._context.timeout=min(reviewer.config.timeout,remaining)
        previous=len(raw)
        try:return original(content,prompt,schema,raw)
        finally:
            if len(raw)>previous:
                raw[-1]['request_audit']={'prompt_text':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                                         'frame_text':[i['text'] for i in content if i['type']=='text'],
                                         'image_count':sum(i['type']=='image_url' for i in content),
                                         'note':'Image data and filenames are not retained in this textual request audit; no human answer is supplied.'}
                raw[-1]['cooldown_before_seconds']=cooldown
    reviewer.call=guarded


def safety_failure(raw,error,gpu):
    if gpu_values(gpu,'temperature_c') and max(gpu_values(gpu,'temperature_c'))>=86:return 'gpu_temperature_reached_86C'
    if any(r.get('transport_error') or r.get('http_status') not in {None,200} for r in raw):return 'model_transport_or_service_failure'
    if error and 'batch_safety_' in str(error):return 'gpu_safety_guard'
    return None


def run_baselines(store,manifest,video_root,evidence_root,methods=('B1','Ours'),max_samples=None,gold_mode='dual_annotator',experiment_id=None):
    state=gold_state(store,manifest,gold_mode)
    if not state['complete']:raise ValueError('real_experiments_blocked_until_annotator_a_reference_valid' if gold_mode=='single_annotator' else 'real_experiments_blocked_until_two_humans_and_coordinator_complete_independent_gold')
    if any(m not in {'B0','B1','Ours'} for m in methods):raise ValueError('B2_deferred_use_B1_and_Ours')
    protocol=manifest['experiment_protocol'];config=VLMConfig.from_env()
    if config.model_id!=protocol['model_id'] or PROMPT_VERSION!=protocol['Ours_prompt_version']:raise ValueError('model_or_prompt_version_differs_from_frozen_protocol')
    config.validate();reviewer=VisualReviewer(config)
    if reviewer.availability()['status']!='available':raise ValueError('frozen_local_model_unavailable')
    with reviewer.client() as client:
        model=next((m for m in client.get(config.base_url.removesuffix('/v1')+'/api/tags').json().get('models',[]) if m.get('name')==config.model_id),None)
    expected=(protocol.get('model_snapshot') or {}).get('digest')
    if not expected or not model or model['digest']!=expected:raise ValueError('model_digest_not_frozen_or_mismatched')
    samples=state['eligible_samples'][:max_samples] if max_samples else state['eligible_samples'];runs=[]
    completed={(r['sample_id'],r['method']) for r in store.experiments(manifest['campaign_id'])
               if r.get('manifest_sha256')==manifest['manifest_sha256'] and r.get('gold_snapshot_sha256')==state['gold_snapshot_sha256'] and r.get('experiment_id')==experiment_id}
    rng=random.Random(manifest['seed'])
    for sample in samples:
        store.verify_source(sample);order=list(methods);rng.shuffle(order)
        for method in order:
            if (sample['sample_id'],method) in completed:continue
            run_id=uuid.uuid4().hex;started=time.perf_counter();raw=[];record=None;error=None;gpu=[]
            stop=threading.Event();worker=threading.Thread(target=gpu_monitor,args=(stop,gpu),daemon=True);worker.start()
            try:
                if getattr(reviewer,'transport',None) is None:
                    monitor_deadline=time.monotonic()+3
                    while not gpu and worker.is_alive() and time.monotonic()<monitor_deadline:time.sleep(.05)
                view=SampleEvidenceView(store.db,sample)
                if method=='B0':
                    decision='confirmed' if sample['source_excel_action']==sample['target_action'] else 'uncertain'
                    raw_decision=decision;frames=[];prompt_version='dataset-candidate-rule-v1'
                elif method=='Ours':
                    adapter=VisualReviewer(replace(config,max_calls=4,max_elapsed_seconds=120,timeout=120))
                    install_request_audit(adapter,gpu,started+120)
                    record=adapter.run(view,sample['sample_id'],{'clip_id':sample['clip_id'],'frame_count':8},video_root,Path(evidence_root)/run_id)
                    raw=record['raw_responses'];decision=record['result']['decision'];raw_decision=record['result']['raw_model_decision'];frames=(record.get('evidence') or {}).get('frames',[]);error=record['error'];prompt_version=PROMPT_VERSION
                else:
                    evidence=extract_evidence(view,sample['sample_id'],sample['clip_id'],Path(evidence_root)/run_id,video_root,8)
                    evidence.update(frame_width=sample['frame_width'],frame_height=sample['frame_height'])
                    batch={**evidence,'frames':[evidence['frames'][0],evidence['frames'][-1]]}
                    single_reviewer=VisualReviewer(config);install_request_audit(single_reviewer,gpu,started+120)
                    single=single_reviewer.call(single_reviewer.content(batch),'Describe visible physical actions with their subjects, then evaluate this region-level target hypothesis, not a provided answer: '+sample['target_action']+'. Echo target_claim and provide concrete support_facts with source view, actual global times, local subject scope, cited frames and relation_to_target. Full-view facts require normalized original-frame anchors within the registered ROI. Do not merge people. Definition: '+json.dumps(ACTION_REQUIREMENTS.get(sample['target_action'],{}),ensure_ascii=False)+'. No verified teaching/audio context is supplied. Cite supplied frames. Missing observations are insufficient; posture does not prove mental states. No additional images will be requested.',GoldSinglePass,raw)
                    judgment=VisualJudgment.model_validate(single.model_dump(exclude={'observed_actions'}))
                    refs={f['frame_id'] for f in batch['frames']}
                    all_refs=judgment.evidence_refs+[r for a in single.observed_actions for r in a.evidence_refs]+[r for c in judgment.counter_evidence for r in c.evidence_refs]
                    if not set(all_refs)<=refs:raise ValueError('unknown_B1_evidence_ref')
                    violations=[v for text in [judgment.reason,*judgment.limitations,*[a.action for a in single.observed_actions],*[c.fact for c in judgment.counter_evidence]] for v in semantic_findings(text)]
                    outcome=decide(judgment,sample['target_action'],evidence,batch['frames'],violations,
                                   {'frame_size':[sample['frame_width'],sample['frame_height']],
                                    'observations':{'observed_actions':[a.model_dump() for a in single.observed_actions],'limitations':[]}})
                    decision=outcome['decision'];raw_decision=judgment.decision;frames=batch['frames'];prompt_version=protocol['B1_prompt_version']
                    record={'result':{**outcome,'observed_actions':[a.model_dump() for a in single.observed_actions]},'raw_responses':raw,'evidence':evidence,'original_judgment':judgment.model_dump()}
            except Exception as exc:
                decision='error';raw_decision=None;frames=[];prompt_version=protocol['B1_prompt_version'] if method=='B1' else PROMPT_VERSION
                error={'type':type(exc).__name__,'message':str(exc)}
            finally:stop.set();worker.join(timeout=4)
            sent_ids={ref for response in raw for ref in response.get('frame_ids',[])}
            frames=[f for f in frames if f['frame_id'] in sent_ids]
            temperatures=gpu_values(gpu,'temperature_c');memory=gpu_values(gpu,'memory_mib')
            stopped=safety_failure(raw,error,gpu)
            if stopped:
                decision='error';error=error or {'type':'BatchSafetyStop','message':stopped}
            claim_items=[]
            for i,obs in enumerate((record or {}).get('result',{}).get('observed_actions',[])):
                for f in frames:
                    if f['frame_id'] not in obs['evidence_refs']:continue
                    for part in observable_parts(obs['action']):
                        claim_items.append({'claim_id':'gc_'+digest([run_id,i,part,f['frame_id']])[:24],
                                            'description':part,'subject_context':obs['action'],
                                            'evidence':[{'global_time':f['global_time'],'local_time':f['local_time'],'frame_id':f['frame_id']}]})
            run={**state['metadata'],'experiment_id':experiment_id,'run_id':run_id,'campaign_id':manifest['campaign_id'],'sample_id':sample['sample_id'],'method':method,
                 'manifest_sha256':manifest['manifest_sha256'],'gold_snapshot_sha256':state['gold_snapshot_sha256'],
                 'execution_kind':'dataset_candidate_baseline' if method=='B0' else 'real_local_vlm',
                 'model_id':config.model_id,'model_digest':model['digest'],'prompt_version':prompt_version,
                 'definition_version':protocol['definition_version'],'decision':decision,'raw_model_decision':raw_decision,
                 'model_calls':len(raw),'elapsed_seconds':time.perf_counter()-started,'gpu_peak_mib':max(memory) if memory else None,
                 'gpu_temperature_peak_c':max(temperatures) if temperatures else None,'gpu_telemetry':gpu,'batch_safety_stop':stopped,
                 'additional_calls':max(0,len(raw)-2) if method=='Ours' else 0,
                 'additional_seconds':sum(r['elapsed_seconds'] for r in raw[2:]) if method=='Ours' else 0,
                 'input_images':[r['input_images'] for r in raw],'sampled_frames':[{'frame_id':f['frame_id'],'global_time':f['global_time']} for f in frames],
                 'visual_record':record,'raw_responses':raw,'error':error,'claim_items':claim_items,
                 'scope':'fixed_sample_hypothesis_not_a_production_fact'}
            store.save_experiment(run);runs.append(run)
            print(json.dumps({'sample_id':sample['sample_id'],'method':method,'decision':decision,'calls':len(raw),
                              'elapsed_seconds':round(run['elapsed_seconds'],2),'gpu_peak_mib':run['gpu_peak_mib'],
                              'temperature_peak_c':run['gpu_temperature_peak_c'],'error':error},ensure_ascii=True),flush=True)
            if stopped:raise RuntimeError('Batch stopped safely after saving failed attempt: '+stopped)
    return runs
