"""Lesson-scoped teaching sources, append-only relations and absence dependencies."""
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException
from pydantic import Field

from src.identity.store import now, uid
from .lessons import (
    PROCESSING_MATERIAL_STATUSES,
    READY_MATERIAL_STATUSES,
    STUDENT_MATERIAL_TYPES,
    material_readiness,
)
from .schemas import Input


ITEM_KINDS = frozenset({'goal', 'question', 'submission', 'annotation', 'rubric_clause'})
RELATION_STATUSES = frozenset({'candidate', 'active', 'revoked'})
JOB_PROCESSING = frozenset({'queued', 'running', 'pausing', 'cancelling'})
SUBMISSION_TYPES = frozenset({'student_work', 'graded_work'})
SCHEMA = """
CREATE TABLE IF NOT EXISTS lesson_items (
    item_id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lesson_sessions(lesson_id),
    kind TEXT NOT NULL CHECK(kind IN ('goal','question','submission','annotation','rubric_clause')),
    passage_id TEXT NOT NULL REFERENCES context_passages(passage_id),
    locator TEXT NOT NULL, local_key TEXT NOT NULL, source_version TEXT NOT NULL,
    owner TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lesson_items_scope ON lesson_items(lesson_id, owner, passage_id);
CREATE TABLE IF NOT EXISTS lesson_relations (
    relation_id TEXT NOT NULL, rev INTEGER NOT NULL CHECK(rev >= 1),
    lesson_id TEXT NOT NULL REFERENCES lesson_sessions(lesson_id),
    src_type TEXT NOT NULL, src_id TEXT NOT NULL, rel_type TEXT NOT NULL,
    dst_type TEXT NOT NULL, dst_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate','active','revoked')),
    basis_ref TEXT NOT NULL, basis_version TEXT NOT NULL, provenance TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(relation_id, rev)
);
CREATE INDEX IF NOT EXISTS lesson_relations_scope ON lesson_relations(lesson_id, relation_id, rev);
CREATE TABLE IF NOT EXISTS lesson_relation_events (
    event_id TEXT PRIMARY KEY, relation_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL REFERENCES lesson_sessions(lesson_id),
    from_rev INTEGER, to_rev INTEGER NOT NULL, action TEXT NOT NULL,
    actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(relation_id, to_rev),
    FOREIGN KEY(relation_id, from_rev) REFERENCES lesson_relations(relation_id, rev),
    FOREIGN KEY(relation_id, to_rev) REFERENCES lesson_relations(relation_id, rev)
);
"""


class RelationConflict(ValueError):
    """A concurrent or incompatible change requires a new review."""


def migrate(repo):
    with repo.connection() as c:
        c.executescript(SCHEMA)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _live_student(row):
    if row['status'] == 'reference_only':
        return True
    try:
        expires = datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00'))
        return expires.tzinfo is not None and expires > datetime.now(timezone.utc)
    except ValueError:
        return False


