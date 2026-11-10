"""Read-only scoped checks. No detector, VLM, identity registration or human writes."""
import json
from pathlib import Path
from src.agent.fusion.evidence import version
from src.attribution.source import IdentityReader
from src.attribution.service import AttributionService
from .repository import track_scope


def covers(intervals,a,b):
    end=a;begun=False
    for lo,hi in sorted(intervals):
        if hi<a or lo>b:continue
        if lo>end+1e-6:return False
        begun=True;end=max(end,hi)
    return begun and end>=b-1e-6


def dimension(status,window,refs=(),intervals=(),reasons=()):
    return {'status':status,'time_scope':window,'source_refs':list(refs),'verified_intervals':list(intervals),'reasons':list(reasons)}


def validate(service,request,actor):
    a,b=request.start,request.end;window=[a,b];rid,tid=request.run_id,request.track_id
    names=['track_verified','identity_verified','attribution_verified','visual_evidence_verified','temporal_alignment_verified','context_source_verified']
    states={k:dimension('unverified',window) for k in names};errors=[];times=[];behavior=None;behavior_source=None;mapping_source=None
    states['identity_verified']=dimension('not_required' if not request.subject_id else 'unverified',window)
    if not(request.require_context or request.activity_id):
        for k in ['temporal_alignment_verified','context_source_verified']:states[k]=dimension('not_required',window)
    try:run,clip=service.source.verified_run(rid)
    except (ValueError,OSError) as e:
        return {'states':states,'final_status':'unknown','complete_chain':False,'invalid_dependencies':[{'dependency_id':'registered_video:'+rid,'reason':str(e)}],
            'subject':request.subject_id or f'track:{rid}:{tid}','time_scope':window,'evidence_times':[],'scope':'no_valid_registered_source'}
    if not clip['start']<=a<=b<=clip['end']:raise ValueError('chain_window_outside_registered_clip')
    obs={o['frame_index']:o for o in service.source.observations(rid,tid) if a<=o['global_timestamp']<=b}
    scope=track_scope(service.repo,rid,tid,a,b,run['metadata']['source_sha256'])
    states['track_verified']=dimension(scope['status'],window,scope['review_ids'],scope['verified_intervals'],['Only explicitly reviewed scope; a positional jump alone is not ID Switch.'])
    if not obs:states['track_verified']=dimension('unverified',window,reasons=['No actual visible observations in this interval.'])
    if request.subject_id:
        identities=IdentityReader(service.identity_dir).intersect(rid,tid,a,b,run['metadata']['source_sha256'])
        identities=[i for i in identities if i['subject_id']==request.subject_id]
        allowed=[]
        with service.repo.connection() as c:
            for identity in identities:
                record=c.execute('SELECT payload FROM validation_identity_assessments WHERE binding_id=? ORDER BY created_at DESC,review_id DESC LIMIT 1',(identity['identity_binding_id'],)).fetchone()
                if not record or json.loads(record[0])['judgment']=='correct':allowed.append(identity)
        identities=allowed
        intervals=[[i['start'],i['end']] for i in identities]
        effective=[[max(x,s),min(y,t)] for x,y in intervals for s,t in scope['verified_intervals'] if max(x,s)<=min(y,t)]
        states['identity_verified']=dimension('verified' if covers(effective,a,b) else 'unverified',window,
            ['identity:'+i['identity_binding_id'] for i in identities],effective,['Identity applies only to authorized AND continuity-reviewed intervals.'])
        states['identity_verified']['raw_authorized_intervals']=intervals
    if request.event_binding_id:
        with service.attribution_repo.connection() as c:
            row=c.execute('SELECT * FROM event_track_bindings WHERE binding_id=?',(request.event_binding_id,)).fetchone()
        if not row:errors.append({'dependency_id':'attribution:'+request.event_binding_id,'reason':'binding_missing'})
        else:
            h=dict(row);payload=json.loads(h['payload']);behavior=payload.get('target_action')
            event=service.source.event(h['event_id'])
            behavior_source={k:event.get(k) for k in ['event_id','source_kind','excel_row','excel_sha256','region_kind','action','t_start','t_end']}
            behavior_source['scope']='original_dataset_claim_not_independent_human_truth'
            valid=h['status']=='human_confirmed' and h['run_id']==rid and h['track_id']==tid and h['start']<=a<=b<=h['end'] and (not request.action or request.action==behavior)
            try:
                attr=AttributionService(service.source,service.attribution_repo,service.identity_dir);attr.candidate(h['candidate_id'])
            except ValueError as e:valid=False;errors.append({'dependency_id':'attribution:'+h['binding_id'],'reason':str(e)})
            with service.attribution_repo.connection() as c:
                conflict=c.execute("SELECT 1 FROM event_track_bindings WHERE event_id=? AND run_id=? AND track_id=? AND status='rejected' AND start<=? AND end>=?",(h['event_id'],rid,tid,b,a)).fetchone()
            if conflict:valid=False;errors.append({'dependency_id':'attribution:'+h['binding_id'],'reason':'conflicting_human_attribution'})
            with service.repo.connection() as c:
                assessed=c.execute('SELECT payload FROM validation_actor_assessments WHERE candidate_id=? ORDER BY created_at,review_id',(h['candidate_id'],)).fetchall()
            relevant=[json.loads(r[0]) for r in assessed if max(json.loads(r[0])['start'],a)<=min(json.loads(r[0])['end'],b)]
            if relevant and relevant[-1]['decision'] in {'rejected','ambiguous','uncertain'}:
                valid=False;errors.append({'dependency_id':'attribution_assessment:'+h['candidate_id'],'reason':'independent_actor_review_not_confirmed'})
            refs=[r for r in payload.get('evidence_refs',[]) if a<=r['global_time']<=b]
            valid=valid and bool(refs)
            states['attribution_verified']=dimension('verified' if valid else 'invalid',window,['attribution:'+h['binding_id']],[[a,b]] if valid else [],['Existential human observation, not continuous performance over the whole interval.'])
            visual=bool(valid and payload.get('same_person_in_reviewed_interval'))
            for ref in refs:
                o=obs.get(ref['frame_index'])
                if not o or abs(o['global_timestamp']-ref['global_time'])>.002:visual=False;continue
                try:service.source.frame(rid,tid,ref['frame_index'])
                except (ValueError,OSError):visual=False;continue
                times.append(ref['global_time'])
            states['visual_evidence_verified']=dimension('verified' if visual else 'unverified',window,
                [f'frame:{rid}:{tid}:{r["frame_index"]}' for r in refs],[[t,t] for t in times] if visual else [],['Human-reviewed action at cited real frames only; not independent recognition accuracy.'])
            if not valid:errors.append({'dependency_id':'attribution:'+h['binding_id'],'reason':'revoked_wrong_action_scope_or_missing_cited_evidence'})
    if request.activity_id:
        activities=service.activities(actor,clip['clip_id']);act=next((x for x in activities if x['activity_id']==request.activity_id),None)
        if act:
            passage=service.repo.passage(act['source_passage_id'],actor);doc=service.repo.document(act['document_id'],actor)
            source_ok=doc['status']=='parsed' and bool(passage['content'])
            states['context_source_verified']=dimension('verified' if source_ok else 'unverified',window,['text:'+passage['passage_id'],'document:'+doc['document_id']],reasons=['Only source content existence; a plan does not prove actual participation.'])
            mapping=act['alignment'];mapping_source=mapping;mapped=mapping.get('mapped_global_window')
            mapped_ok=mapping['status']=='verified_mapping' and mapped and covers([mapped],a,b)
            states['temporal_alignment_verified']=dimension('verified' if mapped_ok else 'unverified',window,['activity:'+act['activity_id']]+(['alignment:'+mapping['link_id']] if mapping.get('link_id') else []),[[a,b]] if mapped_ok else [],['Explicit time correspondence does not confirm the activity occurred.'])
        else:errors.append({'dependency_id':'activity:'+request.activity_id,'reason':'revoked_missing_or_inaccessible_activity'})
    elif request.require_context:errors.append({'dependency_id':'activity:missing','reason':'explicit_activity_and_mapping_required'})
    with service.repo.connection() as c:rules=[json.loads(r[0]) for r in c.execute('SELECT payload FROM knowledge_rules ORDER BY rule_id')]
    complete=all(s['status'] in {'verified','not_required'} for s in states.values())
    for key,s in states.items():
        if s['status'] not in {'verified','not_required'}:errors.append({'dependency_id':key,'reason':s['status'],'time_scope':s['time_scope']})
    # Record safe evidence instants; never attribute the unconfirmed second half
    # of a Track to the subject just because its first half has an identity.
    safe_times=[t for t in times if all(s['status']=='not_required' or any(lo<=t<=hi for lo,hi in s['verified_intervals'])
        for k,s in states.items() if k in {'track_verified','identity_verified','attribution_verified'})]
    rule_basis=['rule:'+r['rule_id'] for r in rules if r.get('action')==(request.action or behavior) or r['rule_id'] in {'evidence:no_upgrade','evidence:no_group_to_person','evidence:no_identity_from_text','evidence:alignment_required'}]
    return {'subject':request.subject_id or f'track:{rid}:{tid}','time_scope':window,'action':request.action or behavior,
        'behavior_evidence':behavior_source,'temporal_mapping':mapping_source,'rule_basis':rule_basis,
        'states':states,'evidence_times':times,'safe_subject_evidence_times':safe_times,'invalid_dependencies':errors,
        'rule_version':version(rules),'final_status':'supported' if complete else ('uncertain' if obs else 'unknown'),
        'complete_chain':complete,'scope':'human-reviewed observable action at cited evidence instants; no cognition or continuous-event assertion',
        'review_claim':{'subject':request.subject_id or f'track:{rid}:{tid}','action':request.action or behavior,'confirmed_instants':safe_times if complete else [],
            'status':'supported' if complete else 'uncertain','identity_source':states['identity_verified']['source_refs'],
            'attribution_source':states['attribution_verified']['source_refs'],'visual_evidence':states['visual_evidence_verified']['source_refs'],
            'text_source':states['context_source_verified']['source_refs'],'time_mapping':states['temporal_alignment_verified']['source_refs'],
            'behavior_evidence':behavior_source,'rule_basis':rule_basis,'rule_version':version(rules)}}


