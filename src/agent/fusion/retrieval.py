"""Unified read-only adapters over 11A-D, with typed evidence and exact citations."""
import json
import hashlib
from pathlib import Path
from src.context.schemas import Question
from src.context.retrieval import multimedia,ReadOnlyEvidence
from src.attribution.service import AttributionService
from src.attribution.source import IdentityReader
from src.attribution.schemas import ActorJudgment
from src.attribution.visual import subject_gate
from src.agent.visual_evidence_policy import decide,semantic_findings
from .evidence import Evidence,version


def actor_alignment(record,clip,allowed_root):
    """Recheck confirmed history using CESA; no model invocation or legacy promotion."""
    if record.get('final_decision')!='confirmed':return {'passed':False,'reason':'historical_final_not_confirmed'}
    try:
        batch=record['rounds'][-1];judgment=ActorJudgment.model_validate(batch['judgment'])
        frames=[f for f in record['frames'] if f['frame_id'] in batch['frame_ids']]
        root=Path(allowed_root).resolve()
        for frame in frames:
            for view in record.get('model_input_views',['input_full','input_roi']):
                path=Path(frame['files'][view]).resolve()
                if root not in path.parents or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=frame['sha256'][view]:
                    return {'passed':False,'reason':'model_frame_missing_or_changed'}
        findings=[p for text in [judgment.reason,*[f.observed_action for f in judgment.support_facts]] for p in semantic_findings(text)]
        outcome=decide(judgment,record['target_action'],{'requested_window':[record['request']['start'],record['request']['end']],
            'playable_window':[clip['start'],clip['end']],'sampling_exhaustive':False},frames,findings,
            {'frame_size':[frames[0]['width'],frames[0]['height']],'input_views':['input_full','input_roi'],'observations':batch['observations']})
        gate=subject_gate(judgment,frames)
        return {'passed':outcome['decision']=='confirmed' and gate['passed'],'cesa':outcome,'subject_checks':gate}
    except (KeyError,ValueError,IndexError,TypeError) as e:return {'passed':False,'reason':'legacy_or_invalid_structured_evidence:'+type(e).__name__}