class _Lesson:
    """Current visible endpoints and sources in one context DB snapshot."""

    def __init__(self, service, c, lesson_id, actor, student_id=None):
        self.service, self.c, self.actor = service, c, actor
        self.id, self.student_id = lesson_id, student_id
        row = c.execute('SELECT * FROM lesson_sessions WHERE lesson_id=? AND owner=?',
                        (lesson_id, actor)).fetchone()
        if row is None:
            raise PermissionError('lesson_access_denied')
        self.row = dict(row)
        self.roster = {}
        cid = row['classroom_id']
        if cid:
            folder = getattr(service, 'identity_dir', None)
            db = Path(folder).resolve() / 'identity.db' if folder else None
            if not db or not db.is_file():
                raise PermissionError('lesson_classroom_access_denied')
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as cx:
                cx.row_factory = sqlite3.Row
                cx.execute('BEGIN')
                if not cx.execute(
                    "SELECT 1 FROM classrooms WHERE classroom_id=? AND owner_actor=? AND status='active'",
                    (cid, actor),
                ).fetchone():
                    raise PermissionError('lesson_classroom_access_denied')
                self.roster = {r['subject_id']: r for r in cx.execute(
                    """SELECT s.subject_id,s.display_name,s.status,s.expires_at
                       FROM classroom_students cs JOIN registered_subjects s USING(subject_id)
                       WHERE cs.classroom_id=? AND cs.status='active'
                       AND s.status IN ('active','reference_only')""", (cid,),
                ) if _live_student(r)}
        if student_id is not None and student_id not in self.roster:
            raise PermissionError('lesson_student_access_denied')

    @property
    def video_id(self):
        return self.row['video_id']

    def video(self, *, require_access=False):
        if not self.video_id:
            return None
        row = self.c.execute('SELECT video_id,owner,status,clip_id FROM analysis_videos WHERE video_id=?',
                             (self.video_id,)).fetchone()
        if not row:
            return None
        if row['owner'] != self.actor:
            if require_access:
                raise PermissionError('lesson_video_access_denied')
            return None
        return row if row['status'] == 'active' else None

    @property
    def documents(self):
        if hasattr(self, '_documents'):
            return self._documents
        self._documents = {}
        for did in json.loads(self.row['document_ids']):
            doc = self.c.execute(
                """SELECT d.document_id,d.title,d.filename,d.source_type,d.file_type,d.file_hash,
                          d.parser_version,d.status,d.version,d.owner,d.is_example,
                          s.run_id,s.classroom_id,s.subject_id,s.track_id
                   FROM context_documents d LEFT JOIN context_document_scope s USING(document_id)
                   WHERE d.document_id=? AND d.status NOT IN ('deleted','superseded')
                   AND (d.owner=? OR EXISTS(SELECT 1 FROM document_grants g
                                            WHERE g.document_id=d.document_id AND g.actor=?))""",
                (did, self.actor, self.actor),
            ).fetchone()
            if doc is None:
                continue
            cid, sid = doc['classroom_id'], doc['subject_id']
            if (cid and cid != self.row['classroom_id']) or (sid and sid not in self.roster):
                continue
            if self.student_id and sid and sid != self.student_id:
                continue
            self._documents[did] = dict(doc)
        return self._documents

    def document(self, did, *, write=False):
        doc = self.documents.get(did)
        if not doc or (write and doc['owner'] != self.actor):
            raise PermissionError('lesson_document_access_denied')
        return doc

    def passages(self, did):
        self.document(did)
        if not hasattr(self, '_passages'):
            self._passages = {}
        if did not in self._passages:
            self._passages[did] = list(self.c.execute(
                """SELECT * FROM context_passages WHERE document_id=?
                   ORDER BY source_start,source_end,passage_id""", (did,),
            ))
        return self._passages[did]

    def document_version(self, did):
        doc = self.document(did)
        if not hasattr(self, '_versions'):
            self._versions = {}
        if did not in self._versions:
            # The library's metadata version also changes on video/track rebind.
            # The graph version tracks extracted source content, not those binds.
            self._versions[did] = _hash([
                [doc[k] for k in ('file_hash', 'parser_version', 'source_type', 'status', 'filename')],
                [(p['passage_id'], p['content'], p['page_or_slide'], p['source_start'],
                  p['source_end'], p['extraction_status']) for p in self.passages(did)],
            ])
        return self._versions[did]

    def passage(self, pid, *, write=False):
        row = self.c.execute('SELECT * FROM context_passages WHERE passage_id=?', (pid,)).fetchone()
        if row is None:
            raise PermissionError('lesson_passage_access_denied')
        self.document(row['document_id'], write=write)
        return row

    def item(self, item_id, *, current=True, write=False):
        row = self.c.execute('SELECT * FROM lesson_items WHERE lesson_id=? AND owner=? AND item_id=?',
                             (self.id, self.actor, item_id)).fetchone()
        if row is None:
            raise PermissionError('lesson_item_access_denied')
        passage = self.passage(row['passage_id'], write=write)
        if current and row['source_version'] != self.document_version(passage['document_id']):
            raise PermissionError('lesson_item_source_changed')
        return {**dict(row), 'document_id': passage['document_id'], 'locator': json.loads(row['locator'])}

    def items(self):
        result = []
        for row in self.c.execute(
            """SELECT i.*,p.document_id FROM lesson_items i JOIN context_passages p USING(passage_id)
               WHERE lesson_id=? AND owner=? ORDER BY p.source_start,i.local_key,i.item_id""",
            (self.id, self.actor),
        ):
            did = row['document_id']
            if did in self.documents and row['source_version'] == self.document_version(did):
                result.append({**dict(row), 'locator': json.loads(row['locator'])})
        return result

    def endpoint(self, kind, key, *, current=True):
        if kind == 'document':
            self.document(key)
            return key
        if kind == 'passage':
            return self.passage(key)['document_id']
        if kind == 'item':
            return self.item(key, current=current)['document_id']
        if kind == 'student':
            if key not in self.roster:
                raise PermissionError('lesson_student_access_denied')
        elif kind == 'video':
            if key != self.video_id or not self.video():
                raise PermissionError('lesson_video_access_denied')
        else:
            raise ValueError('unsupported_lesson_relation_endpoint')
        return None

    def basis_document(self, ref, *, current=True):
        if ref in self.documents:
            return ref
        if self.c.execute('SELECT 1 FROM lesson_items WHERE item_id=?', (ref,)).fetchone():
            return self.item(ref, current=current)['document_id']
        return self.passage(ref)['document_id']

    def basis_version(self, endpoints, ref, *, current=True):
        docs = {did for kind, key in endpoints if (did := self.endpoint(kind, key, current=current))}
        if ref not in docs:
            # A basis may be an attached document, item or passage, but not opaque text.
            docs.add(self.basis_document(ref, current=current))
        return _hash([(did, self.document_version(did)) for did in sorted(docs)])

    def relation_version(self, row, *, current=True):
        if _ownership(row) and row['basis_ref'] == row['src_id'] and row['provenance'] in {
            'explicit_material_scope', 'filename_rule', 'header_rule'
        }:
            doc = self.document(row['src_id'])
            self.endpoint('student', row['dst_id'])
            basis = [doc['document_id'], doc['file_hash'], doc['source_type'], doc['filename']]
            if row['provenance'] == 'header_rule':
                basis.append(sorted(_hint_names(doc, self.passages(doc['document_id']))[1]))
            return _hash(basis)
        return self.basis_version(((row['src_type'], row['src_id']), (row['dst_type'], row['dst_id'])),
                                  row['basis_ref'], current=current)

    def relations(self):
        """Latest revisions only. A stale source never falls back to an earlier revision."""
        result = []
        for row in self.c.execute(
            """SELECT r.* FROM lesson_relations r WHERE r.lesson_id=? AND r.rev=(
                   SELECT MAX(p.rev) FROM lesson_relations p WHERE p.relation_id=r.relation_id)
               ORDER BY r.relation_id""", (self.id,),
        ):
            try:
                version = self.relation_version(row, current=row['status'] != 'revoked')
            except PermissionError:
                continue
            if row['status'] != 'revoked' and _ownership(row) and not _ownership_scope_matches(self, row):
                continue
            if row['status'] == 'revoked' or version == row['basis_version']:
                result.append(dict(row))
        return result


