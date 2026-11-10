"""Typed region-evidence query, atomic analysis and deterministic adjudication.

No rule here claims to perform visual recognition. Visual content comes from
persisted VLM observations or separately attributed human review records.
"""
import hashlib
import math
import re
from pathlib import Path
from urllib.parse import urlencode

from src.schema import EvidenceQuery,ReviewQuestionRequest,AtomicClaim,ReviewRisk,ClaimStatus
from src.agent.visual_evidence_policy import DYNAMIC_ACTIONS,semantic_findings

ACTION_ALIASES={'walk':['走动','走路','行走'],'stand':['站立','站着'],
                'playphone':['手机'],'sleep':['睡觉','睡眠','趴睡'],
                'handup':['举手'],'write':['书写','写字'],'read':['阅读','看书']}
COGNITIVE_QUERY=re.compile(r'专注|注意力|学习效果|学习投入|心理|情绪|认知|走神|玩游戏|\battention\b|\bengagement\b|\bgaming\b',re.I)


def parse_question(request,db):
    request=ReviewQuestionRequest.model_validate(request)
    bounds=db.region_bounds(request.video_id)
    text=request.question.strip()
    if not text:raise ValueError('question_required')
    mentioned=set(re.findall(r'\b\d{2}-[A-Za-z]-\d{2}-\d{2}\b',text))
    if mentioned-{request.video_id}:raise ValueError('question_video_id_conflicts_with_selected_scope')
    parsed=None
    clock=re.search(r'(\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)\s*(?:到|至|[-–~])\s*(\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)',text)
    if clock:
        def seconds(value):
            parts=list(map(float,value.split(':')))
            if any(t>=60 for t in parts[1:]):raise ValueError('invalid_clock_time')
            return sum(t*60**i for i,t in enumerate(reversed(parts)))
        parsed=list(map(seconds,clock.groups()))
    else:
        match=re.search(r'(\d+(?:\.\d+)?)\s*(?:秒|s)?\s*(?:到|至|[-–~])\s*(\d+(?:\.\d+)?)\s*(?:秒|s)',text,re.I)
        if match:parsed=list(map(float,match.groups()))
    if (request.start is None)!=(request.end is None):raise ValueError('provide_both_start_and_end')
    explicit=None if request.start is None else [request.start,request.end]
    if explicit and parsed and explicit!=parsed:raise ValueError('question_time_conflicts_with_form')
    start,end=explicit or parsed or bounds
    with db._get_connection() as conn:
        available={r[0] for r in conn.execute('SELECT DISTINCT action FROM region_annotations WHERE video_id=?',(request.video_id,))}
    actions=list(request.actions)
    if not actions:
        actions=[a for a in sorted(available) if re.search(r'\b'+re.escape(a)+r'\b',text,re.I) or any(alias in text for alias in ACTION_ALIASES.get(a,[]))]
    # Known behavior words may have no matching event in this selected dataset scope.
    if not actions:
        actions=[a for a,aliases in ACTION_ALIASES.items() if re.search(r'\b'+re.escape(a)+r'\b',text,re.I) or any(alias in text for alias in aliases)]
    if len(actions)>13 or any(not re.fullmatch(r'[A-Za-z_]{1,40}',a) for a in actions):raise ValueError('invalid_action_filter')
    sources=list(request.source_kinds)
    if not sources:
        if re.search(r'Excel|原始标注|数据集标注',text,re.I):sources.append('dataset_annotation')
        if 'VLM' in text.upper() or '视觉模型' in text:sources.append('vlm_visual_review')
        if re.search(r'YOLO|模型预测',text,re.I):sources.append('model_prediction')
        if re.search(r'人工复核记录|人工证据|人工来源',text):sources.append('human_review')
    intent='interpretation' if COGNITIVE_QUERY.search(text) else ('review_todo' if re.search(r'需要.*(?:人工|教师)|待复核|进一步.*确认',text) else ('action_check' if actions else ('overview' if sources or request.human_status or request.vlm_status or re.search(r'事件|行为|候选|记录|关注|发生|哪些',text) else 'unsupported')))
    human=request.human_status
    if human is None:
        if '未人工复核' in text:human='not_independently_reviewed'
        elif '人工已确认' in text:human='confirmed'
        elif '人工已驳回' in text:human='rejected'
    vlm=request.vlm_status
    if vlm is None and re.search(r'VLM|视觉模型',text,re.I):
        for phrase,status in [('未核验','unreviewed'),('不确定','uncertain'),('失败','error'),('已确认','confirmed'),('已驳回','rejected')]:
            if phrase in text:vlm=status;break
    return EvidenceQuery(video_id=request.video_id,start=float(start),end=float(end),actions=actions,
                         source_kinds=sources,human_status=human,vlm_status=vlm,intent=intent,limit=request.limit)