def dependencies(service,retrieved,plan):
    deps=[{'dependency_id':e['evidence_id'],'kind':e['evidence_type'],'version':e['source_version'],'time_scope':e['time_scope']} for e in retrieved['evidence']]
    if plan.effective_time_range:
        a,b=plan.effective_time_range
        for e in retrieved['evidence']:
            if e['evidence_type']!='person_track':continue
            p=e['provenance'];scope=track_scope(service.repo,plan.run_id,p['track_id'],a,b,retrieved['bundle']['run']['metadata']['source_sha256'])
            deps.append({'dependency_id':f'continuity:{plan.run_id}:{p["track_id"]}','kind':'track_human_review','version':scope['source_version'],'time_scope':[a,b]})
        with service.repo.connection() as c:
            has=c.execute("SELECT 1 FROM sqlite_master WHERE name='validation_actor_assessments'").fetchone()
            assessments=[dict(r) for r in c.execute('SELECT * FROM validation_actor_assessments ORDER BY created_at,review_id')] if has else []
        related={e['source_id'] for e in retrieved['evidence'] if e['evidence_type']=='event_attribution' and e['provenance'].get('status')=='candidate'}
        for candidate in sorted(related):
            records=[r for r in assessments if r['candidate_id']==candidate and max(json.loads(r['payload'])['start'],a)<=min(json.loads(r['payload'])['end'],b)]
            deps.append({'dependency_id':'actor_assessments:'+candidate,'kind':'human_actor_assessment','version':version(records),'time_scope':[a,b]})
    deps.append({'dependency_id':'validation_policy','kind':'program_policy','version':version({p.name:p.read_text(encoding='utf-8') for p in Path(__file__).parent.glob('*.py')}),'time_scope':None})
    for e in retrieved['evidence']:
        if e['evidence_type']!='identity_binding':continue
        with service.repo.connection() as c:rows=[dict(r) for r in c.execute('SELECT * FROM validation_identity_assessments WHERE binding_id=? ORDER BY created_at,review_id',(e['source_id'],))]
        deps.append({'dependency_id':'identity_assessments:'+e['source_id'],'kind':'identity_human_assessment','version':version(rows),'time_scope':e['time_scope']})
    return deps