def _put_item(lesson, passage, kind, local_key, locator):
    if kind not in ITEM_KINDS or not isinstance(local_key, str) or not local_key.strip():
        raise ValueError('invalid_lesson_item')
    if not isinstance(locator, dict):
        raise ValueError('invalid_lesson_item_locator')
    start, end = locator.get('start'), locator.get('end')
    if not (type(start) is int and type(end) is int and 0 <= start < end <= len(passage['content'])):
        raise ValueError('invalid_lesson_item_offsets')
    locator = {**locator, 'page_or_slide': passage['page_or_slide'],
               'source_start': passage['source_start'] + start, 'source_end': passage['source_start'] + end}
    version = lesson.document_version(passage['document_id'])
    pid = passage['passage_id']
    item_id = _hash([lesson.id, pid, kind, local_key, locator, version])
    lesson.c.execute('INSERT INTO lesson_items VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id) DO NOTHING',
                     (item_id, lesson.id, kind, pid, _json(locator), local_key, version, lesson.actor, now()))
    return lesson.item(item_id)


def upsert_item(service, lesson_id, actor, *, kind, passage_id, local_key, locator):
    """Insert an immutable item, or reuse it for identical input and source version."""
    with service.repo.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        lesson = _Lesson(service, c, lesson_id, actor)
        return _put_item(lesson, lesson.passage(passage_id, write=True), kind, local_key, locator)


def list_items(service, lesson_id, actor, student_id=None):
    with service.repo.connection() as c:
        c.execute('BEGIN')
        return _visible(_Lesson(service, c, lesson_id, actor, student_id))[1]


def get_item(service, lesson_id, actor, item_id, student_id=None):
    for item in list_items(service, lesson_id, actor, student_id):
        if item['item_id'] == item_id:
            return item
    raise PermissionError('lesson_item_access_denied')


def _remove_item(lesson, item_id):
    if lesson.c.execute(
        """SELECT 1 FROM lesson_relations WHERE lesson_id=? AND
           ((src_type='item' AND src_id=?) OR (dst_type='item' AND dst_id=?) OR basis_ref=?)""",
        (lesson.id, item_id, item_id, item_id),
    ).fetchone():
        raise RelationConflict('lesson_item_has_relation_history')
    lesson.c.execute('DELETE FROM lesson_items WHERE item_id=?', (item_id,))


def update_item(service, lesson_id, actor, item_id, *, local_key=None, locator=None):
    """Replace an unreferenced item without mutating a stable evidence reference."""
    with service.repo.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        lesson = _Lesson(service, c, lesson_id, actor)
        old = lesson.item(item_id, write=True)
        new = _put_item(lesson, lesson.passage(old['passage_id']), old['kind'],
                        old['local_key'] if local_key is None else local_key,
                        old['locator'] if locator is None else locator)
        if new['item_id'] != item_id:
            _remove_item(lesson, item_id)
        return new


def delete_item(service, lesson_id, actor, item_id):
    """Remove unreferenced items only, preserving every audited relation endpoint."""
    with service.repo.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        lesson = _Lesson(service, c, lesson_id, actor)
        lesson.item(item_id, current=False, write=True)
        _remove_item(lesson, item_id)


