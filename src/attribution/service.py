import hashlib
import json
import math
import statistics
import uuid
from datetime import datetime,timezone
from .source import signature,IdentityReader

VERSION='attribution-v1'
def uid():return uuid.uuid4().hex
def now():return datetime.now(timezone.utc).isoformat()
def overlap(a,b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return area
def bbox(o):return [o[k] for k in ['bbox_x1','bbox_y1','bbox_x2','bbox_y2']]
def clipped_segments(segments,start,end):
    return [[max(start,s['global_start']),min(end,s['global_end'])] for s in segments if max(start,s['global_start'])<=min(end,s['global_end'])]


class AttributionService:
    def __init__(self,source,repo,identity_dir=None):self.source,self.repo,self.identity=source,repo,IdentityReader(identity_dir)

    def candidate(self,cid):
        d=self.repo.payload('event_track_candidates','candidate_id',cid)
        e,run,clip=self.source.event_run(d['event_id'],d['run_id'])
        if signature(e)!=d['event_signature'] or run['metadata']['source_sha256']!=d['source_sha256']:raise ValueError('candidate_source_changed; regenerate')
        return d,e,run,clip

    def generate(self,eid,rid):
        e,run,clip=self.source.event_run(eid,rid)
        start,end=max(e['t_start'],clip['start']),min(e['t_end'],clip['end'])
        dims=[e['frame_width'],e['frame_height']];region=e['region_xywh']
        if len(region)!=4 or not all(math.isfinite(x) for x in [*region,*dims]) or min(dims)<=0:raise ValueError('invalid_event_region')
        x,y,w,h=region
        if not 0<=x<x+w<=dims[0] or not 0<=y<y+h<=dims[1]:raise ValueError('event_region_out_of_bounds')
        area_ratio=w*h/(dims[0]*dims[1]);candidates=[]
        tracks=self.source.tracks(rid)
        denominator=self.source.rows('SELECT COUNT(*) AS n FROM tracking_frames WHERE run_id=? AND global_timestamp>=? AND global_timestamp<=?',(rid,start,end))[0]['n']
        for t in tracks:
            observations=[o for o in self.source.observations(rid,t['track_id']) if start<=o['global_timestamp']<=end]
            hits=[];ratios=[];ious=[]
            for o in observations:
                r=[x/dims[0]*o['frame_width'],y/dims[1]*o['frame_height'],(x+w)/dims[0]*o['frame_width'],(y+h)/dims[1]*o['frame_height']]
                b=bbox(o);intersection=overlap(r,b);body_area=(b[2]-b[0])*(b[3]-b[1]);roi_area=(r[2]-r[0])*(r[3]-r[1])
                if intersection>0:
                    hits.append(o);ratios.append(intersection/body_area);ious.append(intersection/(roi_area+body_area-intersection))
            if not hits:continue
            segments=clipped_segments(t['segments'],start,end)
            flags=['anonymous_track_not_student_identity','recording_group_unknown','region_overlap_is_not_actor_proof']
            if area_ratio>=.35:flags.append('large_annotation_region')
            if len(segments)>1:flags.append('tracking_gap')
            quality=t.get('quality_flags','[]');quality=json.loads(quality) if isinstance(quality,str) else quality
            if set(quality)&{'possible_identity_switch','id_switch','partial_run','motion_jump'}:flags.append('track_quality_requires_review')
            risks=self.source.continuity_risks(rid,t['track_id'],start,end)
            if risks:flags.append('possible_trajectory_switch')
            # Deterministic key; new source content/annotation versions coexist instead of replacing history.
            cid=hashlib.sha256((VERSION+signature(e)+rid+str(t['track_id'])+run['metadata']['source_sha256']).encode()).hexdigest()[:32]
            d={'candidate_id':cid,'event_id':eid,'run_id':rid,'track_id':t['track_id'],'video_id':e['video_id'],'clip_id':clip['clip_id'],
               'start':start,'end':end,'overlapping_time_range':[hits[0]['global_timestamp'],hits[-1]['global_timestamp']],
               'target_action':e['action'],'generation_version':VERSION,'source_sha256':run['metadata']['source_sha256'],'event_signature':signature(e),
               'temporal_coverage':{'observed_frame_count':len(observations),'overlapping_region_frame_count':len(hits),'registered_processed_frame_count':denominator,
                  'observed_processed_frame_ratio':len(observations)/denominator if denominator else None,'visible_segments':segments,
                  'segment_span_seconds':sum(b-a for a,b in segments),'segment_span_fraction':sum(b-a for a,b in segments)/(end-start) if end>start else None,
                  'exhaustive_behavior_observation':False},
               'spatial_overlap':{'mean_intersection_over_person':statistics.mean(ratios),'max_intersection_over_person':max(ratios),'mean_iou':statistics.mean(ious),'annotation_area_ratio':area_ratio},
               'ambiguity_flags':flags,'candidate_count':0,'status':'candidate','source_kind':'dataset_annotation','confidence':None}
            d['continuity_risks']=risks
            candidates.append(d)
        for d in candidates:
            d['candidate_count']=len(candidates)
            if len(candidates)>1:d['ambiguity_flags'].append('multiple_possible_actors')
            if len(candidates)>1 or area_ratio>=.35 or 'tracking_gap' in d['ambiguity_flags'] or 'track_quality_requires_review' in d['ambiguity_flags']:d['status']='ambiguous'
        with self.repo.connection() as c:
            for d in candidates:
                c.execute('INSERT OR IGNORE INTO event_track_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(d['candidate_id'],eid,rid,d['track_id'],clip['clip_id'],e['video_id'],start,end,VERSION,d['source_sha256'],signature(e),json.dumps(d)))
        return {'event_id':eid,'run_id':rid,'status':'candidates' if candidates else 'no_observed_spatial_candidates',
                'candidate_count':len(candidates),'candidates':candidates,'missing_evidence':[] if candidates else ['No matching visible track in this registered source/window; not proof that behavior did not occur.']}

    def human(self,body,actor):
        d,e,run,clip=self.candidate(body.candidate_id)
        if not d['start']<=body.start<=body.end<=d['end']:raise ValueError('review_interval_outside_candidate')
        self.source.interval(d['run_id'],d['track_id'],body.start,body.end)
        if self.source.continuity_risks(d['run_id'],d['track_id'],body.start,body.end) and not body.continuity_risks_addressed:
            raise ValueError('trajectory_jump_requires_explicit_human_continuity_review_or_shorter_interval')
        obs={o['frame_index']:o for o in self.source.observations(d['run_id'],d['track_id']) if body.start<=o['global_timestamp']<=body.end}
        if len(set(body.evidence_frames))!=len(body.evidence_frames) or not set(body.evidence_frames)<=set(obs):raise ValueError('evidence_frames_not_observed_in_review_interval')
        # Decode/hash/PTS verification prevents accepting nonexistent static references.
        for i in body.evidence_frames:self.source.frame(d['run_id'],d['track_id'],i)
        bid=uid();times=[obs[i]['global_timestamp'] for i in body.evidence_frames]
        payload={**body.model_dump(),'source_kind':'human_event_actor_review','event_source_kind':e['source_kind'],'target_action':e['action'],
                 'evidence_refs':[{'run_id':d['run_id'],'track_id':d['track_id'],'frame_index':i,'global_time':obs[i]['global_timestamp'],'local_time':obs[i]['local_timestamp']} for i in body.evidence_frames],
                 'source_sha256':d['source_sha256'],'event_signature':d['event_signature'],'scope':'behavior_occurs_in_reviewed_interval; not continuous behavior or verified identity'}
        with self.repo.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            active=c.execute("SELECT 1 FROM event_track_bindings WHERE candidate_id=? AND status='human_confirmed' AND end>=? AND start<=?",(d['candidate_id'],body.start,body.end)).fetchone()
            if active and body.decision=='human_confirmed':raise ValueError('overlapping_human_confirmation_exists; revoke_first')
            c.execute('INSERT INTO event_track_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?)',(bid,d['candidate_id'],e['event_id'],d['run_id'],d['track_id'],body.start,body.end,body.decision,actor,now(),json.dumps(payload)))
            c.execute('INSERT INTO attribution_audit VALUES (?,?,?,?,?,?)',(uid(),bid,body.decision,actor,now(),json.dumps(payload)))
        return {'binding_id':bid,'status':body.decision,'evidence_times':times}

    def revoke(self,bid,reason,actor):
        with self.repo.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT status FROM event_track_bindings WHERE binding_id=?',(bid,)).fetchone()
            if not row:raise ValueError('binding_not_found')
            if row[0]=='revoked':raise ValueError('binding_already_revoked')
            c.execute("UPDATE event_track_bindings SET status='revoked' WHERE binding_id=?",(bid,))
            c.execute('INSERT INTO attribution_audit VALUES (?,?,?,?,?,?)',(uid(),bid,'revoked',actor,now(),json.dumps({'reason':reason})))
        return {'binding_id':bid,'status':'revoked'}

    def timeline(self,rid,tid,include_identity=False):
        run,clip=self.source.verified_run(rid)
        tracks=self.source.tracks(rid);t=next((t for t in tracks if t['track_id']==tid),None)
        if not t:raise ValueError('track_not_found')
        with self.repo.connection() as c:
            candidates=[json.loads(r[0]) for r in c.execute('SELECT payload FROM event_track_candidates WHERE run_id=? AND track_id=? ORDER BY start,event_id',(rid,tid))]
            bindings=[dict(r) for r in c.execute('SELECT * FROM event_track_bindings WHERE run_id=? AND track_id=? ORDER BY start,created_at',(rid,tid))]
            reviews=[json.loads(r[0]) for r in c.execute('SELECT payload FROM event_track_reviews WHERE run_id=? AND track_id=? ORDER BY created_at',(rid,tid))]
        confirmed=[]
        for b in bindings:
            b['payload']=json.loads(b['payload']);b['effective_status']=b['status']
            try:self.candidate(b['candidate_id'])
            except ValueError:b['effective_status']='unknown_source_changed'
            if b['effective_status']!='human_confirmed':continue
            parts=clipped_segments(t['segments'],b['start'],b['end'])
            for a,z in parts:
                item={**b,'start':a,'end':z,'local_start':a-clip['start'],'identities':[]}
                # Existential interval label is not asserted outside actual reviewed evidence times.
                evidence=b['payload']['evidence_refs']
                if include_identity:
                    labels=self.identity.intersect(rid,tid,a,z,run['metadata']['source_sha256'])
                    for label in labels:
                        refs=[f for f in evidence if label['start']<=f['global_time']<=label['end']]
                        if refs:item['identities'].append({**label,'behavior_evidence_refs':refs,'behavior_binding_id':b['binding_id']})
                confirmed.append(item)
        return {'run_id':rid,'track_id':tid,'clip_id':clip['clip_id'],'video_id':clip['video_id'],'global_offset':clip['start'],
                'visible_segments':t['segments'],'gaps':self.source.rows('SELECT * FROM track_gaps WHERE run_id=? AND track_id=? ORDER BY gap_index',(rid,tid)),
                'track_identity':'anonymous_unverified_continuity','confirmed_behavior_intervals':confirmed,
                'candidate_behaviors':candidates,'human_history':bindings,'visual_reviews':reviews,
                'note':'Dataset labels are claims; only current scoped human attribution appears in confirmed intervals. Unobserved gaps do not imply absence of behavior.'}