def video_is_ready(clip,root):
    path=Path(clip['path']).resolve()
    duration=clip.get('duration')
    return root in path.parents and path.suffix.lower()=='.mp4' and path.is_file() and clip.get('decode_status')=='ready' and isinstance(duration,(int,float)) and math.isfinite(duration) and duration>0


def video_reference(event,clip,t,root):
    local=t-clip['start']
    ready=video_is_ready(clip,root) and clip['video_id']==event['video_id'] and clip['start']<=t<clip['end'] and 0<=local<clip.get('duration',0)
    key=f"video:{clip['clip_id']}:{t:.6f}"
    return {'ref_id':key,'kind':'video','source_kind':'registered_video','event_id':event['event_id'],
            'video_id':event['video_id'],'clip_id':clip['clip_id'],'global_time':t,'local_time':local,
            'reachable':bool(ready),'media_url':'/api/media/'+clip['clip_id'],
            'replay_url':'/?'+urlencode({'event_id':event['event_id'],'clip_id':clip['clip_id'],'global_time':t}),
            'scope':'evidence_instant_not_entire_lesson','path':clip['path']}


def build_registry(db,bundles,query,root,evidence_root):
    registry={};file_hashes={}
    for bundle in bundles:
        event=bundle['event'];eid=event['event_id']
        path=Path(event['excel_path'])
        if str(path) not in file_hashes:
            file_hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        source='dataset:'+eid
        registry[source]={'ref_id':source,'kind':'source','source_kind':'dataset_annotation','event_id':eid,
                          'excel_row':event['excel_row'],'path':str(path),'sha256':event['excel_sha256'],
                          'temporal_rows':event.get('temporal_rows',[]),
                          'reachable':file_hashes[str(path)]==event['excel_sha256']}
        for clip in bundle['videos']['candidates']:
            t=max(query.start,event['t_start'],clip['start'])
            if t<=min(query.end,event['t_end']) and t<clip['end']:
                ref=video_reference(event,clip,t,root);registry[ref['ref_id']]=ref
        for human in bundle['reviews']:
            source='human:'+human['review_id']
            registry[source]={'ref_id':source,'kind':'source','source_kind':'human_review','event_id':eid,
                              'review_id':human['review_id'],'reachable':bool(human.get('reviewer')),
                              'clip_id':human['clip_id'],'global_time':human['global_time'],'scope':human.get('scope','evidence_point_only')}
            try:
                clip=db.clip(human['clip_id']);ref=video_reference(event,clip,human['global_time'],root)
                if abs(ref['local_time']-human['local_time'])>1e-5:ref['reachable']=False
                registry[ref['ref_id']]=ref
            except (KeyError,ValueError):registry[source]['reachable']=False
        for visual in bundle['visual_reviews']:
            source='visual:'+visual['review_id']
            registry[source]={'ref_id':source,'kind':'source','source_kind':'vlm_visual_review','event_id':eid,
                              'review_id':visual['review_id'],'reachable':True,
                              'model_id':visual['result'].get('model_id'),'created_at':visual['created_at']}
            evidence=visual.get('evidence') or {}
            try:clip=db.clip(visual['clip_id'])
            except KeyError:clip=None
            sent={f for response in visual.get('raw_responses',[]) for f in response.get('frame_ids',[])}
            for frame in evidence.get('frames',[]):
                key=f"frame:{visual['review_id']}:{frame['frame_id']}"
                t=frame['global_time'];reachable=False;ref=None
                if clip:
                    ref=video_reference(event,clip,t,root)
                    image=Path(frame.get('files',{}).get('input_roi','')).resolve()
                    source_file=Path(clip['path'])
                    same_source=source_file.is_file() and evidence.get('event_id')==eid and evidence.get('clip_id')==clip['clip_id'] and Path(evidence.get('source_path','')).resolve()==source_file.resolve() and source_file.stat().st_size==evidence.get('source_size') and source_file.stat().st_mtime_ns==evidence.get('source_mtime_ns')
                    reachable=bool(ref['reachable'] and same_source and evidence_root in image.parents and image.is_file()
                                   and frame['frame_id'] in sent and abs(frame['local_time']-ref['local_time'])<1e-5
                                   and hashlib.sha256(image.read_bytes()).hexdigest()==frame.get('sha256',{}).get('input_roi'))
                registry[key]={**(ref or {}),'ref_id':key,'kind':'frame','source_kind':'vlm_visual_review',
                               'event_id':eid,'review_id':visual['review_id'],'frame_id':frame['frame_id'],
                               'global_time':t,'reachable':reachable,
                               'image_url':f"/api/visual-reviews/{visual['review_id']}/frames/{frame['frame_id']}/input_roi"}
    return registry


