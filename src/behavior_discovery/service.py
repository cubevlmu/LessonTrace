import json
from pathlib import Path
from src.agent.fusion.evidence import version
from src.identity.store import uid,now
from src.runtime_paths import data_path, stored_path
from src.tracking.video import sha256
from .repository import Repository
from .ingest import MEDIA_ROOT


class DiscoveryService:
    def __init__(self,context):self.context=context;self.repo=Repository(context.repo)
    def review(self,event_id,body,actor):
        with self.context.repo.connection() as c:row=c.execute('SELECT payload FROM discovered_behavior_events WHERE event_id=?',(event_id,)).fetchone()
        if not row:raise ValueError('discovered_event_missing')
        event=json.loads(row[0]);self.repo.video(event['video_id'],actor)
        if not event['local_start']<=body.start<=body.end<=event['local_end']:raise ValueError('review_outside_discovered_sample_extent')
        obs={o['frame_index']:o for o in self.context.source.observations(event['run_id'],event['track_id']) if body.start<=o['global_timestamp']<=body.end}
        if not set(body.evidence_frames)<=set(obs):raise ValueError('actual_target_frames_required')
        if body.decision=='confirmed':
            if body.behavior_visible!='yes' or body.attribution_sufficient!='yes' or body.evidence_time_correct!='yes' or not body.same_person_in_reviewed_interval:raise ValueError('human_confirmation_requires_direct_actor_evidence')
            self.context.source.interval(event['run_id'],event['track_id'],body.start,body.end)
        for frame in body.evidence_frames:self.context.source.frame(event['run_id'],event['track_id'],frame)
        rid=uid();payload={**body.model_dump(),'source_kind':'human_confirmed_event' if body.decision=='confirmed' else 'human_discovered_event_review',
            'reviewed_global_times':[obs[i]['global_timestamp'] for i in body.evidence_frames]}
        with self.context.repo.connection() as c:c.execute('INSERT INTO discovered_behavior_reviews VALUES (?,?,?,?,?,?)',(rid,event_id,actor,now(),body.decision,json.dumps(payload,ensure_ascii=False)))
        return {'review_id':rid,'discovered_review_id':rid,'native_binding_id':None,'decision':body.decision}
    def revoke_review(self,review_id,actor,reason):
        with self.context.repo.connection() as c:r=c.execute('SELECT * FROM discovered_behavior_reviews WHERE review_id=?',(review_id,)).fetchone()
        if not r:raise ValueError('discovery_review_missing')
        with self.context.repo.connection() as c:e=json.loads(c.execute('SELECT payload FROM discovered_behavior_events WHERE event_id=?',(r['event_id'],)).fetchone()[0])
        self.repo.video(e['video_id'],actor);rid=uid()
        with self.context.repo.connection() as c:c.execute('INSERT INTO discovered_behavior_reviews VALUES (?,?,?,?,?,?)',(rid,r['event_id'],actor,now(),'revoked',json.dumps({'revokes':review_id,'reason':reason})))
        return {'status':'revoked','review_id':rid}
    def public_event(self,event):
        e={**event};e['frames']=[{k:v for k,v in f.items() if k!='files'} for f in e['frames']]
        history=e.get('human_review_history',[])
        current=history[-1] if history and history[-1]['status']!='revoked' else None
        e['human_correction']=({'status':current['status'],'reviewer':current['actor'],'review_time':current['created_at'],
            'corrected_category':current['data'].get('human_corrected_category',e['action_category']),
            'track_id':current['data'].get('human_corrected_track_id',e['track_id']),
            'start':current['data']['start'],'end':current['data']['end'],'observable_action':current['data']['observable_action']} if current else None)
        return e
    def delete(self,vid,actor):
        video=self.repo.video(vid,actor)
        path=Path(video['metadata']['media_path']).resolve()
        if MEDIA_ROOT.resolve() not in path.parents:raise ValueError('managed_cache_outside_storage')
        with self.context.repo.connection() as c:
            if c.execute("SELECT 1 FROM analysis_jobs WHERE video_id=? AND status IN ('running','pausing','cancelling')",(vid,)).fetchone():raise ValueError('pause_or_cancel_active_job_before_cleanup')
            c.execute("UPDATE analysis_videos SET status='deleted' WHERE video_id=?",(vid,))
        # Soft-delete registry entry so every existing source reader refuses media.
        import sqlite3
        with sqlite3.connect(self.context.source.registry_db) as c:
            r=c.execute('SELECT payload FROM registered_clips WHERE id=?',(vid,)).fetchone();clip=json.loads(r[0]);clip['decode_status']='deleted'
            c.execute('UPDATE registered_clips SET payload=? WHERE id=?',(json.dumps(clip),vid))
        path.unlink(missing_ok=True)
        cache=self.context.source.tracking_db.parent/'tracking_media'
        digest=video['metadata']['processing_sha256']
        if len(digest)==64 and all(c in '0123456789abcdef' for c in digest):
            for suffix in ['.mp4','.json']:(cache/(digest+suffix)).unlink(missing_ok=True)
        return {'status':'deleted','original_retained':True,'history_retained':True,'playback_cache_removed':True}


