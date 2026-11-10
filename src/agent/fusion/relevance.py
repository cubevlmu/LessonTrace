"""Explainable intent ranking. Source validity is separate from relevance."""
from .evidence import version

CONFLICTS={'teacher_visual_conflict','candidate_teacher_difference','human_attribution_conflict'}
GLOBAL={'cognitive_overreach','identity_missing','missing_video_coverage','clip_not_complete_lesson'}


def evidence_relevance(e,plan):
    p=e['provenance'];kind=e['evidence_type'];reasons=[]
    selected_context=kind in {'teacher_plan','teacher_report'} and p.get('document_id') in plan.document_ids
    remembered=kind=='teacher_report' and p.get('source_kind')=='classroom_memory' and plan.use_classroom_memory
    if kind not in plan.evidence_types and not((selected_context or remembered) and plan.intent!='cognitive_boundary'):return 0,['not_required_for_query_intent']
    if plan.intent=='teaching_plan' and (kind=='teacher_report' or (kind=='teaching_activity' and p['activity_status']!='planned')):
        return 0,['reported_actual_not_requested_plan']
    if plan.intent=='verified_person' and not plan.subject_id and not plan.track_id and kind in {'person_track','event_attribution','vlm_observation'}:
        return 0,['identity_must_be_selected_before_person_behavior_query']
    if e['subject_ref'] and plan.track_id and e['subject_ref'].startswith('track:') and not e['subject_ref'].endswith(':'+str(plan.track_id)):
        return 0,['different_track']
    if e['time_scope'] and plan.effective_time_range and max(e['time_scope'][0],plan.effective_time_range[0])>min(e['time_scope'][1],plan.effective_time_range[1]):return 0,['outside_query_time']
    if kind=='knowledge_rule':
        rule=p['rule'];rid=rule['rule_id']
        if plan.focus=='alignment_audit':return (100,['source_version_and_alignment_policy']) if rid=='evidence:alignment_required' else (0,['unrelated_rule'])
        if plan.intent=='cognitive_boundary':return (100,['cognitive_boundary_policy']) if rid=='evidence:no_mental_state' else (0,['unrelated_rule'])
        if rule.get('action') in plan.requested_actions:return 90,['explicit_requested_action_definition']
        wanted={'track_timeline':{'evidence:no_identity_from_text'},'verified_person':{'evidence:no_identity_from_text','evidence:no_group_to_person'},
            'teaching_plan':{'evidence:plan_not_fact','evidence:alignment_required'},'context_relation':{'evidence:plan_not_fact','evidence:alignment_required','evidence:independent_sources'},
            'observations':{'evidence:no_upgrade'},'summary':{'evidence:no_upgrade','evidence:plan_not_fact'},'report':{'evidence:no_upgrade','evidence:plan_not_fact','evidence:alignment_required'},
            'conflicts':{'evidence:independent_sources'},'review_todo':{'evidence:no_upgrade'}}
        return (45,['intent_boundary_rule']) if rid in wanted.get(plan.intent,set()) else (0,['unrelated_rule'])
    if plan.requested_actions and kind in {'dataset_annotation','vlm_observation','human_visual_review','event_attribution','model_discovered_event','human_confirmed_event'} and p.get('action') not in plan.requested_actions:
        return 0,['different_target_action']
    reasons.append('required_source_type_for_'+plan.intent)
    if plan.track_id:reasons.append('selected_track_scope')
    if e['time_scope']:reasons.append('intersects_requested_time')
    if p.get('action') in plan.requested_actions:reasons.append('requested_action')
    priorities={
        'person_track':110 if plan.intent in {'track_timeline','verified_person'} else 50,
        'identity_binding':100 if plan.intent=='verified_person' else 40,
        'teacher_plan':85,'teacher_report':90,'teaching_activity':100,
        'dataset_annotation':70,'vlm_observation':95,'human_visual_review':100,'model_discovered_event':90,'human_confirmed_event':105,
        'event_attribution':105 if p.get('status')!='candidate' else 55}
    # Summaries/reports retain people, but ordinary region questions do not need all Tracks.
    return priorities.get(kind,30),reasons


