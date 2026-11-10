import csv
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from cryptography.fernet import Fernet
from src.identity.store import IdentityStore,uid,now
from src.identity.service import IdentityService
from src.identity.face_backend import PersonBackend
from src.attribution.service import AttributionService
from src.attribution.repository import AttributionRepository
from src.attribution.schemas import Human
from src.agent.fusion import reporting
from src.agent.fusion.evidence import version
from . import repository

ROOT=Path(__file__).resolve().parents[2]


class ExistingIdentityStore(IdentityStore):
    """Native read methods without schema changes, key creation or RW connections."""
    def __init__(self,folder):
        self.directory=Path(folder);self.path=self.directory/'identity.db'
        self.cipher=Fernet((self.directory/'encryption.key').read_bytes())
    @contextmanager
    def connection(self):
        with sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True) as c:
            c.row_factory=sqlite3.Row;yield c


class Workbench:
    def __init__(self,context):self.context=context;repository.migrate(context.repo)
    def identity(self,write=False):
        folder=self.context.identity_dir
        if folder is None:raise ValueError('identity_module_not_configured')
        store=IdentityStore(folder) if write else ExistingIdentityStore(folder)
        return IdentityService(store,self.context.source,PersonBackend(enabled=False))
    def attribution(self,write=False):
        repo=AttributionRepository(self.context.attribution_repo.path) if write else self.context.attribution_repo
        return AttributionService(self.context.source,repo,self.context.identity_dir)
    def pairs(self,actor):
        path=ROOT/'outputs/review_quality_validation/comparison.json'
        if not path.exists():return []
        values=json.loads(path.read_text(encoding='utf-8'))['results'];result=[]
        with self.context.repo.connection() as c:
            for row in values:
                ids=[row['old_report_id'],row['new_report_id']]
                found=c.execute('SELECT report_id,owner FROM fusion_reports WHERE report_id IN (?,?)',ids).fetchall()
                if len(found)!=2 or any(x['owner']!=actor for x in found):continue
                result.append({'case_id':row['case_id'],'question':row['question'],'reports':{'11E':ids[0],'11F':ids[1]},
                    'priority':row['case_id'] in {'q01','q02','q05','q08','q12'}})
        return sorted(result,key=lambda r:(not r['priority'],r['case_id']))
    def track_tasks(self):
        path=ROOT/'outputs/person_tracking_validation/manual_track_review.csv'
        if not path.exists():return []
        with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        registered={r['run_id'] for r in self.context.source.runs()}
        rows=[r for r in rows if r['run_id'] in registered]
        for r in rows:
            r['track_id']=int(r['track_id']);r['sample_times']=[float(t) for t in r['sample_global_times'].split(';')]
            r['legacy_human_fields']={k:r.get(k,'') for k in ['same_person_across_samples','actual_scene_observed','obvious_id_switch_times','reviewer','notes']}
            r['task_id']='track:'+r['run_id']+':'+str(r['track_id'])
        return rows
    def track_detail(self,rid,tid):
        run,clip=self.context.source.verified_run(rid);obs=self.context.source.observations(rid,tid)
        if not obs:raise ValueError('track_has_no_actual_observations')
        a,b=obs[0]['global_timestamp'],obs[-1]['global_timestamp']
        timeline=self.attribution().timeline(rid,tid)
        return {'run_id':rid,'track_id':tid,'clip':{k:clip[k] for k in ['clip_id','video_id','start','end']},
            'observations':obs,'segments':timeline['visible_segments'],'gaps':timeline['gaps'],
            'jump_candidates':self.context.source.continuity_risks(rid,tid,a,b),
            'review_history':repository.tracks(self.context.repo,rid,tid,a,b),
            'review_scope':repository.track_scope(self.context.repo,rid,tid,a,b,run['metadata']['source_sha256']),
            'note':'Position jumps are review candidates, not automatically verified identity switches.'}
    def review_track(self,body,actor):
        run,clip=self.context.source.verified_run(body.run_id)
        if not clip['start']<=body.start<=body.end<=clip['end']:raise ValueError('review_outside_registered_clip')
        obs={o['frame_index']:o for o in self.context.source.observations(body.run_id,body.track_id) if body.start<=o['global_timestamp']<=body.end}
        if len(set(body.evidence_frames))!=len(body.evidence_frames) or not set(body.evidence_frames)<=set(obs):raise ValueError('review_frames_not_actual_visible_observations')
        for frame in body.evidence_frames:self.context.source.frame(body.run_id,body.track_id,frame)
        return repository.append_track(self.context.repo,body,actor,run['metadata']['source_sha256'])
    def score(self,body,actor):
        report=reporting.historical(self.context,body.report_id,actor,True)
        # Report/case association is explicit; never let a score for q01 attach to q02.
        pair=next((p for p in self.pairs(actor) if p['case_id']==body.case_id and body.report_id in p['reports'].values()),None)
        if pair is None and body.case_id!='custom:'+body.report_id:raise ValueError('question_report_pair_mismatch')
        with self.context.repo.connection() as c:payload=c.execute('SELECT payload FROM fusion_reports WHERE report_id=?',(body.report_id,)).fetchone()[0]
        review=uid();repository.append_json(self.context.repo,'validation_qa_scores',(review,body.case_id,body.report_id,actor,now(),version(payload),json.dumps(body.model_dump(),ensure_ascii=False)))
        return {'review_id':review,'reviewer':actor,'report_id':body.report_id,'scope':'human_attested_question_quality_not_model_self_grading'}
    def assess_actor(self,body,actor):
        if body.candidate_id.startswith('discovered:'):
            from src.behavior_discovery.service import DiscoveryService
            return DiscoveryService(self.context).review(body.candidate_id.split(':',1)[1],body,actor)
        native=self.attribution();candidate,_,_,_=native.candidate(body.candidate_id)
        if body.error_in_confirmed_binding=='yes':
            with self.context.attribution_repo.connection() as c:old=c.execute('SELECT * FROM event_track_bindings WHERE binding_id=? AND candidate_id=?',(body.evaluated_binding_id,body.candidate_id)).fetchone()
            if not old or json.loads(old['payload']).get('decision')!='human_confirmed' or max(old['start'],body.start)>min(old['end'],body.end):
                raise ValueError('explicit_error_requires_confirmed_original_binding_in_reviewed_time')
        if not candidate['start']<=body.start<=body.end<=candidate['end']:raise ValueError('assessment_interval_outside_candidate')
        obs={o['frame_index']:o for o in self.context.source.observations(candidate['run_id'],candidate['track_id']) if body.start<=o['global_timestamp']<=body.end}
        if not set(body.evidence_frames)<=set(obs):raise ValueError('assessment_requires_actual_visible_frames')
        for frame in body.evidence_frames:self.context.source.frame(candidate['run_id'],candidate['track_id'],frame)
        binding=None
        if body.decision=='confirmed':
            if body.behavior_visible!='yes' or body.evidence_time_correct!='yes' or body.attribution_sufficient!='yes' or not body.same_person_in_reviewed_interval:
                raise ValueError('confirmation_requires_visible_action_correct_time_and_actor_evidence')
            binding=self.attribution(True).human(Human(candidate_id=body.candidate_id,start=body.start,end=body.end,decision='human_confirmed',
                evidence_frames=body.evidence_frames,observable_action=body.observable_action,basis=body.basis,
                interval_inspected=True,same_person_in_reviewed_interval=True,continuity_risks_addressed=body.continuity_risks_addressed),actor)['binding_id']
        # Rejected / ambiguous / uncertain are independent scoped assessments.
        # They do not claim that the whole scene lacks the action or identify an executor.
        review=uid()
        try:repository.append_json(self.context.repo,'validation_actor_assessments',(review,body.candidate_id,binding,actor,now(),json.dumps(body.model_dump(),ensure_ascii=False)))
        except Exception as error:
            if binding:raise ValueError('native_binding_saved_but_validation_summary_failed; inspect_binding:'+binding) from error
            raise
        return {'review_id':review,'native_binding_id':binding,'decision':body.decision}
