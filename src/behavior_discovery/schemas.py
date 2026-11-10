from typing import Literal,Annotated
from pydantic import BaseModel,ConfigDict,Field,model_validator
from src.attribution.schemas import ActorJudgment
from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS

VERSION='discovery-v1'
PROMPT_VERSION='blind-track-discovery-v1.5-visible-posture-and-partial-target'
CLASSIFICATION_PROMPT_VERSION='discovery-classification-v1.7-bounded'


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Config(Input):
    processing_profile:Literal['standard','efficient']='standard'
    analysis_mode:Literal['sampled','full_timeline']='sampled'
    timeline_window_seconds:float=Field(2,ge=.5,le=4)
    window_seconds:float=Field(6,ge=4,le=8)
    overlap:float=Field(.5,ge=0,le=.75)
    frames:int=Field(4,ge=2,le=4)
    input_long_edge:int=Field(512,ge=320,le=512)
    close_interval:float=Field(.4,ge=.3,le=.5)
    max_tracks:int=Field(3,ge=1,le=50)
    max_windows:int=Field(12,ge=1,le=500)
    max_calls:int=Field(24,ge=2,le=1000)
    timeout:float=Field(90,ge=5,le=600)
    budget_seconds:float=Field(600,ge=10,le=3600)
    imgsz:int=Field(640,ge=320,le=1280)
    confidence:float=Field(.15,gt=0,le=1)
    stride:int=Field(2,ge=1,le=10)
    device:str='0'
    max_people:int=Field(100,ge=1,le=300)
    processing_roi:list[float]|None=Field(None,min_length=4,max_length=4)
    track_ids:list[Annotated[int,Field(ge=1)]]=Field(default_factory=list,max_length=50)
    prefer_clear_targets:bool=False
    @model_validator(mode='after')
    def validate_options(self):
        if self.device!='cpu' and not self.device.isdigit():raise ValueError('device_must_be_cpu_or_gpu_index')
        if self.processing_roi:
            a,b,c,d=self.processing_roi
            if not 0<=a<c<=1 or not 0<=b<d<=1:raise ValueError('invalid_normalized_processing_roi')
        return self


class Start(Input):
    video_id:str
    config:Config=Field(default_factory=Config)
    force_new:bool=False
    reuse_tracking_run_id:str|None=None
    @model_validator(mode='after')
    def target_namespace(self):
        if self.config.track_ids and not self.reuse_tracking_run_id:raise ValueError('existing_tracking_run_required_for_track_selection')
        return self


class Control(Input):
    action:Literal['pause','resume','cancel','retry']
    additional_calls:int=Field(0,ge=0,le=500)


class ReportOptions(Input):
    document_ids:list[str]=Field(default_factory=list,max_length=10)


class DiscoveryJudgment(ActorJudgment):
    target_claim:Literal['stand','walk','playphone','sleep','readbook','homework','chat','eatordrink','usecomputer','discuss','hubbub','call','chase','other_observable_action','unknown']


class Classification(Input):
    judgments:list[DiscoveryJudgment]=Field(max_length=3)
    limitations:list[str]=Field(max_length=3)
    @model_validator(mode='after')
    def categories(self):
        for j in self.judgments:
            if j.target_claim not in {*ACTION_REQUIREMENTS,'unknown','other_observable_action'}:raise ValueError('unknown_discovery_category')
        return self
