"""Single controlled region-evidence Q&A workflow; one existing local VLM only."""
import json
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path

from src.schema import ReviewQuestionRequest,AtomicClaim,ClaimStatus
from src.agent.miner import MinerAgent
from src.agent.critic import CriticAgent
from src.agent.arbiter import ArbiterAgent
from src.agent.guardrail import GuardrailAgent
from src.agent.visual_reviewer import VisualReviewer
from src.agent.review_claims import parse_question,build_registry,analyze_bundle,video_is_ready,in_query


class EvidenceReviewAgent:
    def ask_fusion(self,body,workflow,actor,allow_identity=False,parent_id=None):
        if not self.question_lock.acquire(blocking=False):raise RuntimeError('agent_question_busy')
        try:return workflow.ask(body,actor,allow_identity,parent_id)
        finally:self.question_lock.release()

    def ask_context(self,body,service,actor):
        from src.context.analysis import ContextAnalysis
        if not self.question_lock.acquire(blocking=False):raise RuntimeError('agent_question_busy')
        try:return ContextAnalysis(self,service).ask(body,actor)
        finally:self.question_lock.release()

    def __init__(self,db,reviewer,video_root,evidence_root,visual_lock=None):
        self.db=db;self.reviewer=reviewer
        self.video_root=Path(video_root).resolve();self.evidence_root=Path(evidence_root).resolve()
        self.miner=MinerAgent(db);self.critic=CriticAgent();self.arbiter=ArbiterAgent();self.guardrail=GuardrailAgent()
        self.visual_lock=visual_lock or threading.Lock();self.question_lock=threading.Lock()

    def ask(self,request):
        if not self.question_lock.acquire(blocking=False):raise RuntimeError('agent_question_busy')
        try:return self._ask(ReviewQuestionRequest.model_validate(request))
        finally:self.question_lock.release()

    def _ask(self,request):
        started=time.perf_counter();deadline=started+request.budget_seconds
        timings={};trace={'miner':{},'analyzer':{},'critic':[],'verifier':[],'arbiter':[]}
        t=time.perf_counter();query=parse_question(request,self.db)
        retrieved=self.miner.retrieve_regions(query);bundles=retrieved['bundles']
        registry=build_registry(self.db,bundles,query,self.video_root,self.evidence_root)
        timings['miner']=time.perf_counter()-t
        trace['miner']={'query':query.model_dump(),'parser':'bounded_local_rules_no_llm_sql',
                        'parameterized_sql':True,'matched_count':retrieved['matched_count'],
                        'returned_count':len(bundles),'truncated':retrieved['truncated'],
                        'dataset_time_bounds':retrieved['dataset_time_bounds'],
                        'scope_note':'video_id is a dataset-record scope, not an inferred complete lesson.',
                        'source_mapping_note':retrieved['source_mapping_note']}
        t=time.perf_counter()
        initial_claims=[claim for bundle in bundles for claim in analyze_bundle(bundle,query,registry)]
        timings['analyzer_initial']=time.perf_counter()-t
        t=time.perf_counter()
        initial_risks={b['event']['event_id']:self.critic.review_region_evidence(b,query,registry) for b in bundles}
        timings['critic_initial']=time.perf_counter()-t
        new_records=[];auto_events=0;model_calls=0;health=None
        # Existing evidence is considered for every candidate before any new call.
        ordered=sorted(bundles,key=lambda b:(-sum(r.resolvable_by_visual_review for r in initial_risks[b['event']['event_id']]),b['event']['t_start'],b['event']['event_id']))
        t=time.perf_counter()
        for bundle in ordered:
            event=bundle['event'];eid=event['event_id'];latest=bundle['visual_reviews'][-1] if bundle['visual_reviews'] else None
            entry={'event_id':eid,'action':event['action'],'tool':'existing_evidence_first','new_model_calls':0}
            current_refs=[r for r in registry.values() if r.get('review_id')==(latest or {}).get('review_id') and r.get('kind')=='frame' and in_query(r,query)] if latest else []
            if query.intent in {'interpretation','review_todo','unsupported'}:
                entry['status']='skipped';entry['reason']='Question requires context or review triage, not additional posture images.'
            elif not request.allow_verification or query.source_kinds==['dataset_annotation']:
                entry['status']='disabled';entry['reason']='No automatic visual verification requested.'
            elif latest and not latest.get('error') and latest['result'].get('decision')!='error' and current_refs:
                entry.update(status='reused',review_id=latest['review_id'],reason='Existing valid review answers current evidence scope, including its uncertainty. Repeating the same sampling is not new evidence.')
            elif any(r['decision'] in {'confirmed','rejected'} and query.start<=r['global_time']<=query.end for r in bundle['reviews']):
                entry.update(status='reused',reason='Independent human evidence is present; retain its point scope and any conflicts.')
            elif event['action']=='sleep':
                entry.update(status='skipped',reason='Actual sleep is not resolved by mechanically adding posture frames.')
            elif auto_events>=request.max_auto_reviews or model_calls+2>request.max_model_calls or deadline-time.perf_counter()<1:
                entry.update(status='budget_exhausted',reason='Global event/call/time budget prevents another visual review.')
            else:
                clips=[c for c in bundle['videos']['candidates'] if video_is_ready(c,self.video_root) and max(c['start'],event['t_start'],query.start)<min(c['end'],c['start']+c.get('duration',0),event['t_end'],query.end)]
                if not clips:
                    entry.update(status='missing_coverage',reason='No registered playable clip covers this query intersection.')
                elif not any(r.resolvable_by_visual_review for r in initial_risks[eid]) and latest:
                    entry.update(status='skipped',reason='Remaining risk is not resolvable by repeating the same visual tool.')
                else:
                    if health is None:health=self.reviewer.availability()
                    if health['status']!='available':
                        entry.update(status='unavailable',reason=health['reason'])
                    elif not self.visual_lock.acquire(blocking=False):
                        entry.update(status='busy',reason='The shared visual tool is already running; retain uncertainty.')
                    else:
                        try:
                            remaining=deadline-time.perf_counter()
                            if remaining<1:
                                entry.update(status='budget_exhausted',reason='Global deadline reached before starting the tool.')
                            else:
                                config=replace(self.reviewer.config,max_calls=min(4,request.max_model_calls-model_calls),
                                               max_elapsed_seconds=min(self.reviewer.config.max_elapsed_seconds,remaining),
                                               timeout=min(self.reviewer.config.timeout,remaining))
                                adapter=VisualReviewer(config,transport=self.reviewer.transport)
                                auto_events+=1
                                record=adapter.run(self.db,eid,{'clip_id':clips[0]['clip_id'],'frame_count':config.frame_count},self.video_root,self.evidence_root)
                                new_records.append(record);calls=len(record['raw_responses']);model_calls+=calls
                                entry.update(status='executed',review_id=record['review_id'],clip_id=record['clip_id'],
                                             new_model_calls=calls,elapsed_seconds=record['elapsed_seconds'],
                                             result=record['result']['decision'],error=record['error'])
                        finally:self.visual_lock.release()
            trace['verifier'].append(entry)
        timings['verifier']=time.perf_counter()-t
        # Reload appended review records, never modify original annotation or review rows.
        bundles=[self.db.detail(b['event']['event_id']) for b in bundles]
        registry=build_registry(self.db,bundles,query,self.video_root,self.evidence_root)
        t=time.perf_counter();claims=[];risks=[];candidates=[]
        for bundle in bundles:
            event=bundle['event'];event_risks=self.critic.review_region_evidence(bundle,query,registry);risks.extend(event_risks)
            atomic=analyze_bundle(bundle,query,registry)
            for claim in atomic:
                adjudicated=self.arbiter.arbitrate_atomic(claim,bundle,event_risks,registry,query)
                claims.append(self.guardrail.validate_atomic(adjudicated,registry))
            target=next(c for c in claims if c.claim_id=='claim:'+event['event_id']+':dataset')
            candidates.append({'event_id':event['event_id'],'excel_row':event['excel_row'],'action':event['action'],
                               'original_time_window':[event['t_start'],event['t_end']],
                               'query_intersection':target.global_time_window,'source_kind':'dataset_annotation',
                               'status':target.verification_status.value,'claim_id':target.claim_id,
                               'source_refs':target.source_refs,'evidence_refs':target.evidence_refs,
                               'latest_vlm_decision':bundle['visual_reviews'][-1]['result']['decision'] if bundle['visual_reviews'] else None,
                               'human_review_status':bundle['reviews'][-1]['decision'] if bundle['reviews'] else 'not_independently_reviewed',
                               'risk_types':[r.risk_type for r in event_risks]})
        if query.intent=='interpretation':
            interpretation=AtomicClaim(claim_id='claim:interpretation:'+uuid.uuid4().hex,claim_type='interpretation_claim',action=request.question,
                                       global_time_window=[query.start,query.end],source_refs=[k for k,v in registry.items() if v.get('kind')=='source'],
                                       evidence_refs=[k for k,v in registry.items() if v.get('kind') in {'video','frame'} and in_query(v,query)][:4])
            claims.append(self.guardrail.validate_atomic(self.arbiter.arbitrate_atomic(interpretation,None,risks,registry,query),registry))
        timings['analyzer_arbiter_guardrail']=time.perf_counter()-t
        trace['analyzer']={'initial_atomic_claim_count':len(initial_claims),'initial_claim_ids':[c.claim_id for c in initial_claims],
                           'atomic_claim_count_after_verification':len(claims),'method':'source-preserving decomposition before risk checking, refreshed after tool use; no free-form visual generation'}
        trace['critic']=[r.model_dump() for r in risks]
        trace['arbiter']=[{'claim_id':c.claim_id,'status':c.verification_status.value,'conclusion_source':c.conclusion_source,'reason':c.adjudication_reason} for c in claims]
        coverage=self._coverage(retrieved['registered_clips'],query)
        if query.intent=='unsupported':
            for claim in claims:claim.eligible_for_report=False
        formal=[c for c in claims if c.eligible_for_report]
        todos=[c for c in candidates if c['status']!='supported' or c['risk_types']]
        if query.intent=='interpretation':
            state='unknown';answer='当前证据不能说明学生不专注、学习效果不好或正在玩游戏。手机使用、低头等外显动作不等于教学解释或认知状态；缺少任务语境、屏幕内容或独立跨模态证据，不为这类推断机械增加图片。'
        elif query.intent=='unsupported':
            state='unknown';answer='本版本尚不能从当前行为证据回答这个问题。可检索事件概览、指定动作和人工待办；没有据此生成教师讲授内容或其他无来源答案。'
        elif not candidates:
            state='unknown';answer='当前查询没有检索到可用候选记录。这表示数据或覆盖不足，不能据此断言课堂没有发生相关事件。'
        elif query.intent=='action_check':
            targets=[c for c in claims if c.claim_type=='dataset_claim']
            supported=any(c.verification_status==ClaimStatus.SUPPORTED for c in targets)
            state='supported' if supported else ('uncertain' if any(c.evidence_refs for c in targets) else 'unknown')
            answer=('现有绑定证据支持区域内出现过所查询的外显动作；不证明同一学生或全时段持续。' if supported else '现有证据仍不充分，保留 uncertain / unknown。不能根据Excel标签确认动作，也不能因离散帧未观察到就否定整个区间发生过动作。')
        elif query.intent=='review_todo':
            state='uncertain' if todos else 'unknown';answer=f'当前显示的{len(candidates)}条候选中，{len(todos)}条需要教师进一步查看。待办依据未核验标签、来源冲突、主体歧义或缺失证据生成；不等于已判定标签错误。'
        else:
            state='uncertain' if any(c['status']!='supported' for c in candidates) else 'supported'
            answer=f"检索到{retrieved['matched_count']}条数据集候选，本次显示{len(candidates)}条；形成{len(formal)}条带可回放引用的外显观察。原始标签与独立观察分开展示，候选事件不自动视为已证实行为。"
        if coverage['gaps']:answer+=' 部分查询时段没有可用片段覆盖，不能据此推断行为不存在。'
        if retrieved['truncated']:answer+=' 检索已达单次条数上限，请缩小时间范围查看其余候选。'
        answer+=' 视频编号仅表示数据集记录范围，不代表一节完整课堂。'
        usage={'new_visual_reviews':auto_events,'new_model_calls':model_calls,
               'new_input_images':sum(r.get('input_images',0) for record in new_records for r in record['raw_responses']),
               'model_seconds':sum(r.get('elapsed_seconds',0) for record in new_records for r in record['raw_responses']),
               'external_api_charge':0,'local_energy_cost':None,'cost_note':'Local inference; electricity and hardware costs were not measured.'}
        tokens={'prompt_tokens':0,'completion_tokens':0}
        for record in new_records:
            for raw in record['raw_responses']:
                try:
                    measured=json.loads(raw.get('body') or '{}').get('usage',{})
                    for k in tokens:tokens[k]+=measured.get(k,0)
                except (ValueError,TypeError):pass
        usage['reported_token_usage']=tokens
        result={'review_id':uuid.uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),
                'question':request.question,'query':query.model_dump(),'status':state,'answer':answer,
                'candidate_events':candidates,'claims':[c.model_dump(mode='json') for c in claims],
                'formal_claims':[c.model_dump(mode='json') for c in formal],
                'needs_confirmation':todos,'unresolved_claims':[c.model_dump(mode='json') for c in claims if c.verification_status in {ClaimStatus.UNCERTAIN,ClaimStatus.UNKNOWN}],
                'references':registry,'coverage':coverage,'trace':trace,'timings_seconds':timings,
                'usage':usage,'budget':{'max_auto_reviews':request.max_auto_reviews,'max_model_calls':request.max_model_calls,'seconds':request.budget_seconds},
                'elapsed_seconds':time.perf_counter()-started,
                'limitations':['No independent teacher gold standard; no accuracy metric.','Visual observations remain model/human attributed; no psychological inference.']}
        self.db.save_agent_review(result)
        return result

    def _coverage(self,clips,query):
        intervals=[]
        for clip in clips:
            if not video_is_ready(clip,self.video_root):continue
            a=max(query.start,clip['start']);b=min(query.end,clip['end'],clip['start']+clip.get('duration',0))
            if a<b:intervals.append([a,b])
        merged=[]
        for a,b in sorted(intervals):
            if merged and a<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],b)
            else:merged.append([a,b])
        gaps=[];cursor=query.start
        for a,b in merged:
            if a>cursor:gaps.append([cursor,a])
            cursor=max(cursor,b)
        if cursor<query.end:gaps.append([cursor,query.end])
        return {'available_clip_time_ranges':merged,'gaps':gaps,
                'note':'Union of separately registered clip time mappings, not stitched video or verified continuous lesson coverage.'}