def in_query(ref,query):
    return ref.get('reachable',False) and query.start<=ref.get('global_time',-1)<=query.end


def observable_parts(text):
    """Carry the *source sentence's* subject into every safe action clause.

    Multiple subjects, uncertain syntax or unknown subjects keep the complete
    sentence. Grammatical carry-forward is not person identification/tracking.
    """
    verbs=r'holding|holds?|gesturing|gestures?|looking|looks?|writing|writes?|reading|reads?|sitting|sits?|standing|stands?|moving|moves?|touching|touches|using|uses|leaning|leans?'
    if re.search(r'\bwhile\b|\banother\b|\bothers\b|;|；',text,re.I):return [text]
    subject=re.match(r'(?P<subject>(?:A|An|The|One)\s+(?:person|student|individual|man|woman)(?:\s+in\s+[^,.;]+?)?)\s+(?:'+verbs+r')\b',text,re.I)
    if not subject:return [text]
    pieces=re.split(r',\s*(?=(?:'+verbs+r')\b)|\s+and\s+(?=(?:'+verbs+r')\b)',text,flags=re.I)
    if len(pieces)==1:return [text]
    result=[pieces[0].strip(' ,.')] 
    for piece in pieces[1:]:
        predicate=piece.strip(' ,.');first=predicate.split()[0].lower()
        connector=' is ' if first.endswith('ing') else ' '
        result.append(subject['subject']+connector+predicate)
    return result


