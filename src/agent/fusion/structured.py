"""Wave 2 statement writing, with a legacy answer/narrative projection."""
import json
import re
import time

from filelock import FileLock, Timeout

from src.context import lesson_graph
from src.inference import skills, statement_writer
from src.inference.report_evidence import report_order
from .evidence import version
from .lesson_evidence import relation_ref
from .narrative import HEADINGS

SECTIONS = {'scope', 'materials', 'observations', 'analysis', 'recommendations', 'followup', 'limits', 'people'}
ROLE_SECTIONS = {'fact': 'materials', 'analysis': 'analysis', 'recommendation': 'recommendations',
                 'coverage_note': 'limits', 'change_note': 'limits'}


def section_for(statement):
    key = statement['section_id']
    if key == 'overview':
        return 'scope'
    return key if key in SECTIONS else ROLE_SECTIONS.get(statement['role'], 'analysis')


def change_events(service, request, actor):
    """Read the relation audit, without loading the previous answer as evidence."""
    if not (request.lesson_id and request.previous_report_id
            and re.search(r'刚才|之前|上次|先前|撤销|改口|失效|为什么|为何', request.question)):
        return []
    sid = request.roster_subject_id or request.subject_id
    revoked = {r['relation_id']:r for r in lesson_graph.list_relations(
        service, request.lesson_id, actor, sid, status='revoked')}
    with service.repo.connection() as c:
        row = c.execute('SELECT payload,request FROM fusion_reports WHERE report_id=? AND owner=?',
                        (request.previous_report_id,actor)).fetchone()
        if not row:
            return []
        prior = json.loads(row['request'])
        if (prior.get('roster_subject_id') or prior.get('subject_id')) != sid:
            return []
        payload = json.loads(row['payload'])
        used = {}
        for s in payload.get('statements', []):
            for ref in s.get('relation_refs', []):
                used.setdefault(ref, []).append(s['statement_id'])
        for dep in payload.get('dependency_manifest', []):
            ref = dep['dependency_id'].removeprefix('lesson_relation:')
            if dep['kind']=='lesson_relation':
                used.setdefault(ref, [])
        events = []
        for ref, statement_ids in used.items():
            rid, _, rev = ref.rpartition('@rev')
            if rid not in revoked or not rev.isdecimal():
                continue
            row = c.execute("""SELECT event_id,action,from_rev,to_rev,actor,created_at
                               FROM lesson_relation_events WHERE lesson_id=? AND relation_id=?
                               AND to_rev=? AND action='revoke' AND from_rev>=?""",
                            (request.lesson_id,rid,revoked[rid]['rev'],int(rev))).fetchone()
            if row:
                events.append(dict(row)|{'previous_statement_ids':statement_ids,
                                        'previous_relation_ref':ref,
                                        'relation_type':revoked[rid]['rel_type']})
    return events


def _source(e):
    p = e['provenance']
    return {'evidence_id':e['evidence_id'], 'evidence_type':e['evidence_type'],
            'source_kind':p.get('source_kind'), 'item_kind':p.get('item_kind'),
            'item_id':p.get('item_id'), 'local_key':p.get('local_key'),
            'document_id':p.get('document_id'), 'passage_id':p.get('passage_id'),
            'title':p.get('title'), 'is_example':p.get('is_example',False),
            'content':e['content'][:3000], 'time_scope':e['time_scope'],
            'verification_status':e['verification_status'], 'source_scope':p.get('scope'),
            'action':p.get('action'), 'track_id':p.get('track_id'), 'subject_ref':e.get('subject_ref'),
            'sampled_frames_valid':p.get('sampled_frames_valid'), 'decision':p.get('final_decision'),
            'observation_limitations':p.get('observation_limitations') or p.get('limitations') or [],
            'relation_refs':p.get('relation_refs',[])}


def _append(statements, plan, role, section, text, refs=(), queries=(), changes=()):
    content = {'role':role,'section_id':section,'text':text,'evidence_refs':list(refs),
               'query_refs':list(queries),'change_event_refs':list(changes),
               'depends_on_statements':[],'relation_refs':[]}
    statements.append({'statement_id':'stmt_'+version([plan.scope_key,content])[:32],**content})


