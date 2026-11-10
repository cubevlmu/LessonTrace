"""Render cited observations as observations, independently of taxonomy decisions."""
import re


def readable_action(text):
    # Translate complete elementary clauses only. Negation, modality and unknown
    # grammar stay verbatim, rather than silently becoming positive assertions.
    phrases={'writing':'书写','standing':'站立','sitting':'坐着','sitting at his desk':'坐在课桌前',
        'sitting at her desk':'坐在课桌前','looking down at his book':'低头看向书本',
        'looking down at her book':'低头看向书本','looking down at a book':'低头看向书本',
        'looking up':'抬头','touching the chin':'触摸下巴','touching the side of the head':'触摸头部',
        'holding a phone':'手持手机','looking at a phone':'看向手机','leaning forward':'身体前倾'}
    original=text.strip();key=original.rstrip('.').lower()
    if key in phrases:return phrases[key]
    match=re.fullmatch(r'(the boy|the girl|the target|the target person|the person|the student) is (.+)',key)
    if not match:return original
    clauses=re.split(r',\s*| and ',match[2])
    if clauses and all(part in phrases for part in clauses):
        return '该人物'+ '，'.join(phrases[part] for part in clauses)
    return original


def observation_summary(claims,evidence,limit=3):
    cited={ref for claim in claims for ref in claim['evidence_refs']}
    rows=[];seen=set()
    for e in evidence:
        if e['evidence_id'] not in cited or e['evidence_type']!='vlm_observation':continue
        p=e['provenance']
        if not p.get('sampled_frames_valid') or not p.get('frame_refs') or not e.get('time_scope'):continue
        if e.get('verification_status') in {'error','rejected','contradicted'}:continue
        # No subject is assigned from a window/filename: preserve the evidence's
        # explicitly established scope, including an unknown actor.
        text=readable_action(e['content'])
        unavailable=any(re.search(r'\btarget\s+is\s+not\s+visible\b|目标(?:人物)?(?:在画面中)?不可见',v,re.I) for v in [e['content'],*p.get('observation_limitations',[])])
        if unavailable:text='该次取证未能看清目标行为，不能据此判断人物缺席'
        key=(text,tuple(e['time_scope']),e.get('subject_ref'),p.get('track_id'),p.get('segment_id'))
        if key in seen:continue
        seen.add(key);t=e['time_scope'];when=f'{t[0]:g}秒' if t[0]==t[1] else f'{t[0]:g}–{t[1]:g}秒'
        rows.append({'text':text,'time_label':when,'evidence_refs':[e['evidence_id']],'subject_ref':e.get('subject_ref'),
            'source_label':'AI画面记录','recognition_state':text,'reliability':'中' if e['verification_status']=='supported' else '低',
            'reliability_basis':'模型与证据检查提示，不是实测准确率。','verification_status':e['verification_status'],'observation_available':not unavailable,'track_scope':p.get('track_id'),'segment_scope':p.get('segment_id')})
    grouped={}
    for row in rows:
        key=(row['text'],row['subject_ref'],row['track_scope'],row['segment_scope'])
        current=grouped.setdefault(key,{**row,'observed_times':[],'evidence_refs':[]})
        current['observed_times'].append(row['time_label']);current['evidence_refs']+=row['evidence_refs']
    result=[]
    for row in grouped.values():
        row['observed_times']=list(dict.fromkeys(row['observed_times']));row['sample_count']=len(row['observed_times'])
        row['time_label']='、'.join(row['observed_times']);row['evidence_refs']=list(dict.fromkeys(row['evidence_refs']))
        row['observation_basis']='多时刻观察' if row['sample_count']>1 else '单次画面观察'
        row['classification_note']='行为类别与执行者仍需按各条原始裁决核对；多次观察不是校准准确率。'
        result.append(row)
    return result[:limit]
