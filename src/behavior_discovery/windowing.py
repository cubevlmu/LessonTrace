"""Windows are made only within actual visible trajectory segments."""
from bisect import bisect_left, bisect_right

from src.agent.fusion.evidence import version


def plan_windows(source,rid,config,jid):
    if config.analysis_mode == 'lesson_sample':
        return plan_lesson_samples(source, rid, config, jid)
    if config.analysis_mode=='full_timeline':return plan_full_windows(source,rid,config,jid)
    tracks=source.tracks(rid);tracks.sort(key=lambda t:(-t['observed_frame_count'],t['track_id']))
    quality=None
    if config.prefer_clear_targets and not config.track_ids:
        from src.tracking.quality import for_run
        quality=for_run(source,rid);ranks={r['track_id']:i for i,r in enumerate(quality['tracks'])};preferred={r['track_id'] for r in quality['tracks'] if r['preferred_for_action_review']}
        tracks=[t for t in tracks if t['track_id'] in preferred];tracks.sort(key=lambda t:ranks[t['track_id']])
    selected=[t for t in tracks if not config.track_ids or t['track_id'] in config.track_ids][:config.max_tracks];windows=[];eligible=0
    for track in selected:
        track_windows=0
        obs = sorted(source.observations(rid, track['track_id']), key=lambda row: row['global_timestamp'])
        timestamps = [row['global_timestamp'] for row in obs]
        for si,s in enumerate(track['segments']):
            start=s['global_start'];end=s['global_end'];step=config.window_seconds*(1-config.overlap);a=start
            while a<=end+1e-6:
                b = min(end, a + config.window_seconds)
                actual = obs[bisect_left(timestamps, a - 1e-7):bisect_right(timestamps, b + 1e-7)]
                if actual:
                    eligible+=1
                    if track_windows<config.max_windows or config.prefer_clear_targets:
                        # Hybrid sampler: close temporal sequence + the final visible instant.
                        chosen=[actual[0]]
                        for k in range(1,config.frames-1):
                            candidate=min(actual,key=lambda o:abs(o['global_timestamp']-(a+k*config.close_interval)))
                            if candidate['frame_index'] not in {x['frame_index'] for x in chosen}:chosen.append(candidate)
                        if actual[-1]['frame_index'] not in {x['frame_index'] for x in chosen}:chosen.append(actual[-1])
                        chosen.sort(key=lambda o:o['global_timestamp'])
                        scope={'run_id':rid,'track_id':track['track_id'],'start':a,'end':b,'segment_id':si,'frame_indices':[o['frame_index'] for o in chosen]}
                        windows.append({**scope,'window_id':version({'job':jid,**scope})[:32],
                            'sampling_strategy':'close_sequence_plus_final_visible_instant','exhaustive':False,'track_window_index':track_windows})
                        track_windows+=1
                if b>=end:break
                a+=step
    if config.prefer_clear_targets and selected:
        import math
        distributed=[];per_track=max(1,math.ceil(config.max_windows/len(selected)))
        for track in selected:
            candidates=[w for w in windows if w['track_id']==track['track_id']];take=min(per_track,len(candidates))
            indices=sorted({round(k*(len(candidates)-1)/(take-1)) for k in range(take)}) if take>1 else [len(candidates)//2] if take else []
            for order,index in enumerate(indices):
                value=candidates[index];value['track_window_index']=order;value['sampling_strategy']='distributed_visible_windows_close_sequence_and_last';distributed.append(value)
        windows=distributed
    windows=sorted(windows,key=lambda w:(w['track_window_index'],w['start'],w['track_id']))[:config.max_windows]
    return windows,{'total_tracks':quality['raw_track_count'] if quality else len(tracks),'quality_diagnostics':quality,'selected_tracks':len(selected),'filtered_tracks':(quality['raw_track_count'] if quality else len(tracks))-len(selected),
        'eligible_windows_before_budget':eligible,'selected_windows':len(windows),'omitted_windows':eligible-len(windows),
        'scope':'Budget-limited actual observations, not exhaustive behavior detection coverage.'}


def plan_lesson_samples(source, rid, config, jid):
    """One observed subject near each bin midpoint. Empty bins stay empty."""
    _, clip = source.run(rid)
    lo, hi = clip['start'], clip['end']
    # Bound both database scans and inference on this short preview.
    count = min(8, config.max_windows, max(1, int((hi - lo) / config.window_seconds)))
    windows = []
    tracks = set()
    empty = []
    for index in range(count):
        start = lo + (hi - lo) * index / count
        end = lo + (hi - lo) * (index + 1) / count
        allowed = sorted(tracks) if len(tracks) >= config.max_tracks else config.track_ids
        restrict = ' AND track_id IN (' + ','.join('?' for _ in allowed) + ')' if allowed else ''
        # Half-open bins give a boundary detection to exactly one window.
        stop = '<=' if index == count - 1 else '<'
        anchors = source.rows(
            "SELECT frame_index, track_id, global_timestamp FROM track_observations "
            "WHERE run_id=? AND global_timestamp>=? AND global_timestamp " + stop + " ? "
            "AND bbox_y2-bbox_y1>=80 AND bbox_y2-bbox_y1>=1.05*(bbox_x2-bbox_x1) "
            "AND tracker_state='matched_visible_detection'" + restrict +
            " ORDER BY abs(global_timestamp-?), (bbox_y2-bbox_y1) DESC, track_id LIMIT 1",
            (rid, start, end, *allowed, (start + end) / 2))
        if not anchors:
            empty.append(index + 1)
            continue
        anchor = anchors[0]
        tid, at = anchor['track_id'], anchor['global_timestamp']
        segment, = source.rows(
            'SELECT segment_index, global_start, global_end FROM track_visibility_segments '
            'WHERE run_id=? AND track_id=? AND first_frame<=? AND last_frame>=?',
            (rid, tid, anchor['frame_index'], anchor['frame_index']))
        left = max(segment['global_start'], start, at - config.window_seconds / 2)
        right = min(segment['global_end'], end, at + config.window_seconds / 2)
        actual = source.rows(
            "SELECT frame_index, global_timestamp FROM track_observations WHERE run_id=? AND track_id=? "
            "AND global_timestamp BETWEEN ? AND ? AND global_timestamp " + stop + " ? "
            "AND tracker_state='matched_visible_detection' ORDER BY global_timestamp", (rid, tid, left, right, end))
        first, last = actual[0]['global_timestamp'], actual[-1]['global_timestamp']
        indices = []
        for k in range(config.frames):
            target = first + k * (last - first) / (config.frames - 1)
            frame = min(actual, key=lambda o: abs(o['global_timestamp'] - target))['frame_index']
            if frame not in indices:
                indices.append(frame)
        scope = dict(run_id=rid, track_id=tid, start=first, end=last, segment_id=segment['segment_index'], frame_indices=indices)
        windows.append({**scope, 'window_id': version([jid, 'lesson_sample', index, scope])[:32],
                        'sampling_strategy': 'one_visible_region_near_time_bin_center', 'time_bin': index + 1,
                        'exhaustive': False})
        tracks.add(tid)
    total = source.rows('SELECT COUNT(*) AS n FROM person_tracks WHERE run_id=?', (rid,))[0]['n']
    return windows, {'analysis_mode': 'lesson_sample', 'total_tracks': total, 'selected_tracks': len(tracks),
                     'selected_windows': len(windows), 'eligible_windows_before_budget': len(windows),
                     'omitted_windows': 0, 'time_bins': count, 'empty_time_bins': empty,
                     'scope': 'Sparse lesson preview. A bin can have no sufficiently large visible region.'}


def plan_full_windows(source,rid,config,jid):
    """Cover every actual visible segment; dense samples are not framewise truth."""
    tracks=sorted(source.tracks(rid),key=lambda t:t['track_id'])
    selected=[t for t in tracks if not config.track_ids or t['track_id'] in config.track_ids]
    import math
    expected=sum(max(1,math.ceil((s['global_end']-s['global_start'])/config.timeline_window_seconds-1e-9)) for t in selected for s in t['segments'])
    if expected>10000:raise ValueError('full_timeline_exceeds_10000_windows; split_recording_into_shorter_videos')
    windows=[];covered=0.
    for track in selected:
        obs=sorted((o for o in source.observations(rid,track['track_id']) if o.get('tracker_state','matched_visible_detection')=='matched_visible_detection'),key=lambda o:o['global_timestamp'])
        timestamps=[o['global_timestamp'] for o in obs]
        for si,segment in enumerate(track['segments']):
            start,end=segment['global_start'],segment['global_end'];a=start
            covered+=max(0,end-start)
            while a<=end+1e-7:
                b=min(end,a+config.timeline_window_seconds)
                # Use actual timestamps only; do not synthesize frames across gaps.
                actual=obs[bisect_left(timestamps,a-1e-7):bisect_right(timestamps,b+1e-7)]
                if actual:
                    chosen=[]
                    for k in range(config.frames):
                        timestamp=actual[0]['global_timestamp']+(actual[-1]['global_timestamp']-actual[0]['global_timestamp'])*k/(config.frames-1)
                        candidate=min(actual,key=lambda o:abs(o['global_timestamp']-timestamp))
                        if candidate['frame_index'] not in {o['frame_index'] for o in chosen}:chosen.append(candidate)
                    scope={'run_id':rid,'track_id':track['track_id'],'start':a,'end':b,'segment_id':si,'frame_indices':[o['frame_index'] for o in chosen]}
                    windows.append({**scope,'window_id':version({'job':jid,'mode':'full_timeline',**scope})[:32],
                                    'sampling_strategy':'dense_uniform_actual_timestamps','exhaustive':False,
                                    'window_coverage':'all_visible_segments','track_window_index':len(windows)})
                    if len(windows)>10000:raise ValueError('full_timeline_exceeds_10000_windows; split_recording_into_shorter_videos')
                if b>=end:break
                a=b
    # Interleave subjects by time rather than leaving later people until the end.
    windows.sort(key=lambda w:(w['start'],w['track_id'],w['segment_id']))
    return windows,{'analysis_mode':'full_timeline','total_tracks':len(tracks),'selected_tracks':len(selected),
                    'filtered_tracks':len(tracks)-len(selected),'eligible_windows_before_budget':len(windows),
                    'selected_windows':len(windows),'omitted_windows':0,'visible_track_seconds':covered,
                    'maximum_model_requests':len(windows)*(1 if config.processing_profile=='efficient' else 2),'window_seconds':config.timeline_window_seconds,
                    'frames_per_window':config.frames,'scope':'All selected observed tracks and visible segments; sampled actions, not guaranteed framewise recognition.'}
