"""Authorize a lesson scope and project its current teaching graph into fusion evidence."""
from src.context import lesson_graph
from src.context.lessons import STUDENT_MATERIAL_TYPES
from .evidence import Evidence, version
from .relevance import evidence_relevance
from .roster_scope import assessment_documents


def index_for(service, request, actor):
    if not request.lesson_id:
        return None
    index = lesson_graph.lesson_index(service, request.lesson_id, actor,
                                      request.roster_subject_id or request.subject_id)
    if request.run_id:
        # Validate membership without a second video-window lookup.
        with service.repo.connection() as c:
            source=c.execute('''SELECT v.clip_id FROM analysis_jobs j JOIN analysis_videos v
                                 ON v.video_id=j.video_id AND v.owner=j.owner
                                 WHERE j.run_id=? AND j.owner=? AND j.video_id=? AND v.status='active' ''',
                             (request.run_id,actor,index['video_id'])).fetchone()
        if not source:
            raise PermissionError('lesson_run_access_denied')
        _,clip=service.source.run(request.run_id)
        if clip['video_id']!=index['video_id'] or clip['clip_id']!=source['clip_id']:
            raise PermissionError('lesson_run_access_denied')
    return index


def documents_for(index, scope):
    docs = {d['document_id']: d for d in index['documents']}
    if scope.material_mode != 'none' and not set(scope.document_ids) <= docs.keys():
        raise PermissionError('lesson_document_access_denied')
    selected = set(scope.document_ids) if scope.material_mode == 'selected' or scope.document_ids else docs.keys()
    return {
        did: d for did, d in docs.items()
        if scope.material_mode != 'none' and did in selected and d['readiness'] == 'ready'
        and (scope.use_classroom_memory or d['source_type'] != 'classroom_memory')
    }


def has_assessment(index, request):
    return bool(index and assessment_documents(index) & documents_for(index, request).keys())


def relation_ref(row):
    return f"{row['relation_id']}@rev{row['rev']}"


