"""Human-readable citations derived from one authorized report version."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import cached_property
from html import escape
import json
import math
from pathlib import Path, PurePosixPath
import re

from src.runtime_paths import stored_path
from src.tracking.video import registered_clip, safe_path


MISSING_REF = '出处不可用（该版本缺少来源记录）'
UNKNOWN_TIME = '时间位置无法核实'
UNKNOWN_LOCATION = '原文位置未记录'
MATERIAL_KINDS = {
    'teacher_plan': '教案', 'assignment': '作业要求', 'activity_schedule': '课堂活动安排',
    'student_work': '学生作业', 'graded_work': '作业批改', 'assessment_result': '测评记录',
    'learning_material': '学习资料', 'rubric': '评价标准', 'teacher_observation': '教师观察',
    'teacher_note': '教师补充说明', 'classroom_memory': '课堂记忆', 'course_info': '课程信息',
    'learning_goals': '教学目标', 'other_material': '其他资料',
}
VIDEO_EVIDENCE = {
    'dataset_annotation', 'vlm_observation', 'human_visual_review', 'person_track',
    'identity_binding', 'event_attribution', 'model_discovered_event', 'human_confirmed_event',
}


@dataclass(frozen=True)
class ReportMedia:
    run_id: str
    clip_id: str
    video_id: str
    path: Path
    start: float
    end: float
    filename: str
    file_hash: str
    uploaded: bool

    def matches(self, provenance):
        return all(
            provenance.get(key) in (None, getattr(self, key))
            for key in ('run_id', 'clip_id', 'video_id')
        )


def _table_exists(connection, name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def resolve_report_media(service, classroom, actor):
    """Resolve the base fusion report's registered file, never a lookalike."""
    run_id = classroom['run_id']
    run, clip = service.source.verified_run(run_id)
    clip_id, video_id = clip['clip_id'], clip['video_id']
    if classroom.get('clip_id') not in (None, clip_id) or classroom.get('video_id') not in (None, video_id):
        raise ValueError('report_recording_mismatch')
    path = safe_path(clip, service.source.video_root)
    processing_hash = run['metadata']['source_sha256']
    uploaded = clip.get('source_kind') == 'uploaded_recording'
    filename, digest = path.name, processing_hash
    if uploaded:
        with service.repo.connection() as c:
            row = c.execute(
                "SELECT payload,original_sha256 FROM analysis_videos "
                "WHERE owner=? AND clip_id=? AND video_id=? AND status='active'",
                (actor, clip_id, video_id),
            ).fetchone() if _table_exists(c, 'analysis_videos') else None
        if not row:
            raise PermissionError('report_video_unavailable')
        metadata = json.loads(row['payload'])
        if (metadata.get('processing_sha256') != processing_hash
                or (metadata.get('media_path') and stored_path(metadata['media_path']) != path)):
            raise ValueError('report_video_processing_source_mismatch')
        filename = PurePosixPath(metadata['filename'].replace('\\', '/')).name
        digest = row['original_sha256']
        if not filename or filename in {'.', '..'}:
            raise ValueError('report_video_missing_filename')
    if not _is_digest(digest):
        raise ValueError('report_video_missing_hash')
    return ReportMedia(run_id, clip_id, video_id, path, clip['start'], clip['end'], filename, digest.lower(), uploaded)


def _is_digest(digest):
    return isinstance(digest, str) and re.fullmatch(r'[0-9a-fA-F]{64}', digest) is not None


def _file_identifier(service, actor, digest):
    prefix = digest[:12]
    with service.repo.connection() as c:
        uploads = c.execute(
            "SELECT original_sha256 FROM analysis_videos WHERE owner=? AND status='active' "
            "AND lower(substr(original_sha256,1,12))=?", (actor, prefix)
        ).fetchall() if _table_exists(c, 'analysis_videos') else []
    candidates = {row[0].lower() for row in uploads if _is_digest(row[0])}
    # Legacy recordings have no analysis_videos row. Inspect only colliding
    # tracking hashes, and never treat a managed playback hash as an original.
    for row in service.source.rows(
        "SELECT DISTINCT clip_id,json_extract(metadata,'$.source_sha256') AS digest "
        "FROM tracking_runs WHERE status='completed' "
        "AND lower(substr(json_extract(metadata,'$.source_sha256'),1,12))=?",
        (prefix,),
    ):
        other = row['digest']
        if not _is_digest(other) or other.lower() == digest:
            continue
        try:
            clip = registered_clip(service.source.registry_db, clip_id=row['clip_id'])
        except ValueError:
            continue
        if clip.get('source_kind') != 'uploaded_recording' and clip.get('decode_status') != 'deleted':
            candidates.add(other.lower())
    candidates.discard(digest)
    length = 12
    while any(other.startswith(digest[:length]) for other in candidates):
        length += 1
    return digest[:length]