def _deterministic(statements, plan, sources):
    work = [s for s in sources if s['source_kind'] in skills.ACADEMIC_SOURCE_KINDS]
    visual = [s for s in sources if s['evidence_type'] in skills.VIDEO_SOURCE_KINDS and
              s['evidence_type'] not in {'person_track','dataset_annotation','event_attribution'}]
    material = [s for s in sources if s not in work and s not in visual and s.get('document_id')]
    chosen = (work[:2]+visual[:1]+material[:2] if plan.output_mode=='report' or visual and work
              else (work or visual or material)[:2])
    for s in chosen:
        if s in visual:
            if s['verification_status']=='contradicted':
                continue
            if plan.output_mode=='report' and s['evidence_type']=='vlm_observation' and s.get('sampled_frames_valid'):
                from .observation_summary import readable_action
                from src.agent.visual_evidence_policy import semantic_findings
                text = readable_action(s['content'])[:220]
                if text and not semantic_findings(text):
                    _append(statements,plan,'fact','observations',f'所引画面记录描述为“{text}”。',[s['evidence_id']])
            elif s.get('action') and s['action']!='unknown':
                _append(statements,plan,'fact','observations',f"在所引时段 {s['time_scope']} 秒，记录中有外显动作 [{s['action']}]；适用范围仅限引用。",
                        [s['evidence_id']])
        else:
            locator = ' · '+s['local_key'] if s.get('local_key') else ''
            intro = '演示资料' if s['is_example'] else '资料'
            _append(statements,plan,'fact','materials',f"{intro}《{s['title']}》{locator}记载：“{s['content'].strip()[:220]}”",
                    [s['evidence_id']])
    if work and visual:
        _append(statements,plan,'coverage_note','limits',
                '作答说明这道题写了什么，录像只记录外显动作。两者不能互相证明当时是否专注，也不能单凭画面否定作答。',
                [work[0]['evidence_id'],visual[0]['evidence_id']])
    if work and plan.output_mode=='report':
        _append(statements,plan,'recommendation','recommendations',
                '下次讲评时，先请学生用一句话解释所引题目中的关键一步，再对照原答查看是否写清了理由。',
                [work[0]['evidence_id']])


