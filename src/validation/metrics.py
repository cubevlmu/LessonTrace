"""Only attested human judgments become scientific quality denominators."""
import json
from collections import Counter
from src.agent.fusion.evidence import version
from .evidence_chain import validate
from .schemas import ChainRequest


def ratio(values,target='yes'):
    decided=[v for v in values if v in {'yes','no'}]
    return {'status':'evaluable' if decided else 'not_evaluable','numerator':sum(v==target for v in decided),'denominator':len(decided),
        'uncertain_count':sum(v=='uncertain' for v in values),'value':sum(v==target for v in decided)/len(decided) if decided else None}


def latest(rows,key):
    result={}
    for row in rows:result[key(row)]=row
    return list(result.values())


def reference_integrity(wb,actor):
    from src.agent.fusion.reporting import historical
    runs={r['run_id'] for r in wb.track_tasks()};passages=set();catalog_missing=0;catalog_refs=0;report_access_failures=0
    for pair in wb.pairs(actor):
        for rid in pair['reports'].values():
            try:report=historical(wb.context,rid,actor,True)
            except (ValueError,PermissionError):report_access_failures+=1;continue
            runs.add(report['classroom']['run_id']);es=report.get('audit',{}).get('retrieved_evidence',report['evidence']);registered={e['evidence_id'] for e in es}
            for claim in report['claims']+report.get('additional_claims',[]):
                for ref in claim['evidence_refs']:catalog_refs+=1;catalog_missing+=ref not in registered
            for e in es:
                if e['evidence_type'] in {'teacher_plan','teacher_report'}:passages.add(e['source_id'])
    bad_media=0;bad_text=0
    for rid in runs:
        try:wb.context.source.verified_run(rid)
        except (ValueError,OSError):bad_media+=1
    for pid in passages:
        try:wb.context.repo.passage(pid,actor)
        except (ValueError,PermissionError):bad_text+=1
    return {'registered_media_checked':len(runs),'invalid_registered_media':bad_media,'unique_passages_checked':len(passages),'inaccessible_passages':bad_text,
        'catalog_citation_occurrences':catalog_refs,'missing_catalog_references':catalog_missing,'inaccessible_report_snapshots':report_access_failures,
        'total_access_failures':bad_media+bad_text+catalog_missing+report_access_failures,
        'scope':'Unique registered MP4 hashes, source passage access and saved-claim catalog references; not semantic correctness or exhaustive historical VLM-frame audit.'}


