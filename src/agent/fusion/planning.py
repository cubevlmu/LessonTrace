import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

Intent=Literal['observations','track_timeline','verified_person','teaching_plan','context_relation','conflicts','review_todo','cognitive_boundary','summary','report']
TYPES=['dataset_annotation','vlm_observation','human_visual_review','person_track','identity_binding','event_attribution','model_discovered_event','human_confirmed_event','teacher_plan','teacher_report','teaching_activity','knowledge_rule']


class ReviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    question:str=Field(min_length=1,max_length=1200)
    run_id:str
    lesson_id:str|None=None
    track_id:int|None=Field(None,ge=1)
    subject_id:str|None=None
    roster_subject_id:str|None=None
    previous_report_id:str|None=None
    conversation:bool=False
    report_template:Literal['lesson','students','student']|None=None
    report_context:str=Field('',max_length=16000)
    document_ids:list[str]=Field(default_factory=list,max_length=10)
    material_mode:Literal['linked','selected','none']='linked'
    use_classroom_memory:bool=True
    start:float|None=None
    end:float|None=None
    use_llm:bool=True
    allow_verification:bool=False
    monitor_temperature:bool=False  # Legacy request field accepted, never probes temperature.
    monitor_resources:bool=True
    max_model_calls:int=Field(3,ge=0,le=5)
    budget_seconds:float=Field(60,ge=1,le=180)


