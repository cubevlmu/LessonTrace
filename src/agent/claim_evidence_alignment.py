"""CESA: conservative, auditable claim consistency checks, not visual recognition.

The text grammar only extracts reported physical predicates. It does not prove
they occurred. Geometry checks proposed anchors against registered crops, never
turns a model's person descriptor into a tracking identity or human verification.
"""
import math
import re

from src.schema import VisualSupportFact

VERSION='cesa-v1'
ACTION_REQUIREMENTS={
    'stand':{'all':['upright_posture'],'description':'可见站立姿态；不要求连续运动或已知身份。'},
    'walk':{'all':['stepping','body_translation'],'sequence':True,'description':'局部有序步态与位移；站立本身不足。'},
    'playphone':{'all':['phone_visible','phone_operation'],'description':'可见手机及同一主体实际屏幕/按键操作；持有、遮脸不充分，不推断游戏。'},
    'sleep':{'all':[],'context':'actual_sleep_not_established_by_frames','description':'保留sleep原类别；低头或伏桌不能证明真实睡眠。'},
    'readbook':{'all':['book_visible','reading_cues'],'description':'书页与明确阅读/注视书中文字的动作；拿书、伏书或写字不等于阅读。'},
    'homework':{'all':['writing'],'context':'assignment','description':'书写还需已登记的作业/任务语境，不能由模型自称homework补足。'},
    'chat':{'all':['reciprocal_exchange'],'sequence':True,'description':'同一局部群体可见交替交流线索；吃东西、朝向或单方手势不充分。'},
    'eatordrink':{'all':['consumption_action'],'description':'直接进食/饮水或明确手口-食物饮具交互；仅持容器不足。'},
    'usecomputer':{'all':['computer_visible','computer_operation'],'description':'电脑及键鼠/触屏输入交互；只看屏幕不足。'},
    'discuss':{'all':['reciprocal_exchange','shared_material'],'sequence':True,'description':'围绕同一材料的交替交流；不推断学习效果。'},
    'hubbub':{'all':['group_activity'],'context':'audio','description':'群体活动还需可追溯声音证据；无声画面不证明嘈杂/起哄。'},
    'call':{'all':['phone_at_ear'],'context':'audio','description':'若call指通话，持手机到耳旁仍需通话语境；稀有类别原义须人工核对，不自动重命名。'},
    'chase':{'all':['following_motion','body_translation'],'sequence':True,'description':'局部多人有方向一致的追随运动；并排站立或同框不充分。'},
}

