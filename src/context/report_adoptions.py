"""Map a saved answer to report paragraphs without promoting its evidence."""
from collections import defaultdict
import json
import math

from fastapi import HTTPException
from src.attribution.source import IdentityReader
from src.identity.store import now
from src.agent.fusion.evidence import version as digest
from src.agent.fusion.structured import section_for

from .report_sources import _SourceLabels, _time_range


def unique(values):
    return list(dict.fromkeys(values))


def window(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(t) in (int, float) and math.isfinite(t) for t in value)
            and value[0] <= value[1])


def evidence_index(report, *, retrieved=False):
    evidence = report.get('evidence', [])
    if retrieved:
        evidence = evidence + report.get('audit', {}).get('retrieved_evidence', [])
    result = defaultdict(dict)
    for record in evidence:
        result[record['evidence_id']][record['source_version']] = record
    return result


def _required(answer, refs):
    registry = evidence_index(answer)
    result = {}
    for ref in unique(refs):
        versions = registry.get(ref, {})
        if len(versions) != 1:
            raise HTTPException(422, 'answer_not_adoptable')
        result[ref] = next(iter(versions))
    return result


def _subject_label(plan, report):
    person = (report.get('product_scope') or {}).get('subject_name')
    track = plan.get('track_id')
    return person or ('已确认身份范围' if plan.get('subject_id') else
                      '所选名册人物' if plan.get('roster_subject_id') else
                      f'匿名人物 {track:03d}' if track else None)


def answer_content(answer):
    plan = answer.get('query_plan') or {}
    text_only = bool(plan.get('lesson_id')) and not any(
        plan.get(k) is not None for k in ('run_id','clip_id','effective_time_range'))
    if not text_only and not (plan.get('run_id') and plan.get('clip_id') and window(plan.get('effective_time_range'))):
        raise HTTPException(422, 'answer_not_adoptable')
    paragraphs = []
    scope = {key: plan.get(key) for key in (
        'run_id', 'clip_id', 'lesson_id', 'track_id', 'subject_id', 'roster_subject_id',
        'effective_time_range', 'profile_intervals',
    )}
    scope['subject_label'] = _subject_label(plan, answer)

    def add(section, text, refs=(), kind=None, check=None, statement_id=None):
        if isinstance(text, str) and text.strip():
            paragraph = {'section_id': section, 'text': text.strip(), 'evidence_refs': unique(refs)}
            if statement_id:
                paragraph['source_statement_id'] = statement_id
            if kind:
                paragraph['content_kind'] = kind
            if check is not None:
                paragraph['check_evidence_refs'] = unique(check)
                paragraph['check_scope'] = scope
            paragraphs.append(paragraph)

    direct = answer.get('direct_answer') or {}
    narrative = answer.get('narrative') or []
    statements = answer.get('statements') or []
    if statements:
        by_id = {s['statement_id']:s for s in statements}
        def dependencies(part, seen):
            for key in part.get('depends_on_statements', []):
                if key in by_id and key not in seen:
                    seen.add(key)
                    yield from dependencies(by_id[key], seen)
            yield from part.get('evidence_refs', [])
        for part in statements:
            if part['role']=='change_note':
                continue
            add(section_for(part),part['text'],dependencies(part,{part['statement_id']}),
                'model_analysis_not_independent_fact',statement_id=part['statement_id'])
    elif plan.get('intent') == 'cognitive_boundary' and answer.get('boundary_text'):
        registry = evidence_index(answer)
        rules = [r for r in direct.get('evidence_refs', []) if any(
            e['evidence_type'] == 'knowledge_rule' for e in registry.get(r, {}).values())]
        add('limits', answer['boundary_text'], rules)
        add('recommendations', answer.get('check_text'), check=answer.get('check_evidence_refs', []))
    elif narrative:
        for part in narrative:
            heading = part['heading']
            kind = part.get('analysis_section')
            if heading in {'结论边界', '依据与限制'} or '冲突' in heading:
                section = 'limits'
            elif heading == '可观察行为':
                section = 'observations'
            elif text_only and heading == '结合教学与学习资料':
                section = 'materials'
            elif kind == 'analysis' or heading in {'综合分析', 'AI综合分析'} or heading.startswith('AI分析'):
                section = 'analysis'
            elif kind == 'recommendations' or heading == '教师建议' or heading.startswith('教学建议'):
                section = 'recommendations'
            else:
                continue
            add(section, part.get('text'), part.get('evidence_refs', []), part.get('content_kind'))
    else:
        registry = evidence_index(answer)
        add('analysis', (direct.get('text') or '').strip() or answer.get('answer'),
            [r for r in direct.get('evidence_refs', []) if r in registry])
        if paragraphs:
            for limit in answer.get('limitations', []):
                add('limits', limit)

    if not statements:
        for conflict in answer.get('conflicts', []):
            add('limits', conflict.get('reason'), conflict.get('evidence_refs', []))
    if text_only:
        registry = evidence_index(answer)
        def verified(ref):
            versions=registry.get(ref,{})
            return len(versions)==1 and any(
                e['evidence_type'] in {'teacher_plan','teacher_report','teaching_activity'}
                and e.get('verification_status')=='supported' and e.get('time_scope') is None
                and e['provenance'].get('document_id') and not e['provenance'].get('is_example')
                and e['provenance'].get('lesson_id',plan['lesson_id'])==plan['lesson_id']
                for e in versions.values())
        paragraphs=[p for p in paragraphs if p['evidence_refs'] and all(map(verified,p['evidence_refs']))]
        scope['document_ids']=sorted({next(iter(registry[ref].values()))['provenance']['document_id']
                                     for p in paragraphs for ref in p['evidence_refs']})
        scope['scope_kind']='lesson_text'
    if not paragraphs:
        raise HTTPException(422, 'answer_not_adoptable')
    refs = [ref for part in paragraphs for key in ('evidence_refs', 'check_evidence_refs') for ref in part.get(key, [])]
    return {'scope': scope, 'paragraphs': paragraphs, 'source_versions': _required(answer, refs)}