def _append(lesson, row, prior, action, reason):
    created_at = now()
    rev = prior['rev'] + 1 if prior else 1
    row = {**row, 'rev': rev, 'actor': lesson.actor, 'created_at': created_at}
    lesson.c.execute(
        """INSERT INTO lesson_relations(relation_id,rev,lesson_id,src_type,src_id,rel_type,
           dst_type,dst_id,status,basis_ref,basis_version,provenance,actor,created_at)
           VALUES (:relation_id,:rev,:lesson_id,:src_type,:src_id,:rel_type,:dst_type,:dst_id,
                   :status,:basis_ref,:basis_version,:provenance,:actor,:created_at)""", row,
    )
    lesson.c.execute('INSERT INTO lesson_relation_events VALUES (?,?,?,?,?,?,?,?,?)',
                     (uid(), row['relation_id'], lesson.id, prior['rev'] if prior else None,
                      rev, action, lesson.actor, reason, created_at))
    lesson.service.repo.audit(lesson.c, row['relation_id'], lesson.actor, 'lesson_relation_' + action,
                              {'lesson_id': lesson.id, 'rev': rev, 'status': row['status']})
    return row


def _latest(lesson, relation_id):
    return lesson.c.execute(
        'SELECT * FROM lesson_relations WHERE lesson_id=? AND relation_id=? ORDER BY rev DESC LIMIT 1',
        (lesson.id, relation_id),
    ).fetchone()


def _ownership(row):
    return (row['src_type'] == 'document' and row['rel_type'] == 'belongs_to'
            and row['dst_type'] == 'student')


def _ownership_scope_matches(lesson, row):
    doc = lesson.document(row['src_id'])
    return (doc['source_type'] in STUDENT_MATERIAL_TYPES
            and doc['subject_id'] in (None, row['dst_id']))


def _check_owner(lesson, row):
    if not _ownership(row):
        return
    if not _ownership_scope_matches(lesson, row):
        raise RelationConflict('lesson_document_student_scope_mismatch')
    if lesson.c.execute(
        """SELECT 1 FROM lesson_relations r WHERE r.lesson_id=? AND r.src_type='document'
           AND r.src_id=? AND r.rel_type='belongs_to' AND r.dst_type='student' AND r.dst_id!=?
           AND r.status='active' AND r.rev=(SELECT MAX(p.rev) FROM lesson_relations p
                                             WHERE p.relation_id=r.relation_id) LIMIT 1""",
        (lesson.id, row['src_id'], row['dst_id']),
    ).fetchone():
        raise RelationConflict('lesson_document_already_has_confirmed_student')


def _record(lesson, src_type, src_id, rel_type, dst_type, dst_id, basis_ref, provenance, trusted=False):
    relation_id = _hash([lesson.id, src_type, src_id, rel_type, dst_type, dst_id])
    row = dict(relation_id=relation_id, lesson_id=lesson.id, src_type=src_type, src_id=src_id,
               rel_type=rel_type, dst_type=dst_type, dst_id=dst_id, basis_ref=basis_ref,
               provenance=provenance, status='active' if trusted else 'candidate')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', rel_type):
        raise ValueError('invalid_lesson_relation_type')
    if rel_type == 'belongs_to':
        if not _ownership(row) or lesson.document(src_id)['source_type'] not in STUDENT_MATERIAL_TYPES:
            raise ValueError('ownership_requires_student_work_and_roster_student')
        if lesson.document(src_id)['subject_id'] not in (None, dst_id):
            raise PermissionError('lesson_document_student_scope_mismatch')
    row['basis_version'] = lesson.relation_version(row)
    old = _latest(lesson, relation_id)
    if old:
        if old['status'] != 'candidate':
            return dict(old)  # Never change an active or revoked decision on replay.
        if all(old[key] == row[key] for key in ('basis_ref', 'basis_version', 'provenance', 'status')):
            return dict(old)
    if trusted:
        _check_owner(lesson, row)
    return _append(lesson, row, old, 'extract', provenance)


def record_relation(service, lesson_id, actor, *, src_type, src_id, rel_type, dst_type, dst_id,
                    basis_ref, provenance, trusted=False):
    """Internal importer. Only explicit trusted IDs/mappings may set trusted=True."""
    with service.repo.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        return _record(_Lesson(service, c, lesson_id, actor), src_type, src_id, rel_type, dst_type,
                       dst_id, basis_ref, provenance, trusted)


def _decide(service, lesson_id, relation_id, actor, status, reason, expected_rev):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('lesson_relation_reason_required')
    with service.repo.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        lesson = _Lesson(service, c, lesson_id, actor)
        row = _latest(lesson, relation_id)
        if row is None:
            raise PermissionError('lesson_relation_access_denied')
        version = lesson.relation_version(row, current=False)
        if expected_rev is not None and row['rev'] != expected_rev:
            raise RelationConflict('lesson_relation_revision_changed')
        if status == 'active':
            if row['status'] == 'revoked':
                raise RelationConflict('lesson_relation_already_revoked')
            if version != row['basis_version']:
                raise RelationConflict('lesson_relation_source_changed')
            _check_owner(lesson, row)
        if row['status'] == status:
            return dict(row)
        return _append(lesson, {**dict(row), 'status': status}, row,
                       'confirm' if status == 'active' else 'revoke', reason.strip())


def confirm_relation(service, lesson_id, relation_id, actor, reason='teacher confirmed', *, expected_rev=None):
    return _decide(service, lesson_id, relation_id, actor, 'active', reason, expected_rev)


