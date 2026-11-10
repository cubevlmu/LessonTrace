import json
import math
from src.identity.store import uid,now
from src.agent.fusion.evidence import version


def migrate(repo):
    with repo.connection() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS validation_track_reviews(review_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,track_id INTEGER NOT NULL,start REAL NOT NULL,end REAL NOT NULL,decision TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,source_hash TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS validation_track_scope ON validation_track_reviews(run_id,track_id,start,end);
    CREATE TABLE IF NOT EXISTS validation_qa_scores(review_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,report_id TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,report_hash TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS validation_qa_report ON validation_qa_scores(report_id,actor,created_at);
    CREATE TABLE IF NOT EXISTS validation_actor_assessments(review_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,native_binding_id TEXT,actor TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS validation_question_approvals(review_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS validation_identity_assessments(review_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
    ''')


def tracks(repo,rid,tid,start,end):
    with repo.connection() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='validation_track_reviews'").fetchone():return []
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in c.execute('SELECT * FROM validation_track_reviews WHERE run_id=? AND track_id=? AND start<=? AND end>=? ORDER BY created_at,review_id',(rid,tid,end,start))]


def track_scope(repo,rid,tid,start,end,source_hash):
    reviews=tracks(repo,rid,tid,start,end);valid=[r for r in reviews if r['source_hash']==source_hash]
    points=sorted({start,end,*[max(start,min(end,r[k])) for r in valid for k in ['start','end']],
        *[r['payload']['problem_time'] for r in valid if r['payload'].get('problem_time') is not None and start<=r['payload']['problem_time']<=end]})
    def at(t):
        covering=[r for r in valid if r['start']<=t<=r['end'] and not(r['decision']=='id_switch' and t<r['payload']['problem_time'])]
        return covering[-1] if covering else None
    safe=[];problems=[]
    for a,b in zip(points,points[1:]):
        r=at((a+b)/2)
        if r and r['decision']=='continuous':
            left=a if at(a) and at(a)['decision']=='continuous' else math.nextafter(a,b)
            right=b if at(b) and at(b)['decision']=='continuous' else math.nextafter(b,a)
            if left<=right:safe.append([left,right])
        elif r and r['decision'] in {'id_switch','fragmented'}:problems.append([a,b])
    if start==end:
        r=at(start)
        if r and r['decision']=='continuous':safe=[[start,end]]
        elif r and r['decision'] in {'id_switch','fragmented'}:problems=[[start,end]]
    covered=bool(valid) and all(at(t) and at(t)['decision']=='continuous' for t in points) and all(at((a+b)/2) and at((a+b)/2)['decision']=='continuous' for a,b in zip(points,points[1:]))
    return {'status':'verified' if covered else ('invalid' if problems else 'unverified'),'time_scope':[start,end],
        'verified_intervals':safe,'problem_intervals':problems,'review_ids':[r['review_id'] for r in valid],
        'source_version':version([{k:r[k] for k in ['review_id','start','end','decision','source_hash','payload']} for r in reviews])}


def append_track(repo,body,actor,source_hash):
    rid=uid()
    with repo.connection() as c:c.execute('INSERT INTO validation_track_reviews VALUES (?,?,?,?,?,?,?,?,?,?)',(rid,body.run_id,body.track_id,body.start,body.end,body.decision,actor,now(),source_hash,json.dumps(body.model_dump(),ensure_ascii=False)))
    return {'review_id':rid,'reviewer':actor,'decision':body.decision,'scope':[body.start,body.end]}


def append_json(repo,table,values):
    if table not in {'validation_qa_scores','validation_actor_assessments','validation_question_approvals','validation_identity_assessments'}:raise ValueError('invalid_validation_table')
    with repo.connection() as c:c.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in values)+')',values)
