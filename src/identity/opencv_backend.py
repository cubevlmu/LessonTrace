"""CPU YuNet/SFace implementation of the existing local face backend contract."""
import hashlib
import os
import threading
import time
from pathlib import Path
import cv2
import numpy as np
from .face_backend import feature


class OpenCVFaceBackend:
    def __init__(self,root=None,enabled=True):
        self.root=Path(root or os.getenv('INTICLASS_FACE_MODEL_DIR','data/weights/faces')).resolve()
        self.enabled=enabled;self.detector=None;self.recognizer=None;self.model_id=None;self.lock=threading.Lock()

    def paths(self):
        return self.root/'face_detection_yunet_2026may.onnx',self.root/'face_recognition_sface_2021dec.onnx'

    def probe(self):
        paths=self.paths()
        return {'backend':'OpenCV YuNet/SFace','opencv_version':cv2.__version__,'requested_provider':'CPU',
            'automatic_matching_enabled':self.enabled,'recognition_validation':'not_validated',
            'model_status':'present_not_validated' if all(p.is_file() for p in paths) else 'not_installed',
            'model_files':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths if p.is_file()],
            'network_downloads':False}

    def load(self):
        if not self.enabled:raise ValueError('automatic_identity_matching_disabled')
        if self.detector is not None:return
        detector,recognizer=self.paths()
        if not detector.is_file() or not recognizer.is_file():raise ValueError('yunet_sface_model_missing; install_explicitly')
        self.detector=cv2.FaceDetectorYN_create(str(detector),'',(320,320),.9,.3,5000,cv2.dnn.DNN_BACKEND_OPENCV,cv2.dnn.DNN_TARGET_CPU)
        self.recognizer=cv2.FaceRecognizerSF_create(str(recognizer),'',cv2.dnn.DNN_BACKEND_OPENCV,cv2.dnn.DNN_TARGET_CPU)
        self.model_id='sface-v1:'+hashlib.sha256(recognizer.read_bytes()).hexdigest()+':yunet:'+hashlib.sha256(detector.read_bytes()).hexdigest()

    def faces(self,image):
        with self.lock:
            self.load();began=time.perf_counter()
            if image is None or image.ndim!=3:raise ValueError('face_image_invalid')
            # Bound CPU detector pixels; preserve geometry back to the original frame.
            h,w=image.shape[:2];scale=min(1.,960/max(h,w))
            small=cv2.resize(image,(round(w*scale),round(h*scale))) if scale<1 else image
            self.detector.setInputSize((small.shape[1],small.shape[0]))
            _,rows=self.detector.detect(small);result=[]
            for face in rows if rows is not None else []:
                original=face.astype(np.float32).copy();original[:14]/=scale
                x,y,bw,bh=original[:4];x1=max(0,int(x));y1=max(0,int(y));x2=min(w,int(np.ceil(x+bw)));y2=min(h,int(np.ceil(y+bh)))
                if x2<=x1 or y2<=y1:continue
                crop=image[y1:y2,x1:x2];reason=None
                if min(crop.shape[:2])<48:reason='face_too_small'
                elif cv2.Laplacian(crop,cv2.CV_64F).var()<25:reason='blurred_face'
                points=original[4:14].reshape(5,2);eyes=abs(points[1,0]-points[0,0])
                if eyes<.16*bw or abs(points[2,0]-(points[0,0]+points[1,0])/2)>.45*max(eyes,1):reason=reason or 'profile_or_landmark_ambiguity'
                embedding=None
                if reason is None:
                    aligned=self.recognizer.alignCrop(image,original)
                    embedding=feature(self.recognizer.feature(aligned).reshape(-1))
                result.append({'bbox':[x1,y1,x2,y2],'crop':crop,'feature':embedding,'quality':reason or 'usable_with_unvalidated_thresholds'})
            return result,time.perf_counter()-began