def revoke_relation(service, lesson_id, relation_id, actor, reason='teacher revoked', *, expected_rev=None):
    return _decide(service, lesson_id, relation_id, actor, 'revoked', reason, expected_rev)


def list_relations(service, lesson_id, actor, student_id=None, *, status=None):
    if status is not None and status not in RELATION_STATUSES:
        raise ValueError('invalid_lesson_relation_status')
    with service.repo.connection() as c:
        c.execute('BEGIN')
        lesson = _Lesson(service, c, lesson_id, actor, student_id)
        records = _visible(lesson)[2]
        return [r for r in records if status is None or r['status'] == status]


_GOAL = re.compile(r'^\s*#*\s*(?:教学目标|学习目标|课时目标|目标\s*[一二三四五六七八九十\d]*|(?:learning\s+)?objectives?|goals?)\s*(?:[:：]|$)', re.I)
_CLAUSE = re.compile(r'^\s*#*\s*(?:评分标准|评分细则|评分条款|评分点|得分点|rubric(?:\s+clause)?|criterion|criteria)\s*[\d一二三四五六七八九十]*\s*(?:[:：]|$)', re.I)
_QUESTIONS = re.compile(r'^\s*#*\s*(?:题目|练习题|作业题|questions?)\s*[:：]?\s*$', re.I)
_NUMBER = re.compile(r'^\s*(?:第\s*([\d一二三四五六七八九十]+)\s*题\s*[.．、:：)）]?|(?:Q(?:uestion)?\s*)?(\d+)\s*[.．、:：)）](?!\d)|[（(]([\d一二三四五六七八九十]+)[）)])', re.I)
_HEADING_NAME = re.compile(r'^\s*(?:学生(?:姓名)?|姓名|提交人|student(?:\s+name)?|name)\s*[:：]\s*(.+?)\s*$', re.I)
_FILENAME_LABEL = re.compile(r'(?:(?:课堂)?作业|答卷|试卷|提交|批改|评分|成绩|assignment|homework|submission|graded work|assessment)', re.I)


def _normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _hint_names(doc, passages):
    # Exact normalized roster tokens, never substring or fuzzy name attribution.
    stem = _normalized(Path(doc['filename']).stem)
    parts = re.split(r'[_\-—·\s]+', stem)
    stripped = _FILENAME_LABEL.sub(' ', stem).strip(' _-—·')
    filename = {stem, stripped, *parts, *re.split(r'[_\-—·]+', stripped)}
    headers = set()
    lines = '\n'.join(p['content'][:800] for p in passages[:2]).splitlines()[:6]
    for line in lines:
        if match := _HEADING_NAME.fullmatch(line):
            name = re.split(r'[\t\s]*[;；|，,]\s*|\s+(?:学号|班级|student id)\s*[:：]',
                            match.group(1), maxsplit=1, flags=re.I)[0]
            headers.add(_normalized(name))
    return {_normalized(value) for value in filename if value.strip()}, headers


def _rule_items(doc, passages):
    source_type = doc['source_type']
    default = {'learning_goals': 'goal', 'rubric': 'rubric_clause', 'student_work': 'submission',
               'graded_work': 'annotation', 'assessment_result': 'annotation'}.get(source_type)
    section = default
    for sequence, passage in enumerate(passages, 1):
        emitted = False
        for match in re.finditer(r'[^\r\n]+', passage['content']):
            line = match.group()
            goal, clause, number = _GOAL.match(line), _CLAUSE.match(line), _NUMBER.match(line)
            kind, key = None, None
            if _QUESTIONS.fullmatch(line):
                section = 'question'
                emitted = True
            elif goal or clause:
                heading = goal or clause
                section = 'goal' if goal else 'rubric_clause'
                if line[heading.end():].strip():
                    kind = section
                emitted = True  # Bare section headings are not evidence items.
            elif number:
                kind = section or ('question' if source_type in {'assignment', 'learning_material',
                                                                 'teacher_plan', 'other_material'} else None)
                if kind:
                    key = f'{kind}:{_normalized(next(x for x in number.groups() if x is not None))}'
            elif section in {'goal', 'rubric_clause'} and line.strip():
                kind = section
            if kind:
                emitted = True
                start = match.start() + len(line) - len(line.lstrip())
                end = match.start() + len(line.rstrip())
                yield passage, kind, key or f'{kind}:{sequence}.{start}', {'start': start, 'end': end}
        if not emitted and default in {'submission', 'annotation'}:
            yield passage, default, f'{default}:{sequence}', {'start': 0, 'end': len(passage['content'])}


