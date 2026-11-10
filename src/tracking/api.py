import hashlib
import json
import math
import subprocess
import threading
from pathlib import Path
from fastapi import HTTPException,Query
from fastapi.responses import FileResponse
from .repository import TrackingRepository
from .video import safe_path,sha256,probe


def mount_tracking(app,db,video_root,tracking_db):
    if Path(tracking_db).resolve()==Path(db.db_path).resolve():raise ValueError('trajectory_db_must_differ_from_registry')
    repo=TrackingRepository(tracking_db);cache=Path(tracking_db).resolve().parent/'tracking_media';lock=threading.Lock()

    def valid_run(rid):
        if len(rid)!=32 or any(c not in '0123456789abcdef' for c in rid):raise HTTPException(422,'invalid_tracking_run_id')
        try:r=repo.run(rid)
        except KeyError:raise HTTPException(404,'tracking_run_not_found')
        clip=db.clip(r['clip_id'])
        if clip.get('source_kind')=='uploaded_recording':raise HTTPException(403,'private_upload_requires_authenticated_analysis_page')
        if clip['video_id']!=r['video_id'] or r['metadata'].get('global_offset',clip['start'])!=clip['start']:
            raise HTTPException(409,'registered_video_or_time_mapping_changed_since_tracking')
        return r

    def public(r):
        result=dict(r)
        with repo.connection() as c:
            result['persisted_frame_count']=c.execute('SELECT COUNT(*) FROM tracking_frames WHERE run_id=?',(r['run_id'],)).fetchone()[0]
            result['persisted_track_count']=c.execute('SELECT COUNT(*) FROM person_tracks WHERE run_id=?',(r['run_id'],)).fetchone()[0]
        if result['error_message']:result['error_message']='processing_failed_or_interrupted; see local run report'
        return result

    @app.get('/tracking')
    def tracking_page():return FileResponse(Path(__file__).resolve().parents[2]/'webui/tracking.html',media_type='text/html')

    @app.get('/api/tracking/videos')
    def videos(video_id:str|None=None):
        with db._get_connection() as c:
            rows=c.execute('SELECT payload FROM registered_clips'+(' WHERE video_id=?' if video_id else '')+' ORDER BY video_id,start,id',([video_id] if video_id else [])).fetchall()
        return [{'clip_id':d['clip_id'],'video_id':d['video_id'],'display_name':d['video_id']+' / clip '+d['clip_id'][:8],
                 'global_start':d['start'],'global_end':d['end'],'duration':d['duration'],'width':d['width'],'height':d['height'],
                 'decode_status':d['decode_status']} for d in map(lambda r:json.loads(r[0]),rows) if d.get('source_kind')!='uploaded_recording']

    @app.get('/api/tracking/runs')
    def runs(video_id:str|None=None,clip_id:str|None=None):
        if clip_id:db.clip(clip_id)
        if video_id:
            with db._get_connection() as c:
                if not c.execute('SELECT 1 FROM registered_clips WHERE video_id=? LIMIT 1',(video_id,)).fetchone():raise HTTPException(404,'video_not_registered')
        return [public(r) for r in repo.runs(video_id,clip_id) if db.clip(r['clip_id']).get('source_kind')!='uploaded_recording']

    @app.get('/api/tracking/tracks')
    def tracks(run_id:str):valid_run(run_id);return repo.tracks(run_id)

    @app.get('/api/tracking/tracks/{track_id}')
    def track(track_id:int,run_id:str):
        valid_run(run_id)
        if track_id<1:raise HTTPException(422,'invalid_track_id')
        for t in repo.tracks(run_id):
            if t['track_id']==track_id:return t
        raise HTTPException(404,'track_not_found_in_this_run')

    @app.get('/api/tracking/observations')
    def observations(run_id:str,after_frame:int=Query(-1,ge=-1),limit:int=Query(120,ge=1,le=200),start:float|None=None,end:float|None=None,track_id:int|None=Query(None,ge=1)):
        run=valid_run(run_id);clip=db.clip(run['clip_id'])
        if any(t is not None and not math.isfinite(t) for t in [start,end]):raise HTTPException(422,'invalid_time_range')
        if any(t is not None and not clip['start']<=t<=clip['end']+.15 for t in [start,end]) or start is not None and end is not None and start>end:raise HTTPException(422,'time_range_outside_clip')
        if track_id is not None:track(track_id,run_id)
        return repo.observations(run_id,after_frame,limit,start,end,track_id)

    @app.get('/api/tracking/media/{clip_id}')
    def media(clip_id:str,run_id:str):
        run=valid_run(run_id)
        if run['clip_id']!=clip_id:raise HTTPException(422,'run_clip_mismatch')
        clip=db.clip(clip_id);path=safe_path(clip,video_root)
        if sha256(path)!=run['metadata']['source_sha256']:raise HTTPException(409,'tracking_source_content_changed')
        key=run['metadata']['source_sha256'];cached=cache/(key+'.mp4');audit=cache/(key+'.json')
        with lock:
            if not cached.is_file() or not audit.is_file():
                cache.mkdir(parents=True,exist_ok=True);temporary=cache/(key+'.partial.mp4')
                # Silent derived copy; preserve presentation timing and dimensions.
                try:
                    result=subprocess.run(['ffmpeg','-v','error','-y','-i',str(path),'-map','0:v:0','-an','-vf','setpts=PTS-STARTPTS','-fps_mode','passthrough','-c:v','libx264','-preset','ultrafast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(temporary)],capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if result.returncode:raise HTTPException(422,'tracking_playback_transcode_failed')
                    source=probe(path,clip);copy=probe(temporary,clip)
                    if source['width']!=copy['width'] or source['height']!=copy['height'] or len(source['timestamps'])!=len(copy['timestamps']):raise HTTPException(422,'playback_timebase_or_dimensions_mismatch')
                    drift=max(abs(a-b) for a,b in zip(source['timestamps'],copy['timestamps']))
                    if drift>.002:raise HTTPException(422,'playback_presentation_timebase_mismatch')
                    temporary.replace(cached);audit.write_text(json.dumps({'max_pts_error_seconds':drift,'frame_count':len(copy['timestamps']),'width':copy['width'],'height':copy['height'],'identity_verified':False,'audio':'omitted in tracking-derived copy'},indent=2))
                except FileNotFoundError:raise HTTPException(422,'tracking_media_requires_ffmpeg')
                except subprocess.TimeoutExpired:raise HTTPException(504,'tracking_media_transcode_timeout')
                finally:temporary.unlink(missing_ok=True)
        return FileResponse(cached,media_type='video/mp4',headers={'X-Content-Type-Options':'nosniff'})
