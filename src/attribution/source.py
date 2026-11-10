"""Read-only existing event/track/identity evidence. No cross-clip identity inference."""
import hashlib
import json
import math
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from cryptography.fernet import Fernet
from src.identity.tracks import TrackAccess
from src.tracking.video import safe_path,sha256


def signature(e):
    keys=['event_id','video_id','action','t_start','t_end','region_xywh','frame_width','frame_height','excel_sha256','excel_row']
    return hashlib.sha256(json.dumps({k:e.get(k) for k in keys},sort_keys=True).encode()).hexdigest()


class EvidenceSource(TrackAccess):
    def continuity_risks(self,rid,tid,start,end):
        rows=[o for o in self.observations(rid,tid) if start<=o['global_timestamp']<=end]
        risks=[]
        for a,b in zip(rows,rows[1:]):
            dt=b['local_timestamp']-a['local_timestamp']
            distance=math.hypot((b['bbox_x1']+b['bbox_x2']-a['bbox_x1']-a['bbox_x2'])/2,(b['bbox_y1']+b['bbox_y2']-a['bbox_y1']-a['bbox_y2'])/2)/math.hypot(b['frame_width'],b['frame_height'])
            if dt>0 and distance/dt>1:
                risks.append({'kind':'possible_trajectory_switch_or_detection_jitter','start':a['global_timestamp'],'end':b['global_timestamp'],'normalized_speed':distance/dt})
        return risks

    def events(self,video_id=None):
        with sqlite3.connect(self.registry_db.as_uri()+'?mode=ro',uri=True) as c:
            rows=c.execute('SELECT payload FROM region_annotations'+(' WHERE video_id=?' if video_id else '')+' ORDER BY start,id',(video_id,) if video_id else ()).fetchall()
        return [json.loads(r[0]) for r in rows]

    def event(self,eid):
        with sqlite3.connect(self.registry_db.as_uri()+'?mode=ro',uri=True) as c:r=c.execute('SELECT payload FROM region_annotations WHERE id=?',(eid,)).fetchone()
        if not r:raise ValueError('event_not_found')
        return json.loads(r[0])

    def verified_run(self,rid):
        run,clip=self.run(rid)
        if run['status']!='completed':raise ValueError('tracking_run_not_completed')
        if sha256(safe_path(clip,self.video_root))!=run['metadata']['source_sha256']:raise ValueError('tracking_source_changed')
        return run,clip

    def event_run(self,eid,rid):
        e=self.event(eid);run,clip=self.verified_run(rid)
        if e['video_id']!=clip['video_id'] or min(e['t_end'],clip['end'])<max(e['t_start'],clip['start']):
            raise ValueError('event_run_source_or_time_mismatch')
        return e,run,clip


class IdentityReader:
    def __init__(self,private_dir=None):self.root=Path(private_dir).resolve() if private_dir else None

    def intersect(self,run,tid,start,end,source_hash):
        if not self.root or not (self.root/'identity.db').is_file() or not (self.root/'encryption.key').is_file():return []
        # No IdentityStore constructor/migrations; no biometric reads.
        cipher=Fernet((self.root/'encryption.key').read_bytes())
        with sqlite3.connect((self.root/'identity.db').as_uri()+'?mode=ro',uri=True) as c:
            c.row_factory=sqlite3.Row
            rows=c.execute("SELECT b.*,s.display_name,s.status AS subject_status,s.expires_at,s.consent_record FROM identity_bindings b JOIN registered_subjects s ON s.subject_id=b.subject_id WHERE b.run_id=? AND b.track_id=? AND b.status='human_confirmed' AND b.end>=? AND b.start<=?",(run,tid,start,end)).fetchall()
        result=[]
        for r in rows:
            if r['subject_status']!='active':continue
            expiry=datetime.fromisoformat(r['expires_at'].replace('Z','+00:00'))
            if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):continue
            consent=json.loads(cipher.decrypt(r['consent_record']))
            if 'manual_identity' not in consent.get('purposes',[]):continue
            if json.loads(r['evidence_refs']).get('clip_source_sha256')!=source_hash:continue
            a,b=max(start,r['start']),min(end,r['end'])
            if a>b:continue
            result.append({'subject_id':r['subject_id'],'display_name':json.loads(cipher.decrypt(r['display_name'])) if r['display_name'] else '',
                'identity_binding_id':r['binding_id'],'start':a,'end':b,'source':'human_confirmed_identity_interval'})
        return result
