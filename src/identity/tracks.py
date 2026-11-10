"""Read-only access to 11A; every capture comes from an observed decoded PTS."""
import json
import math
import sqlite3
from pathlib import Path
from collections import OrderedDict
from src.tracking.video import registered_clip, safe_path, sha256, decode_frames


class TrackAccess:
    def __init__(self, tracking_db, registry_db, video_root):
        self.tracking_db=Path(tracking_db).resolve()
        self.registry_db=Path(registry_db).resolve()
        self.video_root=Path(video_root).resolve()
        self._frame_cache=None;self._frame_cache_bytes=0;self._frame_cache_limit=0

    def enable_frame_cache(self,max_bytes=32*1024*1024):
        """Opt-in bounded reuse of exact decoded pixels across tracked people."""
        self._frame_cache=OrderedDict();self._frame_cache_bytes=0;self._frame_cache_limit=max_bytes

    def rows(self, sql, params=()):
        with sqlite3.connect(self.tracking_db.as_uri()+'?mode=ro',uri=True) as c:
            c.row_factory=sqlite3.Row
            return [dict(r) for r in c.execute(sql,params)]

    def runs(self):
        return self.rows('SELECT run_id,video_id,clip_id,status FROM tracking_runs ORDER BY started_at DESC')

    def run(self,rid):
        rows=self.rows('SELECT * FROM tracking_runs WHERE run_id=?',(rid,))
        if not rows: raise ValueError('tracking_run_not_found')
        r=rows[0];r['metadata']=json.loads(r['metadata'])
        clip=registered_clip(self.registry_db,clip_id=r['clip_id'])
        if clip['video_id']!=r['video_id'] or clip['start']!=r['metadata'].get('global_offset'): raise ValueError('source_mapping_changed_or_missing_time_offset')
        return r,clip

    def tracks(self,rid):
        self.run(rid)
        rows=self.rows('SELECT * FROM person_tracks WHERE run_id=? ORDER BY track_id',(rid,))
        for r in rows:
            r['continuity_status']='unverified'
            r['segments']=self.rows('SELECT * FROM track_visibility_segments WHERE run_id=? AND track_id=? ORDER BY segment_index',(rid,r['track_id']))
        return rows

    def interval(self,rid,tid,start,end):
        if not all(math.isfinite(t) for t in (start,end)) or end<start: raise ValueError('invalid_interval')
        run,clip=self.run(rid)
        segments=self.rows('SELECT * FROM track_visibility_segments WHERE run_id=? AND track_id=?',(rid,tid))
        if not any(s['global_start']-1e-6<=start<=end<=s['global_end']+1e-6 for s in segments):
            raise ValueError('interval_must_fit_one_observed_visibility_segment; split_at_gaps')
        return run,clip

    def observations(self,rid,tid):
        self.run(rid)
        return self.rows('SELECT * FROM track_observations WHERE run_id=? AND track_id=? ORDER BY frame_index',(rid,tid))

    def frame(self,rid,tid,frame_index):
        return self.frames(rid,tid,[frame_index])[0]

    def frames(self,rid,tid,frame_indices):
        """Decode a bounded evidence batch once, preserving actual PTS and order."""
        indices=list(frame_indices)
        if not 1<=len(indices)<=4 or len(set(indices))!=len(indices):
            raise ValueError('evidence_batch_requires_one_to_four_distinct_frames')
        run,clip=self.run(rid)
        placeholders=','.join('?' for _ in indices)
        rows=self.rows('SELECT * FROM track_observations WHERE run_id=? AND track_id=? AND frame_index IN ('+placeholders+')',(rid,tid,*indices))
        observations={o['frame_index']:o for o in rows}
        if any(i not in observations for i in indices):raise ValueError('frame_has_no_visible_track_observation')
        if any(o['tracker_state']!='matched_visible_detection' for o in rows):raise ValueError('predicted_location_not_allowed')
        path=safe_path(clip,self.video_root)
        if sha256(path)!=run['metadata']['source_sha256']: raise ValueError('tracking_source_content_changed')
        stat=path.stat();prefix=(str(path),run['metadata']['source_sha256'],stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        found={}
        if self._frame_cache is not None:
            for index in indices:
                key=(*prefix,index)
                if key in self._frame_cache:
                    image,timestamp=self._frame_cache[key];o=observations[index]
                    if abs(timestamp-o['local_timestamp'])>.002 or abs(o['global_timestamp']-clip['start']-timestamp)>.002:raise ValueError('observation_presentation_timestamp_mismatch')
                    if image.shape[:2]!=(o['frame_height'],o['frame_width']):raise ValueError('source_dimensions_changed')
                    self._frame_cache.move_to_end(key);found[index]=(image.copy(),o,run,clip)
        if len(found)==len(indices):return [found[i] for i in indices]
        decoder=decode_frames(path)
        try:
            for data in decoder:
                if data['frame_index'] not in observations or data['frame_index'] in found:continue
                o=observations[data['frame_index']]
                if abs(data['local_timestamp']-o['local_timestamp'])>.002 or abs(o['global_timestamp']-clip['start']-data['local_timestamp'])>.002:
                    raise ValueError('observation_presentation_timestamp_mismatch')
                image=data['frame'].to_ndarray(format='bgr24')
                if image.shape[:2]!=(o['frame_height'],o['frame_width']): raise ValueError('source_dimensions_changed')
                found[data['frame_index']]=(image,o,run,clip)
                if self._frame_cache is not None and image.nbytes<=self._frame_cache_limit:
                    key=(*prefix,data['frame_index']);self._frame_cache[key]=(image.copy(),data['local_timestamp']);self._frame_cache_bytes+=image.nbytes
                    while self._frame_cache_bytes>self._frame_cache_limit:
                        _,(pixels,_)=self._frame_cache.popitem(last=False);self._frame_cache_bytes-=pixels.nbytes
                if len(found)==len(indices):break
        finally:
            decoder.close()
        if len(found)!=len(indices):raise ValueError('observed_frame_not_decodable')
        return [found[i] for i in indices]
