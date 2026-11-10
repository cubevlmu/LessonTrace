"""Deterministic context extension of the existing controlled review orchestrator."""
import hashlib
import json
import re
from pathlib import Path
from src.identity.store import uid,now
from .knowledge import RELATIONS,digest
from .schemas import Question


def risks(bundle,request):
    result=[]
    def add(kind,reason):result.append({'kind':kind,'reason':reason,'new_visual_call_may_help':False})
    if any(a['alignment']['status']=='alignment_unknown' for a in bundle['activities']) or not bundle['activities']:add('teaching_time_unaligned','No current mapping for some/all teacher text.')
    if any(a['is_example'] for a in bundle['activities']):add('example_context_not_original_teacher','Example context cannot establish original classroom intentions.')
    if bundle['events']:add('unverified_dataset_labels','Original event labels do not independently establish visible actions or actors.')
    timeline=bundle['person_timeline']
    if request.track_id and not (timeline and timeline['confirmed_behavior_intervals']):add('behavior_subject_unconfirmed','No scoped human attribution supports a personal behavior fact.')
    if any(e['action'] in {'sleep','playphone','homework','chat','call','hubbub'} for e in bundle['events']):add('behavior_semantic_ambiguity','Retain the existing observable-evidence requirements; context cannot replace physical proof.')
    if bundle['passages']:add('untrusted_text_source','Teacher text is quoted source data, not system instructions or verified observation.')
    confirmed=timeline['confirmed_behavior_intervals'] if timeline else []
    if timeline:
        active=[b for b in timeline['human_history'] if b['effective_status'] in {'human_confirmed','rejected'}]
        for i,a in enumerate(active):
            if any(a['event_id']==b['event_id'] and a['effective_status']!=b['effective_status'] and max(a['start'],b['start'],bundle['start'])<=min(a['end'],b['end'],bundle['end']) for b in active[i+1:]):
                add('human_attribution_conflict','Independent human actor records disagree in an overlapping scope; do not silently select one.')
    for a in bundle['activities']:
        if a['alignment']['status']!='verified_mapping':continue
        for b in confirmed:
            action=b['payload']['target_action'];win=a['alignment']['mapped_global_window']
            refs=[f for f in b['payload']['evidence_refs'] if max(win[0],bundle['start'])<=f['global_time']<=min(win[1],bundle['end'])]
            if refs and a['action_claims'].get(action)=='reported_absent':add('teacher_visual_conflict','Teacher reports absence while a scoped human video record supports the action; retain both sources.')
            elif refs and a['action_claims'] and action not in a['action_claims']:add('possible_plan_observation_difference','Different action labels do not prove misconduct or task nonparticipation.')
    if re.search(r'专注|注意力|学习效果|认知|投入|情绪|attention|engagement|learning outcome',request.question,re.I):add('cognitive_overreach','Plans and external actions cannot establish attention, cognition or learning outcomes.')
    return result


def arbitrate(claim,risk_list):
    c=dict(claim)
    if c['kind']=='text_plan':c['status']='supported'
    elif c['kind']=='human_attribution':c['status']='supported'
    elif c['kind']=='human_rejected_attribution':c['status']='contradicted'
    elif c['kind']=='context_relation':
        c['status']='uncertain' if any(r['kind'] in {'teacher_visual_conflict','cognitive_overreach','example_context_not_original_teacher'} for r in risk_list) else 'unknown'
    else:c['status']='uncertain'
    if any(r['kind']=='cognitive_overreach' for r in risk_list) and c['kind']=='context_relation':c['status']='unknown'
    if c['kind'] in {'human_attribution','human_rejected_attribution'} and any(r['kind']=='human_attribution_conflict' for r in risk_list):
        c['status']='uncertain';c['dimensions']={**c['dimensions'],'visual':'uncertain','attribution':'uncertain'}
    return c


def guard(claim,registry):
    c=dict(claim)
    if not c['source_refs'] or not all(r in registry for r in c['source_refs']):
        c.update(status='unknown',text='缺少当前可访问的证据引用，无法形成可靠声明。',guardrail='missing_source_reference')
    return c


