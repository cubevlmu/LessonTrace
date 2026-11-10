"""Merge compatible observed supports, never bridge tracking segments or people."""
from src.agent.fusion.evidence import version


def merge(proposals,gap=.6):
    groups=[]
    for p in sorted(proposals,key=lambda p:(p['run_id'],p['track_id'],p['action_category'],p['segment_id'],p['local_start'])):
        key=(p['run_id'],p['track_id'],p['action_category'],p['segment_id'],p['final_decision'])
        existing=next((g for g in reversed(groups) if g['_key']==key and p['local_start']<=g['local_end']+gap),None)
        if existing:
            existing['local_end']=max(existing['local_end'],p['local_end']);existing['members'].append(p)
        else:groups.append({'_key':key,'local_start':p['local_start'],'local_end':p['local_end'],'members':[p]})
    result=[]
    for g in groups:
        ms=g.pop('members');g.pop('_key');first=ms[0]
        times=sorted({t for m in ms for t in m['observed_time_support']})
        result.append({**first,**g,'event_id':'discovered_'+version([m['proposal_id'] for m in ms])[:28],
            'window_ids':[m['window_id'] for m in ms],'evidence_refs':list(dict.fromkeys(r for m in ms for r in m['evidence_refs'])),
            'frames':list({f['frame_id']:f for m in ms for f in m['frames']}.values()),
            'observed_time_support':times,'support_facts':[f for m in ms for f in m['support_facts']],
            'raw_vlm_decisions':[m['raw_vlm_decision'] for m in ms],
            'blind_observation_rounds':[m.get('blind_observations',{}) for m in ms],
            'subject_checks':{'passed':all(m['subject_checks']['passed'] for m in ms),'failures':list(dict.fromkeys(f for m in ms for f in m['subject_checks'].get('failures',[])))},
            'limitations':list(dict.fromkeys(l for m in ms for l in m['limitations'])),
            'CESA_checks':[m['CESA_checks'] for m in ms],'unobserved_intervals':[[a,b] for a,b in zip(times,times[1:]) if b>a],
            'source_kind':'model_discovered_event','review_status':'unreviewed',
            'event_boundary_kind':'extent_of_sampled_evidence_not_action_onset_or_offset'})
    return result
