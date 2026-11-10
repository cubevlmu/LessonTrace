import math
from pydantic import BaseModel,Field,model_validator


class ProcessingConfig(BaseModel):
    model_config={'extra':'forbid'}
    confidence:float=Field(default=.15,gt=0,le=1)
    imgsz:int=Field(default=960,ge=320,le=1920)
    device:str='0'
    stride:int=Field(default=1,ge=1,le=10)
    tracker:str='botsort'
    buffer_seconds:float=Field(default=1.,gt=0,le=10)
    max_frames:int|None=Field(default=None,ge=1)
    max_people:int=Field(default=300,ge=1,le=300)
    processing_roi:list[float]|None=Field(default=None,min_length=4,max_length=4)
    thermal_stop_c:int=Field(default=86,ge=65,le=90)
    thermal_pause_c:int=Field(default=80,ge=60,le=85)

    @model_validator(mode='after')
    def valid(self):
        if self.tracker not in {'botsort','bytetrack'}:raise ValueError('unsupported_tracker')
        if self.device!='cpu' and not self.device.isdigit():raise ValueError('device_must_be_cpu_or_gpu_index')
        if self.processing_roi:
            a,b,c,d=self.processing_roi
            if not all(math.isfinite(x) for x in self.processing_roi) or not 0<=a<c<=1 or not 0<=b<d<=1:raise ValueError('invalid_normalized_processing_roi')
        if self.thermal_pause_c>=self.thermal_stop_c:raise ValueError('invalid_thermal_limits')
        return self


class Detection(BaseModel):
    bbox:list[float]=Field(min_length=4,max_length=4)
    confidence:float=Field(ge=0,le=1,allow_inf_nan=False)
    frame_width:int=Field(gt=0)
    frame_height:int=Field(gt=0)
    class_id:int=0

    @model_validator(mode='after')
    def valid(self):
        x,y,X,Y=self.bbox
        if self.class_id!=0 or not all(math.isfinite(v) for v in self.bbox):raise ValueError('invalid_person_detection')
        if not 0<=x<X<=self.frame_width or not 0<=y<Y<=self.frame_height:raise ValueError('invalid_person_box')
        return self


def global_time(local,offset):
    if not all(math.isfinite(v) and v>=0 for v in [local,offset]):raise ValueError('invalid_timestamp')
    return offset+local
