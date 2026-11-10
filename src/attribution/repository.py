import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class AttributionRepository:
    def __init__(self,path):
        self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as c:c.executescript(Path(__file__).with_name('migration.sql').read_text())

    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:yield c
        finally:c.close()

    def payload(self,table,key,value):
        if (table,key) not in {('event_track_candidates','candidate_id'),('event_track_reviews','review_id')}:
            raise ValueError('invalid_table')
        with self.connection() as c:r=c.execute('SELECT payload FROM '+table+' WHERE '+key+'=?',(value,)).fetchone()
        if not r:raise ValueError('attribution_record_not_found')
        return json.loads(r[0])