def compose(report, plan, request, retrieved, provider, history, events, reread, max_calls, timeout, lock_path):
    context = retrieved['lesson_context']
    ordered = report_order(retrieved['evidence']) if plan.output_mode == 'report' else retrieved['evidence']
    sources = [_source(e) for e in ordered if e['evidence_type'] not in {'knowledge_rule','identity_binding'}
               and (e['evidence_type']!='event_attribution' or e['verification_status']=='supported')]
    kinds = {v for s in sources for v in (s['source_kind'],s['item_kind'],s['evidence_type']) if v}
    work = bool(kinds & skills.ACADEMIC_SOURCE_KINDS)
    visual = bool(kinds & (skills.VIDEO_SOURCE_KINDS - {'person_track','dataset_annotation'}))
    intent = 'student' if plan.report_template=='student' else plan.intent
    if not request.report_template and events:
        intent = 'focused_evidence_qa'
    elif plan.output_mode=='qa' and re.search(r'第\s*[0-9一二三四五六七八九十]+\s*题', request.question) and not report['conflicts']:
        intent = 'focused_evidence_qa'
    skill = skills.select_skill(intent,work,visual,bool(report['conflicts']),plan.output_mode)
    methods = skills.select_analysis_methods(skill.skill_id,kinds)
    states = context['states']
    queries = {q['kind']: 'query:'+q['digest'] for q in retrieved['lesson_queries']}
    relations = retrieved['lesson_relations']
    # Prefer original work and its exact related items, then scoped observations.
    priority = lambda s: (0 if s['source_kind'] in skills.ACADEMIC_SOURCE_KINDS else
                           1 if s['relation_refs'] or s['item_kind']=='goal' else
                           2 if s['evidence_type'] not in {'person_track','dataset_annotation'} else 3)
    if plan.output_mode != 'report':
        sources.sort(key=priority)
    packet = {'scope_key':plan.scope_key,'question':request.question,'output_mode':plan.output_mode,
              'student_scope':bool(plan.roster_subject_id or plan.subject_id),
              'allowed_sections':sorted(SECTIONS), 'sources':sources[:36],
              'history':history,'revision_context':request.report_context,
              'coverage':context['group_coverage'],'video_coverage':report.get('coverage'),
              'availability':{k:{'state':v,'query_ref':queries[k]} for k,v in states.items()},
              'conflicts':report['conflicts'],
              'relations':[{k:r[k] for k in ('src_type','src_id','dst_type','dst_id','rel_type')}|
                           {'ref':relation_ref(r)} for r in relations],
              'relation_refs':[relation_ref(r) for r in relations],
              'query_refs':list(queries.values()),
              'changes':[{k:e[k] for k in ('event_id','action','relation_type','from_rev','to_rev')} for e in events],
              'change_event_refs':[e['event_id'] for e in events]}
    budget = max_calls if request.use_llm and plan.intent!='cognitive_boundary' and sources else 0

    def tool_executor(tool, args):
        allowed = {'get_passage': {'passage_id','evidence_id'},
                   'get_rubric_item': {'item_id','evidence_id'},
                   'get_student_work': {'item_id','document_id','local_key','evidence_id'},
                   'get_video_window': {'evidence_id','start','end'}}
        if not args or not args.keys() <= allowed[tool]:
            raise ValueError('invalid_scoped_read')
        if tool=='get_video_window' and 'evidence_id' not in args and not {'start','end'} <= args.keys():
            raise ValueError('video_window_locator_required')
        current = {e['evidence_id']:e['source_version'] for e in reread()['evidence']}
        original = {e['evidence_id']:e['source_version'] for e in retrieved['evidence']}
        checked = []
        for s in sources:
            if current.get(s['evidence_id'])!=original[s['evidence_id']]:
                continue
            if tool=='get_rubric_item' and s['item_kind']!='rubric_clause':
                continue
            if tool=='get_student_work' and (not packet['student_scope'] or s['source_kind'] not in skills.ACADEMIC_SOURCE_KINDS):
                continue
            if tool=='get_passage' and not s['passage_id']:
                continue
            if tool=='get_video_window' and (s['evidence_type'] not in skills.VIDEO_SOURCE_KINDS or not s['time_scope']):
                continue
            if any(s.get(k)!=v for k,v in args.items() if k not in {'start','end'}):
                continue
            if 'start' in args or 'end' in args:
                lo,hi = s['time_scope']
                if not args.get('start',lo)<=lo<=hi<=args.get('end',hi):
                    continue
            checked.append(s)
        return checked[:2]

    audit_started = time.perf_counter()
    kwargs = {'tool_executor':tool_executor,'max_calls':budget}
    try:
        if budget:
            with FileLock(str(lock_path)).acquire(timeout=0):
                draft,audit = statement_writer.write_statements(provider,packet,skill,methods,timeout,**kwargs)
        else:
            draft,audit = statement_writer.write_statements(provider,packet,skill,methods,timeout,max_calls=0)
    except Timeout:
        draft,audit = statement_writer.write_statements(provider,packet,skill,methods,timeout,max_calls=0)
        audit['validation_failures'].append('writer_busy')
    statements = [s.model_dump() for s in draft.statements] if audit['actual_calls'] else []
    if plan.intent=='cognitive_boundary':
        text = ('单凭所引外显动作，无法判断学生是否专注、走神或听懂。' if plan.run_id else
                '现有课次资料不足以判断学生是否专注、走神或在课堂上听懂。')
        _append(statements,plan,'coverage_note','limits',text)
    elif not statements:
        _deterministic(statements,plan,sources)
    elif work and visual and not any(s['role']=='coverage_note' and '不能' in s['text'] and '专注' in s['text'] for s in statements):
        _append(statements,plan,'coverage_note','limits',
                '作答与录像记录的是不同方面，不能据此推断学生是否专注，也不能把作答表现外推到整节课的每一时刻。')
    if events:
        events_used = {r for s in statements for r in s['change_event_refs']}
        missing = [e for e in events if e['event_id'] not in events_used]
        if missing:
            _append(statements,plan,'change_note','limits',
                    '刚才的判断用到了当时有效的资料关联。该关联现已撤销，旧判断不再成立，下面只按仍有效的原文作答。',
                    changes=[e['event_id'] for e in missing])
            statements.insert(0,statements.pop())
    if plan.output_mode=='report' and not (plan.roster_subject_id or plan.subject_id):
        count = context['group_coverage']
        roster = count['roster_size']
        if roster:
            _append(statements,plan,'coverage_note','limits',
                    f"当前名册 {roster} 人，其中 {count['students_with_confirmed_work']} 人有可核验的作答资料。"
                    f"其余 {count['missing_or_unconfirmed']} 人未纳入作答分析，不能据此判断其是否完成。",
                    queries=[queries['submission']])
    if not plan.run_id and plan.intent!='cognitive_boundary':
        state = states['video_interval']
        text = ('录像还在处理，本次只依据课次文字资料。' if state=='processing' else
                '本次未使用录像，只依据可核验的课次文字资料。')
        _append(statements,plan,'coverage_note','limits',text,queries=[queries['video_interval']])
    if not any(s['role']!='coverage_note' for s in statements) and plan.intent!='cognitive_boundary':
        state=states['submission']
        text={'processing':'作答资料正在处理，暂不能给出具体判断。',
              'ambiguous':'有作答资料仍须核对归属，暂不作为个人分析依据。'}.get(state,'当前没有与该问题相符的可核验证据。')
        _append(statements,plan,'coverage_note','limits',text,queries=[queries['submission']])
        statements.insert(0,statements.pop())

    known = {e['evidence_id']:e for e in retrieved['evidence']}
    for s in statements:
        s['relation_refs'] = list(dict.fromkeys(s['relation_refs']+[
            r for ref in s['evidence_refs'] for r in known[ref]['provenance'].get('relation_refs',[])]))
    refs = list(dict.fromkeys(ref for s in statements for ref in s['evidence_refs']))
    if plan.intent=='cognitive_boundary':
        # The legacy check/adoption hints still apply, but cannot request a nonexistent video.
        report.pop('boundary_text',None)
        report.pop('check_text',None)
        report.pop('check_evidence_refs',None)
    existing = {e['evidence_id'] for e in report['evidence']}
    report['evidence'].extend(known[r] for r in refs if r not in existing)
    headings = {**HEADINGS,'scope':HEADINGS['overview'],'followup':'后续跟进','people':'人物范围'}
    narrative = [{'heading':headings[section_for(s)],'text':s['text'],'evidence_refs':s['evidence_refs'],
                  'sentence_ids':[],'analysis_section':section_for(s),'source_statement_id':s['statement_id']}
                 for s in statements]
    paragraphs = [' '.join(s['text'] for s in statements if s['role'] in roles) for roles in (
        {'change_note'}, {'fact','analysis'}, {'recommendation','coverage_note'})]
    answer = '\n\n'.join(filter(None,paragraphs))
    audit['elapsed_seconds'] = time.perf_counter()-audit_started
    record = {'status':'success' if audit['actual_calls'] and not audit['fallback_used'] else 'deterministic_grounded_prose',
              'chat_attempted':bool(audit['actual_calls']),'actual_calls':audit['actual_calls'],
              'prompt_version':'structured-statements-v2'}
    report.update(statements=statements, skill_audit=audit, answer=answer,
                  direct_answer={'text':answer,'evidence_refs':refs}, narrative=narrative,
                  narrative_text='\n\n'.join(p['text'] for p in narrative),
                  narrative_generation=record, lesson_evidence=context,
                  lesson_relation_changes=events,
                  language_composition='model_assisted' if record['status']=='success' else 'deterministic_fallback')
    if not plan.run_id:
        report['classroom']['scope']='lesson_text_materials_only'
        report['limitations']=['本次未使用录像，不推断现场行为。']
