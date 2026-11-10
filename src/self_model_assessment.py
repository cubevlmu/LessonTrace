"""Offline candidate probing. No weight promotion or student attribution."""
import math

def intersection_over_prediction(box,region):
    if len(box)!=4 or len(region)!=4 or not all(math.isfinite(x) for x in [*box,*region]):raise ValueError('invalid_box_or_region')
    x1,y1,x2,y2=box;rx,ry,rw,rh=region
    if x2<=x1 or y2<=y1 or rw<=0 or rh<=0:raise ValueError('non_positive_box_area')
    area=max(0,min(x2,rx+rw)-max(x1,rx))*max(0,min(y2,ry+rh)-max(y1,ry))
    return area/((x2-x1)*(y2-y1))

def candidate_score(frames,target,region,min_overlap=.3):
    matches=[]
    for f in frames:
        for detection in f['detections']:
            if detection['class_name']!=target:continue
            overlap=intersection_over_prediction(detection['bbox_xyxy'],region)
            if overlap>=min_overlap:matches.append({'frame_index':f['frame_index'],'global_time':f['global_time'],'score':detection['confidence'],'overlap':overlap,'bbox_xyxy':detection['bbox_xyxy']})
    return {'score':max((m['score'] for m in matches),default=0.),'matches':matches,'scope':'region_candidate_only_not_person_or_verified_action'}

def candidate_decision(score,threshold):
    """Adapt a proposal to the historical scorer; 'confirmed' is NOT a final verdict.

    A positive output remains an unverified regional candidate. The adapter must
    not be used to persist a human review, verified action or person attribution.
    """
    if not 0<threshold<=1 or not 0<=score<=1:raise ValueError('invalid_threshold_or_score')
    # Non-detection in a sparse frame never refutes an interval claim.
    return 'confirmed' if score>=threshold else 'uncertain'
