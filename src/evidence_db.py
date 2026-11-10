"""Additive region-evidence storage. Legacy detection tables remain untouched."""
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from src.db import ClassroomDB
from src.schema import HumanRegionReview


def local_time(global_time, clip):
    if not math.isfinite(global_time) or not clip['start'] <= global_time < clip['end']:
        raise ValueError('target_time_not_covered')
    value = global_time - clip['start']
    if clip.get('duration') is not None and value >= clip['duration']:
        raise ValueError('target_exceeds_decoded_duration')
    return value


class EvidenceDB(ClassroomDB):
    def __init__(self, db_path='data/evidence_replay.db'):
        super().__init__(str(db_path))
        with self._get_connection() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS region_annotations (
              id TEXT PRIMARY KEY, video_id TEXT NOT NULL, action TEXT NOT NULL,
              start REAL NOT NULL, end REAL NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS region_lookup ON region_annotations(video_id,start,end);
            CREATE TABLE IF NOT EXISTS registered_clips (
              id TEXT PRIMARY KEY, video_id TEXT NOT NULL, start REAL NOT NULL,
              end REAL NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS clip_lookup ON registered_clips(video_id,start,end);
            CREATE TABLE IF NOT EXISTS visual_reviews (
              id TEXT PRIMARY KEY, event_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS visual_review_lookup ON visual_reviews(event_id,created_at);
            CREATE TABLE IF NOT EXISTS region_reviews (
              id TEXT PRIMARY KEY, event_id TEXT NOT NULL, created_at TEXT NOT NULL,
              payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS review_lookup ON region_reviews(event_id,created_at);
            CREATE TABLE IF NOT EXISTS agent_reviews (
              id TEXT PRIMARY KEY, video_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    def events(self, video_id=None, action=None, start=None, end=None, todo=False):
        conditions=[]; values=[]
        for expression,value in [('video_id=?',video_id),('action=?',action),('end>=?',start),('start<=?',end)]:
            if value is not None:conditions.append(expression);values.append(value)
        if start is not None and end is not None and start > end:raise ValueError('invalid_time_window')
        with self._get_connection() as c:
            rows=c.execute('SELECT payload FROM region_annotations'+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY start,id',values).fetchall()
        result=[]
        for row in rows:
            e=json.loads(row[0])
            if todo and not e['review_todo']:continue
            history=self.history(e['event_id'])
            e['review_status']=history[-1]['decision'] if history else 'not_independently_reviewed'
            result.append(e)
        return result

    def event(self, event_id):
        with self._get_connection() as c:
            row=c.execute('SELECT payload FROM region_annotations WHERE id=?',(event_id,)).fetchone()
        if row is None:raise KeyError('event_not_found')
        return json.loads(row[0])

    def clip(self, clip_id):
        with self._get_connection() as c:
            row=c.execute('SELECT payload FROM registered_clips WHERE id=?',(clip_id,)).fetchone()
        if row is None:raise KeyError('clip_not_registered')
        return json.loads(row[0])

    def candidates(self, event_id, target=None):
        e=self.event(event_id)
        if target is not None and not e['t_start'] <= target <= e['t_end']:raise ValueError('target_outside_event')
        with self._get_connection() as c:
            rows=c.execute('SELECT payload FROM registered_clips WHERE video_id=? AND start<? AND end>? ORDER BY start,id',(e['video_id'],e['t_end'],e['t_start'])).fetchall()
        result=[]
        for row in rows:
            clip=json.loads(row[0]); t=target if target is not None else max(e['t_start'],clip['start'])
            try:
                clip['local_time']=local_time(t,clip);clip['target_global_time']=t
                clip['availability']='ready' if Path(clip['path']).is_file() and clip['decode_status']=='ready' else 'missing_or_undecodable'
            except ValueError as ex:clip['availability']=str(ex)
            result.append(clip)
        result.sort(key=lambda c: (not (c['start'] <= e['t_start'] and c['end'] >= e['t_end']), c.get('filename_action') != e['action'], c['end']-c['start'], c['clip_id']))
        return {'status':'candidates' if any(c['availability']=='ready' for c in result) else 'missing_coverage','candidates':result}

    def history(self,event_id):
        with self._get_connection() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT payload FROM region_reviews WHERE event_id=? ORDER BY created_at,rowid',(event_id,))]

    def review(self,event_id,request):
        request=HumanRegionReview.model_validate(request)
        if not request.reviewer.strip():raise ValueError('reviewer_required')
        e=self.event(event_id);clip=self.clip(request.clip_id)
        if e['video_id']!=clip['video_id'] or not e['t_start']<=request.global_time<=e['t_end']:raise ValueError('evidence_not_bound_to_event')
        t=local_time(request.global_time,clip)
        from src.video_evidence import read_frame
        _,error=read_frame(Path(clip['path']),t)
        if error:raise ValueError(error)
        record=request.model_dump()
        record.update(review_id=uuid.uuid4().hex,event_id=event_id,source_kind='human_review',created_at=datetime.now(timezone.utc).isoformat(),local_time=t,
                      evidence_ref={'clip_id':clip['clip_id'],'path':clip['path'],'global_time':request.global_time,'local_time':t},
                      scope='observable_action_only_not_cognitive_state')
        with self._get_connection() as c:
            c.execute('INSERT INTO region_reviews VALUES (?,?,?,?)',(record['review_id'],event_id,record['created_at'],json.dumps(record,ensure_ascii=False)))
        return record

    def detail(self,event_id):
        return {'event':self.event(event_id),'videos':self.candidates(event_id),'reviews':self.history(event_id),'visual_reviews':self.visual_history(event_id)}


    def save_visual_review(self,record):
        self.event(record['event_id'])
        with self._get_connection() as c:
            c.execute('INSERT INTO visual_reviews VALUES (?,?,?,?)',(record['review_id'],record['event_id'],record['created_at'],json.dumps(record,ensure_ascii=False,allow_nan=False)))

    def visual_history(self,event_id):
        with self._get_connection() as c:
            return [json.loads(row[0]) for row in c.execute('SELECT payload FROM visual_reviews WHERE event_id=? ORDER BY created_at,rowid',(event_id,))]

    def visual_review(self,review_id):
        with self._get_connection() as c:
            row=c.execute('SELECT payload FROM visual_reviews WHERE id=?',(review_id,)).fetchone()
        if row is None:raise KeyError('visual_review_not_found')
        return json.loads(row[0])

    def region_bounds(self,video_id):
        with self._get_connection() as c:
            row=c.execute('SELECT min(start),max(end) FROM region_annotations WHERE video_id=?',(video_id,)).fetchone()
        if row[0] is None:raise ValueError('video_id_not_registered')
        return list(row)

    def search_region_evidence(self,query):
        from src.schema import EvidenceQuery
        query=EvidenceQuery.model_validate(query)
        where=['a.video_id=?','a.end>=?','a.start<=?'];params=[query.video_id,query.start,query.end]
        if query.actions:
            where.append('a.action IN ('+','.join('?' for _ in query.actions)+')');params.extend(query.actions)
        human="(SELECT json_extract(h.payload,'$.decision') FROM region_reviews h WHERE h.event_id=a.id ORDER BY h.created_at DESC,h.rowid DESC LIMIT 1)"
        visual="(SELECT coalesce(json_extract(v.payload,'$.result.final_decision'),json_extract(v.payload,'$.result.decision')) FROM visual_reviews v WHERE v.event_id=a.id ORDER BY v.created_at DESC,v.rowid DESC LIMIT 1)"
        if query.human_status:
            where.append("coalesce("+human+",'not_independently_reviewed')=?");params.append(query.human_status)
        if query.vlm_status:
            where.append("coalesce("+visual+",'unreviewed')=?");params.append(query.vlm_status)
        if query.source_kinds:
            # Legacy YOLO events have no verified mapping to these region IDs.
            expressions={'dataset_annotation':'1=1',
                         'human_review':'EXISTS(SELECT 1 FROM region_reviews h WHERE h.event_id=a.id)',
                         'vlm_visual_review':'EXISTS(SELECT 1 FROM visual_reviews v WHERE v.event_id=a.id)',
                         'model_prediction':'0=1'}
            where.append('('+' OR '.join(expressions[k] for k in query.source_kinds)+')')
        suffix=' WHERE '+' AND '.join(where)
        with self._get_connection() as c:
            count=c.execute('SELECT count(*) FROM region_annotations a'+suffix,params).fetchone()[0]
            rows=c.execute('SELECT a.id FROM region_annotations a'+suffix+' ORDER BY a.start,a.id LIMIT ?',params+[query.limit]).fetchall()
            clips=[json.loads(r[0]) for r in c.execute('SELECT payload FROM registered_clips WHERE video_id=? AND end>? AND start<? ORDER BY start,id',(query.video_id,query.start,query.end))]
        return {'bundles':[self.detail(row[0]) for row in rows],'matched_count':count,
                'truncated':count>len(rows),'registered_clips':clips,'query':query.model_dump(),
                'dataset_time_bounds':self.region_bounds(query.video_id),
                'source_mapping_note':'Legacy YOLO model_prediction records are not joined without an explicit region/video provenance mapping.'}

    def save_agent_review(self,record):
        with self._get_connection() as c:
            c.execute('INSERT INTO agent_reviews VALUES (?,?,?,?)',(record['review_id'],record['query']['video_id'],record['created_at'],json.dumps(record,ensure_ascii=False,allow_nan=False)))

    def agent_review(self,review_id):
        with self._get_connection() as c:
            row=c.execute('SELECT payload FROM agent_reviews WHERE id=?',(review_id,)).fetchone()
        if row is None:raise KeyError('agent_review_not_found')
        return json.loads(row[0])