# A feature is a predicate/object relationship, not equality with a class name.
FEATURES={
 'upright_posture':r'\b(?:stands?|standing)(?:\s+up)?\b|\bupright (?:body|standing) posture\b|站立|站着|直立',
 'stepping':r'\b(?:walk(?:s|ing)?|stroll(?:s|ing)?|strid(?:es|ing)|alternating (?:footsteps|steps))\b|行走|迈步|步行|交替迈步',
 'body_translation':r'\b(?:walk(?:s|ing)?|stroll(?:s|ing)?|mov(?:es|ing) (?:across|through|toward|towards)|changes? position)\b|行走|位置移动|迈步|穿过教室|向前移动',
 'phone_visible':r'\b(?:phone|smartphone|cellphone|mobile device)\b|手机',
 'phone_operation':r'\b(?:tap(?:s|ping)?|swip(?:es|ing)|scroll(?:s|ing)?|typ(?:es|ing)|press(?:es|ing)?)\b.{0,45}\b(?:phone|smartphone|phone screen)\b|\b(?:phone|smartphone)\b.{0,45}\b(?:tap(?:s|ping)?|swip(?:es|ing)|scroll(?:s|ing)?|typ(?:es|ing))\b|(?:点击|滑动|操作|输入).{0,10}手机|手机.{0,12}(?:点击|滑动|输入)',
 'book_visible':r'\b(?:book|textbook|book pages|printed pages)\b|书本|书页|课本',
 'reading_cues':r'\bread(?:s|ing)?\s+(?:(?:an?|the|open|text|from|in)\s+){0,3}(?:book|textbook|pages|text)\b|\b(?:gaze|eyes)\b.{0,30}\b(?:text on|printed|book)\b|(?:阅读|看着|注视).{0,8}(?:书本|书页|课本|书中文字)',
 'writing':r'\b(?:writ(?:es|ing)|pen (?:moves|moving|strokes))\b|书写|写字|用笔写',
 'reciprocal_exchange':r'\b(?:take turns (?:speaking|talking|gesturing)|alternating (?:speech|speaking|gestures)|one (?:person|student) speaks.{0,60}(?:another|other).{0,25}responds|exchang(?:e|es|ing) (?:spoken turns|verbal responses))\b|轮流(?:说话|发言|交流)|交替(?:发言|口型变化)|一人说话.{0,15}另一人回应',
 'consumption_action':r'\b(?:eat(?:s|ing)?\s+(?:an? |the |some )?(?:snack|food|meal|sandwich)|drink(?:s|ing)?\s+(?:from\s+)?(?:a |the |some )?(?:cup|bottle|water|mug|beverage)|rais(?:es?|ing).{0,35}(?:cup|bottle).{0,35}(?:mouth|lips).{0,20}drink(?:s|ing)?|(?:cup|bottle|mug).{0,25}(?:mouth|lips)\s+and\s+drink(?:s|ing)?\s+from\s+it)\b|(?:进食|吃零食|咬食物|喝水|饮水|从杯中喝|举杯到嘴边喝)',
 'computer_visible':r'\b(?:computer|laptop|monitor|desktop screen)\b|电脑|计算机|笔记本电脑',
 'computer_operation':r'\b(?:typ(?:es|ing) on (?:a |the )?keyboard|click(?:s|ing)? (?:a |the )?mouse|operat(?:es|ing) (?:a |the )?(?:keyboard|mouse))\b|(?:敲击|操作|输入).{0,8}键盘|点击鼠标|操作鼠标',
 'shared_material':r'\b(?:jointly|both|together|shared)\b.{0,40}\b(?:book|worksheet|document|pages|material)\b|共同.{0,12}(?:材料|书页|文档)|同一份材料',
 'group_activity':r'\b(?:group of|multiple|several)\s+(?:people|students|individuals)\b|多人|群体',
 'phone_at_ear':r'\bphone\b.{0,25}\b(?:to|at|against)\s+(?:(?:their|his|her|the)\s+)?ear\b|手机.{0,8}(?:耳旁|耳边)',
 'following_motion':r'\b(?:chasing|chases|runs? after|running after|following another moving person)\b|追逐|追赶|跑着追随',
 'drink_container_visible':r'\b(?:cup|bottle|mug)\b|杯子|水瓶',
 'mutual_orientation':r'\b(?:facing each other|turns? toward another|mutual gaze)\b|相互朝向|面对面',
}
NEGATION=re.compile(r"(?:\b(?:not|no|never|cannot|can't|unable to|without|isn't|aren't)\b|没有|未见|无法|不能|并未|不在)[^.;。；!?！？]{0,70}$",re.I)
HEDGE=re.compile(r'\b(?:may|might|appears? to|appearing to|possibly|seems? to|consistent with)\b|可能|似乎|好像',re.I)
ACTOR=re.compile(r'\b(?:person|student|individual|man|woman|people|wearer|he|she|they)\b|人|学生|同学',re.I)


def reported_features(text):
    """Extract positive unhedged predicates; keep negated/unknown relations out."""
    text=re.sub(r'''\b(?:claim|label|target)\s*["'“][^"'”]+["'”]''','label mention',text,flags=re.I)
    found=set()
    for clause in re.split(r'[.;。；!?！？]|\bbut\b|\bhowever\b|但是|但',text,flags=re.I):
        for name,pattern in FEATURES.items():
            for match in re.finditer(pattern,clause,re.I):
                before=clause[:match.start()]
                # Negation in a previous actor's comma clause cannot negate this actor.
                before=re.split(r'\band\b|,\s*(?=(?:a |the |another |one |学生|另一人))',before,flags=re.I)[-1]
                if NEGATION.search(before) or HEDGE.search(before):continue
                after=clause[match.end():].split(',')[0]
                if re.search(r'^.{0,25}(?:cannot be (?:verified|confirmed|observed)|is not (?:visible|clear|established)|remains? uncertain|无法确认|不能证实)',after,re.I):continue
                if name in {'reading_cues','consumption_action'} and HEDGE.search(clause[max(0,match.start()-20):match.end()]):continue
                found.add(name)
    return found


