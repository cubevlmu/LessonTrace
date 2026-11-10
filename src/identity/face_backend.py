"""Real PersonAnalysis 2.1 adapter. No network download or reference self-updates."""
import hashlib
import importlib.metadata
import json
import threading
import time
from pathlib import Path
import cv2
import numpy as np


def feature(value):
    a=np.asarray(value,dtype=np.float32)
    if a.ndim!=1 or not a.size or not np.isfinite(a).all() or np.linalg.norm(a)<1e-9:
        raise ValueError('invalid_face_feature')
    return a/np.linalg.norm(a)


def cosine(a,b):
    a,b=feature(a),feature(b)
    if a.shape!=b.shape: raise ValueError('incompatible_feature_dimensions')
    return float(np.clip(a@b,-1,1))


def owner(box, observations):
    """Ambiguous overlapping people or multiple potential owners -> no assignment."""
    x1,y1,x2,y2=map(float,box)
    if not np.isfinite([x1,y1,x2,y2]).all() or x2<=x1 or y2<=y1: return None
    owners=[]
    for o in observations:
        a,b,c,d=[o[k] for k in ('bbox_x1','bbox_y1','bbox_x2','bbox_y2')]
        area=max(0,min(c,x2)-max(a,x1))*max(0,min(d,y2)-max(b,y1))
        # Face must be predominantly inside, near upper half of the observed body.
        if area/((x2-x1)*(y2-y1))>=.9 and b<=(y1+y2)/2<=b+.6*(d-b): owners.append(o['track_id'])
    return owners[0] if len(owners)==1 else None


class PersonBackend:
    def __init__(self, root=None, name='cheetah_s', enabled=False):
        self.root=Path(root or '~/.insightface').expanduser().resolve()
        self.name=name
        self.enabled=enabled
        self.model=None
        self.lock=threading.Lock()
        self.model_id=None

    def probe(self):
        import onnxruntime as ort
        folder=self.root/'models'/self.name
        files=[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in folder.glob('*.onnx')]
        return {'backend':'InsightFace PersonAnalysis','package_version':importlib.metadata.version('insightface'),
                'onnxruntime_version':ort.__version__,'available_providers':ort.get_available_providers(),
                'requested_provider':'CPUExecutionProvider','configured_model_package':self.name,
                'model_files':files,'model_status':'present_not_validated' if files else 'not_installed',
                'automatic_matching_enabled':self.enabled,'recognition_validation':'not_validated',
                'model_license':'Read local MODEL.LICENSE and upstream terms before use; code license does not license weights.',
                'network_downloads':False}

    def load(self):
        if not self.enabled: raise ValueError('automatic_identity_matching_disabled')
        if self.model is not None: return
        folder=self.root/'models'/self.name
        if not (folder/'manifest.json').is_file() or not (folder/'MODEL.LICENSE').is_file():
            raise ValueError('local_model_package_or_license_missing; no_download_performed')
        # Official local validation checks signed license and every model hash before prepare.
        from insightface.model_zoo.person_package import load_person_package
        load_person_package(self.name,str(self.root))
        from insightface.app import PersonAnalysis
        # Existing, validated package: SDK refuses to replace/download invalid local packages.
        model=PersonAnalysis(name=self.name,root=str(self.root),providers=['CPUExecutionProvider'])
        model.prepare()
        self.model=model
        self.model_id=model.face_model_id

    def faces(self,image):
        with self.lock:
            self.load()
            began=time.perf_counter()
            persons=self.model.get(image)
            result=[]
            for p in persons:
                f=p.face
                if f is None: continue
                x1,y1,x2,y2=np.asarray(f.bbox,dtype=int)
                h,w=image.shape[:2]
                x1,x2=max(0,x1),min(w,x2);y1,y2=max(0,y1),min(h,y2)
                if x2<=x1 or y2<=y1: continue
                crop=image[y1:y2,x1:x2]
                reason=getattr(f,'_reason',None)
                if min(crop.shape[:2])<48: reason='face_too_small'
                elif cv2.Laplacian(crop,cv2.CV_64F).var()<25: reason='blurred_face'
                elif f.kps is None or len(f.kps)!=5: reason='landmarks_unavailable'
                else:
                    points=np.asarray(f.kps)
                    eye_width=abs(points[1,0]-points[0,0])
                    if eye_width<.16*(x2-x1) or abs(points[2,0]-(points[0,0]+points[1,0])/2)>.45*max(eye_width,1): reason='profile_or_landmark_ambiguity'
                result.append({'bbox':[int(x1),int(y1),int(x2),int(y2)],'crop':crop,
                               'feature':None if reason or f.embedding is None else feature(f.embedding),
                               'quality':reason or 'usable_with_unvalidated_thresholds'})
            return result,time.perf_counter()-began


def choose_match(scores, minimum=.55, margin=.08):
    """Uncalibrated cosine operating points; never identity probabilities."""
    if not scores: return 'unknown',None,'no_compatible_reference'
    scores=sorted(scores,key=lambda x:x['score'],reverse=True)
    best=scores[0]
    rivals=[s for s in scores[1:] if s['subject_id']!=best['subject_id']]
    if best['score']<minimum: return 'unknown',best,'below_uncalibrated_similarity_threshold'
    if rivals and best['score']-rivals[0]['score']<margin: return 'needs_review',best,'competing_subjects'
    return 'candidate',best,'human_confirmation_required'