def analyze_bundle(bundle,query,registry):
    event=bundle['event'];eid=event['event_id']
    source='dataset:'+eid
    videos=[k for k,r in registry.items() if r.get('event_id')==eid and r.get('kind')=='video' and in_query(r,query)]
    claims=[AtomicClaim(claim_id='claim:'+eid+':dataset',claim_type='dataset_claim',event_id=eid,
                        action=event['action'],global_time_window=[max(query.start,event['t_start']),min(query.end,event['t_end'])],
                        source_refs=[source],evidence_refs=videos[:1],missing_evidence=['independent_verification_of_dataset_label'],
                        limitations=['Excel区域标签是原始声明，不是逐帧人物事实。'],basis={'kind':'dataset_target'})]
    for human in bundle['reviews']:
        ref_id=f"video:{human['clip_id']}:{human['global_time']:.6f}"
        if ref_id not in registry or not in_query(registry[ref_id],query):continue
        t=human['global_time']
        claims.append(AtomicClaim(claim_id='claim:human:'+human['review_id'],claim_type='observable_claim',event_id=eid,
                      action=f"人工复核原标签 [{event['action']}]：{human['decision']}（仅该证据点）",
                      global_time_window=[t,t],source_refs=['human:'+human['review_id']],evidence_refs=[ref_id],
                      basis={'kind':'human_point','decision':human['decision'],'target_action':event['action']},
                      limitations=['复核者标识为自行填写；复核仅绑定该证据点，不证明整段持续或心理状态。']))
    visual=bundle['visual_reviews'][-1] if bundle['visual_reviews'] else None
    if visual and not visual.get('error') and visual['result'].get('decision')!='error':
        result=visual['result']
        seen=set()
        for index,observation in enumerate(result.get('observed_actions',[])):
            refs=[f"frame:{visual['review_id']}:{r}" for r in observation['evidence_refs']]
            valid=[r for r in refs if r in registry and in_query(registry[r],query)]
            if not valid or semantic_findings(observation['action']):continue
            for part_index,part in enumerate(observable_parts(observation['action'])):
                for ref in valid:
                    if (part,ref) in seen:continue
                    seen.add((part,ref));t=registry[ref]['global_time']
                    frame_id=registry[ref]['frame_id']
                    claims.append(AtomicClaim(claim_id=f"claim:visual:{visual['review_id']}:{index}:{part_index}:{frame_id}",claim_type='observable_claim',event_id=eid,
                                  action=part,global_time_window=[t,t],
                                  source_refs=['visual:'+visual['review_id']],evidence_refs=[ref],
                                  limitations=['模型对单个所引时刻的外显观察，未经教师独立金标准确认；不建立人物身份或持续性。'],
                                  basis={'kind':'visual_observation','original_observation':observation['action'],'observation_index':index,
                                         'subject_context':observation['action'],'text_transform':'subject_preserving_split_or_complete_source',
                                         'subject_context_unresolved':bool(re.match(r'^(holding|gesturing|looking|writing|reading|sitting|standing|moving|touching|using|leaning)\b',observation['action'],re.I)),
                                         'independent_human_verified':False,
                                         'review_decision':result.get('final_decision') or result['decision'],
                                         'semantic_override':result.get('decision_source')=='system_semantic_guardrail'}))
    return claims


