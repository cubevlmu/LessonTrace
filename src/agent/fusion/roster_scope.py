"""Resolve teacher-selected roster profiles without turning labels into identities."""
from src.identity.store import IdentityStore
from src.attribution.source import IdentityReader
from src.context.person_profiles import labels
from .evidence import version


def build_plan(service,request,actor,*,has_assessment_evidence=False,resolved=None):
    from .planning import plan
    request,intervals=resolved if resolved is not None else resolve(service,request,actor)
    return request,plan(request,service.source,has_assessment_evidence=has_assessment_evidence).model_copy(
        update={'profile_intervals':intervals})


def lesson_roster(service,lesson_id,actor):
    from src.context.lesson_graph import _Lesson

    with service.repo.connection() as c:
        lesson=_Lesson(service,c,lesson_id,actor)
        cid=lesson.row['classroom_id']
        if not cid:return []
        roster=lesson.roster
    store=IdentityStore(service.identity_dir)
    with store.connection() as c:
        classroom=c.execute("SELECT name FROM classrooms WHERE classroom_id=? AND owner_actor=? AND status='active'",
                            (cid,actor)).fetchone()
    if not classroom:raise PermissionError('lesson_classroom_access_denied')
    return [{'subject_id':sid,'display_name':store.unseal(row['display_name']),
             'classroom_name':classroom['name']} for sid,row in roster.items()]


def assessment_documents(index):
    from src.context.lessons import STUDENT_MATERIAL_TYPES
    return {d['document_id'] for d in index['documents']
            if d['source_type'] in STUDENT_MATERIAL_TYPES and d['readiness']=='ready'}


def resolve(service,request,actor,*,allow_clarification=False):
    if not getattr(service,'identity_dir',None):return request,[]
    store=IdentityStore(service.identity_dir)
    run,clip=service.source.verified_run(request.run_id) if request.run_id else (None,None)
    if request.lesson_id:
        rows=lesson_roster(service,request.lesson_id,actor)
    elif clip:
        with store.connection() as c:
            raw=c.execute("""SELECT s.subject_id,s.display_name,cl.name FROM registered_subjects s
                JOIN classroom_students cs USING(subject_id) JOIN classrooms cl USING(classroom_id)
                JOIN classroom_videos cv USING(classroom_id) WHERE cv.video_id=? AND cl.owner_actor=?
                AND cs.status='active' AND s.status IN ('active','reference_only')""",(clip['video_id'],actor))
            rows=[{'subject_id':r['subject_id'],'display_name':store.unseal(r['display_name']),
                   'classroom_name':r['name']} for r in raw]
    else:return request,[]
    sid=request.roster_subject_id
    if request.lesson_id and request.subject_id and request.subject_id not in {r['subject_id'] for r in rows}:
        raise PermissionError('lesson_student_access_denied')
    if sid and request.subject_id and sid!=request.subject_id:
        raise ValueError('conflicting_student_scope')
    if not sid and not request.subject_id:
        found={r['subject_id']:r for r in rows if r['display_name'] and r['display_name'] in request.question}
        if request.lesson_id:
            # A longer name consumes its own occurrence, not a separately named student.
            import re
            spans={key:[m.span() for m in re.finditer(re.escape(r['display_name']),request.question)]
                   for key,r in found.items()}
            found={key:r for key,r in found.items() if any(
                not any(a<=lo and hi<=b and (a,b)!=(lo,hi) for other in spans.values() for a,b in other)
                for lo,hi in spans[key])}
        if len(found)>1:
            if not allow_clarification:
                raise ValueError('multiple_students_in_question; select_one_person_or_classroom_report')
            from src.context.lesson_graph import lesson_index
            from src.context.material_library import scope_rows
            scopes=scope_rows(service) if not request.lesson_id else {}
            candidates=[]
            for key,row in sorted(found.items()):
                if request.lesson_id:
                    has_work=bool(assessment_documents(lesson_index(service,request.lesson_id,actor,key)))
                else:
                    with service.repo.connection() as c:
                        has_work=any(scopes.get(d['document_id'],{}).get('subject_id')==key and
                                     scopes[d['document_id']].get('run_id')==request.run_id
                                     for d in c.execute("""SELECT document_id FROM context_documents
                                         WHERE owner=? AND status IN ('parsed','parsed_teacher_transcription')
                                           AND source_type IN ('student_work','graded_work','assessment_result')""",(actor,)))
                candidates.append({**row,'has_submission':has_work})
            return request,{'clarification_candidates':candidates}
        if found:sid=next(iter(found))
    if not sid:return request,[]
    if sid not in {r['subject_id'] for r in rows}:raise PermissionError('roster_person_not_in_owned_video_classroom')
    request=request.model_copy(update={'roster_subject_id':sid});intervals=[]
    if not clip:return request,intervals
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