def prepare(canonical,plan):
    registry=canonical['registry'];scores={};details={}
    for eid,e in registry.items():scores[eid],details[eid]=evidence_relevance(e,plan)
    for f in canonical['facts']:
        refs=f['evidence_refs'];score=max((scores.get(r,0) for r in refs),default=0)
        f.update(relevance_score=score,relevance_reason=list(dict.fromkeys(s for r in refs for s in details.get(r,[]))))
    catalog={}
    for r in canonical['risks']:
        refs=list(dict.fromkeys(r['evidence_refs']));es=[registry[x] for x in refs if x in registry]
        events=sorted({e['provenance'].get('event_id',e['source_id'] if e['evidence_type']=='dataset_annotation' else '') for e in es}- {''})
        groups=events or sorted({e['subject_ref'] or e['source_id'] for e in es})
        kinds=sorted({e['evidence_type'] for e in es})
        key=version({'kind':r['kind'],'groups':groups,'source_kinds':kinds})[:24]
        related=r['kind'] in GLOBAL or any(scores.get(x,0)>0 for x in refs)
        if plan.intent=='track_timeline' and r['kind'] not in {'track_visibility_gap','missing_video_coverage'}:related=False
        if plan.intent=='cognitive_boundary':related=r['kind'] in {'cognitive_overreach','missing_video_coverage'}
        if plan.focus=='behavior_definition':related=r['kind']=='missing_video_coverage'
        if plan.focus in {'alignment_audit','identity_basis'}:related=r['kind'] in {'missing_video_coverage','identity_missing'}
        if plan.intent=='teaching_plan' and r['kind'] in CONFLICTS:related=False
        states=[e['provenance'].get('review_status','unreviewed') for e in es]
        status='unreviewed' if not states or any(s in {'unreviewed','partially_reviewed'} for s in states) else ('revoked' if all(s=='revoked' for s in states) else 'resolved')
        if r['kind'] in CONFLICTS:status='unreviewed'
        blocking=related and r['kind'] in (CONFLICTS|GLOBAL|{'teaching_time_unaligned','actor_unconfirmed','multiple_possible_actors','track_visibility_gap','visual_evidence_insufficient','conservative_confirmation_override'})
        item={**r,'evidence_refs':refs,'review_id':'risk_'+key,'relevance_reason':'blocks_or_limits_current_question' if blocking else ('related_source_caution' if related else 'general_todo_not_required_for_this_question'),
            'severity':'high' if blocking else 'normal','blocking_current_answer':bool(blocking and status=='unreviewed'),'review_status':status,'relevant':related}
        if key in catalog:
            catalog[key]['evidence_refs']=list(dict.fromkeys(catalog[key]['evidence_refs']+refs))
            if status=='unreviewed':catalog[key].update(review_status='unreviewed',blocking_current_answer=blocking)
        else:catalog[key]=item
    risks=list(catalog.values());relevant=[r for r in risks if r['relevant'] and r['review_status']=='unreviewed']
    important=[r for r in relevant if r['kind'] in CONFLICTS]
    mandatory={f['fact_id'] for f in canonical['facts'] if any(set(f['evidence_refs'])&set(r['evidence_refs']) for r in important)}
    eligible={f['fact_id'] for f in canonical['facts'] if f['relevance_score']>0}|mandatory
    # Resolve both sides of a conflict even if normal source preferences exclude one.
    canonical.update(query_plan=plan.model_dump(),risk_catalog=risks,relevant_risks=relevant,
        eligible_fact_ids=sorted(eligible),mandatory_fact_ids=sorted(mandatory),
        evidence_scores={r:{'score':scores[r],'reasons':details[r]} for r in scores})
    return canonical


def select(canonical,accepted=()):
    facts={f['fact_id']:f for f in canonical['facts']}
    if 'eligible_fact_ids' not in canonical:return list(facts.values()),[]
    eligible=[facts[k] for k in canonical['eligible_fact_ids']]
    mandatory=set(canonical['mandatory_fact_ids']);accepted=set(accepted)
    ranked=sorted(eligible,key=lambda f:(f['fact_id'] not in mandatory,-f['relevance_score'],f['fact_id'] not in accepted,f['fact_id']))
    selected=[];others=[];seen=set()
    # Diversity across roles/actions/subjects prevents a dozen similar track/model rows
    # crowding out the actual plan, contradictory observation or boundary policy.
    for f in ranked:
        if f in selected:continue
        e=canonical['registry'][f['evidence_refs'][0]];p=e['provenance']
        role=(e['evidence_type'],p.get('action'),f['subject_ref'] if f['category']=='people' else e['source_id'] if e['evidence_type']=='vlm_observation' else None)
        if role not in seen:others.append(f);seen.add(role)
    others +=[f for f in ranked if f not in selected and f not in others]
    limit=8
    if canonical['query_plan']['intent'] in {'report','summary'}:
        for category in ['behaviors','activities','people','limits']:
            representative=next((f for f in others if f['category']==category),None)
            if representative and representative not in selected:selected.append(representative)
    # Mandatory conflict facts lead the ranking. Overflow stays in additional
    # evidence, with BOTH sides always pinned by the visible conflict ledger.
    for f in others:
        if f in selected:continue
        if len(selected)<limit:selected.append(f)
    additional=[f for f in ranked if f not in selected]
    return selected,additional
