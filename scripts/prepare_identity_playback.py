"""Generate 11A-compatible validated playback copies, without database writes."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.identity.tracks import TrackAccess
from src.tracking.video import safe_path,sha256,probe


def prepare(access,rid):
    run,clip=access.run(rid);source=safe_path(clip,access.video_root)
    digest=sha256(source)
    if digest!=run['metadata']['source_sha256']: raise ValueError('tracking_source_content_changed')
    cache=access.tracking_db.parent/'tracking_media';cache.mkdir(exist_ok=True)
    target=cache/(digest+'.mp4');audit=cache/(digest+'.json')
    if target.exists() and audit.exists(): return {'status':'existing_validated_copy','run_id':rid}
    temporary=cache/(digest+'.identity.partial.mp4')
    try:
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-map','0:v:0','-an','-vf','setpts=PTS-STARTPTS','-fps_mode','passthrough','-c:v','libx264','-preset','ultrafast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(temporary)],check=True,capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        a,b=probe(source,clip),probe(temporary,clip)
        if (a['width'],a['height'],len(a['timestamps']))!=(b['width'],b['height'],len(b['timestamps'])):
            raise ValueError('playback_dimensions_or_frame_count_mismatch')
        delta=max(abs(x-y) for x,y in zip(a['timestamps'],b['timestamps']))
        if delta>.002: raise ValueError('playback_presentation_timestamp_mismatch')
        temporary.replace(target)
        audit.write_text(json.dumps({'max_pts_error_seconds':delta,'frame_count':len(b['timestamps']),'width':b['width'],'height':b['height'],'identity_verified':False,'audio':'omitted'},indent=2))
        return {'status':'created_validated_copy','max_pts_error_seconds':delta,'run_id':rid}
    finally: temporary.unlink(missing_ok=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',default='data/tracking_runtime.json');p.add_argument('--run-id')
    a=p.parse_args();config=json.loads(Path(a.runtime).read_text())
    access=TrackAccess(config['tracking_db'],config['registry_db'],config['video_root'])
    ids=[a.run_id] if a.run_id else [r['run_id'] for r in access.runs() if r['status']=='completed']
    for rid in ids: print(json.dumps(prepare(access,rid)))
