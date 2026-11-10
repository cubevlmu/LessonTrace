"""COCO person ONNX Runtime CPU detector, same tracker-facing array contract."""
import ast
from pathlib import Path
import cv2
import numpy as np
from .video import sha256
from .schemas import Detection

class Boxes:
    def __init__(self,rows):
        self.data=np.asarray(rows,dtype=np.float32).reshape(-1,6)
        self.xyxy=self.data[:,:4];self.conf=self.data[:,4];self.cls=self.data[:,5]
        self.xywh=self.xyxy.copy()
        self.xywh[:,0]=(self.xyxy[:,0]+self.xyxy[:,2])/2
        self.xywh[:,1]=(self.xyxy[:,1]+self.xyxy[:,3])/2
        self.xywh[:,2:]=self.xyxy[:,2:]-self.xyxy[:,:2]
    def __len__(self):return len(self.data)

class OnnxPersonDetector:
    def __init__(self,weights,config):
        import onnxruntime as ort
        if config.device!='cpu':raise ValueError('onnx_detector_cpu_device_required')
        path=Path(weights)
        if not path.is_file():raise ValueError('person_onnx_weights_missing')
        opts=ort.SessionOptions();opts.intra_op_num_threads=4;opts.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(path),sess_options=opts,providers=['CPUExecutionProvider'])
        meta=self.session.get_modelmeta().custom_metadata_map
        try:names=ast.literal_eval(meta.get('names','{}'))
        except (ValueError,SyntaxError):raise ValueError('detector_coco_metadata_invalid')
        if len(names)!=80 or names[0]!='person':raise ValueError('detector_must_be_COCO_80_class_person_model_not_behavior_weights')
        self.input=self.session.get_inputs()[0];shape=self.input.shape
        self.size=(shape[3],shape[2]) if isinstance(shape[2],int) and isinstance(shape[3],int) else (config.imgsz,config.imgsz)
        self.config=config
        self.metadata={'detector_name':path.stem+' COCO person','detector_version':ort.__version__,'weights_name':path.name,'weights_sha256':sha256(path),
            'requested_device':'cpu','actual_parameter_device':'cpu:onnxruntime','providers':self.session.get_providers(),'input_size':self.size,'confidence_semantics':'detector score, not identity probability'}
    def predict(self,image):
        h,w=image.shape[:2];tw,th=self.size;scale=min(tw/w,th/h)
        resized=cv2.resize(image,(round(w*scale),round(h*scale)))
        x=(tw-resized.shape[1])//2;y=(th-resized.shape[0])//2
        canvas=np.full((th,tw,3),114,np.uint8);canvas[y:y+resized.shape[0],x:x+resized.shape[1]]=resized
        tensor=np.ascontiguousarray(canvas[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255.
        output=self.session.run(None,{self.input.name:tensor})[0]
        if output.ndim!=3 or output.shape[1]!=84:raise ValueError('unexpected_yolo_onnx_output_contract')
        raw=output[0].T;scores=raw[:,4:];mask=(scores.argmax(axis=1)==0)&(scores[:,0]>=self.config.confidence)
        raw=raw[mask];conf=scores[mask,0];boxes=raw[:,:4].copy()
        boxes[:,0]=(boxes[:,0]-x)/scale;boxes[:,1]=(boxes[:,1]-y)/scale;boxes[:,2:]/=scale
        boxes[:,:2]-=boxes[:,2:]/2
        keep=cv2.dnn.NMSBoxes(boxes.tolist(),conf.tolist(),self.config.confidence,.7)
        rows=[]
        for i in np.asarray(keep).reshape(-1)[:self.config.max_people]:
            a,b,bw,bh=boxes[int(i)];a,b,X,Y=max(0,float(a)),max(0,float(b)),min(w,float(a+bw)),min(h,float(b+bh))
            if a>=X or b>=Y:continue
            if self.config.processing_roi:
                p,q,r,s=self.config.processing_roi
                if not p*w<=(a+X)/2<=r*w or not q*h<=(b+Y)/2<=s*h:continue
            rows.append([a,b,X,Y,float(conf[int(i)]),0.])
        result=Boxes(rows)
        det=[Detection(bbox=r[:4],confidence=r[4],class_id=0,frame_width=w,frame_height=h).model_dump() for r in rows]
        return det,result