def extract_document(service, lesson_id, document_id, actor, *, expected_version=None):
    """Worker/import hook. A queued extraction may pin the immutable source or the library version."""
    with service.repo.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        lesson = _Lesson(service, c, lesson_id, actor)
        doc = lesson.document(document_id, write=True)
        source_version = lesson.document_version(document_id)
        if expected_version is not None and expected_version not in (source_version, doc['version']):
            return {'status': 'stale', 'source_version': source_version, 'items': [], 'relations': []}
        passages = lesson.passages(document_id)
        items = [_put_item(lesson, p, kind, key, locator)
                 for p, kind, key, locator in _rule_items(doc, passages)]
        relations = []
        if doc['source_type'] in STUDENT_MATERIAL_TYPES and lesson.roster:
            if sid := doc['subject_id']:
                try:
                    relations.append(_record(lesson, 'document', document_id, 'belongs_to', 'student',
                                             sid, document_id, 'explicit_material_scope', True))
                except RelationConflict:
                    # A metadata edit is not permission to replace a reviewed attribution.
                    pass
            else:
                filenames, headers = _hint_names(doc, passages)
                cipher = Fernet((Path(service.identity_dir) / 'encryption.key').read_bytes())
                for sid, person in sorted(lesson.roster.items()):
                    name = _normalized(json.loads(cipher.decrypt(person['display_name']))) if person['display_name'] else ''
                    if name and name in filenames | headers:
                        relations.append(_record(lesson, 'document', document_id, 'belongs_to', 'student',
                                                 sid, document_id, 'header_rule' if name in headers else 'filename_rule'))
        return {'status': 'extracted', 'source_version': source_version, 'items': items, 'relations': relations}


def refresh_attached_document(service, document_id, actor):
    """Refresh after a library metadata or transcription edit, outside its write transaction."""
    with service.repo.connection() as c:
        ids = [r[0] for r in c.execute(
            """SELECT lesson_id FROM lesson_sessions l WHERE owner=? AND
               EXISTS(SELECT 1 FROM json_each(l.document_ids) WHERE value=?)""", (actor, document_id),
        )]
    for lesson_id in ids:
        try:
            extract_document(service, lesson_id, document_id, actor)
        except PermissionError:
            continue


def _relation_documents(lesson, relation):
    return {key for key in (
        lesson.endpoint(relation['src_type'], relation['src_id'], current=False),
        lesson.endpoint(relation['dst_type'], relation['dst_id'], current=False),
        lesson.basis_document(relation['basis_ref'], current=False),
    ) if key}


def _visible(lesson):
    docs, items, relations = lesson.documents, lesson.items(), lesson.relations()
    sid = lesson.student_id
    if sid is None:
        return docs, items, relations
    owned = {r['src_id']: r['dst_id'] for r in relations if _ownership(r) and r['status'] == 'active'}
    visible = {did: doc for did, doc in docs.items()
               if (doc['source_type'] not in STUDENT_MATERIAL_TYPES and not doc['subject_id'])
               or owned.get(did) == sid or (doc['source_type'] not in STUDENT_MATERIAL_TYPES
                                          and doc['subject_id'] == sid)}
    review = {r['src_id'] for r in relations if _ownership(r) and r['dst_id'] == sid
              and r['status'] in {'candidate', 'revoked'} and owned.get(r['src_id'], sid) == sid}
    scoped = []
    for r in relations:
        people = {r[k + '_id'] for k in ('src', 'dst') if r[k + '_type'] == 'student'}
        if people - {sid}:
            continue
        allowed = visible.keys() | {r['src_id']} if _ownership(r) and r['src_id'] in review else visible.keys()
        if _relation_documents(lesson, r) <= allowed:
            scoped.append(r)
    return visible, [i for i in items if i['document_id'] in visible], scoped


def _light_document(doc, readiness):
    return {**{key: doc[key] for key in ('document_id', 'title', 'source_type', 'file_type', 'status', 'version', 'is_example')},
            'readiness': readiness}


def lesson_index(service, lesson_id, actor, student_id=None):
    with service.repo.connection() as c:
        c.execute('BEGIN')
        lesson = _Lesson(service, c, lesson_id, actor, student_id)
        docs, items, relations = _visible(lesson)
        active = [r for r in relations if r['status'] == 'active']
        pending = [r for r in relations if r['status'] == 'candidate']
        review_docs = {r['src_id']: lesson.documents[r['src_id']] for r in pending if _ownership(r)}
        readiness = material_readiness((docs | review_docs).values(), active)
        return {'lesson_id': lesson_id, 'title': lesson.row['title'], 'classroom_id': lesson.row['classroom_id'],
                'video_id': lesson.video_id if lesson.video() else None, 'student_id': student_id,
                'roster_size': len(lesson.roster),
                'documents': [_light_document(doc, readiness['by_document'][did]) for did, doc in docs.items()],
                'items': items, 'relations': active, 'candidates': pending, 'readiness': readiness}


