import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from src.identity.store import uid,now
from .knowledge import rules


def tokens(text):
    """Unicode Latin words plus CJK unigrams/bigrams, indexed by real FTS5."""
    words=re.findall(r'[a-zA-Z0-9_]+',text.lower())
    for run in re.findall(r'[\u3400-\u9fff]+',text):words.extend(run);words.extend(run[i:i+2] for i in range(len(run)-1))
    return list(dict.fromkeys(words))


class ContextRepository:
    def __init__(self,path):
        self.path=Path(path).resolve()
        with self.connection() as c:
            c.executescript(Path(__file__).with_name('migration.sql').read_text())
            for r in rules():c.execute('INSERT OR IGNORE INTO knowledge_rules VALUES (?,?,?,?)',(r['rule_id'],r['category'],r['version'],json.dumps(r,ensure_ascii=False)))

    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA secure_delete=ON')
        try:
            with c:yield c
        finally:c.close()

    def revision(self):
        with self.connection() as c:return c.execute("SELECT value FROM context_meta WHERE key='revision'").fetchone()[0]

    def audit(self,c,object_id,actor,operation,payload):
        c.execute('INSERT INTO context_audit VALUES (?,?,?,?,?,?)',(uid(),object_id,actor,operation,now(),json.dumps(payload,ensure_ascii=False)))
        c.execute("UPDATE context_meta SET value=value+1 WHERE key='revision'")

    def document(self,did,actor,write=False,c=None):
        if c is None:
            with self.connection() as cx:return self.document(did,actor,write,cx)
        row=c.execute('SELECT * FROM context_documents WHERE document_id=?',(did,)).fetchone()
        if not row:raise ValueError('document_not_found')
        if row['owner']!=actor and (write or not c.execute('SELECT 1 FROM document_grants WHERE document_id=? AND actor=?',(did,actor)).fetchone()):raise PermissionError('document_access_denied')
        if row['status'] in {'deleted','superseded'}:raise ValueError('document_deleted_or_superseded')
        return dict(row)

    def passage(self,pid,actor,c=None):
        if c is None:
            with self.connection() as cx:return self.passage(pid,actor,cx)
        r=c.execute('SELECT * FROM context_passages WHERE passage_id=?',(pid,)).fetchone()
        if not r:raise ValueError('passage_not_found')
        self.document(r['document_id'],actor,c=c)
        return dict(r)

    def documents(self,actor):
        with self.connection() as c:
            rows=[dict(r) for r in c.execute("SELECT document_id,title,source_type,file_type,file_hash,parser_version,uploaded_by,created_at,status,is_example,version FROM context_documents d WHERE status NOT IN ('deleted','superseded') AND (owner=? OR EXISTS(SELECT 1 FROM document_grants g WHERE g.document_id=d.document_id AND g.actor=?)) ORDER BY created_at",(actor,actor))]
            scopes={r['document_id']:dict(r) for r in c.execute('SELECT * FROM context_document_scope')}
            for row in rows:row['scope']={k:v for k,v in scopes.get(row['document_id'],{}).items() if k!='document_id'}
            return rows
