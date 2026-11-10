import json
import time
from pathlib import Path
from filelock import FileLock,Timeout as FileLockTimeout
from .planning import ReviewRequest,plan
from .retrieval import retrieve
from .reasoning import reason,adjudicate,CandidateAnalysis
from .language import TextModel
from .verification import ResourceMonitor,decision
from . import reporting


class FusionWorkflow:
    def __init__(self,agent,service,text_model=None):
        from src.inference.text import text_provider
        self.agent,self.service=agent,service;self.injected_model=text_model;self.text_model=text_model or text_provider(service)
        self._managed_model=self.text_model
        reporting.migrate(service.repo)
    def ask(self,body,actor,allow_identity=False,parent_id=None):
        request=ReviewRequest.model_validate(body);started=time.perf_counter();deadline=started+request.budget_seconds
        if self.injected_model is None and self.text_model is self._managed_model:
            from src.inference.text import text_provider
            self.text_model=text_provider(self.service)
            self._managed_model=self.text_model
        from .roster_scope import resolve
        request,_=resolve(self.service,request,actor)
        if request.previous_report_id:
            with self.service.repo.connection() as c:previous=c.execute('SELECT owner,request FROM fusion_reports WHERE report_id=?',(request.previous_report_id,)).fetchone()
            if not previous or previous['owner']!=actor:raise PermissionError('conversation_report_access_denied')
            prior=ReviewRequest.model_validate_json(previous['request'])
            if prior.run_id!=request.run_id:raise ValueError('conversation_source_mismatch')
            # Inherit only structured scope; every turn retrieves current evidence again.
            request=request.model_copy(update={key:getattr(prior,key) for key in ['track_id','subject_id','roster_subject_id','lesson_id','start','end'] if getattr(request,key) is None and key not in request.model_fields_set})
            if 'document_ids' not in request.model_fields_set and prior.document_ids:
                request=request.model_copy(update={'document_ids':prior.document_ids})
        if (request.subject_id or request.roster_subject_id or request.allow_verification) and not allow_identity:raise PermissionError('review_role_required_for_identity_or_visual_tool')
        from .roster_scope import build_plan
        request,query=build_plan(self.service,request,actor)
        retrieved=self.agent.miner.retrieve_fusion(self.service,query,actor,allow_identity)
        from .conversation import history,key,cached,set_head
        conversation=history(self.service,actor,request,allow_identity)
        fingerprint=key(request,query,self.text_model,reporting.snapshot(self.service,retrieved,query),conversation)
        if not parent_id and not request.allow_verification:
            reused=cached(self.service,actor,fingerprint,allow_identity)
            if reused:
                set_head(self.service,actor,request,reused);return reused
        verify=decision(query,retrieved,request);verify.update(actual_vlm_calls=0,elapsed_seconds=0.)
        model_record={'status':'disabled','model_id':self.text_model.model};candidate=None;model_calls=0
        with ResourceMonitor(request.monitor_resources) as monitor:
            # Delegate to the existing 11C tool only for explicitly enabled,
            # unresolved and visually resolvable subject evidence.
            if verify['action']=='verify_subject':
                if request.max_model_calls<2 or deadline-time.perf_counter()<2:verify['action']='budget_deferred'
                else:
                    from src.attribution.repository import AttributionRepository
                    from src.attribution.service import AttributionService
                    from src.attribution.visual import ActorReviewer
                    from src.attribution.schemas import Verify
                    from src.attribution.transport import SubjectVLM
                    from src.agent.visual_reviewer import VLMConfig
                    from dataclasses import replace
                    remaining=deadline-time.perf_counter();reserve=1 if request.use_llm else 0
                    max_calls=min(4,request.max_model_calls-reserve)
                    if max_calls<2:verify['action']='budget_deferred'
                    else:
                        from src.inference.runtime import ModelRegistry
                        config=ModelRegistry.from_env().vlm_config(max_calls=max_calls,max_elapsed_seconds=min(120,remaining),timeout=min(120,remaining))
                        service=AttributionService(self.service.source,AttributionRepository(self.service.attribution_repo.path),self.service.identity_dir)
                        tool=ActorReviewer(service,self.service.attribution_repo.path.parent/'evidence',SubjectVLM(config),temperature_checks=False)
                        c,_,_,_=service.candidate(verify['candidate_id']);a=max(query.effective_time_range[0],c['start']);b=min(query.effective_time_range[1],c['end'])
                        start=time.perf_counter()
                        try:
                            record=tool.run(Verify(candidate_id=c['candidate_id'],start=a,end=b));verify.update(review_id=record['review_id'],actual_vlm_calls=record['api_calls'],elapsed_seconds=time.perf_counter()-start,
                                final_decision=record['final_decision'],frame_refs=record['evidence_refs']);model_calls+=record['api_calls']
                        except Exception as error:
                            verify.update(action='visual_tool_error_or_busy',error={'type':type(error).__name__,'message':str(error)[:500]},elapsed_seconds=time.perf_counter()-start)
                        retrieved=retrieve(self.service,query,actor,allow_identity)
            canonical=self.agent.critic.review_fusion(retrieved,query)
            initial_snapshot=reporting.snapshot(self.service,retrieved,query)
            relevant_facts=[f for f in canonical['facts'] if f['fact_id'] in canonical['eligible_fact_ids']]
            if request.use_llm and relevant_facts and model_calls<request.max_model_calls and deadline-time.perf_counter()>1 and not callable(getattr(self.text_model,'write',None)) and not(query.output_mode=='report' and callable(getattr(self.text_model,'compose_narrative',None))):
                lock=FileLock(str(self.service.attribution_repo.path.parent/'actor_vlm.lock'))
                try:
                    with lock.acquire(timeout=0):
                        candidate,model_record=self.text_model.generate(query,relevant_facts,min(45,deadline-time.perf_counter()));model_calls+=int(model_record.get('chat_attempted',False))
                except FileLockTimeout:model_record={'status':'busy_fallback','model_id':self.text_model.model,'chat_attempted':False}
            elif request.use_llm:model_record={'status':'budget_or_empty_fallback','model_id':self.text_model.model,'chat_attempted':False}
            if candidate is None:
                candidate=CandidateAnalysis(intent=query.intent,claims=[],outline=[])
            judged=self.agent.arbiter.arbitrate_fusion(candidate,canonical)
            if model_record['status']!='success':judged['composition']='deterministic_fallback'
        resource=monitor.summary();timings={'elapsed_seconds':time.perf_counter()-started,'model_calls':model_calls,'logical_repository_tools':len([x for x in query.tools if x!='subject_visual_review'])}
        report=reporting.assemble(request,query,retrieved,canonical,judged,model_record,verify,resource,timings)
        from src.context.product_scope import metadata
        report['product_scope']=metadata(self.service,query,actor)
        from src.validation.evidence_chain import dependencies,guard_claims
        guard_claims(self.service,report,actor)
        report['_conversation']=conversation
        # All permitted scoped sources are background, not all are promoted to primary facts.
        report['_classroom_background']=retrieved['evidence']
        report['_writer_call_budget']=min(2,request.max_model_calls-model_calls)
        from .narrative import compose
        remaining=max(0,deadline-time.perf_counter())
        with ResourceMonitor(False):
            compose(report,query,self.text_model,request.use_llm and bool(report['claims']) and model_calls<request.max_model_calls,min(120,remaining),self.service.attribution_repo.path.parent/'actor_vlm.lock')
        report.pop('_conversation',None);report.pop('_classroom_background',None);report.pop('_writer_call_budget',None)
        writer_record=report.get('narrative_generation',{});writer_calls=writer_record.get('actual_calls',int(writer_record.get('chat_attempted',False)))
        report['trace']['answer_cache']={'key':fingerprint,'hit':False,'new_model_calls':report['trace']['timings']['model_calls']+writer_calls}
        report['trace']['timings']['model_calls']+=writer_calls
        report['trace']['timings']['elapsed_seconds']=time.perf_counter()-started
        report['dependency_manifest']=dependencies(self.service,retrieved,query)
        saved=reporting.save(self.service,report,request,query,actor,initial_snapshot,parent_id)
        set_head(self.service,actor,request,saved)
        return reporting.load(self.service,saved['report_id'],actor,allow_identity)