def evaluate(wb,actor):
    svc=wb.context
    with svc.repo.connection() as c:
        track=[dict(r) for r in c.execute('SELECT * FROM validation_track_reviews ORDER BY created_at,review_id')]
        grades=[dict(r) for r in c.execute('SELECT * FROM validation_qa_scores WHERE actor=? ORDER BY created_at,review_id',(actor,))]
        assessments=[dict(r) for r in c.execute('SELECT * FROM validation_actor_assessments ORDER BY created_at,review_id')]
        identities=[dict(r) for r in c.execute('SELECT * FROM validation_identity_assessments ORDER BY created_at,review_id')]
        approved=[dict(r) for r in c.execute('SELECT * FROM validation_question_approvals ORDER BY created_at,review_id')]
    tracks=latest(track,lambda r:(r['run_id'],r['track_id'],r['start'],r['end']))
    track_counts=Counter(r['decision'] for r in tracks)
    valid_grades=[];stale_grades=0
    from src.agent.fusion.reporting import historical
    for row in latest(grades,lambda r:r['report_id']):
        try:
            historical(svc,row['report_id'],actor,True)
            with svc.repo.connection() as c:payload=c.execute('SELECT payload FROM fusion_reports WHERE report_id=?',(row['report_id'],)).fetchone()[0]
            if version(payload)!=row['report_hash']:raise ValueError('graded_snapshot_changed')
        except (ValueError,PermissionError):stale_grades+=1;continue
        valid_grades.append(json.loads(row['payload']))
    active_subjects=[];bindings=[]
    if svc.identity_dir and (svc.identity_dir/'identity.db').exists():
        native=wb.identity();bindings=native.bindings()
        with native.store.connection() as c:
            for r in c.execute('SELECT subject_id FROM registered_subjects'):
                try:native.store.subject(r[0],c);active_subjects.append(r[0])
                except ValueError:pass
    with svc.attribution_repo.connection() as c:
        candidate_count=c.execute('SELECT COUNT(*) FROM event_track_candidates').fetchone()[0]
        native_bindings=[dict(r) for r in c.execute('SELECT * FROM event_track_bindings')]
    all_track_keys={(r['run_id'],r['track_id']) for r in svc.source.rows("SELECT p.run_id,p.track_id FROM person_tracks p JOIN tracking_runs r ON r.run_id=p.run_id WHERE r.status='completed'")}
    known_track_keys=set()
    from .repository import track_scope
    for b in bindings:
        if b['effective_status']!='human_confirmed':continue
        try:
            run,_=svc.source.verified_run(b['run_id'])
            if track_scope(svc.repo,b['run_id'],b['track_id'],b['start'],b['end'],run['metadata']['source_sha256'])['status']=='verified':known_track_keys.add((b['run_id'],b['track_id']))
        except (ValueError,OSError):pass
    assessed=latest(assessments,lambda r:r['candidate_id']);actor_labels=Counter(json.loads(r['payload'])['decision'] for r in assessed)
    wrong_actor=[json.loads(r['payload']) for r in assessed if json.loads(r['payload']).get('evaluated_binding_id')]
    identity_labels=Counter(json.loads(r['payload'])['judgment'] for r in latest(identities,lambda r:r['binding_id']))
    chain_checks=[];named_checks=[]
    for row in native_bindings:
        if row['status']!='human_confirmed':continue
        try:
            check=validate(svc,ChainRequest(run_id=row['run_id'],track_id=row['track_id'],start=row['start'],end=row['end'],event_binding_id=row['binding_id']),actor)
            chain_checks.append(check)
            for b in bindings:
                if b['effective_status']=='human_confirmed' and b['run_id']==row['run_id'] and b['track_id']==row['track_id'] and b['start']<=row['start']<=row['end']<=b['end']:
                    named_checks.append(validate(svc,ChainRequest(run_id=row['run_id'],track_id=row['track_id'],start=row['start'],end=row['end'],event_binding_id=row['binding_id'],subject_id=b['subject_id']),actor))
        except (ValueError,PermissionError,OSError):chain_checks.append({'complete_chain':False,'invalid_dependencies':[{'reason':'source_or_scope_inaccessible'}]})
    cases=wb.track_tasks();graded_questions={r['case_id'] for r in valid_grades}
    qa={k:ratio([r[k] for r in valid_grades]) for k in ['answers_the_question','key_facts_correct','source_support_valid','subject_scope_correct','time_scope_correct','uncertainty_appropriate']}
    references=reference_integrity(wb,actor)
    return {'tracking':{'legacy_tasks':len(cases),'reviewed_distinct_tracks':len({(r['run_id'],r['track_id']) for r in tracks}),
        'reviewed_scoped_records':len(tracks),'judgments':dict(track_counts),'id_switch_cases':track_counts['id_switch'],'fragmented_cases':track_counts['fragmented'],
        'uncertain_cases':track_counts['uncertain'],'denominator':'latest human record per run/track/reviewed interval; not tracking accuracy','accuracy':'not_evaluable'},
        'identity':{'authorized_active_subjects':len(active_subjects),'native_human_confirmed_intervals':sum(b['effective_status']=='human_confirmed' for b in bindings),
            'audited_intervals':sum(identity_labels.values()),'wrong_confirmations':{'status':'evaluable' if identity_labels else 'not_evaluable','count':identity_labels['wrong'] if identity_labels else None,'denominator':sum(identity_labels.values())},
            'unknown_native_bindings':sum(b['effective_status'] in {'unknown','needs_review'} for b in bindings),
            'tracks_without_any_current_reviewed_identity_interval':len(all_track_keys-known_track_keys),
            'track_namespace_denominator':len(all_track_keys),'not_unique_people_count':True,
            'automatic_face_matching_real_validation':'not_evaluable','note':'No authorized independent face test cohort; no recognition rate is fabricated.'},
        'attribution':{'candidates':candidate_count,'native_human_confirmed':sum(b['status']=='human_confirmed' for b in native_bindings),
            'assessed_candidates':len(assessed),'assessments':dict(actor_labels),'ambiguous':actor_labels['ambiguous'],
            'unreviewed_candidates':max(0,candidate_count-len(assessed)),
            'still_pending_candidates':max(0,candidate_count-len(assessed))+actor_labels['ambiguous']+actor_labels['uncertain'],
            'wrong_confirmed_attributions':{'status':'evaluable' if wrong_actor else 'not_evaluable','count':sum(r.get('error_in_confirmed_binding')=='yes' for r in wrong_actor) if wrong_actor else None,'denominator':len(wrong_actor)},
            'attribution_accuracy':'not_evaluable_without_independent_executor_truth'},
        'question_quality':{'reviewed_distinct_questions':len(graded_questions),'reviewed_version_answers':len(valid_grades),'expected_development_version_answers':len(wb.pairs(actor))*2,
            'stale_or_inaccessible_grades_excluded':stale_grades,'ratios':qa,
            'subject_errors':sum(r['subject_scope_correct']=='no' for r in valid_grades) if valid_grades else 'not_evaluable',
            'time_errors':sum(r['time_scope_correct']=='no' for r in valid_grades) if valid_grades else 'not_evaluable',
            'inappropriate_certainty':ratio([r['inappropriate_certainty'] for r in valid_grades]),
            'mean_usefulness':sum(r['overall_usefulness'] for r in valid_grades)/len(valid_grades) if valid_grades else 'not_evaluable',
            'question_approvals':len(latest(approved,lambda r:r['case_id']))},
        'integrity':{'checked_native_confirmed_chains':len(chain_checks),'valid_complete_anonymous_chains':sum(c['complete_chain'] for c in chain_checks),
            'checked_named_chains':len(named_checks),'valid_complete_named_chains':sum(c['complete_chain'] for c in named_checks),
            'broken_or_incomplete_chains':sum(not c['complete_chain'] for c in chain_checks),
            'invalid_dependency_count_in_checked_chains':sum(len(c['invalid_dependencies']) for c in chain_checks),
            'stale_propagation_error_rate':'not_evaluable_without_independent_lifecycle_audit',
            'reference_failure_count':references['total_access_failures'],'reference_integrity':references,
            'historical_frame_content_correctness':'not_evaluable_without_frame_and_semantic_audit'},
        'scientific_acceptance':'pending_real_human_reviews','model_calls':0}