def negated_features(text):
    # A quoted category name is a label mention, not an additional predicate.
    text=re.sub(r'''["'“](?:'''+ '|'.join(ACTION_REQUIREMENTS)+r''')["'”]''','label mention',text,flags=re.I)
    result=set()
    for clause in re.split(r'[.;。；!?！？]|\bbut\b|但是|但',text,flags=re.I):
        for name,pattern in FEATURES.items():
            for match in re.finditer(pattern,clause,re.I):
                before=re.split(r'\band\b|,\s*(?=(?:a |the |another |one |学生|另一人))',clause[:match.start()],flags=re.I)[-1]
                if NEGATION.search(before):result.add(name)
    return result


def _conflicts(target,text,refs=None):
    """Recognize explicit action denial, not generic identity/continuity limits."""
    generic_identity=re.compile(r'identity|identif|tracked|tracking|same (?:person|individual)|prior frame|身份|跟踪',re.I)
    patterns={
        'stand':[r'(?:not visible|not verifiable).{0,30}(?:f\d|frame)',r'ambiguous posture',r'(?:cannot|unable to).{0,20}(?:standing|upright)'],
        'walk':[r'no (?:motion|movement|position change)',r'(?:cannot|not).{0,25}(?:walk|walking)'],
        'playphone':[r'no.{0,25}(?:phone interaction|phone operation|screen interaction)',r'(?:cannot|not).{0,30}(?:using|operating).{0,15}phone'],
        'readbook':[r'no evidence.{0,25}(?:reading|writing or reading)',r'(?:reading|reading action).{0,20}(?:not visible|not verifiable)',r'posture alone is insufficient'],
        'homework':[r'no.{0,20}(?:task|assignment) context',r'(?:writing|hand movements).{0,20}(?:obscur|not verifiable)'],
        'eatordrink':[r'no (?:observable action|evidence).{0,35}(?:eating|drinking|person.{0,10}consuming)',r'(?:eating|drinking).{0,20}(?:not visible|not verifiable)'],
        'call':[r'(?:action|phone call).{0,25}(?:not visible|not verifiable)'],
        'chat':[r'no.{0,20}(?:interaction|exchange|conversation)',r'(?:conversation|speech).{0,25}(?:not visible|not verifiable)'],
    }
    conflicts=[]
    for clause in re.split(r'[.;。；!?！？]',text):
        named=set(re.findall(r'\b[fd]\d{3}\b',clause))
        if refs is not None and named and not named.intersection(refs):continue
        if generic_identity.search(clause) or re.search(r'\bbefore\s+[fd]\d{3}\b',clause,re.I):continue
        if negated_features(clause).intersection(ACTION_REQUIREMENTS.get(target,{}).get('all',[])):
            conflicts.append(clause.strip());continue
        if re.search(r'\b(?:this|the action)\s+is\s+not (?:verifiable|visible|established)\b',clause,re.I):conflicts.append(clause.strip());continue
        if any(re.search(p,clause,re.I) for p in patterns.get(target,[])):conflicts.append(clause.strip())
    return conflicts


def _finite(values):return all(isinstance(v,(int,float)) and math.isfinite(v) for v in values)