class ContextAnalysis:
    def __init__(self,agent,service):self.agent,self.service=agent,service

    def external_version(self,request):
        source=self.service.source
        paths=[source.registry_db,source.tracking_db,self.service.attribution_repo.path]
        if self.service.identity_dir:paths.append(Path(self.service.identity_dir)/'identity.db')
        h=hashlib.sha256()
        for p in paths:
            h.update(p.name.encode())
            if p.is_file():h.update(p.read_bytes())
            else:h.update(b'absent')
        # Cached video/identity claims must also react to source-file changes and
        # natural consent expiry, even when no SQLite bytes have changed.
        from src.tracking.video import safe_path,sha256
        from src.attribution.source import IdentityReader
        try:
            run,clip=source.run(request.run_id)
            actual=sha256(safe_path(clip,source.video_root));h.update(actual.encode())
            if request.include_identity and request.track_id:
                labels=IdentityReader(self.service.identity_dir).intersect(request.run_id,request.track_id,
                    clip['start'] if request.start is None else request.start,clip['end'] if request.end is None else request.end,actual)
                h.update(json.dumps(labels,sort_keys=True).encode())
        except (ValueError,OSError):h.update(b'inaccessible_or_changed_source')
        h.update(digest().encode());return h.hexdigest()

    def ask(self,body,actor):
        request=Question.model_validate(body);bundle=self.agent.miner.retrieve_context(request,self.service,actor)
        registry={};claims=[]
        for p in bundle['passages']:
            d=bundle['documents'][p['document_id']]
            registry[p['passage_id']]={'kind':'text','document_id':p['document_id'],'passage_id':p['passage_id'],'text':p['content'],
                'page_or_slide':p['page_or_slide'],'source_start':p['source_start'],'source_end':p['source_end'],
                'source_kind':d['source_type'],'is_example':bool(d['is_example']),'source_status':'project_example' if d['is_example'] else 'uploader_declared',
                'href':'/context?document_id='+p['document_id']+'&passage_id='+p['passage_id']}
        clip=bundle['clip'];start,end=bundle['start'],bundle['end']
        for e in bundle['events']:
            t=max(start,e['t_start']);ref='video:'+e['event_id']
            registry[ref]={'kind':'video','event_id':e['event_id'],'run_id':request.run_id,'clip_id':clip['clip_id'],'global_time':t,'local_time':t-clip['start'],
                           'source_kind':'dataset_annotation','href':'/lesson-review?run_id='+request.run_id+'&global_time='+str(t),'action_claim':e['action']}
        intent=request.intent or ('person' if request.track_id else ('arrangement' if re.search(r'安排|计划|教案',request.question) and not re.search(r'关系|行为|观察',request.question) else 'relation'))
        for p in bundle['passages'][:6]:
            if any(a['source_passage_id']==p['passage_id'] for a in bundle['activities']):continue
            claims.append({'claim_id':uid(),'kind':'text_plan','claim_scope':'source_text_only_not_video_fact',
                'text':'未建立时间对应的资料原文（不是视频事实）：'+p['content'][:200],'source_refs':[p['passage_id']],
                'dimensions':{'visual':'unknown','identity':'unknown','attribution':'unknown','text_source':registry[p['passage_id']]['source_status'],'alignment':'alignment_unknown'}})
        if intent in {'arrangement','relation','person'}:
            for a in bundle['activities']:
                if a['source_passage_id'] not in registry:continue
                claims.append({'claim_id':uid(),'kind':'text_plan','text':('项目示例资料' if a['is_example'] else '教师资料')+'的人工整理活动（请核对所引原文）：'+a['description'],
                    'claim_scope':'source_text_only_not_video_fact',
                    'source_refs':[a['source_passage_id']],'dimensions':{'visual':'unknown','identity':'unknown','attribution':'unknown','text_source':a['source_status'],
                    'alignment':a['alignment']['status']},'activity_id':a['activity_id'],'activity_status':a['status'],'temporal_mapping':a['alignment']})
        if intent!='arrangement':
            candidates=bundle['events']
            if request.track_id:
                ids={c['event_id'] for c in bundle['person_timeline']['candidate_behaviors']};candidates=[e for e in candidates if e['event_id'] in ids]
            for e in candidates[:40]:
                vs=[r for r in bundle['region_visual_reviews'].get(e['event_id'],[]) if r['clip_id']==clip['clip_id']]
                model_state=vs[-1]['result'].get('final_decision',vs[-1]['result'].get('decision')) if vs else 'not_reviewed'
                actor_reviews=[]
                if bundle['person_timeline']:actor_reviews=[r['final_decision'] for r in bundle['person_timeline']['visual_reviews'] if r['event_id']==e['event_id']]
                claims.append({'claim_id':uid(),'kind':'dataset_candidate','text':f"原始数据集有 [{e['action']}] 候选声明；不能据此认定目标人物执行。",'source_refs':['video:'+e['event_id']],
                    'dimensions':{'visual':'uncertain','identity':'unknown','attribution':'unknown','text_source':'not_applicable','alignment':'not_applicable'},
                    'region_model_state':model_state,'actor_model_states':actor_reviews,'event_id':e['event_id']})
        if request.track_id:
            for b in bundle['person_timeline']['confirmed_behavior_intervals']:
                refs=[f for f in b['payload']['evidence_refs'] if start<=f['global_time']<=end]
                if not refs:continue
                ref='human:'+b['binding_id'];registry[ref]={'kind':'human_attribution','binding_id':b['binding_id'],'run_id':request.run_id,'track_id':request.track_id,
                    'evidence_refs':refs,'source_kind':'human_event_actor_review','href':'/person-review?run_id='+request.run_id+'&track_id='+str(request.track_id)}
                identities=[i for i in b['identities'] if any(i['start']<=f['global_time']<=i['end'] for f in refs)]
                claims.append({'claim_id':uid(),'kind':'human_attribution','text':f"独立人工记录支持匿名 Track {request.track_id} 在复核证据时刻执行 [{b['payload']['target_action']}]。",'source_refs':[ref],
                    'dimensions':{'visual':'supported','identity':'supported' if identities else 'unknown','attribution':'supported','text_source':'not_applicable','alignment':'not_applicable'},'identities':identities})
            for b in bundle['person_timeline']['human_history']:
                if b['effective_status']!='rejected':continue
                refs=[f for f in b['payload']['evidence_refs'] if start<=f['global_time']<=end]
                if not refs:continue
                ref='human_rejected:'+b['binding_id'];registry[ref]={'kind':'human_attribution','binding_id':b['binding_id'],'evidence_refs':refs,'source_kind':'human_event_actor_review',
                    'href':'/person-review?run_id='+request.run_id+'&track_id='+str(request.track_id)}
                claims.append({'claim_id':uid(),'kind':'human_rejected_attribution','text':'人工记录驳回了此匿名Track在其复核范围内执行目标的归属声明；不表示画面中没有该行为。','source_refs':[ref],
                    'dimensions':{'visual':'unknown','identity':'unknown','attribution':'contradicted','text_source':'not_applicable','alignment':'not_applicable'}})
        risks_found=self.agent.critic.review_context(bundle,request)
        relations=[]
        for a in bundle['activities']:
            for e in bundle['events']:
                mapping=a['alignment'];window=mapping.get('mapped_global_window')
                temporal=mapping['status']=='verified_mapping' and max(window[0],e['t_start'],start)<=min(window[1],e['t_end'],end)
                if mapping['status']=='outside_clip_coverage':continue
                potential=e['action'] in RELATIONS.get(a['activity_type'],[]) or a['action_claims'].get(e['action'])=='expected'
                supported=False
                if temporal and bundle['person_timeline']:
                    supported=any(b['event_id']==e['event_id'] and any(max(window[0],start)<=f['global_time']<=min(window[1],end) for f in b['payload']['evidence_refs']) for b in bundle['person_timeline']['confirmed_behavior_intervals'])
                relations.append({'event_id':e['event_id'],'activity_id':a['activity_id'],'source_passage_id':a['source_passage_id'],
                    'action_claim':e['action'],'time_relation':'verified_mapping_overlap' if temporal else 'alignment_unknown',
                    'background_compatibility':'potentially_compatible_not_observed_participation' if temporal and potential else 'unable_to_judge',
                    'visual_status':'supported' if supported else 'uncertain','actor_status':'supported' if supported else 'unknown','teaching_participation_status':'unknown','is_example':a['is_example']})
        relation_refs=[p['passage_id'] for p in bundle['passages'][:3]]+[r for r,v in registry.items() if v['kind'] in {'video','human_attribution'}][:3]
        if intent!='arrangement' and relation_refs:
            claims.append({'claim_id':uid(),'kind':'context_relation','text':'教学资料只能提供背景，不能替代动作、人物归属或认知状态的事实证据。',
                'source_refs':relation_refs,'dimensions':{'visual':'uncertain','identity':'unknown','attribution':'unknown','text_source':'uploader_declared',
                'alignment':'verified_mapping' if any(a['alignment']['status']=='verified_mapping' for a in bundle['activities']) else 'alignment_unknown'}})
        adjudicated=[self.agent.guardrail.validate_context(self.agent.arbiter.arbitrate_context(c,risks_found),registry) for c in claims]
        aligned=any(a['alignment']['status']=='verified_mapping' for a in bundle['activities'])
        answer=('已按当前锚点换算资料中的计划时段；这不证明活动实际发生。' if aligned else '没有有效时间锚点，无法确定具体视频时段对应哪项教学活动；可查看未对齐的资料背景。')
        if any(a['is_example'] for a in bundle['activities']):answer+=' 当前资料为项目示例，不代表原授课教师的真实安排。'
        if request.track_id and not any(c['kind']=='human_attribution' for c in adjudicated):answer+=' 当前人物行为归属尚未人工确认，不能形成已证实的个人行为报告。'
        if any(r['kind']=='cognitive_overreach' for r in risks_found):answer+=' 无法据此判断专注、认知投入或学习效果。'
        final_state='unknown' if any(r['kind']=='cognitive_overreach' for r in risks_found) else ('supported' if intent=='arrangement' and aligned else ('uncertain' if claims else 'unknown'))
        result={'analysis_id':uid(),'created_at':now(),'answer':answer,'status':'current','intent':intent,'claims':adjudicated,'evidence_registry':registry,
            'analysis_statement_status':final_state,'final_status_scope':'Only cited source/mapping statements; never an overall classroom/student score.',
            'activities':bundle['activities'],'risks':risks_found,'run_id':request.run_id,'clip_id':clip['clip_id'],'global_offset':clip['start'],
            'cross_modal_relations':relations,
            'trace':{'miner':{'events':len(bundle['events']),'tracks':len(bundle['tracks']),'passages':len(bundle['passages']),'parameterized_queries':True},
                     'analyzer':{'candidate_claims':len(claims),'method':'bounded_source_templates_not_document_instructions'},'critic':risks_found,
                     'verifier':{'status':'existing_evidence_only','new_vlm_calls':0,'reason':'Existing uncertainty/context or actor gaps are not fixed by more unrequested model calls. Use existing 11C review for independent visual follow-up.'},
                     'arbiter':{'states':['supported','uncertain','contradicted','unknown'],'dimension_states_kept_separate':True}},
            'knowledge_version':digest(),'dependency_document_ids':list({v['document_id'] for v in registry.values() if v['kind']=='text'})}
        with self.service.repo.connection() as c:
            c.execute('INSERT INTO context_analyses VALUES (?,?,?,?,?,?,?)',(result['analysis_id'],actor,now(),self.service.repo.revision(),self.external_version(request),json.dumps(request.model_dump()),json.dumps(result,ensure_ascii=False)))
        return result

    def load(self,aid,actor):
        with self.service.repo.connection() as c:r=c.execute('SELECT * FROM context_analyses WHERE analysis_id=?',(aid,)).fetchone()
        if not r:raise ValueError('analysis_not_found_or_purged')
        if r['owner']!=actor:raise PermissionError('analysis_access_denied')
        request=Question.model_validate_json(r['request'])
        if r['revision']!=self.service.repo.revision() or r['external_version']!=self.external_version(request):return {'analysis_id':aid,'status':'stale','answer':None,'claims':[],'reason':'Sources, permissions, alignment, identity or behavior bindings changed; recompute required.'}
        result=json.loads(r['result'])
        for did in result['dependency_document_ids']:self.service.repo.document(did,actor)
        return result