def retrieve(service,plan,actor,allow_identity=False):
    if plan.effective_time_range is None:return {'evidence':[],'bundle':None,'timelines':{},'rules':[],'subject_missing':bool(plan.subject_id)}
    if plan.roster_subject_id:
        from .roster_scope import retrieve_roster
        return retrieve_roster(service,plan,actor,allow_identity,retrieve)
    a,b=plan.effective_time_range
    request=Question(run_id=plan.run_id,lesson_id=plan.lesson_id,document_ids=plan.document_ids,question=plan.user_question,track_id=plan.track_id,start=a,end=b,include_identity=allow_identity,roster_subject_id=plan.roster_subject_id,material_mode=plan.material_mode,use_classroom_memory=plan.use_classroom_memory)
    bundle=multimedia(service,request,actor);clip=bundle['clip'];evidence=[]
    def add(eid,kind,sid,content,verification='uncertain',subject=None,win=None,**provenance):
        evidence.append(Evidence(evidence_id=eid,evidence_type=kind,source_id=sid,subject_ref=subject,time_scope=win,content=content,
            verification_status=verification,provenance=provenance,source_version=version({'content':content,'status':verification,'scope':win,'source':provenance})).model_dump())
    for e in bundle['events']:
        lo,hi=max(a,e['t_start']),min(b,e['t_end'])
        add('dataset:'+e['event_id'],'dataset_annotation',e['event_id'],e['action'],win=[lo,hi],source_kind='dataset_annotation',
            excel_row=e['excel_row'],region_kind=e['region_kind'],region_xywh=e['region_xywh'],frame_size=[e['frame_width'],e['frame_height']],
            action=e['action'],run_id=plan.run_id,clip_id=clip['clip_id'],global_offset=clip['start'],scope='annotation_claim_only')
        db=ReadOnlyEvidence(service.source.registry_db)
        with db._get_connection() as c:has_reviews=c.execute("SELECT 1 FROM sqlite_master WHERE name='region_reviews'").fetchone()
        if has_reviews:
            for r in db.history(e['event_id']):
                if r['clip_id']!=clip['clip_id'] or not a<=r['global_time']<=b:continue
                add('human_visual:'+r['review_id'],'human_visual_review',r['review_id'],r['decision'],
                    'supported' if r['decision']=='confirmed' else ('contradicted' if r['decision']=='rejected' else 'uncertain'),
                    win=[r['global_time'],r['global_time']],action=e['action'],global_offset=clip['start'],scope='human_region_point_review_no_track_identity')
        for r in bundle['region_visual_reviews'].get(e['event_id'],[]):
            if r['clip_id']!=clip['clip_id']:continue
            frames=[f for f in (r.get('evidence') or {}).get('frames',[]) if a<=f['global_time']<=b]
            if not frames:continue
            add('region_vlm:'+r['review_id'],'vlm_observation',r['review_id'],r['result'].get('reason',''),
                win=[min(f['global_time'] for f in frames),max(f['global_time'] for f in frames)],
                final_decision=r['result'].get('final_decision',r['result'].get('decision')),action=e['action'],
                scope='region_model_report_not_person_attribution',event_id=e['event_id'],raw_model_decision=r['result'].get('raw_model_decision'),frame_refs=[f['frame_id'] for f in frames])
    tracks=[t for t in bundle['tracks'] if not plan.track_id or t['track_id']==plan.track_id]
    attr=AttributionService(service.source,service.attribution_repo,service.identity_dir);timelines={};subject_found=False;named_intervals=[]
    for t in tracks:
        observed=[o for o in service.source.observations(plan.run_id,t['track_id']) if a<=o['global_timestamp']<=b]
        observed_by_frame={o['frame_index']:o for o in observed}
        segments=[]
        for s in t['segments']:
            times=[o['global_timestamp'] for o in observed if s['global_start']<=o['global_timestamp']<=s['global_end']]
            if times:segments.append([min(times),max(times)])
        if not segments:continue
        subject='track:'+plan.run_id+':'+str(t['track_id'])
        timeline=attr.timeline(plan.run_id,t['track_id'],allow_identity)
        identities=IdentityReader(service.identity_dir).intersect(plan.run_id,t['track_id'],a,b,bundle['run']['metadata']['source_sha256']) if allow_identity else []
        if plan.subject_id:
            identities=[i for i in identities if i['subject_id']==plan.subject_id]
            if not identities:continue
            subject_found=True
            named_intervals.extend((t['track_id'],i) for i in identities)
        timelines[t['track_id']]=timeline
        add(subject,'person_track',str(t['track_id']),'匿名轨迹的实际检测观测与可见段','supported',subject,[segments[0][0],segments[-1][1]],
            run_id=plan.run_id,track_id=t['track_id'],visible_segments=segments,gaps=timeline['gaps'],observed_frame_count=len(observed),
            global_offset=clip['start'],scope='observed_detection_segments_not_verified_identity',full_track_continuity='unverified')
        for i in identities:
            add('identity:'+i['identity_binding_id'],'identity_binding',i['identity_binding_id'],'当前授权的人物关联区间','supported',
                'subject:'+i['subject_id'],[i['start'],i['end']],track_ref=subject,subject_id=i['subject_id'],scope='authorized_identity_interval_only')
        for c in timeline['candidate_behaviors']:
            if c['end']<a or c['start']>b:continue
            history=[h for h in timeline['human_history'] if h['candidate_id']==c['candidate_id'] and h['start']<=max(a,c['start']) and h['end']>=min(b,c['end'])]
            closed=[h for h in history if h['effective_status'] in {'human_confirmed','rejected','revoked'}]
            reviewed='resolved' if any(h['effective_status'] in {'human_confirmed','rejected'} for h in closed) else ('revoked' if closed else 'unreviewed')
            add('candidate:'+c['candidate_id'],'event_attribution',c['candidate_id'],c['target_action'],subject=subject,win=[max(a,c['start']),min(b,c['end'])],
                status='candidate',review_status=reviewed,event_id=c['event_id'],action=c['target_action'],candidate_count=c['candidate_count'],ambiguity_flags=c['ambiguity_flags'],scope='spatiotemporal_candidate_not_actor_proof')
        for h in timeline['human_history']:
            refs=[f for f in h['payload']['evidence_refs'] if a<=f['global_time']<=b and f['frame_index'] in observed_by_frame and abs(observed_by_frame[f['frame_index']]['global_timestamp']-f['global_time'])<=.002]
            if not refs or h['effective_status'] not in {'human_confirmed','rejected','unknown'}:continue
            status=h['effective_status'];named=[]
            if status=='human_confirmed':
                for item in timeline['confirmed_behavior_intervals']:
                    if item['binding_id']!=h['binding_id']:continue
                    for identity in item['identities']:
                        if plan.subject_id and identity['subject_id']!=plan.subject_id:continue
                        owned=[f['global_time'] for f in refs if identity['start']<=f['global_time']<=identity['end']]
                        if owned:
                            from src.validation.repository import track_scope
                            identity={**identity,'track_review':track_scope(service.repo,plan.run_id,t['track_id'],min(owned),max(owned),bundle['run']['metadata']['source_sha256'])}
                            named.append(identity)
            add('attribution:'+h['binding_id'],'event_attribution',h['binding_id'],h['payload']['observable_action'],
                'supported' if status=='human_confirmed' else ('contradicted' if status=='rejected' else 'uncertain'),subject,[min(f['global_time'] for f in refs),max(f['global_time'] for f in refs)],
                status=status,event_id=h['event_id'],review_status='resolved',action=h['payload']['target_action'],frame_refs=refs,identities=named,global_offset=clip['start'],scope='human_actor_review_at_cited_instants')
        for r in timeline['visual_reviews']:
            frames=[f for f in r['frames'] if a<=f['global_time']<=b]
            if not frames:continue
            alignment=actor_alignment(r,clip,service.attribution_repo.path.parent/'evidence')
            add('actor_vlm:'+r['review_id'],'vlm_observation',r['review_id'],r['reason'],'supported' if alignment['passed'] else 'uncertain',subject,
                [min(f['global_time'] for f in frames),max(f['global_time'] for f in frames)],action=r['target_action'],final_decision=r['final_decision'],
                raw_model_decision=r.get('raw_model_decision'),alignment_checks=alignment,frame_refs=[f['frame_id'] for f in frames],
                scope='model_observation_not_human_confirmation',model_id=r['model_id'],prompt_version=r['prompt_version'])
    from src.behavior_discovery.service import evidence as discovered_evidence
    if not plan.subject_id:evidence.extend(discovered_evidence(service,plan.run_id,a,b,plan.track_id))
    else:
        # Product discoveries have their own append-only human reviews. Restrict
        # each to the active identity interval instead of dropping all of them.
        for tid,identity in named_intervals:
            for item in discovered_evidence(service,plan.run_id,max(a,identity['start']),min(b,identity['end']),tid):
                if not item['time_scope']:continue
                if item['time_scope'][0]<identity['start'] or item['time_scope'][1]>identity['end']:continue
                item['evidence_id']+=':identity:'+identity['identity_binding_id']
                item['provenance']={**item['provenance'],'identity_binding_id':identity['identity_binding_id'],'subject_id':identity['subject_id'],'identity_interval':[identity['start'],identity['end']]}
                item['source_version']=version([item['source_version'],identity])
                evidence.append(item)
    for p in bundle['passages']:
        d=bundle['documents'][p['document_id']];kind='teacher_report' if d['source_type'] in {'teacher_observation','teacher_note','classroom_memory','student_work','graded_work','assessment_result','learning_material','other_material'} else 'teacher_plan'
        add('text:'+p['passage_id'],kind,p['passage_id'],p['content'],'supported',document_id=p['document_id'],is_example=bool(d['is_example']),
            page_or_slide=p['page_or_slide'],source_start=p['source_start'],source_end=p['source_end'],source_kind=d['source_type'],
            text_source_status='project_example' if d['is_example'] else 'uploader_declared',scope='source_text_content_only_not_video_fact')
        evidence[-1]['provenance'].update(file_hash=d['file_hash'],document_version=d['version'],parser_version=d['parser_version'])
        evidence[-1]['provenance'].update(title=d['title'],material_scope=d.get('scope',{}))
        evidence[-1]['source_version']=version(evidence[-1]['provenance']|{'content':evidence[-1]['content']})
    for activity in bundle['activities']:
        mapping=activity['alignment'];window=mapping.get('mapped_global_window') if mapping['status']=='verified_mapping' else None
        add('activity:'+activity['activity_id'],'teaching_activity',activity['activity_id'],activity['description'],'supported',win=window,
            passage_ref='text:'+activity['source_passage_id'],document_id=activity['document_id'],activity_type=activity['activity_type'],activity_status=activity['status'],
            action_claims=activity['action_claims'],alignment=mapping,is_example=activity['is_example'],scope='human_entered_plan_or_report_not_observed_activity')
    with service.repo.connection() as c:rules=[json.loads(r[0]) for r in c.execute('SELECT payload FROM knowledge_rules ORDER BY rule_id')]
    for rule in rules:
        add('rule:'+rule['rule_id'],'knowledge_rule',rule['rule_id'],rule.get('rule',rule.get('ambiguities','')),'supported',
            rule=rule,rule_version=rule['version'],scope='versioned_analysis_constraint_not_classroom_observation')
    from .relevance import evidence_relevance
    return {'product_owner':actor,'evidence':evidence,'query_evidence_ids':[e['evidence_id'] for e in evidence if evidence_relevance(e,plan)[0]>0],
        'bundle':bundle,'timelines':timelines,'rules':rules,'subject_missing':bool(plan.subject_id) and not subject_found}
