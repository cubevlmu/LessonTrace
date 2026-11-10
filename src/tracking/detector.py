from pathlib import Path
import os
from .schemas import Detection
from .video import sha256


def parse_detections(boxes,width,height):
    return [Detection(bbox=[float(v) for v in row[:4]],confidence=float(row[4]),class_id=int(row[5]),frame_width=width,frame_height=height).model_dump() for row in boxes]


class PersonDetector:
    def __new__(cls,weights,config):
        if Path(weights).suffix.lower()=='.onnx':
            from .onnx_detector import OnnxPersonDetector
            return OnnxPersonDetector(weights,config)
        return super().__new__(cls)
    def __init__(self,weights,config):
        (Path(weights).resolve().parent/'ultralytics_config').mkdir(parents=True,exist_ok=True)
        os.environ.setdefault('YOLO_CONFIG_DIR',str(Path(weights).resolve().parent/'ultralytics_config'))
        os.environ['YOLO_AUTOINSTALL']='false'
        from ultralytics import YOLO,__version__,settings
        settings.update({'sync':False})
        import torch
        path=Path(weights)
        if not path.is_file():raise ValueError('person_weights_missing; download official yolo11s.pt explicitly')
        self.model=YOLO(str(path));self.config=config
        names=self.model.names
        if self.model.task!='detect' or len(names)!=80 or names[0]!='person':raise ValueError('detector_must_be_COCO_80_class_person_model_not_behavior_weights')
        if config.device!='cpu' and not torch.cuda.is_available():raise ValueError('requested_gpu_unavailable_no_silent_cpu_fallback')
        self.metadata={'detector_name':path.stem+' COCO person','detector_version':__version__,
            'weights_name':path.name,'weights_sha256':sha256(path),'torch_version':torch.__version__,
            'requested_device':config.device,'gpu_name':torch.cuda.get_device_name(int(config.device)) if config.device!='cpu' else None,
            'confidence_semantics':'detector score, not identity probability'}

    def predict(self,image):
        result=self.model.predict(image,classes=[0],conf=self.config.confidence,imgsz=self.config.imgsz,
                                  device=self.config.device,verbose=False,max_det=self.config.max_people)[0]
        if self.config.processing_roi:
            h,w=image.shape[:2];a,b,c,d=self.config.processing_roi
            if not 0<=a<c<=1 or not 0<=b<d<=1:raise ValueError('invalid_processing_roi')
            keep=[i for i,box in enumerate(result.boxes.xyxy.cpu().tolist()) if a*w<=(box[0]+box[2])/2<=c*w and b*h<=(box[1]+box[3])/2<=d*h]
            result.boxes=result.boxes[keep]
        det=parse_detections(result.boxes.data.cpu().tolist(),image.shape[1],image.shape[0])
        self.metadata['actual_parameter_device']=str(next(self.model.model.parameters()).device)
        return det,result.boxes.cpu().numpy()
