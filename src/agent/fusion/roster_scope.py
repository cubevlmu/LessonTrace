"""Resolve teacher-selected roster profiles without turning labels into identities."""
from src.identity.store import IdentityStore
from src.attribution.source import IdentityReader
from src.context.person_profiles import labels
from .evidence import version


def build_plan(service,request,actor):
    from .planning import plan
    request,intervals=resolve(service,request,actor)
    return request,plan(request,service.source).model_copy(update={'profile_intervals':intervals})


def resolve(service,request,actor):
    if not service.identity_dir:return request,[]
    run,clip=service.source.verified_run(request.run_id);store=IdentityStore(service.identity_dir)
    with store.connection() as c:
        rows=c.execute("SELECT s.subject_id,s.display_name,s.status FROM registered_subjects s JOIN classroom_students cs ON cs.subject_id=s.subject_id JOIN classrooms cl ON cl.classroom_id=cs.classroom_id JOIN classroom_videos cv ON cv.classroom_id=cl.classroom_id WHERE cv.video_id=? AND cl.owner_actor=? AND cs.status='active' AND s.status IN ('active','reference_only')",(clip['video_id'],actor)).fetchall()
    sid=request.roster_subject_id
    if not sid and not request.subject_id:
        found=[r['subject_id'] for r in rows if (name:=store.unseal(r['display_name'])) and name in request.question]
        if len(set(found))>1:raise ValueError('multiple_students_in_question; select_one_person_or_classroom_report')
        if found:sid=found[0]
    if not sid:return request,[]
    if sid not in {r['subject_id'] for r in rows}:raise PermissionError('roster_person_not_in_owned_video_classroom')
    request=request.model_copy(update={'roster_subject_id':sid});intervals=[]
    for label in labels(store,service,request.run_id,clip,actor,'teacher'):
        if label['subject_id']==sid:intervals.append({'track_id':label['track_id'],'start':label['local_start']+clip['start'],'end':label['local_end']+clip['start'],'source_id':label['binding_id'],'scope_kind':'roster_track_registration_not_independent_identity'})
    reader=IdentityReader(service.identity_dir)
    for track in service.source.tracks(request.run_id):
        for binding in reader.intersect(request.run_id,track['track_id'],clip['start'],clip['end'],run['metadata']['source_sha256']):
            if binding['subject_id']==sid:intervals.append({'track_id':track['track_id'],'start':binding['start'],'end':binding['end'],'source_id':binding['identity_binding_id'],'scope_kind':'independently_reviewed_identity_interval'})
    if request.track_id is not None:intervals=[i for i in intervals if i['track_id']==request.track_id]
    return request,intervals


def retrieve_roster(service,plan,actor,allow_identity,retrieve):
    from .relevance import evidence_relevance
    from src.context.material_library import scope_rows
    current_scopes=scope_rows(service)
    for did in plan.document_ids:
        service.repo.document(did,actor)
        if current_scopes.get(did,{}).get('subject_id') not in {None,plan.roster_subject_id}:raise ValueError('selected_material_belongs_to_another_student')
    chunks=[]
    for interval in plan.profile_intervals:
        a=max(plan.effective_time_range[0],interval['start']);b=min(plan.effective_time_range[1],interval['end'])
        if a>b:continue
        sub=plan.model_copy(update={'roster_subject_id':None,'profile_intervals':[],'track_id':interval['track_id'],'effective_time_range':[a,b]})
        chunk=retrieve(service,sub,actor,allow_identity)
        # Filter student-specific documents against the original requested person.
        from src.context.material_library import scope_rows
        scopes=scope_rows(service)
        chunk['evidence']=[e for e in chunk['evidence'] if not scopes.get(e['provenance'].get('document_id'),{}).get('subject_id') or scopes[e['provenance']['document_id']]['subject_id']==plan.roster_subject_id]
        for e in chunk['evidence']:
            if e['evidence_type'] not in {'teacher_plan','teacher_report','teaching_activity','knowledge_rule'}:
                e['provenance'].update(roster_scope=interval,roster_subject_id=plan.roster_subject_id)
                e['source_version']=version([e['source_version'],interval])
        chunks.append(chunk)
    covered=bool(chunks)
    if not chunks:
        sub=plan.model_copy(update={'roster_subject_id':None,'profile_intervals':[],'track_id':None})
        chunk=retrieve(service,sub,actor,allow_identity)
        chunk['evidence']=[e for e in chunk['evidence'] if e['evidence_type'] in {'teacher_plan','teacher_report','teaching_activity','knowledge_rule'}]
        chunks=[chunk]
    merged=chunks[0];merged['evidence']=list({(e['evidence_id'],tuple(e['time_scope'] or [])):e for chunk in chunks for e in chunk['evidence']}.values())
    from src.context.material_library import scope_rows
    scopes=scope_rows(service)
    merged['evidence']=[e for e in merged['evidence'] if not scopes.get(e['provenance'].get('document_id'),{}).get('subject_id') or scopes[e['provenance']['document_id']]['subject_id']==plan.roster_subject_id]
    # Disjoint intervals may reuse source IDs. Namespace them rather than widening a time claim.
    seen=set()
    for e in merged['evidence']:
        if e['evidence_id'] in seen:e['evidence_id']+=':scope:'+version(e['time_scope'])[:12]
        seen.add(e['evidence_id'])
    merged['subject_missing']=not covered
    merged['query_evidence_ids']=[e['evidence_id'] for e in merged['evidence'] if evidence_relevance(e,plan)[0]>0]
    return merged