def _lesson_title(service, actor, video_id, lesson_id):
    with service.repo.connection() as c:
        if not _table_exists(c, 'lesson_sessions'):
            return '登记录像'
        if lesson_id:
            rows = c.execute(
                'SELECT title FROM lesson_sessions WHERE owner=? AND video_id=? AND lesson_id=?',
                (actor, video_id, lesson_id),
            ).fetchall()
        else:
            rows = c.execute(
                'SELECT title FROM lesson_sessions WHERE owner=? AND video_id=? LIMIT 2',
                (actor, video_id),
            ).fetchall()
    return rows[0]['title'] if len(rows) == 1 else '登记录像'


def locate_media_range(media, time_scope):
    """Return verified (file-local, registered) ranges, or None."""
    if not isinstance(time_scope, (tuple, list)) or len(time_scope) != 2:
        return None
    lo, hi = time_scope
    start, end = media.start, media.end
    if not all(type(t) in (int, float) and math.isfinite(t) for t in (lo, hi, start, end)):
        return None
    if start > end or lo > hi or lo < start - .002 or hi > end + .002:
        return None
    lo, hi = (min(end, max(start, t)) for t in (lo, hi))
    # Subtract in decimal so an offset does not nudge a half-ms time
    # just below the round-up boundary.
    local = tuple(float(Decimal(str(t)) - Decimal(str(start))) for t in (lo, hi))
    return local, (lo, hi)


def _time_range(interval):
    def stamp(t):
        millis = int((Decimal(str(t)) * 1000).to_integral_value(rounding=ROUND_HALF_UP))
        sign = '-' if millis < 0 else ''
        seconds, fraction = divmod(abs(millis), 1000)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        base = (f'{hours:02d}:' if hours else '') + f'{minutes:02d}:{seconds:02d}'
        return sign + base + (f'.{fraction:03d}'.rstrip('0') if fraction else '')

    start, end = map(stamp, interval)
    return start if start == end else f'{start}–{end}'


def _short_quote(text):
    normalized = _inline_text(text)
    return normalized[:24] + ('…' if len(normalized) > 24 else '')


def _inline_text(text):
    return ' '.join((text or '').split())


def escape_markdown(text):
    """Preserve the printed text while neutralizing inline Markdown/HTML."""
    return re.sub(r'([\\`*_\[\]~])', r'\\\1', escape(str(text), quote=False))


