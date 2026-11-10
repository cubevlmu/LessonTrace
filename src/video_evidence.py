"""Ordered raw-frame evidence. Excel ROI remains an unverified spatial region."""
import hashlib
import math
from pathlib import Path
import cv2


def read_frame(path,local_time):
    """Legacy clip preview helper; product tracking keeps its actual PTS path."""
    if not Path(path).exists():return None,'missing_video'
    cap=cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():return None,'cannot_open_video'
        fps=cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps<=0:return None,'invalid_fps'
        cap.set(cv2.CAP_PROP_POS_FRAMES,round(local_time*fps));ok,frame=cap.read()
        return (frame,None) if ok and frame is not None else (None,'decode_failed_at_'+str(local_time))
    finally:cap.release()


class EvidenceError(ValueError):
    pass


def extract_evidence(db,event_id,clip_id,output,video_root,frame_count=8,*,sample_times=None,frame_prefix='f'):
    if not 6 <= frame_count <= 12:raise EvidenceError('frame_count_must_be_6_to_12')
    event=db.event(event_id);clip=db.clip(clip_id)
    path=Path(clip['path']).resolve();root=Path(video_root).resolve()
    if root not in path.parents:raise EvidenceError('registered_path_outside_video_root')
    if event['video_id']!=clip['video_id']:raise EvidenceError('clip_event_video_mismatch')
    if not path.is_file():raise EvidenceError('video_missing')
    cap=cv2.VideoCapture(str(path))
    try:
        fps=cap.get(cv2.CAP_PROP_FPS);total=cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not cap.isOpened() or not math.isfinite(fps) or fps<=0 or total<1:raise EvidenceError('video_undecodable')
        duration=total/fps
        start=max(event['t_start'],clip['start'])
        end=min(event['t_end'],clip['end'],clip['start']+duration)
        if end<=start:raise EvidenceError('no_playable_time_coverage')
        first=max(0,math.ceil((start-clip['start'])*fps-1e-8))
        last=min(int(total)-1,math.ceil((end-clip['start'])*fps-1e-8)-1)
        if sample_times is None:
            indices=[round(first+(last-first)*i/(frame_count-1)) for i in range(frame_count)]
        else:
            if not 1<=len(sample_times)<=4 or any(not math.isfinite(t) or not start<=t<end for t in sample_times):
                raise EvidenceError('additional_times_outside_coverage')
            indices=[min(last,max(first,round((t-clip['start'])*fps))) for t in sample_times]
            if indices!=sorted(indices):raise EvidenceError('additional_times_not_ordered')
        expected=frame_count if sample_times is None else len(sample_times)
        if first>last or len(set(indices))<expected:raise EvidenceError('insufficient_distinct_frames')
        output=Path(output);output.mkdir(parents=True,exist_ok=False)
        frames=[]
        for i,index in enumerate(indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES,index);ok,full=cap.read()
            if not ok or full is None:raise EvidenceError(f'frame_decode_failed_index_{index}')
            h,w=full.shape[:2]
            # Scaling a mismatched coordinate system would add an unvalidated assumption.
            if (w,h)!=(event['frame_width'],event['frame_height']):raise EvidenceError('excel_video_dimensions_mismatch')
            x,y,rw,rh=event['region_xywh']
            if not all(math.isfinite(v) for v in [x,y,rw,rh]) or rw<=0 or rh<=0:raise EvidenceError('invalid_roi')
            x1,y1=max(0,int(math.floor(x))),max(0,int(math.floor(y)))
            x2,y2=min(w,int(math.ceil(x+rw))),min(h,int(math.ceil(y+rh)))
            if x2<=x1 or y2<=y1:raise EvidenceError('roi_outside_frame')
            roi=full[y1:y2,x1:x2]
            frame={'frame_id':f'{frame_prefix}{i:03d}','frame_index':index,'local_time':index/fps,'global_time':clip['start']+index/fps,'roi_xyxy':[x1,y1,x2,y2],'sampling_role':'uniform' if sample_times is None else 'additional_contiguous','files':{},'sha256':{}}
            for view,img in [('full',full),('roi',roi),('input_full',full),('input_roi',roi)]:
                if view.startswith('input_'):
                    limit=768 if view=='input_full' else 640
                    scale=min(1,limit/max(img.shape[:2]))
                    if scale<1:img=cv2.resize(img,(max(1,round(img.shape[1]*scale)),max(1,round(img.shape[0]*scale))))
                dest=output/f'{i:03d}_{view}.jpg'
                if not cv2.imwrite(str(dest),img,[cv2.IMWRITE_JPEG_QUALITY,92]):raise EvidenceError('evidence_write_failed')
                frame['files'][view]=str(dest.resolve());frame['sha256'][view]=hashlib.sha256(dest.read_bytes()).hexdigest()
            frames.append(frame)
        partial=start>event['t_start'] or end<event['t_end']
        limitations=['Sparse ordered samples do not verify every frame or the entire event.','The fixed Excel ROI may contain multiple people; no identity or tracking is established.']
        if partial:limitations.append('Partial clip coverage: judgment is restricted to the sampled intersection, not the full annotated interval.')
        return {'event_id':event_id,'clip_id':clip_id,'source_path':str(path),'source_size':path.stat().st_size,'source_mtime_ns':path.stat().st_mtime_ns,'requested_window':[event['t_start'],event['t_end']],'playable_window':[start,end],'partial_coverage':partial,'fps':fps,'sampling_exhaustive':False,'subject_identity_verified':False,'frames':frames,'limitations':limitations}
    finally:cap.release()