def critic_bundle(bundle,query,registry):
    event=bundle['event'];eid=event['event_id'];risks=[]
    visual=bundle['visual_reviews'][-1] if bundle['visual_reviews'] else None
    decision=(visual['result'].get('final_decision') or visual['result']['decision']) if visual else None
    def add(kind,reason,resolvable=False):
        risks.append(ReviewRisk(event_id=eid,risk_type=kind,reason=reason,resolvable_by_visual_review=resolvable,source_refs=['dataset:'+eid]))
    if decision!='confirmed' and not any(r['decision']=='confirmed' for r in bundle['reviews']):
        add('unverified_dataset_annotation','原始Excel标签尚未获充分独立核验。',not visual)
    if visual:
        alignment=visual['result'].get('alignment_checks') or {}
        if alignment.get('version') and not alignment.get('passed'):
            add('unsupported_confirmation','模型正向支持未通过声明与证据语义对齐；保留系统降级及原始判断。',False)
    results={r['result'].get('final_decision') or r['result']['decision'] for r in bundle['visual_reviews']}
    rounds={r.get('model_judgment',{}).get('decision') for v in bundle['visual_reviews'] for r in v.get('review_rounds',[])}
    if len(results-{'error'})>1 or len(rounds-{None,'error'})>1:
        add('model_round_disagreement','模型历史/批次状态不一致（包括不确定），需核对各批次范围，不能用最新输出抹去历史。')
    human=[r for r in bundle['reviews'] if query.start<=r['global_time']<=query.end]
    if decision in {'confirmed','rejected'} and any(r['decision'] in {'confirmed','rejected'} and r['decision']!=decision for r in human):
        add('human_model_conflict','人工与模型对原标签存在分歧，范围未完全对齐时仍须人工查证。')
    by_time={}
    for r in human:by_time.setdefault(r['global_time'],set()).add(r['decision'])
    if any({'confirmed','rejected'}<=d for d in by_time.values()):add('human_model_conflict','同一证据点存在相反人工复核，不能自动择一。')
    x,y,w,h=event['region_xywh'];area=w*h/(event['frame_width']*event['frame_height'])
    if area>=.25 or not any(r.get('box_kind')=='single_person' for r in human):
        add('region_subject_ambiguity',f'区域比例{area:.1%}，无独立人物跟踪；结论仅限区域事实。')
    if event['action'] in DYNAMIC_ACTIONS and (not visual or not visual['result'].get('sampling_strategy',{}).get('additional')):
        add('dynamic_temporal_insufficiency','动态动作缺少局部连续采样；离散未见不构成反证。',not visual)
    if query.intent=='interpretation':add('cognitive_overreach','外显动作不能直接证明专注、学习效果或心理状态。')
    refs=[r for r in registry.values() if r.get('event_id')==eid and r.get('kind') in {'video','frame'}]
    if not any(in_query(r,query) for r in refs):add('missing_video_coverage','查询区间内没有可回放证据，不能断言行为未发生。')
    if any(not r.get('reachable') for r in refs):add('unreachable_evidence','部分视频/图像引用失效、内容变化或不属于实际送入模型的帧。',bool(visual))
    return risks


def arbitrate_claim(claim,bundle,risks,registry,query):
    claim=claim.model_copy(deep=True)
    kinds={r.risk_type for r in risks}
    conflict=bool(kinds & {'human_model_conflict','model_round_disagreement'})
    if claim.claim_type=='interpretation_claim':
        claim.verification_status=ClaimStatus.UNKNOWN
        claim.conclusion_source='system_cognitive_boundary'
        claim.missing_evidence=['teaching_task_context','cross_modal_independent_evidence']
        claim.adjudication_reason='外显动作与认知状态不能直接等同；当前证据不足以作教学解释。'
    elif claim.claim_type=='dataset_claim':
        claim.verification_status=ClaimStatus.UNCERTAIN if claim.evidence_refs else ClaimStatus.UNKNOWN
        claim.conclusion_source='dataset_annotation_candidate'
        claim.adjudication_reason='原始标签仅作为检索候选；不把Excel或VLM状态直接转换成课堂事实。'
        visual=bundle['visual_reviews'][-1] if bundle['visual_reviews'] else None
        if visual and not visual.get('error') and not conflict:
            result=visual['result'];refs=[f"frame:{visual['review_id']}:{r}" for r in result.get('evidence_refs',[])]
            if result.get('decision')=='confirmed' and result.get('alignment_checks',{}).get('passed') and result.get('target_claim')==claim.action and refs and all(r in registry and in_query(registry[r],query) for r in refs) and not result.get('semantic_guardrail_violations'):
                claim.verification_status=ClaimStatus.SUPPORTED;claim.evidence_refs=refs
                claim.source_refs.append('visual:'+visual['review_id']);claim.conclusion_source='cited_visual_target_support'
                claim.adjudication_reason='存在当前查询时间内的绑定图像支持区域外显动作；不证明全区间持续或同一学生。'
                claim.missing_evidence=[]
        for human in bundle['reviews']:
            key=f"video:{human['clip_id']}:{human['global_time']:.6f}"
            if human['decision']=='confirmed' and key in registry and in_query(registry[key],query) and not conflict:
                claim.verification_status=ClaimStatus.SUPPORTED;claim.evidence_refs=[key]
                claim.source_refs.append('human:'+human['review_id']);claim.conclusion_source='human_point_support'
                claim.adjudication_reason='人工证据点支持该区间内出现过动作，不外推为持续行为。';claim.missing_evidence=[]
        if claim.action=='sleep' and claim.verification_status==ClaimStatus.SUPPORTED:
            claim.verification_status=ClaimStatus.UNKNOWN;claim.adjudication_reason='姿态与单点动作复核不能独立证明真实睡眠。'
    else:
        kind=claim.basis.get('kind')
        if conflict or claim.basis.get('semantic_override') or claim.basis.get('subject_context_unresolved'):
            claim.verification_status=ClaimStatus.UNCERTAIN;claim.conclusion_source='system_conflict_guardrail'
            claim.missing_evidence=['independent_conflict_resolution']
            claim.adjudication_reason='来源冲突或语义越界尚未解决，保留候选观察，不进入正式事实。'
        elif kind=='human_point':
            decision=claim.basis['decision']
            claim.verification_status={'confirmed':ClaimStatus.SUPPORTED,'rejected':ClaimStatus.CONTRADICTED,'unable_to_judge':ClaimStatus.UNCERTAIN}[decision]
            claim.conclusion_source='human_review_at_evidence_instant'
            claim.adjudication_reason='独立保存的人工记录，仅对所引证据点裁决，不将点上驳回外推到整个区间。'
            if claim.basis.get('target_action')=='sleep':
                claim.verification_status=ClaimStatus.UNKNOWN;claim.adjudication_reason='复核记录已保留，但可观察姿态不能独立证明真实睡眠。'
        else:
            claim.verification_status=ClaimStatus.SUPPORTED;claim.conclusion_source='cited_vlm_observation'
            claim.adjudication_reason='仅有模型图像观察支持，尚未经独立人工事实核验；与原行为标签及认知解释分开。'
    return claim


