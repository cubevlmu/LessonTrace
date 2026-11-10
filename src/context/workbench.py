"""Bounded playback reads: observed boxes and independently scoped identity labels."""
import math
import json
import os
from fastapi import Depends,Query
from src.behavior_discovery.repository import Repository
from src.attribution.source import IdentityReader


def mount(app,service,account):
    def source(rid,actor):
        run,clip=service.source.verified_run(rid)
        video=Repository(service.repo).video(clip['video_id'],actor)
        if video['status']!='active':raise ValueError('video_not_active')
        return run,clip
    from .track_groups import mount as mount_groups
    mount_groups(app,service,account,source)
    @app.get('/api/context/workbench/full-analysis-plan')
    def full_plan(run_id:str,a=Depends(account)):
        source(run_id,a['actor'])
        from src.behavior_discovery.schemas import Config
        from src.behavior_discovery.windowing import plan_windows
        _,scope=plan_windows(service.source,run_id,Config(analysis_mode='full_timeline',frames=4,processing_profile='efficient' if os.getenv('INTICLASS_PROFILE')=='product' else 'standard'),'preview')
        with service.repo.connection() as c:
            row=c.execute("SELECT payload FROM analysis_jobs WHERE run_id=? AND owner=? AND status IN ('completed','completed_with_errors') AND json_extract(payload,'$.elapsed_seconds')>0 ORDER BY updated_at DESC LIMIT 1",(run_id,a['actor'])).fetchone()
            old=json.loads(row[0]) if row else None
            if old:
                prior=c.execute("SELECT COUNT(*) FROM discovery_windows WHERE run_id=? AND status='completed' AND job_id=(SELECT job_id FROM analysis_jobs WHERE run_id=? AND owner=? AND status IN ('completed','completed_with_errors') ORDER BY updated_at DESC LIMIT 1)",(run_id,run_id,a['actor'])).fetchone()[0]
            else:prior=0
        scope['historical_seconds_per_window']=old.get('elapsed_seconds',0)/prior if old and prior else None
        scope['rough_processing_seconds']=scope['historical_seconds_per_window']*scope['selected_windows'] if scope['historical_seconds_per_window'] else None
        scope['estimate_basis']='Prior local job elapsed time only, not a runtime or accuracy guarantee.'
        return scope
    @app.get('/api/context/workbench/track-quality')
    def track_quality(run_id:str,a=Depends(account)):
        source(run_id,a['actor'])
        from src.tracking.quality import for_run
        return for_run(service.source,run_id)

    @app.get('/api/context/workbench/results')
    def results(run_id:str,offset:int=Query(0,ge=0),a=Depends(account)):
        """All saved windows in this tracking namespace, independent of selected job."""
        source(run_id,a['actor'])
        with service.repo.connection() as c:
            rows=[dict(r) for r in c.execute('SELECT w.* FROM discovery_windows w JOIN analysis_jobs j ON j.job_id=w.job_id WHERE w.run_id=? AND j.owner=? ORDER BY w.start,w.track_id,w.window_id LIMIT 2001 OFFSET ?',(run_id,a['actor'],offset))]
        truncated=len(rows)>2000
        for row in rows[:2000]:
            payload=json.loads(row.pop('payload'));result=payload.get('result',{})
            row['summary']={'observations':result.get('observations'),'unknown_or_empty':result.get('unknown_or_empty'),'error':result.get('classification_error') if result else payload.get('error'),'frames':[{k:v for k,v in f.items() if k!='files'} for f in result.get('frames',[])]}
        from src.behavior_discovery.service import DiscoveryService
        # Use the same public correction adapter as the analysis API.
        svc=DiscoveryService(service)
        events=Repository(service.repo).events(run_id)
        events=[svc.public_event(e) for e in events]
        from src.agent.visual_evidence_policy import semantic_findings
        by_window={w['window_id']:w['summary'].get('observations') or {} for w in rows[:2000]}
        for event in events:
            event['display_blind_observations']=[o for wid in event.get('window_ids',[]) for o in by_window.get(wid,{}).get('observed_actions',[]) if not semantic_findings(o['action'])]
        return {'windows':rows[:2000],'events':events,'truncated':truncated,'next_offset':offset+2000 if truncated else None}

    @app.get('/api/context/workbench/observations')
    def observations(run_id:str,start:float=0,end:float=5,a=Depends(account)):
        _,clip=source(run_id,a['actor']);duration=clip['end']-clip['start']
        if not all(math.isfinite(x) for x in (start,end)) or not 0<=start<=end<=duration+.002 or end-start>12:
            raise ValueError('invalid_playback_window; maximum_12_local_seconds')
        rows=service.source.rows("SELECT * FROM track_observations WHERE run_id=? AND local_timestamp>=? AND local_timestamp<=? AND tracker_state='matched_visible_detection' ORDER BY local_timestamp,track_id LIMIT 20001",(run_id,start,end))
        if len(rows)>20000:raise ValueError('observation_window_too_dense; request_shorter_interval')
        return rows

    @app.get('/api/context/workbench/identities')
    def identities(run_id:str,a=Depends(account)):
        run,clip=source(run_id,a['actor']);store=app.state.product_matcher.store
        visible=service.source.tracks(run_id);bindings=[]
        reader=IdentityReader(service.identity_dir)
        with store.connection() as c:
            row=c.execute('SELECT cl.* FROM classrooms cl JOIN classroom_videos v ON v.classroom_id=cl.classroom_id WHERE v.video_id=? AND (cl.owner_actor=? OR ?=\'admin\')',(clip['video_id'],a['actor'],a['role'])).fetchone()
            classroom=dict(row) if row else None
            allowed=set()
            if classroom:
                people=c.execute("SELECT s.subject_id,s.status FROM registered_subjects s JOIN classroom_students cs ON cs.subject_id=s.subject_id WHERE cs.classroom_id=? AND cs.status='active'",(classroom['classroom_id'],)).fetchall()
                allowed={p['subject_id'] for p in people if p['status']=='active'}
                classroom={'classroom_id':classroom['classroom_id'],'name':classroom['name'],'registered_count':len(allowed),'reference_only_count':sum(p['status']=='reference_only' for p in people)}
        if a['role'] in {'admin','teacher','reviewer'}:
            for track in visible:
                for binding in reader.intersect(run_id,track['track_id'],clip['start'],clip['end'],run['metadata']['source_sha256']):
                    if binding['subject_id'] not in allowed:continue
                    from src.validation.repository import track_scope
                    scope=track_scope(service.repo,run_id,track['track_id'],binding['start'],binding['end'],run['metadata']['source_sha256'])
                    pieces=[[binding['start'],binding['end']]]
                    for lo,hi in scope['problem_intervals']:
                        remaining=[]
                        for begin,end in pieces:
                            if hi<begin or lo>end:remaining.append([begin,end]);continue
                            if begin<lo:remaining.append([begin,math.nextafter(lo,begin)])
                            if hi<end:remaining.append([math.nextafter(hi,end),end])
                        pieces=remaining
                    for begin,end in pieces:
                        bindings.append({**binding,'start':begin,'end':end,'track_id':track['track_id'],'local_start':begin-clip['start'],'local_end':end-clip['start'],'status':'human_confirmed'})
        existing=app.state.product_identity_results(run_id,a) if classroom and allowed and a['role'] in {'admin','teacher','reviewer'} else {'candidates':[]}
        candidates=[{'subject_id':r['subject_id'],'display_name':r['display_name'],'track_id':r['track_id'],'local_start':r['start']-clip['start'],'local_end':r['end']-clip['start'],'status':'candidate'} for r in existing['candidates'] if r.get('display_name') and r['subject_id'] in allowed and r['effective_status'] in {'candidate','needs_review'}]
        from .person_profiles import labels
        track_labels=labels(store,service,run_id,clip,a['actor'],a['role'])
        return {'bindings':bindings,'candidates':candidates,'track_labels':track_labels,'classroom':classroom,'global_offset':clip['start'],
            'matching_status':'confirmed_intervals_available' if bindings else 'references_only_no_identity_enrollment' if classroom and classroom['reference_only_count'] and not classroom['registered_count'] else 'no_current_confirmed_identity',
            'track_id_is_student_identity':False}
