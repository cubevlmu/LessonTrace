import math
import re
from datetime import datetime


def seconds(value,reference):
    if reference=='unknown':
        if value is not None:raise ValueError('unknown_time_reference_cannot_have_precise_times')
        return None
    if reference=='wall_clock':
        if not isinstance(value,str):raise ValueError('wall_clock_requires_iso8601_with_timezone')
        try:d=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as e:raise ValueError('invalid_wall_clock') from e
        if d.tzinfo is None:raise ValueError('wall_clock_timezone_required')
        return d.timestamp()
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<0:raise ValueError('time_requires_nonnegative_finite_seconds')
    return float(value)


def map_activity(activity,link,clip):
    if not link or link['status']!='confirmed' or activity['time_reference']!=link['time_reference'] or activity['start_seconds'] is None:
        return {'status':'alignment_unknown','reason':'No current confirmed mapping for this document, clip and time reference.'}
    a=link['video_global_anchor']+activity['start_seconds']-link['text_anchor'];b=link['video_global_anchor']+activity['end_seconds']-link['text_anchor']
    lo,hi=max(a,clip['start']),min(b,clip['end'])
    if lo>hi:return {'status':'outside_clip_coverage','mapped_global_window':[a,b],'link_id':link['link_id'],'link_version':link['version']}
    return {'status':'verified_mapping','mapped_global_window':[lo,hi],'mapped_clip_local_window':[lo-clip['start'],hi-clip['start']],
            'link_id':link['link_id'],'link_version':link['version'],'planned_interval_fully_covered':lo==a and hi==b,'mapping_confirms_activity_occurred':False}


def explicit_suggestions(passage):
    pattern=r'\[(clip_local|dataset_global|lesson_elapsed)\s+(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\]\s*([^\n]+)'
    return [{'source_passage_id':passage['passage_id'],'time_reference':m[1],'start':float(m[2]),'end':float(m[3]),'description':m[4],
             'extraction_status':'candidate_requires_manual_review','matched_original':m[0]} for m in re.finditer(pattern,passage['content']) if float(m[2])<=float(m[3])]