def check_alignment(judgment,target,evidence,frames,*,observations=None,legacy=False,context=None):
    """Never reads gold labels. A passed check is consistency, not visual truth."""
    context=context or {};observations=observations or {'observed_actions':[],'limitations':[]}
    definition=ACTION_REQUIREMENTS.get(target)
    checks=[];accepted=[];issues=[];warnings=[]
    facts=list(judgment.support_facts)
    derivation='model_structured'
    known={f['frame_id']:f for f in frames}
    if legacy:
        facts=legacy_support_facts(observations,known,set(judgment.evidence_refs))
        derivation='legacy_text_adapter_not_new_model_inference'
        warnings.append('Legacy paired-view action attribution is the old model region claim, not independently verified localization. No bbox or identity is invented.')
    if judgment.target_claim is not None and judgment.target_claim!=target:issues.append('target_claim_mismatch')
    if not legacy and judgment.target_claim is None:issues.append('missing_target_claim')
    if not definition:issues.append('unknown_action_definition')
    if not facts:issues.append('missing_support_facts')
    action_features={'upright_posture','stepping','body_translation','phone_operation','reading_cues','writing',
                     'reciprocal_exchange','consumption_action','computer_operation','following_motion','phone_at_ear'}
    reason_features=reported_features(judgment.reason).intersection(action_features)
    limitation_text=' '.join(observations.get('limitations',[])+judgment.limitations)
    contradictions=[]
    for index,fact in enumerate(facts):
        data=fact.model_dump();fail=[];notes=[];refs=fact.evidence_refs
        features=reported_features(fact.observed_action)
        if re.search(r'\b(?:label|category|hypothesis|definition|forbidden|should|must|would|could)\b|标签|类别|假设|应该|必须|标语',fact.observed_action,re.I):fail.append('non_observational_statement')
        if not fact.subject_ref.strip() or fact.subject_scope=='unknown':fail.append('unknown_subject_scope')
        if target not in {'chat','discuss','chase','hubbub'} and fact.subject_scope!='one_unverified_person':fail.append('unresolved_multi_subject_attribution')
        if re.search(r'\b(?:another|different|other|second) (?:person|student|individual)\b|另一(?:人|名学生)',fact.observed_action,re.I) and target not in {'chat','discuss','chase','hubbub'}:fail.append('cross_subject_evidence')
        if fact.relation_to_target!='supports' and not legacy:fail.append('model_does_not_claim_support')
        if not refs or not set(refs)<=set(known):fail.append('invalid_frame_reference')
        if not set(refs)<=set(judgment.evidence_refs):fail.append('support_not_cited_by_final_judgment')
        times=[known[r]['global_time'] for r in refs if r in known]
        win=fact.global_time_window
        requested=evidence.get('requested_window');playable=evidence.get('playable_window')
        if not _finite(win) or win[0]>win[1]:fail.append('invalid_fact_time_window')
        elif not requested or not playable:fail.append('missing_registered_time_scope')
        else:
            if win[0]<max(requested[0],playable[0])-.051 or win[1]>min(requested[1],playable[1])+.051:fail.append('fact_outside_event_time')
            if any(t<win[0]-.051 or t>win[1]+.051 for t in times):fail.append('fact_reference_time_mismatch')
        if fact.temporal_scope=='entire_interval' and not evidence.get('sampling_exhaustive'):fail.append('unsupported_entire_interval_claim')
        # Source geometry is checked; proposed visual locations are not independent truth.
        anchors={a.frame_id:a for a in fact.spatial_anchors}
        if len(anchors)!=len(fact.spatial_anchors):fail.append('duplicate_spatial_anchor')
        if fact.view=='unknown':
            if legacy and all('roi' in known[r].get('files',{}) for r in refs if r in known):notes.append('legacy_region_claim_unverified')
            else:fail.append('unknown_region_attribution')
        elif fact.view=='roi':
            if 'input_roi' not in context.get('input_views',['input_full','input_roi']):fail.append('claimed_view_not_sent')
            if any('roi' not in known[r].get('files',{}) and 'input_roi' not in known[r].get('files',{}) for r in refs if r in known):fail.append('roi_evidence_not_available')
        elif fact.view=='full':
            if 'input_full' not in context.get('input_views',['input_full','input_roi']):fail.append('claimed_view_not_sent')
            if not set(refs)<=set(anchors):fail.append('full_frame_fact_requires_region_anchors')
        width,height=context.get('frame_size',[0,0])
        for ref in refs:
            if ref not in known:continue
            roi=known[ref].get('roi_xyxy',[])
            if width<=0 or height<=0 or len(roi)!=4 or not _finite(roi) or not 0<=roi[0]<roi[2]<=width or not 0<=roi[1]<roi[3]<=height:fail.append('invalid_or_missing_registered_roi')
        for ref,anchor in anchors.items():
            box=anchor.box_xyxy_normalized
            if ref not in known or ref not in refs:fail.append('invalid_anchor_reference');continue
            if anchor.subject_ref!=fact.subject_ref:fail.append('cross_subject_evidence')
            if not _finite(box) or not 0<=box[0]<box[2]<=1 or not 0<=box[1]<box[3]<=1:fail.append('invalid_spatial_anchor');continue
            if width<=0 or height<=0:fail.append('missing_registered_frame_size');continue
            roi=known[ref].get('roi_xyxy',[])
            if len(roi)!=4:fail.append('missing_registered_roi');continue
            actual=[box[0]*width,box[1]*height,box[2]*width,box[3]*height]
            intersection=max(0,min(actual[2],roi[2])-max(actual[0],roi[0]))*max(0,min(actual[3],roi[3])-max(actual[1],roi[1]))
            if intersection/((actual[2]-actual[0])*(actual[3]-actual[1]))<.8:fail.append('evidence_outside_target_region')
        needed=set(definition.get('all',[])) if definition else set()
        missing=needed-features
        if missing:fail.append('missing_required_action_features')
        if features and not features.intersection(needed) and needed:fail.append('unrelated_support_action')
        if not ACTOR.search(fact.observed_action) and len(fact.subject_ref.split())<2:fail.append('insufficient_subject_description')
        if definition and definition.get('context'):
            context_type=definition['context'];allowed=context.get('verified_context_refs',{}).get(context_type,[])
            if not allowed or not fact.context_refs or not set(fact.context_refs)<=set(allowed):fail.append('missing_nonvisual_context')
        if definition and definition.get('sequence'):
            ordered=sorted(set(times))
            if fact.temporal_scope!='local_sequence' or len(ordered)<2 or any(b-a>.55 for a,b in zip(ordered,ordered[1:])):fail.append('insufficient_ordered_temporal_support')
            if not set(refs)<=set(anchors):fail.append('unverified_sequence_subject_correspondence')
        # A reason supporting a different physical action does not support this target.
        if features.intersection(needed) and reason_features and not reason_features.intersection(needed):fail.append('support_reason_mismatch')
        if _conflicts(target,judgment.reason,set(refs)):fail.append('support_reason_contradiction')
        local_contradictions=_conflicts(target,limitation_text,set(refs))
        if local_contradictions:fail.append('support_limitation_conflict');contradictions.extend(local_contradictions)
        for other in facts:
            if other is fact or other.subject_ref!=fact.subject_ref or not set(other.evidence_refs).intersection(refs):continue
            if negated_features(other.observed_action).intersection(needed):fail.append('same_subject_same_instant_fact_conflict')
        if not fail:accepted.append(index)
        checks.append({'fact_index':index,'observed_action':fact.observed_action,'derived_features':sorted(features),
                       'required_features':sorted(needed),'missing_features':sorted(missing),'passed':not fail,
                       'failures':list(dict.fromkeys(fail)),'notes':notes,'evidence_refs':refs})
    if facts and not accepted:issues.append('no_semantically_sufficient_support_fact')
    if not legacy and any(c['failures'] and facts[c['fact_index']].relation_to_target=='supports' for c in checks):issues.append('invalid_claimed_support_fact')
    passed=bool(accepted) and not issues
    physical_missing={reason for check in checks for reason in check['failures']}
    irreducible='target_claim_mismatch' in issues or bool(physical_missing.intersection({'missing_nonvisual_context','support_reason_mismatch','support_reason_contradiction'})) or (definition or {}).get('context') is not None
    precursors={'walk':{'upright_posture','body_translation'},'chase':{'body_translation'},
                'playphone':{'phone_visible'},'readbook':{'book_visible'},'chat':{'mutual_orientation','reciprocal_exchange'},
                'eatordrink':{'drink_container_visible','consumption_action'},'usecomputer':{'computer_visible'},'stand':{'upright_posture'}}
    seen=set().union(*(set(c['derived_features']) for c in checks)) if checks else set()
    extra_possible=not passed and not irreducible and bool(seen.intersection(precursors.get(target,set())))
    return {'version':VERSION,'target_claim':target,'passed':passed,'risk_type':None if passed else 'unsupported_confirmation',
            'error_kind':None if passed else 'claim_evidence_mismatch','issues':list(dict.fromkeys(issues)),
            'checks':checks,'accepted_fact_indexes':accepted,'support_facts':[f.model_dump() for f in facts],
            'contradiction_excerpts':list(dict.fromkeys(contradictions)),'derivation':derivation,'warnings':warnings,
            'additional_video_evidence_may_help':extra_possible,
            'localization_independently_verified':False,'person_identity_verified':False,
            'note':'CESA checks model-text/source consistency. It cannot certify pixel truth, identity, or independent human correctness.'}


def legacy_support_facts(observations,known,judgment_refs):
    """Derive only what old text actually states, retaining the exact source sentence.

    No source labels, reference answers, new pixels, detected boxes or identities.
    Paired-view localization remains explicitly unverified during offline replay.
    """
    result=[]
    for item in observations.get('observed_actions',[]):
        text=item['action'];refs=[r for r in item['evidence_refs'] if r in known and r in judgment_refs]
        if not refs:continue
        # Each instant stands alone; do not invent same-person tracking between frames.
        for ref in refs:
            t=known[ref]['global_time']
            result.append(VisualSupportFact(observed_action=text,subject_ref=text[:200],
                subject_scope='multiple_unverified_people' if re.search(r'\b(?:multiple|several|two|people)\b|多人',text,re.I) else 'one_unverified_person',
                temporal_scope='sampled_instants',global_time_window=[t,t],evidence_refs=[ref],
                relation_to_target='uncertain',view='unknown'))
    return result