def verified_frame_path(frame, project_root=None):
    base = project_root / 'data' if project_root is not None else data_path()
    root = (base / 'analysis_private' / 'evidence').resolve()
    try:
        path = stored_path(frame['files']['input_combined'])
    except ValueError:
        return None
    if root in path.parents and path.is_file() and sha256(path)==frame['sha256']['input_combined']:
        return path
    return None


def evidence(service,rid,a,b,tid=None):
    from src.agent.fusion.coverage import valid_observation_result
    with service.repo.connection() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_videos'").fetchone():return []
    repo=Repository(service.repo);result=[]
    for e in repo.events(rid):
        history=e['human_review_history'];frames=e['frames']
        valid=all(verified_frame_path(f) is not None for f in frames)
        scoped=[f for f in frames if a<=f['global_time']<=b]
        in_scope=all(a<=t<=b for t in e['observed_time_support'])
        p={'event_id':e['event_id'],'action':e['action_category'],'source_kind':'model_discovered_event','run_id':rid,'track_id':e['track_id'],
            'global_offset':0.,'clip_id':e['video_id'],'final_decision':(e['final_decision'] if in_scope else 'uncertain') if valid else 'error','raw_model_decisions':e['raw_vlm_decisions'],
            'model':e['model'],'prompt_version':e['prompt_version'],'observed_time_support':[t for t in e['observed_time_support'] if a<=t<=b],'review_status':e['review_status'],
            'subject_checks':e['subject_checks'],'limitations':e['limitations']+([] if in_scope else ['Query includes only part of the original sampled support; full-window confirmation does not transfer.']),
            'CESA_checks':e['CESA_checks'],'frame_refs':[f['frame_id'] for f in scoped],
            'sampled_frames_valid':valid,'human_review_version':version(history),'scope':'model_candidate_at_sampled_instants_not_human_confirmation'}
        item={'evidence_id':'discovered:'+e['event_id'],'evidence_type':'model_discovered_event','source_id':e['event_id'],
            'subject_ref':f'track:{rid}:{e["track_id"]}' if e['subject_checks']['passed'] else None,'time_scope':[min(f['global_time'] for f in scoped),max(f['global_time'] for f in scoped)] if scoped else None,
            'content':e['reason'],'verification_status':'supported' if valid and in_scope and e['final_decision']=='confirmed' else 'uncertain','provenance':p,'source_version':version(p)}
        if scoped and (tid is None or tid==e['track_id']):result.append(item)
        if history and history[-1]['status'] in {'confirmed','rejected','ambiguous','uncertain'}:
            h=history[-1];data=h['data'];times=[t for t in data['reviewed_global_times'] if a<=t<=b]
            if times and (tid is None or tid==data.get('human_corrected_track_id',e['track_id'])):
                hp={'event_id':e['event_id'],'action':data.get('human_corrected_category',e['action_category']),'status':h['status'],'reviewer_kind':'human','global_offset':0.,'reviewed_time_support':times,
                    'source_kind':'human_confirmed_event' if h['status']=='confirmed' else 'human_discovered_event_review','scope':'human_review_at_cited_instants_not_verified_identity'}
                result.append({'evidence_id':'discovered_human:'+h['review_id'],'evidence_type':'human_confirmed_event','source_id':h['review_id'],
                    'subject_ref':f"track:{rid}:{data.get('human_corrected_track_id',e['track_id'])}",'time_scope':[min(times),max(times)],'content':data['observable_action'],
                    'verification_status':'supported' if h['status']=='confirmed' and valid else ('contradicted' if h['status']=='rejected' else 'uncertain'),
                    'provenance':hp,'source_version':version(h)})
    with service.repo.connection() as c:windows=c.execute("SELECT * FROM discovery_windows WHERE run_id=? AND status='completed' AND start<=? AND end>=?",(rid,b,a)).fetchall()
    clip_id=None
    direct_tracks=set()
    for row in windows:
        if tid and tid!=row['track_id']:continue
        payload=valid_observation_result(row['payload'])
        if payload is None:continue
        if clip_id is None:clip_id=service.source.run(rid)[1]['clip_id']
        for i,obs in enumerate(payload.get('observations',{}).get('observed_actions',[])):
            refs=[f for f in payload['frames'] if f['frame_id'] in obs['evidence_refs'] and a<=f['global_time']<=b]
            if not refs:continue
            direct_tracks.add(row['track_id'])
            accessible=all(verified_frame_path(f) is not None for f in refs)
            p={'action':'unknown','final_decision':'uncertain','run_id':rid,'track_id':row['track_id'],'segment_id':row['segment_id'],'clip_id':clip_id,'global_offset':0.,
                'observation_limitations':payload.get('observations',{}).get('limitations',[]),
                'frame_refs':[f['frame_id'] for f in refs],'sampled_frames_valid':accessible,'scope':'blind_description_no_reliable_category_or_confirmed_actor','source_kind':'model_discovered_event'}
            result.append({'evidence_id':'discovery_observation:'+row['window_id']+':'+str(i),'evidence_type':'vlm_observation','source_id':row['window_id'],
                'subject_ref':None,'time_scope':[min(f['global_time'] for f in refs),max(f['global_time'] for f in refs)],'content':obs['action'],
                'verification_status':'uncertain','provenance':p,'source_version':version([payload,p])})
    # Carry forward the latest prior state within the same visible tracking segment
    # when a queried interval falls between discrete state-change observations.
    track_segments={}
    try:
        for tr in service.source.tracks(rid):
            if tid is None or tr['track_id']==tid:
                track_segments[tr['track_id']]=tr.get('segments',[])
    except Exception:
        track_segments={}
    carry_candidates={}
    if track_segments:
        sql="SELECT * FROM discovery_windows WHERE run_id=? AND status='completed' AND start<=?"
        args=[rid,a]
        if tid is not None:
            sql+=" AND track_id=?"
            args.append(tid)
        with service.repo.connection() as c:prior_windows=c.execute(sql,args).fetchall()
        eps=0.002
        for row in prior_windows:
            track_id=row['track_id']
            if track_id in direct_tracks:continue
            segs=track_segments.get(track_id) or []
            active_seg=next((s for s in segs if s['global_start']-eps<=a<=s['global_end']+eps),None)
            if not active_seg:continue
            payload=valid_observation_result(row['payload'])
            if payload is None:continue
            limitations=payload.get('observations',{}).get('limitations',[])
            if any('not visible' in str(t).lower() or '不可见' in str(t) for t in limitations):continue
            for i,obs in enumerate(payload.get('observations',{}).get('observed_actions',[])):
                refs=[f for f in payload['frames'] if f['frame_id'] in obs['evidence_refs'] and active_seg['global_start']-eps<=f['global_time']<a]
                if not refs:continue
                origin_time=max(f['global_time'] for f in refs)
                prev=carry_candidates.get(track_id)
                entry=(origin_time,row,payload,i,obs,refs,active_seg)
                if prev is None or origin_time>prev[0][0]+eps:
                    carry_candidates[track_id]=[entry]
                elif abs(origin_time-prev[0][0])<=eps:
                    prev.append(entry)
        for entries in carry_candidates.values():
            for origin_time,row,payload,i,obs,refs,active_seg in entries:
                if clip_id is None:clip_id=service.source.run(rid)[1]['clip_id']
                accessible=all(verified_frame_path(f) is not None for f in refs)
                p={'action':'unknown','final_decision':'uncertain','run_id':rid,'track_id':row['track_id'],'segment_id':row['segment_id'],'clip_id':clip_id,'global_offset':0.,
                    'observation_limitations':payload.get('observations',{}).get('limitations',[]),
                    'frame_refs':[f['frame_id'] for f in refs],'sampled_frames_valid':accessible,'carried_forward':True,'origin_time':origin_time,
                    'scope':'blind_description_no_reliable_category_or_confirmed_actor','source_kind':'model_discovered_event'}
                result.append({'evidence_id':'discovery_observation:'+row['window_id']+':'+str(i),'evidence_type':'vlm_observation','source_id':row['window_id'],
                    'subject_ref':None,'time_scope':[origin_time,min(b,active_seg['global_end'])],'content':obs['action'],
                    'verification_status':'uncertain','provenance':p,'source_version':version([payload,p])})
    return result