def _contains(outer, inner):
    return window(inner) and window(outer) and outer[0] <= inner[0] <= inner[1] <= outer[1]


def _intervals(service, plan):
    bounds = plan.get('effective_time_range')
    if not window(bounds):
        return []
    if plan.get('roster_subject_id'):
        raw = plan.get('profile_intervals') or []
    elif plan.get('subject_id'):
        run, _ = service.source.run(plan['run_id'])
        reader = IdentityReader(service.identity_dir)
        raw = [
            {**binding, 'track_id': t['track_id']}
            for t in service.source.tracks(plan['run_id'])
            for binding in reader.intersect(plan['run_id'], t['track_id'], *bounds, run['metadata']['source_sha256'])
            if binding['subject_id'] == plan['subject_id']
        ]
    elif plan.get('track_id'):
        raw = [{'track_id': plan['track_id'], 'start': bounds[0], 'end': bounds[1]}]
    else:
        return []
    if plan.get('track_id'):
        raw = [item for item in raw if item['track_id'] == plan['track_id']]
    return [(i['track_id'], max(bounds[0], i['start']), min(bounds[1], i['end']))
            for i in raw if i['start'] <= bounds[1] and i['end'] >= bounds[0]]


def _covers_intervals(available, required):
    for track, start, end in required:
        edge = start
        for lo, hi in sorted((lo, hi) for tid, lo, hi in available if tid == track and hi >= start):
            if lo > edge:
                return False
            edge = max(edge, hi)
            if edge >= end:
                break
        else:
            return False
    return True


def _covers_subject(service, target, source):
    target_person = target.get('subject_id') or target.get('roster_subject_id')
    source_person = source.get('subject_id') or source.get('roster_subject_id')
    if not target_person and not target.get('track_id'):
        return True
    if source_person and target_person != source_person:
        return False
    if not source_person and not source.get('track_id'):
        return False
    if not target_person:
        return target.get('track_id') == source.get('track_id')
    if target.get('run_id') != source.get('run_id') or target.get('clip_id') != source.get('clip_id'):
        # Track numbers and binding intervals are local to a run.
        return False
    required = _intervals(service, source)
    if not required:
        # Text-only answers about one roster/verified person can stay in that
        # person's report, without claiming any bound video time.
        return target_person == source_person and (
            not target.get('track_id') or target['track_id'] == source.get('track_id'))
    return _covers_intervals(_intervals(service, target), required)


def source_reasons(required, registry, labels=None):
    reasons = []
    for kind in ('source_missing', 'source_version'):
        for ref, version in required.items():
            versions = registry.get(ref)
            if (kind == 'source_missing' and not versions) or (
                    kind == 'source_version' and versions and version not in versions):
                label = labels.label(ref) if labels else ref
                message = f'草稿未收录来源：{label}。' if kind == 'source_missing' else f'草稿中的来源版本不同：{label}。'
                reasons.append({'kind': kind, 'evidence_id': ref, 'message': message})
    return reasons