class QueryPlan(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    user_question:str
    intent:Intent
    query_object:str
    run_id:str
    clip_id:str
    lesson_id:str|None=None
    document_ids:list[str]=Field(default_factory=list,max_length=10)
    track_id:int|None=None
    subject_id:str|None=None
    roster_subject_id:str|None=None
    profile_intervals:list[dict]=Field(default_factory=list)
    material_mode:Literal['linked','selected','none']='linked'
    use_classroom_memory:bool=True
    requested_time_range:list[float]
    effective_time_range:list[float]|None
    missing_time_ranges:list[list[float]]
    evidence_types:list[str]
    required_relationships:list[str]
    requested_actions:list[str]=Field(default_factory=list)
    output_mode:Literal['qa','report']='qa'
    report_template:Literal['lesson','students','student']|None=None
    report_context:str=''
    focus:Literal['ordinary','behavior_definition','identity_basis','alignment_audit','source_inventory']='ordinary'
    parsing_status:Literal['recognized','limited']='recognized'
    clarification:str|None=None
    missing_evidence_policy:Literal['retain_unknown_no_scope_invention']='retain_unknown_no_scope_invention'
    allow_verification:bool=False
    tools:list[Literal['registered_video','track_repository','identity_reader','attribution_repository','context_repository','knowledge_rules','subject_visual_review']]


def plan(request,source):
    run,clip=source.verified_run(request.run_id);q=request.question
    if any(v!=clip['video_id'] for v in re.findall(r'\d{2}-[a-z]-\d{2}-\d{2}',q,re.I)):raise ValueError('question_video_conflicts_with_selected_source')
    tid=request.track_id
    found=re.search(r'(?<![A-Za-z0-9_])track\s*0*(\d+)(?!\d)',q,re.I)
    if found:
        parsed=int(found[1])
        if tid is not None and tid!=parsed:raise ValueError('question_track_conflicts_with_selected_track')
        if parsed<1:raise ValueError('track_id_must_be_positive')
        tid=parsed
    if tid and not any(t['track_id']==tid for t in source.tracks(request.run_id)):raise ValueError('track_not_in_selected_run')
    cognition_query=' '.join(part for part in re.split(r'[，。；;\n]',q) if not re.search(r'不要|不得|避免|禁止|不评价|不推断|不臆测',part))
    if request.report_template:intent='report'
    elif re.search(r'专注|注意力|认知|学习能力|掌握|动机|纪律|学习效果|attent|engagement|cognit|motivation|learning outcome',cognition_query,re.I):intent='cognitive_boundary'
    elif re.search(r'报告|report',q,re.I) and not (re.search(r'为什么|为何|原因',q) and re.search(r'撤销|失效|重算|锚点|映射',q)):intent='report'
    elif re.search(r'冲突|矛盾|不一致|conflict',q,re.I):intent='conflicts'
    elif re.search(r'复核|核实|待办|review todo',q,re.I):intent='review_todo'
    elif request.subject_id or re.search(r'已确认身份|已验证.*行为',q) or (tid and re.search(r'身份|是谁|名字|实名|人名',q)):intent='verified_person'
    elif re.search(r'哪些时间|什么时候|何时|出现|timeline|中断|可见|位置',q,re.I) and tid:intent='track_timeline'
    elif re.search(r'时间映射|锚点|对齐|撤销|失效',q):intent='context_relation'
    elif re.search(r'分别列出|分别展示|来源类型',q):intent='summary'
    elif re.search(r'关系|相关|结合|活动有关|作业|批改|成绩|资料|补充|记忆|学生状态|课堂状态|学习状态|教学.*建议|建议.*教学|反馈|如何改进|怎么教',q) or (re.search(r'计划|安排|教案|教学活动|文档',q) and re.search(r'证明|等于|能否|是否',q)):intent='context_relation'
    elif re.search(r'计划|安排|教案',q):intent='teaching_plan'
    elif re.search(r'总结|summary',q,re.I):intent='summary'
    else:intent='observations'
    a,b=request.start,request.end
    match=re.search(r'(\d+(?:\.\d+)?)\s*(?:到|至|[-–~])\s*(\d+(?:\.\d+)?)\s*秒',q)
    if match:
        qa,qb=float(match[1]),float(match[2])
        if a is not None and a!=qa or b is not None and b!=qb:raise ValueError('question_time_conflicts_with_explicit_filters')
        a,b=qa,qb
    a=clip['start'] if a is None else a;b=clip['end'] if b is None else b
    if a<0 or b<a:raise ValueError('invalid_query_time_range')
    lo,hi=max(a,clip['start']),min(b,clip['end']);window=[lo,hi] if lo<=hi else None
    missing=[]
    if a<clip['start']:missing.append([a,min(b,clip['start'])])
    if b>clip['end']:missing.append([max(a,clip['end']),b])
    aliases={'stand':['站立'],'walk':['走动','行走'],'playphone':['手机'],'sleep':['睡眠','伏桌'],'readbook':['读书'],'homework':['作业','书写'],
             'chat':['交谈','聊天'],'eatordrink':['吃零食','喝水'],'usecomputer':['电脑'],'discuss':['讨论'],'hubbub':['嘈杂'],'call':['打电话'],'chase':['追逐']}
    actions=[key for key,words in aliases.items() if re.search(r'(?<![a-z])'+key+r'(?![a-z])',q,re.I) or any(w in q for w in words)]
    if intent in {'report','summary','context_relation','teaching_plan'} and not re.search(r'写作业|作业行为|homework',q,re.I):
        actions=[action for action in actions if action!='homework']
    strategies={
        'observations':['dataset_annotation','vlm_observation','human_visual_review','event_attribution','model_discovered_event','human_confirmed_event','knowledge_rule'],
        'track_timeline':['person_track','knowledge_rule'],
        'verified_person':['person_track','identity_binding','event_attribution','model_discovered_event','human_confirmed_event','vlm_observation','knowledge_rule'],
        'teaching_plan':['teacher_plan','teacher_report','teaching_activity','knowledge_rule'],
        'context_relation':['dataset_annotation','vlm_observation','human_visual_review','event_attribution','model_discovered_event','human_confirmed_event','teacher_plan','teacher_report','teaching_activity','knowledge_rule'],
        'cognitive_boundary':['knowledge_rule'],
        'conflicts':TYPES,'review_todo':TYPES,'summary':TYPES,'report':TYPES}
    focus='behavior_definition' if actions and re.search(r'能否证明|能够证明|能证明|可以证明|等于|支持',q) and not re.search(r'视频|这段|教案|计划|Track',q,re.I) else 'ordinary'
    if intent=='verified_person' and re.search(r'身份|是谁|名字|实名|人名',q) and not re.search(r'行为|动作|事件',q):focus='identity_basis'
    if re.search(r'为什么|为何|原因',q) and re.search(r'时间映射|锚点|撤销|失效',q):focus='alignment_audit'
    if re.search(r'分别列出|分别展示|来源类型',q):focus='source_inventory'
    focus_types={'behavior_definition':['knowledge_rule'],'alignment_audit':['knowledge_rule'],
        'identity_basis':['person_track','identity_binding','knowledge_rule'],
        'source_inventory':['teacher_plan','teacher_report','teaching_activity','vlm_observation','human_visual_review','knowledge_rule']}
    recognized=bool(actions or tid or request.subject_id or intent!='observations' or re.search(r'行为|事件|观察|画面|视频|发生|有哪些|what|action|behavior',q,re.I))
    return QueryPlan(user_question=q,intent=intent,query_object='registered_identity' if request.subject_id else ('anonymous_track' if tid else 'registered_clip'),
        run_id=request.run_id,clip_id=clip['clip_id'],lesson_id=request.lesson_id,document_ids=list(dict.fromkeys(request.document_ids)),track_id=tid,subject_id=request.subject_id,roster_subject_id=request.roster_subject_id,material_mode=request.material_mode,use_classroom_memory=request.use_classroom_memory,
        requested_time_range=[a,b],effective_time_range=window,missing_time_ranges=missing,evidence_types=focus_types.get(focus,strategies[intent]),
        output_mode='report' if intent=='report' else 'qa',report_template=request.report_template,report_context=request.report_context,focus=focus,parsing_status='recognized' if recognized else 'limited',
        clarification=None if recognized else '暂不能可靠理解该自由问题。请明确要查询的行为、Track、教学计划、冲突或时间范围；以下仅给出所选片段的受限行为候选。',
        required_relationships=['registered_source_and_time','current_permission','visible_track_interval','valid_identity_and_human_attribution_intersection'] if intent=='verified_person' else ['registered_source_and_time','current_permission','explicit_text_time_alignment'],
        requested_actions=actions,
        allow_verification=request.allow_verification,tools=['registered_video','track_repository','identity_reader','attribution_repository','context_repository','knowledge_rules']+(['subject_visual_review'] if request.allow_verification else []))
