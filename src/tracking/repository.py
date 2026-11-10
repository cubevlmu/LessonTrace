import json
import sqlite3
import uuid
import math
from pathlib import Path
from datetime import datetime,timezone
from contextlib import contextmanager
from .schemas import Detection


def now():return datetime.now(timezone.utc).isoformat()


class TrackingRepository:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as c:c.executescript((Path(__file__).parent/'migrations/001_tracking.sql').read_text())

    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:yield c
        finally:c.close()

    def start(self,video_id,clip_id,fingerprint,metadata,rerun=False):
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute('SELECT * FROM tracking_runs WHERE fingerprint=? ORDER BY started_at DESC,rowid DESC LIMIT 1',(fingerprint,)).fetchone()
            if prior and prior['status']=='running':raise ValueError('tracking_run_already_running; interrupt explicitly before a fresh run')
            if prior and prior['status']=='completed' and not rerun:return prior['run_id'],False
            rid=uuid.uuid4().hex
            c.execute('INSERT INTO tracking_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,video_id,clip_id,fingerprint,prior['run_id'] if prior else None,'running',now(),None,json.dumps(metadata),None,None))
            return rid,True

    def finish(self,rid,status,report,error=None):
        if status not in {'completed','failed','interrupted'}:raise ValueError('invalid_finish_status')
        with self.connection() as c:
            cur=c.execute('UPDATE tracking_runs SET status=?,finished_at=?,report=?,error_message=? WHERE run_id=? AND status=?',(status,now(),json.dumps(report),error,rid,'running'))
            if cur.rowcount!=1:raise ValueError('run_is_not_active')
            if status!='completed':c.execute("UPDATE person_tracks SET quality_flags=? WHERE run_id=?",(json.dumps(['partial_run','identity_unverified','occlusion_cause_unknown']),rid))

    def save_batch(self,rid,frames):
        with self.connection() as c:
            run=c.execute('SELECT * FROM tracking_runs WHERE run_id=?',(rid,)).fetchone()
            if not run or run['status']!='running':raise ValueError('run_is_not_active')
            offset=json.loads(run['metadata']).get('global_offset')
            for frame in frames:
                i=frame['frame_index'];local=frame['local_timestamp'];glob=frame['global_timestamp'];processed=frame['processed_index']
                if i<0 or processed<0 or not all(math.isfinite(t) and t>=0 for t in [local,glob]):raise ValueError('invalid_observation_timestamp')
                if offset is not None and abs(glob-local-offset)>1e-6:raise ValueError('observation_global_offset_mismatch')
                if frame['time_base_den']<=0 or frame['time_base_num']<=0:raise ValueError('invalid_presentation_timebase')
                for detection in frame['detections']:Detection.model_validate(detection)
                previous_frame=c.execute('SELECT frame_index,local_timestamp FROM tracking_frames WHERE run_id=? ORDER BY frame_index DESC LIMIT 1',(rid,)).fetchone()
                if previous_frame and (i<=previous_frame['frame_index'] or local<=previous_frame['local_timestamp']):raise ValueError('out_of_order_frame_batch')
                c.execute('INSERT INTO tracking_frames VALUES(?,?,?,?,?,?,?,?,?,?)',(rid,i,processed,local,glob,frame['pts'],frame['time_base_num'],frame['time_base_den'],len(frame['detections']),len(frame['matches'])))
                matches={m['detection_index']:m['track_id'] for m in frame['matches']}
                if len(matches)!=len(frame['matches']) or len(set(matches.values()))!=len(matches):raise ValueError('duplicate_tracker_assignment')
                for idx,det in enumerate(frame['detections']):
                    tid=matches.get(idx)
                    if tid is not None:
                        prior=c.execute('SELECT o.frame_index,o.global_timestamp,f.processed_index FROM track_observations o JOIN tracking_frames f ON f.run_id=o.run_id AND f.frame_index=o.frame_index WHERE o.run_id=? AND o.track_id=? ORDER BY o.frame_index DESC LIMIT 1',(rid,tid)).fetchone()
                        c.execute('''INSERT INTO person_tracks VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,track_id) DO UPDATE SET local_end_time=excluded.local_end_time,global_end_time=excluded.global_end_time,last_frame=excluded.last_frame,observed_frame_count=person_tracks.observed_frame_count+1''',(rid,tid,run['video_id'],local,local,glob,glob,i,i,1,json.dumps(['identity_unverified','no_cross_clip_identity','occlusion_cause_unknown'])))
                        c.execute('INSERT INTO track_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,tid,i,local,glob,*det['bbox'],det['confidence'],det['frame_width'],det['frame_height'],'matched_visible_detection'))
                        segment=c.execute('SELECT MAX(segment_index) FROM track_visibility_segments WHERE run_id=? AND track_id=?',(rid,tid)).fetchone()[0]
                        if prior and prior['processed_index']==processed-1 and glob-prior['global_timestamp']<=.25:
                            c.execute('UPDATE track_visibility_segments SET last_frame=?,local_end=?,global_end=? WHERE run_id=? AND track_id=? AND segment_index=?',(i,local,glob,rid,tid,segment))
                        else:
                            c.execute('INSERT INTO track_visibility_segments VALUES(?,?,?,?,?,?,?,?,?)',(rid,tid,0 if segment is None else segment+1,i,i,local,local,glob,glob))
                            c.execute('UPDATE track_gaps SET next_visible_frame=?,next_visible_global=? WHERE run_id=? AND track_id=? AND next_visible_frame IS NULL',(i,glob,rid,tid))
                    c.execute('INSERT INTO person_detections VALUES(?,?,?,?,?)',(rid,i,idx,tid,json.dumps(det)))
                if processed>0:
                    previous=c.execute('SELECT o.track_id FROM track_observations o JOIN tracking_frames f ON f.run_id=o.run_id AND f.frame_index=o.frame_index WHERE o.run_id=? AND f.processed_index=?',(rid,processed-1)).fetchall()
                    for row in previous:
                        tid=row['track_id']
                        if tid in matches.values():continue
                        last=c.execute('SELECT last_frame FROM person_tracks WHERE run_id=? AND track_id=?',(rid,tid)).fetchone()[0]
                        gi=c.execute('SELECT COALESCE(MAX(gap_index),-1)+1 FROM track_gaps WHERE run_id=? AND track_id=?',(rid,tid)).fetchone()[0]
                        c.execute('INSERT INTO track_gaps VALUES(?,?,?,?,?,?,?,?,?)',(rid,tid,gi,last,i,glob,None,None,'unobserved; detector_miss_or_occlusion_or_exit_or_association_failure_unknown'))

    def run(self,rid):
        with self.connection() as c:row=c.execute('SELECT * FROM tracking_runs WHERE run_id=?',(rid,)).fetchone()
        if not row:raise KeyError('tracking_run_not_found')
        data=dict(row)
        for key in ['metadata','report']:data[key]=json.loads(data[key]) if data[key] else None
        return data

    def runs(self,video_id=None,clip_id=None):
        where=[];args=[]
        for k,v in [('video_id',video_id),('clip_id',clip_id)]:
            if v is not None:where.append(k+'=?');args.append(v)
        with self.connection() as c:ids=[r[0] for r in c.execute('SELECT run_id FROM tracking_runs'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY started_at DESC LIMIT 200',args)]
        return [self.run(r) for r in ids]

    def tracks(self,rid):
        self.run(rid)
        with self.connection() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM person_tracks WHERE run_id=? ORDER BY track_id',(rid,))]
            for row in rows:
                row['quality_flags']=json.loads(row['quality_flags'])
                row['segments']=[dict(r) for r in c.execute('SELECT * FROM track_visibility_segments WHERE run_id=? AND track_id=? ORDER BY segment_index',(rid,row['track_id']))]
                row['gaps']=[dict(r) for r in c.execute('SELECT * FROM track_gaps WHERE run_id=? AND track_id=? ORDER BY gap_index',(rid,row['track_id']))]
                row['record_id']=rid+':'+str(row['track_id'])
        return rows

    def observations(self,rid,after_frame=-1,limit=120,start=None,end=None,track_id=None):
        self.run(rid);where=['run_id=?','frame_index>?'];args=[rid,after_frame]
        if start is not None:where.append('global_timestamp>=?');args.append(start)
        if end is not None:where.append('global_timestamp<=?');args.append(end)
        with self.connection() as c:
            frames=[dict(r) for r in c.execute('SELECT * FROM tracking_frames WHERE '+' AND '.join(where)+' ORDER BY frame_index LIMIT ?',args+[limit+1])]
            more=len(frames)>limit;frames=frames[:limit]
            if not frames:return {'frames':[],'observations':[],'next_after_frame':None}
            clause=' AND track_id=?' if track_id is not None else ''
            params=[rid,frames[0]['frame_index'],frames[-1]['frame_index']]+([track_id] if track_id is not None else [])
            obs=[dict(r) for r in c.execute('SELECT * FROM track_observations WHERE run_id=? AND frame_index BETWEEN ? AND ?'+clause+' ORDER BY frame_index,track_id',params)]
        return {'frames':frames,'observations':obs,'next_after_frame':frames[-1]['frame_index'] if more else None}