def plan_dynamic_samples(evidence, interval=.4):
    """Choose one suspect gap by measured ROI change; this does not detect an action.

    Four additional frame indices are new, ordered and near-contiguous. Full images
    remain stored, but ROI-only batches keep model requests at four images.
    """
    if not .3<=interval<=.5:raise EvidenceError('dynamic_interval_must_be_0_3_to_0_5')
    frames=sorted(evidence['frames'],key=lambda f:f['global_time'])
    scored=[]
    for a,b in zip(frames,frames[1:]):
        images=[cv2.imread(f['files']['input_roi'],cv2.IMREAD_GRAYSCALE) for f in [a,b]]
        if any(img is None for img in images):raise EvidenceError('dynamic_probe_image_missing')
        images=[cv2.resize(img,(160,90)) for img in images]
        score=float(cv2.absdiff(images[0],images[1]).mean()/255)
        scored.append((score,a,b))
    if not scored:raise EvidenceError('dynamic_probe_requires_multiple_frames')
    score,a,b=max(scored,key=lambda v:v[0])
    fps=evidence['fps'];start,end=evidence['playable_window']
    step=max(1,round(interval*fps))
    clip_start=frames[0]['global_time']-frames[0]['local_time']
    first=max(0,math.ceil((start-clip_start)*fps-1e-8))
    last=math.ceil((end-clip_start)*fps-1e-8)-1
    used={f['frame_index'] for f in frames}
    anchor=(a['global_time']+b['global_time'])/2
    center=round((anchor-clip_start)*fps-1.5*step)
    candidates=sorted(range(first,max(first,last-3*step+1)),key=lambda i:(abs(i-center),i))
    indices=next(([i+j*step for j in range(4)] for i in candidates
                  if all(i+j*step not in used for j in range(4))),None)
    if indices is None:raise EvidenceError('insufficient_new_contiguous_frames')
    return {'strategy':'contiguous_roi_sequence','anchor_gap':[a['global_time'],b['global_time']],
            'roi_mean_absolute_change':score,'change_is_not_person_tracking':True,
            'requested_interval_seconds':interval,
            'global_times':[clip_start+i/fps for i in indices],
            'reason':'Investigate greatest measured region change, possibly motion, occlusion or illumination; not a detected action.'}