def retrieve_lesson(service, plan, actor, allow_identity, read_video, index=None):
    index = index if index is not None else index_for(service, plan, actor)
    docs = documents_for(index, plan)
    items = {i['item_id']: i for i in index['items'] if i['document_id'] in docs}
    sid = index['student_id']
    passages = {}

    def passage(pid):
        if pid not in passages:
            passages[pid] = service.repo.passage(pid, actor)
        return passages[pid]

    def endpoint(kind, key):
        if kind == 'item':
            return items.get(key, {}).get('document_id')
        if kind == 'document':
            return key if key in docs else None
        if kind == 'passage':
            did = passage(key)['document_id']
            return did if did in docs else None
        return None

    relations = []
    for row in index['relations']:
        ends = [(row[k+'_type'], row[k+'_id']) for k in ('src', 'dst')]
        if any(kind in {'item', 'document', 'passage'} and not endpoint(kind, key)
               for kind, key in ends):
            continue
        if sid and any(kind == 'student' and key != sid for kind, key in ends):
            continue
        relations.append((row, ends, {endpoint(kind, key) for kind, key in ends} - {None}))

    all_questions = {key for key, item in items.items() if item['kind'] == 'question'}
    questions = all_questions
    if sid:
        work = assessment_documents(index) & docs.keys()
        questions = {key for _, ends, related in relations if related & work
                     for kind, key in ends if kind == 'item' and key in questions}
    rubric_items = {key for row, ends, _ in relations
                    if row['rel_type'] == 'applies_to' and any(k == 'item' and v in questions for k, v in ends)
                    for kind, key in ends if kind == 'item' and items[key]['kind'] == 'rubric_clause'}
    excluded_question_docs = set()
    if sid and questions:
        excluded_question_docs = ({items[key]['document_id'] for key in all_questions}
                                  - {items[key]['document_id'] for key in questions})
    items = {key: item for key, item in items.items()
             if (item['kind'] != 'rubric_clause' or key in rubric_items)
             and (not sid or not questions or item['kind']!='question' or key in questions)}
    relations = [r for r in relations if all(k != 'item' or v in items for k, v in r[1])
                 and all(docs[did]['source_type'] != 'rubric' or
                         any(k == 'item' and v in rubric_items and items[v]['document_id'] == did
                             for k, v in r[1]) for did in r[2])]

    # A lesson never inherits unrelated classroom memory or roster submissions from a video run.
    if plan.run_id:
        video_plan = plan.model_copy(update={'lesson_id': None, 'document_ids': [],
                                             'material_mode': 'none', 'use_classroom_memory': False})
        retrieved = read_video(service, video_plan, actor, allow_identity)
        evidence = [e for e in retrieved['evidence'] if e['evidence_type'] not in
                    {'teacher_plan', 'teacher_report', 'teaching_activity'}]
        # Region-only evidence cannot be attributed to the selected student.
        if sid:
            evidence = [e for e in evidence if e['evidence_type'] == 'knowledge_rule' or
                        e.get('subject_ref') and e['evidence_type'] not in
                        {'dataset_annotation', 'human_visual_review'}]
    else:
        retrieved = {'bundle': None, 'timelines': {}, 'rules': [], 'subject_missing': False}
        evidence = []

    owner = {row['src_id']: row['dst_id'] for row, _, _ in relations
             if row['rel_type'] == 'belongs_to' and row['src_type'] == 'document'
             and row['dst_type'] == 'student'}
    by_doc = {i['document_id'] for i in items.values()}
    allowed_docs = {did for did, d in docs.items()
                    if (d['source_type'] != 'rubric' or did in by_doc) and did not in excluded_question_docs}
    items = {key:item for key,item in items.items() if item['document_id'] in allowed_docs}
    relations = [r for r in relations if r[2] <= allowed_docs]

    def add(did, p, item=None):
        doc = docs[did]
        locator = item['locator'] if item else {k:p[k] for k in ('page_or_slide', 'source_start', 'source_end')}
        content = p['content'][locator['start']:locator['end']] if item else p['content']
        key = item['item_id'] if item else p['passage_id']
        refs = [relation_ref(r) for r, ends, related in relations
                if ('item', key) in ends or (r['rel_type']=='belongs_to' and did in related)]
        provenance = {'document_id': did, 'passage_id': p['passage_id'], 'lesson_id': plan.lesson_id,
                      'title': doc['title'], 'document_version': doc['version'],
                      'source_kind': doc['source_type'], 'is_example': bool(doc['is_example']),
                      'text_source_status': 'project_example' if doc['is_example'] else 'uploader_declared',
                      'scope': 'source_text_content_only_not_video_fact', **locator,
                      'relation_refs': refs, 'locator': locator,
                      'source_url': '/api/context/passages/'+p['passage_id']}
        if item:
            provenance.update(item_id=key, item_kind=item['kind'], local_key=item['local_key'])
        evidence.append(Evidence(
            evidence_id=('lesson_item:' if item else 'text:')+key,
            evidence_type='teacher_report' if doc['source_type'] in STUDENT_MATERIAL_TYPES |
            {'teacher_observation', 'teacher_note', 'classroom_memory'} else 'teacher_plan',
            source_id=p['passage_id'], subject_ref='subject:'+owner[did] if did in owner else None,
            content=content, verification_status='supported', time_scope=None, provenance=provenance,
            source_version=version([content, provenance, item and item['source_version']])).model_dump())

    for item in items.values():
        add(item['document_id'], passage(item['passage_id']), item)
    # For unstructured records use their original passages. Never expose the raw
    # rubric document, which could reintroduce a revoked neighboring clause.
    for did in sorted(allowed_docs):
        if docs[did]['source_type'] == 'rubric' or did in by_doc:
            continue
        with service.repo.connection() as c:
            ids = [r[0] for r in c.execute('SELECT passage_id FROM context_passages WHERE document_id=? ORDER BY source_start',
                                          (did,))]
        for pid in ids:
            add(did, passage(pid))

    filters = {'student_id': sid}
    queries = [lesson_graph.negative_query_digest(service,plan.lesson_id,actor,kind,**filters)
               for kind in ('submission','rubric_clause')]
    video = {'run_id': plan.run_id, 'track_id': plan.track_id}
    if plan.requested_time_range:
        video.update(zip(('start', 'end'), plan.requested_time_range))
    queries.append(lesson_graph.negative_query_digest(
        service,plan.lesson_id,actor,'video_interval',**filters,**video))
    # Expose state only. Matches and revoked relation metadata are dependencies,
    # not evidence or model input. An inapplicable rubric is not a valid standard.
    states = {q['kind']: q['state'] for q in queries}
    if not (assessment_documents(index) & docs.keys()) and states['submission']=='present':
        states['submission']='no_data'
    if not rubric_items and states['rubric_clause'] == 'present':
        states['rubric_clause'] = 'no_data'
    readiness = index['readiness']['counts']
    roster_size = index['roster_size'] if not sid else None
    covered_students = {owner[e['provenance']['document_id']] for e in evidence
                        if e['provenance'].get('document_id') in owner and not e['provenance'].get('is_example')}
    group_coverage = {'roster_size': roster_size, 'students_with_confirmed_work': len(covered_students),
                      'missing_or_unconfirmed': roster_size-len(covered_students) if roster_size else None}
    return {**retrieved, 'product_owner': actor, 'evidence': evidence,
            'query_evidence_ids': [e['evidence_id'] for e in evidence if evidence_relevance(e,plan)[0]>0],
            'lesson_relations': [r for r,_,_ in relations], 'lesson_queries': queries,
            'lesson_context': {'title': index['title'], 'classroom_id': index['classroom_id'],
                               'states': states, 'readiness_counts': readiness, 'group_coverage': group_coverage}}
