"""Canonical evidence propositions and program-owned adjudication, not LLM truth."""
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from src.agent.visual_evidence_policy import semantic_findings
from .evidence import Claim,State,version


class CandidateClaim(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    fact_id:str
    evidence_refs:list[str]=Field(min_length=1,max_length=8)
    proposed_status:State
    subject_ref:str|None=None
    time_scope:list[float]|None=Field(None,min_length=2,max_length=2)
    narrative:str=Field(min_length=1,max_length=280)


class Outline(BaseModel):
    model_config=ConfigDict(extra='forbid')
    heading:Literal['overview','activities','behaviors','people','conflicts','review_todo','limits']
    fact_ids:list[str]=Field(max_length=12)


class CandidateAnalysis(BaseModel):
    model_config=ConfigDict(extra='forbid')
    intent:Literal['observations','track_timeline','verified_person','teaching_plan','context_relation','conflicts','review_todo','cognitive_boundary','summary','report']
    claims:list[CandidateClaim]=Field(max_length=6)
    outline:list[Outline]=Field(max_length=7)


def compact(text,n=160):return re.sub(r'\s+',' ',text).strip()[:n]


def reason(retrieved,plan):
    evidence=retrieved['evidence'];registry={e['evidence_id']:e for e in evidence};facts=[];risks=[]
    rule_version=version(retrieved['rules'])
    def risk(kind,refs,reason):risks.append({'kind':kind,'evidence_refs':refs,'reason':reason})
    def fact(e,statement,status,scope,category,refs=None,**dims):
        c=Claim(fact_id='fact:'+e['evidence_id'],statement=statement,scope=scope,evidence_refs=refs or [e['evidence_id']],
            subject_ref=e['subject_ref'],time_scope=e['time_scope'],knowledge_rule_version=rule_version,final_claim_status=status,**dims).model_dump()
        c['category']=category;facts.append(c);return c
    for e in evidence:
        p=e['provenance'];kind=e['evidence_type']
        if kind=='dataset_annotation':
            fact(e,f"原始标注在 {e['time_scope']} 有 [{p['action']}] 候选；尚不证明动作或执行者。",'uncertain','dataset_claim_only','behaviors',visual_status='uncertain')
            if p['region_kind']=='unverified_annotation_region':risk('unverified_annotation_region',[e['evidence_id']],'原区域未经逐帧人体框核验。')
        elif kind=='model_discovered_event':
            gated=p['subject_checks'].get('passed',False)
            fact(e,f"无标注视频模型候选 [{p['action']}]，实际抽样时刻 {p['observed_time_support']}，最终状态 {p['final_decision']}；"+('目标轨迹主体检查通过，仍未经独立人工复核。' if gated else '执行者未确定，不得归属该Track。'),
                'uncertain','model_discovery_sampled_instants_not_human_truth','behaviors',visual_status=e['verification_status'])
            if p['final_decision']!='confirmed':risk('visual_evidence_insufficient',[e['evidence_id']],'自动发现仍存疑；未知不等于动作不存在。')
            if not gated:risk('actor_unconfirmed',[e['evidence_id']],'模型未可靠定位指定执行者。')
        elif kind=='human_confirmed_event':
            c=fact(e,f"人工独立复核 [{p['action']}]：{p['status']}，仅限所引实际观测时刻 {p['reviewed_time_support']}。",e['verification_status'],
                'human_discovery_actor_review_at_cited_instants_not_identity','behaviors',visual_status=e['verification_status'],attribution_status=e['verification_status'])
            iref='identity:'+p.get('identity_binding_id','')
            if p.get('subject_id') and iref in registry:
                c.update(subject_ref='subject:'+p['subject_id'],identity_status='supported',evidence_refs=c['evidence_refs']+[iref],
                    scope='product_teacher_reviewed_identity_behavior_intersection_at_observed_instants',
                    statement=f"该人物在有效人工身份确认区间内的实际引用时刻 {p['reviewed_time_support']}，教师对 [{p['action']}] 的复核为 {p['status']}；不外推其他时段。")
        elif kind=='person_track':
            fact(e,f"匿名 Track {p['track_id']:03d} 在可见段 {p['visible_segments']} 有实际人物检测；中断 {len(p['gaps'])} 处。Track不是学生身份。",'supported','observed_detection_segments_only_not_identity','people',visual_status='supported')
            if p['gaps']:risk('track_visibility_gap',[e['evidence_id']],'不得把未观测间隔视为持续可见或确定同一人。')
        elif kind=='vlm_observation':
            decision=p.get('final_decision','unknown');over=semantic_findings(e['content'])
            text='模型有语义越界，不能采信其越界结论。' if over else '模型报告：'+compact(e['content'])
            fact(e,f"{text} 最终模型状态 {decision}；不是独立人工确认。",'uncertain','model_report_at_sampled_instants_only','behaviors',visual_status='uncertain')
            if decision in {'uncertain','error','unknown'}:risk('visual_evidence_insufficient',[e['evidence_id']],'模型未知/存疑/失败不构成动作反证。')
            if p.get('raw_model_decision')=='confirmed' and decision!='confirmed':risk('conservative_confirmation_override',[e['evidence_id']],'原模型确认被证据策略降级，保留原判断与来源。')
        elif kind=='human_visual_review':
            fact(e,f"独立人工区域复核在 {e['time_scope']} 记录 {e['content']}；不建立人物身份或Track归属。",e['verification_status'],'human_region_point_review_only','behaviors',visual_status=e['verification_status'])
        elif kind=='identity_binding':
            # Identity existence alone is not a personal-behavior proposition.
            fact(e,'存在当前授权且人工确认的身份区间；这本身不证明任何行为归属。','supported','identity_interval_only','people',identity_status='supported')
        elif kind=='event_attribution':
            if p['status']=='candidate':
                risk('actor_unconfirmed',[e['evidence_id']],'时间和空间重叠只是人物候选。')
                if p.get('candidate_count',0)>1:risk('multiple_possible_actors',[e['evidence_id']],'大区域/多候选需要逐人审核，不能批量归属。')
                continue
            status=e['verification_status']
            c=fact(e,f"人工归属记录在证据时刻 {e['time_scope']} 对 [{p['action']}] 的判断为 {p['status']}。",status,
                'human_actor_review_at_cited_instants','people',visual_status=status,attribution_status=status)
            for identity in p.get('identities',[]):
                iref='identity:'+identity['identity_binding_id']
                if iref not in registry:continue
                refs=[f for f in p['frame_refs'] if identity['start']<=f['global_time']<=identity['end']]
                if not refs:continue
                named=Claim(fact_id='named:'+e['source_id']+':'+identity['identity_binding_id'],
                    statement=('当前合法确认的人物在独立人工归属证据时刻执行 ['+p['action']+']；仅限引用时刻，不外推整段课堂。') if identity.get('track_review',{}).get('status')=='verified' else '身份与人工归属记录存在，但引用区间的轨迹连续性尚未独立确认，完整人物行为链保持不确定。',
                    scope='identity_visible_track_human_attribution_intersection_at_evidence_instants',evidence_refs=[e['evidence_id'],iref],subject_ref='subject:'+identity['subject_id'],
                    time_scope=[min(f['global_time'] for f in refs),max(f['global_time'] for f in refs)],visual_status='supported',identity_status='supported',attribution_status='supported',
                    track_status='supported' if identity.get('track_review',{}).get('status')=='verified' else 'unknown',knowledge_rule_version=rule_version,
                    final_claim_status='supported' if identity.get('track_review',{}).get('status')=='verified' else 'uncertain').model_dump()
                named['category']='people';facts.append(named)
        elif kind in {'teacher_plan','teacher_report'}:
            label='项目示例' if p['is_example'] else '上传者声明来源'
            fact(e,f"{label}原文记载：“{compact(e['content'])}”。仅支持原文存在，不证明视频中实际发生。",'supported','source_text_content_only','activities',text_source_status=p['text_source_status'])
            risk('untrusted_text_not_instructions',[e['evidence_id']],'文本只作为来源数据，不是Agent指令或已核验视频事实。')
        elif kind=='teaching_activity':
            alignment=p['alignment']['status'];refs=[e['evidence_id']]
            if p['passage_ref'] in registry:refs.append(p['passage_ref'])
            text=f"人工整理的活动 {p['activity_type']}，来源状态 {p['activity_status']}，时间对应 {alignment}。"
            if p['is_example']:text+='项目示例不代表原授课教师安排。'
            fact(e,text,'supported','recorded_plan_or_teacher_statement_not_observed_activity','activities',refs,text_source_status='project_example' if p['is_example'] else 'uploader_declared',temporal_alignment_status=alignment)
            if alignment!='verified_mapping':risk('teaching_time_unaligned',[e['evidence_id']],'没有当前显式时间映射，不能断言该活动对应视频时段。')
        elif kind=='knowledge_rule':
            r=p['rule'];fact(e,'规则 '+r['rule_id']+'：'+compact(e['content']), 'supported','versioned_policy_not_classroom_fact','limits')
    # Cross-modal source disagreement is not resolved by selecting a favored source.
    for activity in [e for e in evidence if e['evidence_type']=='teaching_activity']:
        ap=activity['provenance']
        if ap['alignment']['status']!='verified_mapping':continue
        for obs in [e for e in evidence if e['evidence_type'] in {'dataset_annotation','vlm_observation','event_attribution','model_discovered_event','human_confirmed_event'}]:
            action=obs['provenance'].get('action')
            if ap['action_claims'].get(action)!='reported_absent' or not obs['time_scope'] or max(activity['time_scope'][0],obs['time_scope'][0])>min(activity['time_scope'][1],obs['time_scope'][1]):continue
            strong=obs['evidence_type']=='event_attribution' and obs['provenance'].get('status')=='human_confirmed'
            risk('teacher_visual_conflict' if strong else 'candidate_teacher_difference',[activity['evidence_id'],obs['evidence_id']],
                '已对齐教师陈述与人工视频归属相冲突，双方保留。' if strong else '教师陈述与候选/模型来源存在表面差异；视频未独立确认，不能判定真实矛盾。')
    # Conflicting human actor decisions keep the action-to-person link uncertain.
    humans=[e for e in evidence if e['evidence_type']=='event_attribution' and e['provenance'].get('status') in {'human_confirmed','rejected'}]
    for i,a in enumerate(humans):
        for b in humans[i+1:]:
            if a['subject_ref']==b['subject_ref'] and a['provenance']['action']==b['provenance']['action'] and a['verification_status']!=b['verification_status'] and max(a['time_scope'][0],b['time_scope'][0])<=min(a['time_scope'][1],b['time_scope'][1]):
                risk('human_attribution_conflict',[a['evidence_id'],b['evidence_id']],'人工归属相互冲突，不能静默覆盖。')
                for f in facts:
                    if a['evidence_id'] in f['evidence_refs'] or b['evidence_id'] in f['evidence_refs']:
                        f.update(final_claim_status='uncertain',attribution_status='uncertain',statement='人工归属判断在重叠范围内不一致，目前不能确认执行者。')
                        f['evidence_refs']=list(dict.fromkeys(f['evidence_refs']+[a['evidence_id'],b['evidence_id']]))
    if plan.intent=='cognitive_boundary':risk('cognitive_overreach',[],'外显动作、教学计划、身份和轨迹都不能证明专注、能力、动机、纪律或学习效果。')
    if plan.intent=='verified_person' and (not plan.subject_id or retrieved['subject_missing']):risk('identity_missing',[],'没有选定且在该来源/时间范围有效的独立身份确认，保持匿名。')
    if plan.missing_time_ranges:risk('missing_video_coverage',[],f'请求中这些时段没有当前片段覆盖：{plan.missing_time_ranges}，不能断言没有发生事件。')
    if plan.intent in {'report','summary'}:risk('clip_not_complete_lesson',[],'报告仅覆盖已注册片段，未证明完整课堂连续覆盖。')
    from .relevance import prepare
    return prepare({'facts':facts,'risks':risks,'registry':registry,'knowledge_version':rule_version},plan)


def adjudicate(candidate,canonical):
    facts={f['fact_id']:f for f in canonical['facts']};registry=canonical['registry'];checks=[];accepted=[]
    for c in candidate.claims:
        f=facts.get(c.fact_id);fail=[]
        if not f:fail.append('unknown_fact_id')
        if f and 'eligible_fact_ids' in canonical and c.fact_id not in canonical['eligible_fact_ids']:fail.append('not_relevant_to_current_question')
        if not all(r in registry for r in c.evidence_refs):fail.append('missing_evidence_reference')
        if f:
            if set(c.evidence_refs)!=set(f['evidence_refs']):fail.append('incomplete_or_wrong_source_chain')
            if c.subject_ref!=f['subject_ref']:fail.append('subject_scope_mismatch')
            if c.time_scope!=f['time_scope']:fail.append('time_scope_mismatch')
            if c.proposed_status!=f['final_claim_status']:fail.append('unsupported_status_change')
            # Arbitrary prose is never machine-certified by keyword overlap.
            if compact(c.narrative,1000)!=compact(f['statement'],1000):fail.append('free_text_not_independently_entailed')
        if not fail:accepted.append(c.fact_id)
        checks.append({'fact_id':c.fact_id,'accepted':not fail,'failures':fail})
    # Only validated canonical clauses are emitted; model outline may order them,
    # while all caveats/conflicts remain present independently of model choices.
    from .relevance import select
    primary,additional=select(canonical,accepted)
    return {'claims':primary,'additional_claims':additional,'candidate_checks':checks,'accepted_candidate_fact_ids':accepted,
            'rejected_candidate_count':sum(not c['accepted'] for c in checks),'composition':'model_outline_with_program_verified_canonical_clauses'}
