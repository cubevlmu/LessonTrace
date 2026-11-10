"""Observable crop quality and nested-region diagnostics, never identity merging."""
import math,statistics
from collections import defaultdict,Counter

def assess(tracks,observations):
    grouped=defaultdict(list);frames=defaultdict(list);nested=defaultdict(Counter)
    for o in observations:
        grouped[o['track_id']].append(o);frames[o['frame_index']].append(o)
    for rows in frames.values():
        for a in rows:
            area=(a['bbox_x2']-a['bbox_x1'])*(a['bbox_y2']-a['bbox_y1'])
            if area<=0:continue
            for b in rows:
                if a['track_id']==b['track_id']:continue
                other=(b['bbox_x2']-b['bbox_x1'])*(b['bbox_y2']-b['bbox_y1'])
                overlap=max(0,min(a['bbox_x2'],b['bbox_x2'])-max(a['bbox_x1'],b['bbox_x1']))*max(0,min(a['bbox_y2'],b['bbox_y2'])-max(a['bbox_y1'],b['bbox_y1']))
                if other>=2*area and overlap/area>=.88:nested[a['track_id']][b['track_id']]+=1
    result=[]
    for track in tracks:
        tid=track['track_id'];rows=grouped[tid];flags=[];duplicate=None;ratio=0
        if not rows:continue
        widths=[o['bbox_x2']-o['bbox_x1'] for o in rows];heights=[o['bbox_y2']-o['bbox_y1'] for o in rows]
        height=statistics.median(heights);aspect=statistics.median(h/w for h,w in zip(heights,widths) if w>0)
        duration=track.get('global_end_time',rows[-1]['global_timestamp'])-track.get('global_start_time',rows[0]['global_timestamp'])
        if nested[tid]:
            other,count=nested[tid].most_common(1)[0];ratio=count/len(rows)
            if count>=3 and ratio>=.7:duplicate=other;flags.append('possible_nested_duplicate_or_body_part')
        if height<80 or aspect<1.05:flags.append('small_or_partial_person_region')
        if len(rows)<3 or duration<.5:flags.append('short_lived_region')
        score=math.log1p(len(rows))*min(height/200,1.5)*min(max(aspect,.4),2.)*(.1 if duplicate else 1)
        result.append({'track_id':tid,'quality_flags':flags,'possible_nested_with':duplicate,'nested_fraction':ratio,'sampled_observations':len(rows),'median_height_px':round(height,2),'median_aspect':round(aspect,3),'selection_score':round(score,3),'preferred_for_action_review':not duplicate and height>=80 and aspect>=1.05 and len(rows)>=3,'identity_merge_performed':False})
    return sorted(result,key=lambda r:(not r['preferred_for_action_review'],-r['selection_score'],r['track_id']))

def for_run(source,rid):
    tracks=source.tracks(rid)
    rows=source.rows("SELECT * FROM track_observations WHERE run_id=? AND tracker_state='matched_visible_detection' ORDER BY frame_index,track_id LIMIT 60001",(rid,))
    if len(rows)>60000:rows=rows[:60000]
    result=assess(tracks,rows)
    return {'tracks':result,'raw_track_count':len(tracks),'preferred_region_count':sum(t['preferred_for_action_review'] for t in result),'possible_nested_region_count':sum(t['possible_nested_with'] is not None for t in result),'not_a_student_count':True,'sample_limit':60000,'diagnostic_only_no_identity_merge':True}
