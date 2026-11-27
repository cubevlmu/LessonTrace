import json
import time
from filelock import FileLock,Timeout as FileLockTimeout
from .planning import ReviewRequest,make_scope_key
from .reasoning import CandidateAnalysis
from .verification import ResourceMonitor,decision
from .coverage import window_coverage
from . import lesson_evidence, reporting, structured


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
        if request.run_id:
            from src.behavior_discovery.ingest import authorize_run
            authorize_run(self.service,request.run_id,actor,'teacher' if allow_identity else 'viewer')
        if (request.subject_id or request.roster_subject_id or request.allow_verification) and not allow_identity:
            raise PermissionError('review_role_required_for_identity_or_visual_tool')
        request,scoped=resolve(self.service,request,actor,allow_clarification=allow_identity)
        if isinstance(scoped,dict):
            if not allow_identity:raise PermissionError('review_role_required_for_identity_or_visual_tool')
            if request.lesson_id:lesson_evidence.index_for(self.service,request,actor)
            return {'status':'clarification_required', 'scope_key':make_scope_key(request.lesson_id,request.run_id),
                    'answer':'班内有多个匹配的学生，请选择一位后再继续。',
                    'claims':[], 'evidence':[], 'narrative':[], 'statements':[], **scoped}
        if request.previous_report_id:
            with self.service.repo.connection() as c:previous=c.execute('SELECT owner,request FROM fusion_reports WHERE report_id=?',(request.previous_report_id,)).fetchone()
            if not previous or previous['owner']!=actor:raise PermissionError('conversation_report_access_denied')
            prior=ReviewRequest.model_validate_json(previous['request'])
            if make_scope_key(prior.lesson_id,prior.run_id)!=make_scope_key(request.lesson_id,request.run_id):
                raise ValueError('conversation_source_mismatch')
            # Inherit only structured scope; every turn retrieves current evidence again.
            same_run = prior.run_id == request.run_id
            same_person = not (request.roster_subject_id or request.subject_id) or (
                request.roster_subject_id,request.subject_id)==(prior.roster_subject_id,prior.subject_id)
            keys = (['roster_subject_id','subject_id'] if same_person else []) + (
                ['track_id','start','end'] if same_person and same_run else [])
            request=request.model_copy(update={key:getattr(prior,key) for key in keys
                if getattr(request,key) is None and key not in request.model_fields_set})
            if same_person and 'document_ids' not in request.model_fields_set and prior.document_ids:
                request=request.model_copy(update={'document_ids':prior.document_ids})
        if (request.subject_id or request.roster_subject_id or request.allow_verification) and not allow_identity:raise PermissionError('review_role_required_for_identity_or_visual_tool')
        from .roster_scope import build_plan
        index=lesson_evidence.index_for(self.service,request,actor)
        has_work=lesson_evidence.has_assessment(index,request)
        request,query=build_plan(self.service,request,actor,has_assessment_evidence=has_work,
                                 resolved=None if request.previous_report_id else (request,scoped))
        if index is not None and index['student_id']!=(query.roster_subject_id or query.subject_id):
            index=lesson_evidence.index_for(self.service,query,actor)
        def read_evidence():
            return reporting.read_evidence(self.service,query,actor,allow_identity,lesson_graph=bool(index),
                                           read_video=self.agent.miner.retrieve_fusion, index=index)
        max_model_calls=min(2,request.max_model_calls) if index is not None else request.max_model_calls
        coverage=window_coverage(self.service,query,actor)
        coverage=coverage.model_dump() if coverage is not None else None
        generate_text=request.use_llm and query.intent!='cognitive_boundary'
        retrieved=read_evidence()
        from .conversation import history,key,cached,set_head
        conversation=history(self.service,actor,request,allow_identity)
        events=structured.change_events(self.service,request,actor) if index is not None else []
        initial_snapshot=reporting.snapshot(self.service,retrieved,query)
        fingerprint=key(request,query,self.text_model,initial_snapshot,conversation,
                        {'coverage':coverage,'events':events} if index is not None else coverage)
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
                if max_model_calls<2 or deadline-time.perf_counter()<2:verify['action']='budget_deferred'
                else:
                    from src.attribution.repository import AttributionRepository
                    from src.attribution.service import AttributionService
                    from src.attribution.visual import ActorReviewer
                    from src.attribution.schemas import Verify
                    from src.attribution.transport import SubjectVLM
                    remaining=deadline-time.perf_counter();reserve=1 if request.use_llm else 0
                    max_calls=min(4,max_model_calls-reserve)
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
                        retrieved=read_evidence()
                        initial_snapshot=reporting.snapshot(self.service,retrieved,query)
            canonical=self.agent.critic.review_fusion(retrieved,query)
            relevant_facts=[f for f in canonical['facts'] if f['fact_id'] in canonical['eligible_fact_ids']]
            if index is None and generate_text and relevant_facts and model_calls<request.max_model_calls and deadline-time.perf_counter()>1 and not callable(getattr(self.text_model,'write',None)) and not(query.output_mode=='report' and callable(getattr(self.text_model,'compose_narrative',None))):
                lock=FileLock(str(self.service.attribution_repo.path.parent/'actor_vlm.lock'))
                try:
                    with lock.acquire(timeout=0):
                        candidate,model_record=self.text_model.generate(query,relevant_facts,min(45,deadline-time.perf_counter()));model_calls+=int(model_record.get('chat_attempted',False))
                except FileLockTimeout:model_record={'status':'busy_fallback','model_id':self.text_model.model,'chat_attempted':False}
            elif generate_text:model_record={'status':'budget_or_empty_fallback','model_id':self.text_model.model,'chat_attempted':False}
            if candidate is None:
                candidate=CandidateAnalysis(intent=query.intent,claims=[],outline=[])
            judged=self.agent.arbiter.arbitrate_fusion(candidate,canonical)
            if model_record['status']!='success':judged['composition']='deterministic_fallback'
        resource=monitor.summary();timings={'elapsed_seconds':time.perf_counter()-started,'model_calls':model_calls,'logical_repository_tools':len([x for x in query.tools if x!='subject_visual_review'])}
        report=reporting.assemble(request,query,retrieved,canonical,judged,model_record,verify,resource,timings)
        report['coverage']=coverage
        from src.context.product_scope import metadata
        report['product_scope']=metadata(self.service,query,actor)
        from src.validation.evidence_chain import guard_claims
        guard_claims(self.service,report,actor)
        remaining=max(0,deadline-time.perf_counter())
        lock_path=self.service.attribution_repo.path.parent/'actor_vlm.lock'
        if index is not None:
            def reread():
                return reporting.read_evidence(self.service,query,actor,allow_identity,lesson_graph=True)
            structured.compose(report,query,request,retrieved,self.text_model,conversation,events,reread,
                               max(0,max_model_calls-model_calls),min(120,remaining),lock_path)
        else:
            # Keep the run-only protocol and its older provider schema intact.
            report['_conversation']=conversation
            report['_classroom_background']=retrieved['evidence']
            report['_writer_call_budget']=min(2,max_model_calls-model_calls)
            from .narrative import compose
            compose(report,query,self.text_model,generate_text and bool(report['claims']) and model_calls<max_model_calls,
                    min(120,remaining),lock_path)
            report.pop('_conversation',None);report.pop('_classroom_background',None);report.pop('_writer_call_budget',None)
        writer_record=report.get('narrative_generation',{});writer_calls=writer_record.get('actual_calls',int(writer_record.get('chat_attempted',False)))
        report['trace']['answer_cache']={'key':fingerprint,'hit':False,'new_model_calls':report['trace']['timings']['model_calls']+writer_calls}
        report['trace']['timings']['model_calls']+=writer_calls
        report['trace']['timings']['elapsed_seconds']=time.perf_counter()-started
        report['dependency_manifest']=reporting.manifest(self.service,retrieved,query)
        saved=reporting.save(self.service,report,request,query,actor,initial_snapshot,parent_id,
                             allow_identity=allow_identity)
        set_head(self.service,actor,request,saved)
        return reporting.load(self.service,saved['report_id'],actor,allow_identity)
