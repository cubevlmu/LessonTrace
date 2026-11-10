"""Class-scoped matching reuses 11B observations, candidates, authorizations and bindings."""
import json
from .service import IdentityService
from .tracks import TrackAccess
from .opencv_backend import OpenCVFaceBackend
from .schemas import Match,VideoAuthorization


class ClassroomMatcher:
    def __init__(self,store,context,backend=None):
        self.store=store;self.context=context
        self.tracks=TrackAccess(context.source.tracking_db,context.source.registry_db,context.source.video_root)
        self.identity=IdentityService(store,self.tracks,backend or OpenCVFaceBackend())

    def run_classroom(self,cid,rid,actor,consent,expires,all_visible_authorized):
        if not all_visible_authorized:raise ValueError('video_identity_processing_authorization_required')
        run,clip=self.tracks.run(rid)
        from src.behavior_discovery.repository import Repository
        Repository(self.context.repo).video(clip['video_id'],actor)
        with self.store.connection() as c:
            if not c.execute('SELECT 1 FROM classroom_videos WHERE classroom_id=? AND video_id=?',(cid,clip['video_id'])).fetchone():raise ValueError('video_not_in_classroom')
            ids=[r[0] for r in c.execute("SELECT subject_id FROM classroom_students WHERE classroom_id=? AND status='active'",(cid,))]
        allowed=[]
        for sid in ids:
            try:self.identity.purpose(sid,'face_matching');allowed.append(sid)
            except ValueError:continue
        if not allowed:raise ValueError('no_classroom_subjects_authorized_for_matching')
        # Loading a backend is allowed only after the scope and source permission checks.
        self.identity.backend.load()
        authorization=self.identity.authorize_video(VideoAuthorization(subject_id=allowed[0],run_id=rid,consent_record=consent,expires_at=expires,all_visible_people_authorized=True),actor)
        results=[]
        for track in self.tracks.tracks(rid)[:20]:
            observations=[o for o in self.tracks.observations(rid,track['track_id']) if o['tracker_state']=='matched_visible_detection']
            if not observations:continue
            # Each visibility segment is independent; never bridge an unobserved gap.
            for segment in track['segments']:
                obs=[o for o in observations if segment['global_start']<=o['global_timestamp']<=segment['global_end']]
                if not obs:continue
                indices=sorted({obs[round(i*(len(obs)-1)/min(5,len(obs)-1))]['frame_index'] for i in range(min(6,len(obs)))}) if len(obs)>1 else [obs[0]['frame_index']]
                record=self.identity.match(Match(authorization_id=authorization['authorization_id'],run_id=rid,track_id=track['track_id'],frame_indices=indices),actor,subject_ids=set(allowed))
                results.append({'track_id':track['track_id'],**record})
        return {'run_id':rid,'classroom_id':cid,'results':results,'track_limit':20,'tracks_not_exhaustive':len(self.tracks.tracks(rid))>20,'model_id':self.identity.backend.model_id,'source_kind':'ai_identity_candidates_not_human_confirmation'}
