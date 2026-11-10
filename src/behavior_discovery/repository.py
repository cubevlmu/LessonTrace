import json
from src.identity.store import uid,now


def migrate(repo):
    with repo.connection() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS analysis_videos(video_id TEXT PRIMARY KEY,owner TEXT NOT NULL,original_sha256 TEXT NOT NULL,clip_id TEXT NOT NULL,recording_id TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,original_sha256));
    CREATE TABLE IF NOT EXISTS analysis_jobs(job_id TEXT PRIMARY KEY,video_id TEXT NOT NULL REFERENCES analysis_videos(video_id),owner TEXT NOT NULL,fingerprint TEXT NOT NULL UNIQUE,status TEXT NOT NULL,stage TEXT NOT NULL,run_id TEXT,call_limit INTEGER NOT NULL,attempted_calls INTEGER NOT NULL DEFAULT 0,completed_calls INTEGER NOT NULL DEFAULT 0,failed_calls INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS discovery_windows(window_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES analysis_jobs(job_id),run_id TEXT NOT NULL,track_id INTEGER NOT NULL,start REAL NOT NULL,end REAL NOT NULL,segment_id INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS discovery_window_job ON discovery_windows(job_id,status,start,track_id);
    CREATE TABLE IF NOT EXISTS discovery_call_attempts(attempt_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,cache_key TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS discovery_response_cache(cache_key TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS discovered_behavior_events(event_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,video_id TEXT NOT NULL,run_id TEXT NOT NULL,track_id INTEGER NOT NULL,action_category TEXT NOT NULL,local_start REAL NOT NULL,local_end REAL NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS discovered_event_lookup ON discovered_behavior_events(run_id,track_id,local_start,local_end);
    CREATE TABLE IF NOT EXISTS analysis_event_heads(job_id TEXT NOT NULL,event_id TEXT NOT NULL REFERENCES discovered_behavior_events(event_id),PRIMARY KEY(job_id,event_id));
    CREATE TABLE IF NOT EXISTS discovered_behavior_reviews(review_id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES discovered_behavior_events(event_id),actor TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS analysis_job_audit(audit_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,created_at TEXT NOT NULL,operation TEXT NOT NULL,payload TEXT NOT NULL);
    ''')


class Repository:
    def __init__(self,repo):self.repo=repo;migrate(repo)
    def video(self,vid,actor=None):
        with self.repo.connection() as c:r=c.execute('SELECT * FROM analysis_videos WHERE video_id=?',(vid,)).fetchone()
        if not r:raise ValueError('analysis_video_missing')
        if actor is not None and r['owner']!=actor:raise PermissionError('analysis_video_owner_required')
        return {**dict(r),'metadata':json.loads(r['payload'])}
    def job(self,jid,actor=None):
        with self.repo.connection() as c:r=c.execute('SELECT * FROM analysis_jobs WHERE job_id=?',(jid,)).fetchone()
        if not r:raise ValueError('analysis_job_missing')
        if actor is not None and r['owner']!=actor:raise PermissionError('analysis_job_owner_required')
        result={**dict(r),'config_and_runtime':json.loads(r['payload'])}
        with self.repo.connection() as c:
            counts=dict(c.execute('SELECT status,COUNT(*) FROM discovery_windows WHERE job_id=? GROUP BY status',(jid,)))
        result.update(total_windows=sum(counts.values()),processed_windows=counts.get('completed',0),failed_windows=counts.get('error',0),
            estimated_remaining_work=counts.get('pending',0)+counts.get('running',0),window_states=counts)
        return result
    def update(self,jid,**values):
        if set(values)-{'status','stage','run_id','payload','call_limit'}:raise ValueError('invalid_job_update')
        with self.repo.connection() as c:
            c.execute('UPDATE analysis_jobs SET '+','.join(k+'=?' for k in values)+',updated_at=? WHERE job_id=?',(*values.values(),now(),jid))
    def audit(self,jid,operation,data):
        with self.repo.connection() as c:c.execute('INSERT INTO analysis_job_audit VALUES (?,?,?,?,?)',(uid(),jid,now(),operation,json.dumps(data,ensure_ascii=False)))
    def events(self,run_id,start=None,end=None,track_id=None):
        sql='SELECT e.payload FROM discovered_behavior_events e JOIN analysis_jobs j ON j.job_id=e.job_id JOIN analysis_videos v ON v.video_id=e.video_id WHERE e.run_id=? AND v.status=\'active\' AND (EXISTS(SELECT 1 FROM analysis_event_heads h WHERE h.event_id=e.event_id) OR EXISTS(SELECT 1 FROM discovered_behavior_reviews r WHERE r.event_id=e.event_id))';args=[run_id]
        for expr,value in [('e.local_end>=?',start),('e.local_start<=?',end),('e.track_id=?',track_id)]:
            if value is not None:sql+=' AND '+expr;args.append(value)
        with self.repo.connection() as c:result=[json.loads(r[0]) for r in c.execute(sql+' ORDER BY e.local_start,e.track_id,e.event_id',args)]
        for e in result:
            with self.repo.connection() as c:reviews=[{**dict(r),'data':json.loads(r['payload'])} for r in c.execute('SELECT * FROM discovered_behavior_reviews WHERE event_id=? ORDER BY created_at,review_id',(e['event_id'],))]
            e['human_review_history']=reviews;e['review_status']=reviews[-1]['status'] if reviews else 'unreviewed'
        return result
