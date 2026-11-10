"""Blind physical description then classification; no Excel/event/filename access."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import cv2
import httpx
from pydantic import ValidationError
from src.attribution.visual import ActorReviewer,subject_gate,scaled
from src.attribution.transport import SubjectVLM
from src.attribution.schemas import ActorObservations
from src.agent.visual_reviewer import VLMConfig
from src.agent.visual_evidence_policy import decide,semantic_findings
from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS
from src.agent.fusion.evidence import version
from src.identity.store import uid,now
from .schemas import Classification,PROMPT_VERSION,CLASSIFICATION_PROMPT_VERSION,VERSION


def model_info():
    from src.inference.runtime import ModelRegistry
    return ModelRegistry.from_env().info()


class Observer:
    def __init__(self,source,repo,job_id,root,config,model,deadline,transport=None):
        self.source,self.repo,self.jid,self.root,self.config,self.model,self.deadline=source,repo,job_id,Path(root),config,model,deadline
        from src.inference.runtime import ModelRegistry
        cfg=ModelRegistry.from_env().vlm_config(timeout=config.timeout,max_tokens=int(os.getenv('INTICLASS_VLM_MAX_TOKENS','1600')))
        self.vlm=SubjectVLM(cfg,transport=transport)
        self.extractor=ActorReviewer(SimpleNamespace(source=source),root,self.vlm,temperature_checks=False)
        self.cache_enabled=True
        if config.processing_profile=='efficient' and hasattr(source,'enable_frame_cache'):source.enable_frame_cache()
    def invoke(self,content,prompt,schema,frames,prompt_version=None):
        import time
        prompt_version=prompt_version or PROMPT_VERSION
        if time.monotonic()>=self.deadline:raise TimeoutError('job_time_budget_exhausted')
        key=version({'model':self.model,'prompt_version':prompt_version,'schema':schema.model_json_schema(),'prompt':prompt,
            'frames':[(f['frame_id'],f['sha256']['input_combined'],f['roi_xyxy'],f['global_time']) for f in frames]})
        with self.repo.connection() as c:
            cached=c.execute('SELECT payload FROM discovery_response_cache WHERE cache_key=?',(key,)).fetchone() if self.cache_enabled else None
        if cached:return schema.model_validate_json(cached[0]),{'cache_hit':True,'cache_key':key}
        with self.repo.connection() as c:
            c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT status,attempted_calls,call_limit FROM analysis_jobs WHERE job_id=?',(self.jid,)).fetchone()
            if j['status']!='running':raise InterruptedError('job_control_requested')
            if j['attempted_calls']>=j['call_limit']:raise TimeoutError('job_call_budget_exhausted')
            c.execute('UPDATE analysis_jobs SET attempted_calls=attempted_calls+1,updated_at=? WHERE job_id=?',(now(),self.jid))
        raw=[];self.vlm._context.timeout=min(self.config.timeout,max(.1,self.deadline-time.monotonic()))
        try:
            answer=self.vlm.call(content,prompt,schema,raw);serialized=answer.model_dump_json()
            with self.repo.connection() as c:
                c.execute('INSERT INTO discovery_call_attempts VALUES (?,?,?,?,?,?)',(uid(),self.jid,key,now(),'success',json.dumps({'raw':raw,'parsed':answer.model_dump(),'model':self.model,'prompt_version':prompt_version})))
                if self.cache_enabled:c.execute('INSERT OR IGNORE INTO discovery_response_cache VALUES (?,?)',(key,serialized))
                c.execute('UPDATE analysis_jobs SET completed_calls=completed_calls+1 WHERE job_id=?',(self.jid,))
            return answer,{'cache_hit':False,'cache_key':key}
        except Exception as e:
            with self.repo.connection() as c:
                c.execute('INSERT INTO discovery_call_attempts VALUES (?,?,?,?,?,?)',(uid(),self.jid,key,now(),'error',json.dumps({'raw':raw,'error':type(e).__name__+': '+str(e),'model':self.model,'prompt_version':prompt_version})))
                c.execute('UPDATE analysis_jobs SET failed_calls=failed_calls+1 WHERE job_id=?',(self.jid,))
            raise
    def observe(self,window):
        obs={o['frame_index']:o for o in self.source.observations(window['run_id'],window['track_id'])}
        selected=[obs[i] for i in window['frame_indices']]
        frames=self.extractor.extract(window,selected,window['window_id'],'f')
        # Neutral opaque IDs; never send the video filename or any annotation.
        for f in frames:
            image=cv2.imread(f['files']['input_combined']);image=scaled(image,self.config.input_long_edge)
            cv2.imwrite(f['files']['input_combined'],image)
            from src.tracking.video import sha256
            f['sha256']['input_combined']=sha256(f['files']['input_combined'])
            # Stable, opaque IDs permit exact-evidence reuse across overlapping
            # windows. Run/track/time/ROI/image all participate; never fuzzy reuse.
            f['frame_id']='f_'+version([window['run_id'],window['track_id'],f['global_time'],f['roi_xyxy'],f['sha256']['input_combined']])[:24]
        content=[item for f in frames for item in self.extractor.content([f])]
        if sum(i['type']=='image_url' for i in content)>4:raise ValueError('four_image_request_limit')
        observation,first=self.invoke(content,'Independently describe only physical actions of cyan TARGET in chronological frames. No behavior label is supplied. Distinguish TARGET from others. At most two short observations. Write action descriptions and limitations in concise Simplified Chinese; keep schema keys in English. Describe concrete visible posture, hand movement or object interaction, with original subject and frame references. A back-facing head or partially occluded body may still be a visible person region: describe only genuinely visible orientation or posture if clear. Not seeing hands or a face does NOT mean the person is absent. Distinguish no visible target from visible target with unobservable action; explain the limitation at the sampled frame, never for unsampled intervals. Avoid vague category-only answers such as unknown. If no physical action is observable, return observed_actions=[]. Do not guess. Put genuinely missing evidence in limitations. Do not infer identity, real sleep, attention, motivation or learning outcome.',ActorObservations,frames)
        known={f['frame_id'] for f in frames}
        if any(not set(o.evidence_refs)<=known for o in observation.observed_actions):raise ValueError('unknown_blind_observation_reference')
        if self.config.processing_profile=='efficient':
            # Product behavior descriptions use this independent pass directly.
            # Strict SCB/ICAP judgments stay available in the standard verifier;
            # a physical description is never relabelled as a verified category.
            return {'observations':observation.model_dump(),'classification':None,
                    'classification_status':'on_demand','processing_profile':'efficient',
                    'proposals':[],'frames':frames,'cache':[first], 'sampling':window,
                    'unknown_or_empty':not observation.observed_actions,
                    'category_verification':'not_performed; raw physical observations retained'}
        prompt=('Discover candidate observable actions for cyan TARGET, using ONLY the supplied images and the blind observations. '
            'Return judgments=[] when no reliable category is observable. Allowed categories: '+','.join([*ACTION_REQUIREMENTS,'other_observable_action','unknown'])+'. '
            'Do NOT force a class. At most ONE judgment and ONE support fact. Cite at most TWO representative frame IDs, and provide spatial anchors only for those same cited frames, with coordinates rounded to three decimals. Keep reason under 100 characters and each limitation under 80 characters; write concise Simplified Chinese. Do not repeat or echo the schema. Each judgment must echo target_ref="target", specify executor_relation, and cite supplied frame IDs. '
            'Supporting facts require actor_role="target", subject_ref="target" and ORIGINAL full-frame normalized spatial_anchors per cited frame. '
            'Only another person performing an action is insufficient. Use uncertain for ambiguous actors. '
            'Temporal_change requires close consecutive evidence: standing and moving boxes alone do not prove walk; camera motion and tracker drift are possible. '
            'Sleep and learning states cannot be confirmed from posture. Requirements: '+json.dumps(ACTION_REQUIREMENTS,ensure_ascii=False)+
            '. Blind observations (not ground truth): '+observation.model_dump_json())
        if observation.observed_actions:
            try:
                classification,second=self.invoke(content,prompt,Classification,frames,prompt_version=CLASSIFICATION_PROMPT_VERSION)
            except (ValidationError,httpx.HTTPError) as error:
                # A failed category response cannot erase a valid independent
                # observation. The failed raw response remains in call_attempts.
                return {'observations':observation.model_dump(),'classification':None,
                        'classification_status':'error','classification_error':type(error).__name__+': '+str(error)[:1200],
                        'classification_prompt_version':CLASSIFICATION_PROMPT_VERSION,
                        'proposals':[],'frames':frames,'cache':[first,{'error':'classification_schema_validation_failed','cache_hit':False}],
                        'sampling':window,'unknown_or_empty':True}
        else:
            # Empty independent observation provides no physical action to classify.
            # Preserve its limitations; do not spend another call inventing a label.
            classification=Classification(judgments=[],limitations=['No target action was independently observed; category remains unknown.'])
            second={'skipped':True,'reason':'no_independent_target_observations','cache_hit':False}
        proposals=[];risks=self.source.continuity_risks(window['run_id'],window['track_id'],window['start'],window['end'])
        for idx,j in enumerate(classification.judgments):
            if not set(j.evidence_refs)<=known:raise ValueError('unknown_discovery_frame_reference')
            violations=[v for text in [j.reason,*j.limitations,*[o.action for o in observation.observed_actions],*[f.observed_action for f in j.support_facts]] for v in semantic_findings(text,'discovery')]
            evidence={'requested_window':[window['start'],window['end']],'playable_window':[window['start'],window['end']],'sampling_exhaustive':False}
            result=decide(j,j.target_claim,evidence,frames,violations,{'frame_size':[frames[0]['width'],frames[0]['height']],
                'input_views':['input_full','input_roi'],'observations':observation.model_dump()})
            gate=subject_gate(j,frames)
            if not gate['passed'] or risks or j.target_claim in {'unknown','other_observable_action'}:
                result.update(decision='uncertain',final_decision='uncertain',safety_override=True)
                result['override_reason']+=gate['failures']+(['trajectory_jump_or_drift_unresolved'] if risks else [])
            used=[f for f in frames if f['frame_id'] in j.evidence_refs];times=sorted(f['local_time'] for f in used)
            proposals.append({'proposal_id':version([window['window_id'],idx,j.model_dump()]),'window_id':window['window_id'],
                'run_id':window['run_id'],'track_id':window['track_id'],'segment_id':window['segment_id'],'action_category':j.target_claim,
                'local_start':min(times),'local_end':max(times),'observed_time_support':times,'raw_vlm_decision':j.decision,
                'final_decision':result['decision'],'support_facts':result['support_facts'],'evidence_refs':j.evidence_refs,'frames':used,
                'blind_observations':observation.model_dump(),
                'CESA_checks':result,'subject_checks':gate,'reason':j.reason,'model':self.model,'prompt_version':PROMPT_VERSION,'classification_prompt_version':CLASSIFICATION_PROMPT_VERSION,
                'discovery_pipeline_version':VERSION,'limitations':list(dict.fromkeys(j.limitations+classification.limitations+observation.limitations+
                    ['Only sampled instants, anonymous Track; trajectory continuity not independently reviewed.']))})
        return {'observations':observation.model_dump(),'classification':classification.model_dump(),'proposals':proposals,
            'classification_prompt_version':CLASSIFICATION_PROMPT_VERSION,
            'frames':frames,'cache':[first,second],'sampling':window,'unknown_or_empty':not proposals}
