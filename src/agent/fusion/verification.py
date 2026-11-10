"""Budgeted reuse-first verification. Text/identity gaps do not trigger pictures."""
import os
import subprocess
import threading
import time


class ResourceMonitor:
    def __init__(self,enabled=True):self.enabled=enabled;self.samples=[];self.stop=threading.Event();self.thread=None
    def sample(self):
        if not self.enabled:return None
        try:
            output=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            mem,util=map(float,output.splitlines()[0].split(','));value={'memory_mib':mem,'utilization_pct':util};self.samples.append(value);return value
        except (OSError,ValueError,subprocess.SubprocessError):return None
    def __enter__(self):
        if self.enabled:
            self.sample()
            def loop():
                while not self.stop.wait(1):self.sample()
            self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()
        return self
    def __exit__(self,*args):
        self.stop.set()
        if self.thread:self.thread.join(4)
    def summary(self):
        return {'status':'disabled_by_request' if not self.enabled else ('observed' if self.samples else 'unobservable'),
            'peak_temperature_c':None,'temperature_monitoring':'removed_by_user','peak_memory_mib':max((s['memory_mib'] for s in self.samples),default=None),
            'samples':self.samples,'safety_note':'Memory/utilization only, whole-device samples. No temperature query or thermal gate.'}


def decision(plan,retrieved,request):
    if plan.intent=='cognitive_boundary':return {'action':'abstain','reason':'Cognition cannot be established by additional posture frames.'}
    if plan.effective_time_range is None:return {'action':'no_coverage','reason':'No registered clip coverage; do not fabricate video evidence.'}
    if plan.intent=='verified_person' and (not plan.subject_id or retrieved['subject_missing']):return {'action':'request_identity_review','reason':'A picture call cannot establish authorized identity confirmation.'}
    if plan.intent in {'teaching_plan','context_relation'} and not any(e['evidence_type']=='teaching_activity' and e['provenance']['alignment']['status']=='verified_mapping' for e in retrieved['evidence']):
        return {'action':'request_time_anchor','reason':'Only explicit human text-video mapping resolves this gap.'}
    if not plan.track_id:return {'action':'reuse','reason':'Existing scene evidence is retrieved; actor verification requires an explicitly selected Track.'}
    timeline=retrieved['timelines'].get(plan.track_id)
    if not timeline:return {'action':'retain_unknown','reason':'Track has no current evidence in scope.'}
    if timeline['confirmed_behavior_intervals']:return {'action':'reuse','reason':'Existing human evidence is available; retain scoped facts and conflicts.'}
    if any(r['final_decision']!='error' for r in timeline['visual_reviews']):return {'action':'reuse','reason':'Existing model uncertainty is already evidence; repeating the same frames is not new information.'}
    candidates=[c for c in timeline['candidate_behaviors'] if c['target_action']!='sleep' and 'tracking_gap' not in c['ambiguity_flags'] and plan.effective_time_range[0]<=c['end'] and plan.effective_time_range[1]>=c['start']]
    if not candidates:return {'action':'retain_unknown','reason':'No resolvable subject-specific candidate; posture does not establish sleep.'}
    if not request.allow_verification:return {'action':'suggest_subject_review','reason':'Missing actor evidence may benefit from the existing 11C tool; automatic calls disabled by default.','candidate_id':candidates[0]['candidate_id']}
    return {'action':'verify_subject','reason':'Explicit permission, relevant unresolved subject and a bounded tool are available.','candidate_id':candidates[0]['candidate_id']}