def changed(before,after):
    old={d['dependency_id']:d for d in before};current={d['dependency_id']:d for d in after}
    return [{'dependency_id':key,'kind':old.get(key,current.get(key))['kind'],'reason':'removed' if key not in current else ('added' if key not in old else 'source_version_changed'),
        'time_scope':old.get(key,current.get(key)).get('time_scope')} for key in sorted(set(old)|set(current)) if old.get(key)!=current.get(key)]


def guard_claims(service,report,actor):
    """Check personal-action clauses, leaving source-record assertions independent."""
    from .schemas import ChainRequest
    registry={e['evidence_id']:e for e in report['evidence']};checks=[]
    for claim in report['claims']+report.get('additional_claims',[]):
        if claim['scope'] not in {'identity_visible_track_human_attribution_intersection_at_evidence_instants','human_actor_review_at_cited_instants'}:continue
        if claim['final_claim_status'] not in {'supported','uncertain'}:continue
        ref=next((r for r in claim['evidence_refs'] if r.startswith('attribution:')),None)
        if not ref or ref not in registry:continue
        source=registry[ref];p=source['provenance'];track=source['subject_ref'].rsplit(':',1)[1]
        body=ChainRequest(run_id=report['classroom']['run_id'],track_id=int(track),start=claim['time_scope'][0],end=claim['time_scope'][1],
            subject_id=claim['subject_ref'].removeprefix('subject:') if claim['subject_ref'].startswith('subject:') else None,
            action=p['action'],event_binding_id=source['source_id'])
        check=validate(service,body,actor);checks.append({'fact_id':claim['fact_id'],'chain':check})
        if not check['complete_chain']:
            claim.update(final_claim_status='uncertain',track_status='supported' if check['states']['track_verified']['status']=='verified' else 'unknown',
                statement='人工归属来源存在，但完整人物证据链尚未通过，不能作为已确认的个人行为；请查看各依赖的有效时间范围。')
    report['evidence_chain_checks']=checks