def _video_query(lesson, filters):
    from src.attribution.source import IdentityReader

    video = lesson.video(require_access=True)
    rid, tid = filters.get('run_id'), filters.get('track_id')
    if rid is not None and not isinstance(rid, str):
        raise ValueError('invalid_video_run_filter')
    if tid is not None and (type(tid) is not int or tid < 1):
        raise ValueError('invalid_video_track_filter')
    start, end = filters.get('start'), filters.get('end')
    for value in (start, end):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError('invalid_video_interval_filter')
    if start is not None and end is not None and start > end:
        raise ValueError('invalid_video_interval_filter')
    if not video:
        if rid is not None:
            raise PermissionError('lesson_run_access_denied')
        return [], False
    jobs = list(lesson.c.execute(
        'SELECT job_id,status,run_id FROM analysis_jobs WHERE video_id=? AND owner=?',
        (video['video_id'], lesson.actor),
    ))
    if rid is not None:
        jobs = [job for job in jobs if job['run_id'] == rid]
        if not jobs:
            raise PermissionError('lesson_run_access_denied')
        _, clip = lesson.service.source.run(rid)
        if clip['video_id'] != video['video_id'] or clip['clip_id'] != video['clip_id']:
            raise PermissionError('lesson_run_access_denied')
    run_cache, matches = {}, []
    conditions, args = ['j.video_id=?', 'j.owner=?', 'w.run_id=j.run_id', "w.status='completed'"], [
        video['video_id'], lesson.actor]
    for condition, value in (('w.run_id=?', rid), ('w.track_id=?', tid), ('w.end>=?', start), ('w.start<=?', end)):
        if value is not None:
            conditions.append(condition)
            args.append(value)
    for row in lesson.c.execute(
        """SELECT w.window_id,w.run_id,w.track_id,w.start,w.end
           FROM discovery_windows w JOIN analysis_jobs j USING(job_id) WHERE """
        + ' AND '.join(conditions) + ' ORDER BY w.window_id', args,
    ):
        key = row['run_id']
        if key not in run_cache:
            try:
                run, clip = lesson.service.source.verified_run(key)
                run_cache[key] = (run['metadata']['source_sha256'] if
                                  clip['video_id'] == video['video_id'] and clip['clip_id'] == video['clip_id']
                                  else None)
            except (ValueError, OSError):
                run_cache[key] = None
        if not (source_hash := run_cache[key]):
            continue
        lo = max(row['start'], start) if start is not None else row['start']
        hi = min(row['end'], end) if end is not None else row['end']
        if lesson.student_id:
            reader = IdentityReader(lesson.service.identity_dir)
            bindings = reader.intersect(key, row['track_id'], lo, hi, source_hash)
            for b in bindings:
                if b['subject_id'] == lesson.student_id:
                    matches.append([row['window_id'], source_hash, b['identity_binding_id'], b['start'], b['end']])
        else:
            matches.append([row['window_id'], source_hash, lo, hi])
    return sorted(matches), any(j['status'] in JOB_PROCESSING for j in jobs)


def _material_query(lesson, kind, filters):
    docs, items, relations = _visible(lesson)
    doc_type = {
        'assignment': {'assignment'},
        'submission': SUBMISSION_TYPES,
        'rubric_clause': {'rubric'},
    }[kind]
    if kind == 'assignment' and lesson.student_id:
        kind, doc_type = 'submission', SUBMISSION_TYPES
    ownerships = [r for r in relations if _ownership(r) and lesson.documents[r['src_id']]['source_type'] in doc_type]
    review_ids = {r['src_id'] for r in ownerships if r['status'] in {'candidate', 'revoked'}}
    if did := filters.get('document_id'):
        if did not in docs and did not in review_ids:
            raise PermissionError('lesson_document_access_denied')
    local_key = filters.get('local_key')
    question_id = filters.get('question_id')
    if question_id is not None:
        question = lesson.item(question_id)
        if question['kind'] != 'question':
            raise ValueError('lesson_question_required')
        if question['document_id'] not in docs:
            raise PermissionError('lesson_item_access_denied')
    relevant_docs = {did: doc for did, doc in docs.items()
                     if doc['source_type'] in doc_type and did == filters.get('document_id', did)}
    relevant_items = {i['item_id']: i for i in items if i['kind'] == kind
                      and (not local_key or local_key == i['local_key'])
                      and i['document_id'] == filters.get('document_id', i['document_id'])}
    matched_docs = relevant_docs.keys() | {i['document_id'] for i in relevant_items.values()}
    relevant_relations, matched_items, linked_docs = [], set(), set()
    bound_docs = {r['src_id'] for r in ownerships if r['status'] == 'active'}
    for r in relations:
        endpoints = {(r['src_type'], r['src_id']), (r['dst_type'], r['dst_id'])}
        related = (_relation_documents(lesson, r) & matched_docs
                   or kind == 'submission' and _ownership(r) and
                   lesson.documents[r['src_id']]['source_type'] in doc_type and
                   r['src_id'] == filters.get('document_id', r['src_id']))
        matches_question = question_id is None or ('item', question_id) in endpoints
        if not related or (not matches_question and not (kind == 'submission' and _ownership(r))):
            continue
        relevant_relations.append([r['relation_id'], r['rev'], r['status'], r['basis_version']])
        if r['status'] == 'active' and matches_question:
            matched_items.update(key for tag, key in endpoints if tag == 'item' and key in relevant_items)
            linked_docs.update(key for tag, key in endpoints if tag == 'document' and key in relevant_docs)
    if question_id is not None:
        relevant_items = {key: item for key, item in relevant_items.items()
                          if key in matched_items or item['document_id'] in linked_docs}
    if kind == 'submission':
        # Unconfirmed work is a review task, not evidence that anyone submitted it.
        relevant_items = {key: item for key, item in relevant_items.items() if item['document_id'] in bound_docs}
    positive = [[key, item['source_version']] for key, item in relevant_items.items()]
    if not local_key and question_id is None and kind in {'assignment', 'submission'}:
        positive.extend([did, lesson.document_version(did)] for did, doc in relevant_docs.items()
                        if doc['status'] in READY_MATERIAL_STATUSES and (kind == 'assignment' or did in bound_docs))
    pending_docs = relevant_docs.keys() | {r['src_id'] for r in ownerships
                                           if kind == 'submission' and r['status'] == 'candidate'
                                           and r['src_id'] == filters.get('document_id', r['src_id'])}
    pending = any(lesson.documents[did]['status'] in PROCESSING_MATERIAL_STATUSES for did in pending_docs)
    ambiguous = (any(r[2] == 'candidate' for r in relevant_relations)
                 or any(doc['status'] not in READY_MATERIAL_STATUSES | PROCESSING_MATERIAL_STATUSES
                        for doc in relevant_docs.values())
                 or kind == 'submission' and not lesson.student_id and bool(relevant_docs.keys() - bound_docs))
    return sorted(positive), pending, ambiguous, sorted(relevant_relations)