def mismatch_reasons(service, target, answer, content, document, actor):
    source = answer['query_plan']
    target_plan = target.get('query_plan') or {}
    labels = _SourceLabels(service, answer, actor, answer.get('evidence', []))
    reasons = []
    if content['scope'].get('scope_kind')=='lesson_text':
        source_person=source.get('subject_id') or source.get('roster_subject_id')
        target_person=target_plan.get('subject_id') or target_plan.get('roster_subject_id')
        if not source.get('lesson_id') or target_plan.get('lesson_id')!=source['lesson_id']:
            reasons.append({'kind':'lesson','message':'草稿与回答不属于同一课次。'})
        if (target_person and target_person!=source_person) or target_plan.get('track_id') and not (
                target_person and target_person==source_person):
            reasons.append({'kind':'subject','message':'草稿未覆盖本回答的学生范围。'})
        return reasons + source_reasons(content['source_versions'], evidence_index(document), labels)
    for kind, mismatch, message in (
        ('run', target_plan.get('run_id') != source['run_id'], '草稿与回答属于不同的分析记录。'),
        ('clip', target_plan.get('clip_id') != source['clip_id'], '草稿与回答引用的录像片段不同。'),
        ('subject', not _covers_subject(service, target_plan, source), '草稿的人物或有效绑定区间未覆盖本回答的人物范围。'),
        ('time', not _contains(target_plan.get('effective_time_range'), source['effective_time_range']),
         '草稿的时间范围未完整覆盖本回答。'),
    ):
        if mismatch:
            if kind == 'time':
                target_window = target_plan.get('effective_time_range')
                old = _time_range(target_window) if window(target_window) else '未核实'
                message += f'登记时间：草稿 {old}，回答 {_time_range(source["effective_time_range"])}。'
            reasons.append({'kind': kind, 'message': message})
    return reasons + source_reasons(content['source_versions'], evidence_index(document), labels)


def complete_first_registry(document, base, content):
    """Only supplement a new draft from retrieval authorized for its own scope."""
    authorized = evidence_index(base, retrieved=True)
    selected = evidence_index(document)
    for ref, version in content['source_versions'].items():
        if ref not in selected and version in authorized.get(ref, {}):
            document['evidence'].append(authorized[ref][version])


def adoption_blocks(adoption):
    fid=adoption['fusion_report_id']
    for i,part in enumerate(adoption['paragraphs']):
        yield {
            'block_id':'block_'+digest([fid,i,part['section_id']])[:32],
            'section_id':part['section_id'],'origin':'adopted','text':part['text'],
            'evidence_refs':part['evidence_refs'],'source_statement_id':part.get('source_statement_id'),
            'version':adoption['adopted_version'],'actor':adoption['actor'],'reference_status':'verified',
            'fusion_report_id':fid,'subject_label':adoption.get('scope',{}).get('subject_label'),
            **{k:part[k] for k in ('check_scope','check_evidence_refs') if k in part},
        }


def restore_section_refs(doc):
    sections = {s['section_id']: s for s in doc['sections']}
    for adoption in doc.get('adoptions', []):
        for part in adoption['paragraphs']:
            section = sections.get(part['section_id'])
            if section is None:continue
            section['evidence_refs'] = unique(section['evidence_refs'] + part['evidence_refs'])


def append_adoption(doc, fusion_report_id, version, actor, content):
    known = {s['section_id'] for s in doc['sections']}
    content = {**content, 'paragraphs': [p if p['section_id'] in known else {**p,'section_id':'analysis'}
                                        for p in content['paragraphs']]}
    adoption = {
        **content, 'fusion_report_id': fusion_report_id, 'adopted_version': version,
        'actor': actor, 'created_at': now(),
        'section_ids': unique(part['section_id'] for part in content['paragraphs']),
    }
    doc.setdefault('adoptions', []).append(adoption)
    doc.setdefault('blocks', []).extend(adoption_blocks(adoption))
    restore_section_refs(doc)
    return adoption


def find_adoption(doc, fid):
    return next((item for item in doc.get('adoptions', []) if item['fusion_report_id'] == fid), None)


def result(rid, version, adoption, *, duplicate=False, created=False):
    return {'outcome': 'already_adopted' if duplicate else 'adopted',
            'report_id': rid, 'version': version,
            **{key: adoption[key] for key in ('adopted_version', 'fusion_report_id', 'section_ids')},
            **({'created': True} if created else {})}


def serialized(repo, validate, write):
    """Validate through normal loaders before the lock, then fence that read.

    Fusion loaders can themselves mark stale rows using a writer connection.
    The connection's data_version detects any intervening context DB commit,
    including one made by a loader, so validation never waits on our own lock.
    """
    for _ in range(8):
        with repo.connection() as c:
            revision = c.execute('PRAGMA data_version').fetchone()[0]
            checked = validate()
            c.execute('BEGIN IMMEDIATE')
            if c.execute('PRAGMA data_version').fetchone()[0] == revision:
                return write(c, checked)
    raise HTTPException(409, 'report_sources_changed; reload_current_version')


def insert_version(c, rid, version, base_id, origin, actor, doc, instruction):
    c.execute('INSERT INTO lesson_report_versions VALUES(?,?,?,?,?,?,?,?)',
              (rid, version, base_id, origin, actor, now(), json.dumps(doc, ensure_ascii=False), instruction))
