"""Registered files only; decoded presentation timestamps, never index/fps."""
import hashlib
import json
import sqlite3
from pathlib import Path
from fractions import Fraction
from .schemas import global_time


def registered_clip(db,clip_id=None,video_id=None):
    uri=Path(db).resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as c:
        if clip_id:
            row=c.execute('SELECT payload FROM registered_clips WHERE id=?',(clip_id,)).fetchone()
            if not row:raise ValueError('clip_not_registered')
            clip=json.loads(row[0])
            if video_id and clip['video_id']!=video_id:raise ValueError('video_clip_mismatch')
            return clip
        rows=c.execute('SELECT payload FROM registered_clips WHERE video_id=? ORDER BY start,id',(video_id,)).fetchall()
        if not rows:raise ValueError('video_not_registered')
        if len(rows)>1:raise ValueError('ambiguous_video_id; specify --clip-id (no cross-clip merging)')
        return json.loads(rows[0][0])


def safe_path(clip,root,require_exists=True):
    path=Path(clip['path']).resolve();root=Path(root).resolve()
    managed_root=Path(__file__).resolve().parents[2]/'data/analysis_private/media'
    managed=(clip.get('source_kind')=='uploaded_recording' and managed_root.resolve() in path.parents and path.name==clip['clip_id']+'.mp4')
    if (root not in path.parents and not managed) or path.suffix.lower()!='.mp4':raise ValueError('registered_path_outside_root')
    if clip.get('decode_status')=='deleted':raise ValueError('registered_video_deleted')
    if require_exists and not path.is_file():raise ValueError('registered_video_missing')
    return path


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def frame_time(pts,time_base,origin,previous=None):
    if pts is None or time_base is None:raise ValueError('missing_presentation_timestamp')
    value=Fraction(pts)*Fraction(time_base)-Fraction(origin)
    if value<0 or (previous is not None and value<=previous):raise ValueError('non_monotonic_presentation_timestamp')
    return value


def decode_frames(path):
    import av
    with av.open(str(path)) as container:
        if not container.streams.video:raise ValueError('no_video_stream')
        stream=container.streams.video[0];previous=None;origin=None
        for index,frame in enumerate(container.decode(stream)):
            if frame.pts is None or frame.time_base is None:raise ValueError('missing_presentation_timestamp')
            if origin is None:origin=Fraction(frame.pts)*frame.time_base
            t=frame_time(frame.pts,frame.time_base,origin,previous);previous=t
            yield {'frame_index':index,'local_timestamp':float(t),'pts':frame.pts,
                   'time_base_num':frame.time_base.numerator,'time_base_den':frame.time_base.denominator,
                   'original_pts_seconds':float(Fraction(frame.pts)*frame.time_base),
                   'origin_pts_seconds':float(origin),'frame':frame}
        if previous is None:raise ValueError('video_has_no_decodable_frames')


def probe(path,clip):
    import statistics
    times=[];width=height=0;first_origin=None
    for data in decode_frames(path):
        frame=data['frame'];width,height=frame.width,frame.height
        if first_origin is None:first_origin=data['origin_pts_seconds']
        times.append(data['local_timestamp'])
    if len(times)<2:raise ValueError('insufficient_timebase_samples')
    deltas=[b-a for a,b in zip(times,times[1:])];step=statistics.median(deltas)
    duration=times[-1]+step
    return {'width':width,'height':height,'decoded_frame_count':len(times),'duration':duration,
        'global_offset':clip['start'],'filename_duration':clip['end']-clip['start'],
        'duration_delta':duration-(clip['end']-clip['start']),
        'median_fps_from_pts':1/step,'vfr_detected':max(deltas)-min(deltas)>.0005,
        'source_pts_origin':first_origin,'time_origin':'first_decoded_presentation_timestamp',
        'timebase_validated':True,'presentation_times_sha256':hashlib.sha256(json.dumps(times).encode()).hexdigest(),
        'timestamps':times,'note':'Clip-local time starts at first decoded PTS; global offset is registered filename metadata, not independently verified classroom time.'}