def guard_atomic(claim,registry):
    claim=claim.model_copy(deep=True)
    sources=claim.source_refs and all(r in registry and registry[r].get('reachable') for r in claim.source_refs)
    evidence=claim.evidence_refs and all(r in registry and registry[r].get('reachable') and registry[r].get('kind') in {'video','frame'} for r in claim.evidence_refs)
    independent=[registry[r] for r in claim.source_refs if r in registry and registry[r].get('source_kind') in {'vlm_visual_review','human_review'}]
    bound=bool(independent and evidence)
    for key in claim.evidence_refs:
        ref=registry.get(key,{})
        if not claim.global_time_window[0]<=ref.get('global_time',-1)<=claim.global_time_window[1]:bound=False
        if claim.event_id and ref.get('event_id')!=claim.event_id:bound=False
        if not any(s.get('event_id')==ref.get('event_id') and (
            (s['source_kind']=='vlm_visual_review' and ref.get('kind')=='frame' and s.get('review_id')==ref.get('review_id')) or
            (s['source_kind']=='human_review' and s.get('clip_id')==ref.get('clip_id') and abs(s.get('global_time',-100)-ref.get('global_time',-1))<1e-5)
        ) for s in independent):bound=False
    if claim.verification_status in {ClaimStatus.SUPPORTED,ClaimStatus.CONTRADICTED} and (not sources or not evidence or not bound):
        claim.verification_status=ClaimStatus.UNKNOWN;claim.conclusion_source='system_reference_guardrail'
        claim.adjudication_reason='来源、时间、主体范围或独立复核绑定不足，不能输出有据裁决。';claim.missing_evidence.append('reachable_independent_source_bound_to_evidence')
    if claim.claim_type=='interpretation_claim':
        claim.verification_status=ClaimStatus.UNKNOWN
    if claim.claim_type=='observable_claim' and semantic_findings(claim.action):
        claim.verification_status=ClaimStatus.UNKNOWN;claim.conclusion_source='system_semantic_guardrail'
        claim.adjudication_reason='声明包含非直接可观察的状态判断。'
    claim.eligible_for_report=bool(claim.claim_type=='observable_claim' and claim.verification_status==ClaimStatus.SUPPORTED and sources and evidence)
    return claim