def negative_query_digest(service, lesson_id, actor, kind, **filters):
    """Hash a repeatable, authorized query scope. Unauthorized lookups raise PermissionError."""
    kind = {'homework': 'submission', 'rubric': 'rubric_clause'}.get(kind, kind)
    allowed = {
        'assignment': {'student_id', 'document_id', 'local_key', 'question_id'},
        'submission': {'student_id', 'document_id', 'local_key', 'question_id'},
        'rubric_clause': {'student_id', 'document_id', 'local_key', 'question_id'},
        'video_interval': {'student_id', 'run_id', 'track_id', 'start', 'end'},
    }
    if kind not in allowed or filters.keys() - allowed[kind]:
        raise ValueError('invalid_lesson_negative_query')
    filters = {k: v for k, v in filters.items() if v is not None}
    for key in {'student_id', 'document_id', 'local_key', 'question_id'} & filters.keys():
        if not isinstance(filters[key], str) or not filters[key]:
            raise ValueError('invalid_lesson_negative_query_filter')
    for key in {'start', 'end'} & filters.keys():
        if type(filters[key]) in (int, float):
            filters[key] = float(filters[key]) or 0.0
    with service.repo.connection() as c:
        c.execute('BEGIN')
        lesson = _Lesson(service, c, lesson_id, actor, filters.get('student_id'))
        scope = {'lesson_id': lesson_id, 'actor': actor, 'classroom_id': lesson.row['classroom_id'],
                 'filters': filters}
        if kind == 'video_interval':
            matches, processing = _video_query(lesson, filters)
            scope.update(video_id=lesson.video_id if lesson.video() else None, time_reference='dataset_global')
            ambiguous, revisions = False, []
        else:
            matches, processing, ambiguous, revisions = _material_query(lesson, kind, filters)
        state = 'present' if matches else 'processing' if processing else 'ambiguous' if ambiguous else 'no_data'
        payload = {'version': 1, 'kind': kind, 'scope': scope, 'state': state,
                   'matches': matches, 'relation_revisions': revisions}
        return {**payload, 'digest': _hash(payload)}


class RelationDecision(Input):
    expected_rev: int | None = Field(None, ge=1)
    reason: str | None = Field(None, min_length=1, max_length=2000)


def mount(app, service, account, writer):
    migrate(service.repo)
    base = '/api/context/lessons/{lesson_id}'

    @app.get(base + '/index')
    def index(lesson_id: str, student_id: str | None = None, a=Depends(account)):
        return lesson_index(service, lesson_id, a['actor'], student_id)

    @app.get(base + '/relations')
    def relations(lesson_id: str, student_id: str | None = None,
                  status: Literal['candidate', 'active', 'revoked'] | None = None, a=Depends(account)):
        return list_relations(service, lesson_id, a['actor'], student_id, status=status)

    def decide(operation, lesson_id, relation_id, actor, body):
        options = body.model_dump(exclude_none=True) if body else {}
        try:
            return operation(service, lesson_id, relation_id, actor, **options)
        except RelationConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post(base + '/relations/{relation_id}/confirm')
    def confirm(lesson_id: str, relation_id: str, body: RelationDecision | None = None, a=Depends(writer)):
        return decide(confirm_relation, lesson_id, relation_id, a['actor'], body)

    @app.post(base + '/relations/{relation_id}/revoke')
    def revoke(lesson_id: str, relation_id: str, body: RelationDecision | None = None, a=Depends(writer)):
        return decide(revoke_relation, lesson_id, relation_id, a['actor'], body)
