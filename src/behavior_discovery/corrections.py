"""Append-only teacher corrections in the existing discovery review table."""
import json
import hashlib
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from fastapi import Depends,HTTPException
from src.identity.store import uid,now
from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS
from .repository import Repository

class Correction(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    event_id:str
    decision:Literal['confirmed','uncertain','rejected']
    corrected_category:str
    track_id:int=Field(ge=1)
    start:float
    end:float
    observable_action:str=Field(min_length=5,max_length=1000)
    reason:str=Field(min_length=5,max_length=2000)
    evidence_frames:list[int]=Field(min_length=1,max_length=20)
    reviewed:bool
    @model_validator(mode='after')
    def valid(self):
        if self.end<self.start:raise ValueError('invalid_review_interval')
        if self.corrected_category not in {*ACTION_REQUIREMENTS,'unknown','other_observable_action'}:raise ValueError('invalid_behavior_category')
        if not self.reviewed:raise ValueError('independent_review_attestation_required')
        if self.decision=='confirmed' and self.corrected_category=='unknown':raise ValueError('unknown_cannot_be_confirmed_as_category')
        return self

def mount(app,context,account,writer):
    repo=Repository(context.repo)
    def resolve(event_id,actor):
        """A blind observation may receive a human review without inventing a model class."""
        if not event_id.startswith('window:'):
            with context.repo.connection() as c:r=c.execute('SELECT payload FROM discovered_behavior_events WHERE event_id=?',(event_id,)).fetchone()
            if not r:raise HTTPException(404,'event_not_found')
            event=json.loads(r[0]);repo.video(event['video_id'],actor)
            return event,False
        wid=event_id.split(':')[1]
        with context.repo.connection() as c:r=c.execute("SELECT * FROM discovery_windows WHERE window_id=? AND status='completed'",(wid,)).fetchone()
        if not r:raise HTTPException(404,'observation_window_not_found')
        job=repo.job(r['job_id'],actor);repo.video(job['video_id'],actor)
        eid='review_window_'+hashlib.sha256(wid.encode()).hexdigest()[:28]
        with context.repo.connection() as c:existing=c.execute('SELECT payload FROM discovered_behavior_events WHERE event_id=?',(eid,)).fetchone()
        if existing:return json.loads(existing[0]),False
        result=json.loads(r['payload']).get('result',{});frames=result.get('frames',[])
        if not frames:raise ValueError('observation_has_no_evidence_frames')
        times=sorted({f['local_time'] for f in frames})
        return {'event_id':eid,'job_id':job['job_id'],'video_id':job['video_id'],'run_id':r['run_id'],'track_id':r['track_id'],
                'action_category':'unknown','local_start':times[0],'local_end':times[-1],
                'observed_time_support':times,'frames':frames,'window_ids':[wid],
                'final_decision':'uncertain','raw_vlm_decisions':[],
                'reason':'Unclassified saved observation; human review is stored independently.',
                'support_facts':[],'evidence_refs':[f['frame_id'] for f in frames],
                'subject_checks':{'passed':False,'failures':['no_model_subject_attribution_judgment']},
                'limitations':(result.get('observations') or {}).get('limitations',[]),
                'CESA_checks':[],'source_kind':'window_observation_review','review_status':'unreviewed',
                'discovery_pipeline_version':'human_review_adapter_v1',
                'event_boundary_kind':'extent_of_sampled_evidence_not_action_onset_or_offset'},True
    @app.get('/api/context/corrections')
    def history(event_id:str,a=Depends(account)):
        event,_=resolve(event_id,a['actor'])
        with context.repo.connection() as c:rows=[{**dict(r),'data':json.loads(r['payload'])} for r in c.execute('SELECT * FROM discovered_behavior_reviews WHERE event_id=? ORDER BY created_at,review_id',(event['event_id'],))]
        return rows

    @app.post('/api/context/corrections',status_code=201)
    def correct(body:Correction,a=Depends(writer)):
        event,new_event=resolve(body.event_id,a['actor'])
        body=body.model_copy(update={'event_id':event['event_id']})
        _,clip=context.source.verified_run(event['run_id'])
        if not clip['start']<=body.start<=body.end<=clip['end']:raise ValueError('correction_outside_video')
        observations={o['frame_index']:o for o in context.source.observations(event['run_id'],body.track_id) if body.start<=o['global_timestamp']<=body.end}
        if not set(body.evidence_frames)<=set(observations):raise ValueError('correction_requires_visible_target_frames')
        if body.decision=='confirmed':context.source.interval(event['run_id'],body.track_id,body.start,body.end)
        for idx in body.evidence_frames:context.source.frame(event['run_id'],body.track_id,idx)
        rid=uid();data={**body.model_dump(),'source_kind':'human_confirmed_event' if body.decision=='confirmed' else 'human_discovered_event_review',
            'human_corrected_category':body.corrected_category,'human_corrected_track_id':body.track_id,
            'reviewed_global_times':[observations[idx]['global_timestamp'] for idx in body.evidence_frames],
            'original_action':event['action_category'],'original_track_id':event['track_id'],'scope':'reviewed_instants_and_interval_only'}
        with context.repo.connection() as c:
            if new_event:c.execute('INSERT OR IGNORE INTO discovered_behavior_events VALUES (?,?,?,?,?,?,?,?,?)',(event['event_id'],event['job_id'],event['video_id'],event['run_id'],event['track_id'],'unknown',event['local_start'],event['local_end'],json.dumps(event,ensure_ascii=False)))
            c.execute('INSERT INTO discovered_behavior_reviews VALUES (?,?,?,?,?,?)',(rid,body.event_id,a['actor'],now(),body.decision,json.dumps(data,ensure_ascii=False)))
        return {'review_id':rid,'decision':body.decision,'source_kind':data['source_kind']}

    @app.post('/api/context/corrections/{rid}/revoke')
    def revoke(rid:str,body:dict,a=Depends(writer)):
        reason=body.get('reason','')
        if not isinstance(reason,str) or not 5<=len(reason)<=2000:raise ValueError('revocation_reason_required')
        from .service import DiscoveryService
        return DiscoveryService(context).revoke_review(rid,a['actor'],reason)