class _SourceLabels:
    def __init__(self, service, base, actor, evidence):
        self.service = service
        self.classroom = base.get('classroom') or {}
        self.actor = actor
        self.evidence = {e['evidence_id']: e for e in evidence}
        self.quote_counts = {}
        self.documents = {}

    @cached_property
    def media(self):
        try:
            return resolve_report_media(self.service, self.classroom, self.actor)
        except (ValueError, PermissionError, OSError, KeyError, TypeError):
            return None

    @cached_property
    def video_heading(self):
        media = self.media
        title = _lesson_title(self.service, self.actor, media.video_id, self.classroom.get('lesson_id'))
        identifier = _file_identifier(self.service, self.actor, media.file_hash)
        return f'录像《{_inline_text(title)}》· {media.filename}〔{identifier}〕'

    def video_range(self, time_scope, subject_label=None):
        media = self.media
        if media is None:
            return f'录像出处不可用 · {UNKNOWN_TIME}'
        ranges = locate_media_range(media, time_scope)
        if ranges is None:
            return f'{self.video_heading}· {UNKNOWN_TIME}'
        local, registered = ranges
        label = f'{self.video_heading}· 文件内 {_time_range(local)}'
        if media.start != 0:
            label += f'（登记时间 {_time_range(registered)}）'
        if subject_label:
            label += f' · {_inline_text(subject_label)}'
        return label

    def video(self, evidence, provenance):
        media = self.media
        if media is None or not media.matches(provenance):
            return f'录像出处不可用 · {UNKNOWN_TIME}'
        return self.video_range(evidence.get('time_scope'))

    def _activity_source(self, provenance, connection):
        ref = provenance.get('passage_ref') or ''
        pid = ref.removeprefix('text:')
        if pid:
            try:
                passage = self.service.repo.passage(pid, self.actor, c=connection)
            except (ValueError, PermissionError):
                passage = None
            if passage and passage['document_id'] == provenance['document_id']:
                saved = self.evidence.get('text:'+pid, {})
                saved_source = saved.get('provenance') or {}
                if saved_source.get('document_id') != passage['document_id']:
                    saved, saved_source = {}, {}
                return {'content': passage['content'], **saved}, {
                    **provenance,
                    **{key: passage[key] for key in ('page_or_slide', 'source_start', 'source_end')},
                    **saved_source,
                }
        return {}, {**provenance, 'page_or_slide': None}

    def material(self, evidence, provenance, activity=False):
        did = provenance.get('document_id')
        prefix = '计划活动 · ' if activity else ''
        repo = self.service.repo
        try:
            with repo.connection() as c:
                if did not in self.documents:
                    doc = repo.document(did, self.actor, c=c)
                    self.documents[did] = {k: doc[k] for k in ('title', 'source_type', 'file_type', 'is_example')}
                document = self.documents[did]
                if activity:
                    evidence, provenance = self._activity_source(provenance, c)
                page = provenance.get('page_or_slide')
                quote = _short_quote(evidence.get('content'))
                location = UNKNOWN_LOCATION
                if type(page) is int and page > 0:
                    unit = '张幻灯片' if document['file_type'].lower().lstrip('.') == 'pptx' else '页'
                    location = f'第{page}{unit}'
                elif quote:
                    if did not in self.quote_counts:
                        self.quote_counts[did] = Counter(
                            _short_quote(row[0]) for row in c.execute(
                                'SELECT content FROM context_passages WHERE document_id=?', (did,)
                            )
                        )
                    location = f'段首“{quote}”'
                    if self.quote_counts[did][quote] > 1:
                        start, end = provenance.get('source_start'), provenance.get('source_end')
                        location += (
                            f'（摘录字符 {start + 1}–{end}）'
                            if type(start) is int and type(end) is int and 0 <= start < end
                            else f'（{UNKNOWN_LOCATION}）'
                        )
        except (ValueError, PermissionError):
            return prefix + f'资料出处不可用 · {UNKNOWN_LOCATION}'
        kind = MATERIAL_KINDS.get(provenance.get('source_kind') or document['source_type'], '教学资料')
        title = _inline_text(provenance.get('title') or document['title'])
        label = f'{prefix}{kind}《{title}》· {location}'
        if provenance.get('is_example') or provenance.get('text_source_status') == 'project_example' or document['is_example']:
            label += '（项目示例）'
        return label

    def label(self, ref):
        evidence = self.evidence.get(ref)
        if evidence is None:
            return MISSING_REF
        provenance = evidence.get('provenance') or {}
        kind = evidence['evidence_type']
        if kind == 'knowledge_rule':
            rule = provenance.get('rule') or {}
            name = _inline_text(rule.get('name') or rule.get('title') or provenance.get('title'))
            text = evidence.get('content') or rule.get('rule') or rule.get('ambiguities')
            return f'判断边界规则《{name or _short_quote(text) or "未命名规则"}》'
        if kind in VIDEO_EVIDENCE:
            return self.video(evidence, provenance)
        if kind in {'teacher_plan', 'teacher_report', 'teaching_activity'}:
            return self.material(evidence, provenance, activity=kind == 'teaching_activity')
        return f'出处不可用 · {UNKNOWN_LOCATION}'


def source_labels(service, base, doc, actor):
    formatter = _SourceLabels(service, base, actor, doc['evidence'])
    refs = list(dict.fromkeys(
        [ref for section in doc['sections'] for ref in section['evidence_refs']]
        + [ref for adoption in doc.get('adoptions', [])
           for part in adoption.get('paragraphs', [])
           for key in ('evidence_refs', 'check_evidence_refs')
           for ref in part.get(key, [])]
    ))
    return {ref: formatter.label(ref) for ref in refs}


def adoption_check_labels(service, base, doc, actor):
    formatter = _SourceLabels(service, base, actor, doc['evidence'])
    labels = {}
    for adoption in doc.get('adoptions', []):
        fid = adoption.get('fusion_report_id')
        if not fid:
            continue
        for part in adoption.get('paragraphs', []):
            check_scope = part.get('check_scope')
            if check_scope and check_scope.get('effective_time_range'):
                labels[fid] = formatter.video_range(
                    check_scope['effective_time_range'],
                    check_scope.get('subject_label'),
                )
                break
    return labels
