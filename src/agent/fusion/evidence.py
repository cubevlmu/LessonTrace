import hashlib
import json
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

State=Literal['supported','uncertain','contradicted','unknown']
EvidenceType=Literal['dataset_annotation','vlm_observation','human_visual_review','person_track','identity_binding','event_attribution','teacher_plan','teacher_report','teaching_activity','knowledge_rule','model_discovered_event','human_confirmed_event']


def version(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


class Evidence(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    evidence_id:str
    evidence_type:EvidenceType
    source_id:str
    subject_ref:str|None=None
    time_scope:list[float]|None=Field(None,min_length=2,max_length=2)
    content:str
    verification_status:State
    provenance:dict
    source_version:str


class Claim(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    fact_id:str
    statement:str
    scope:str
    evidence_refs:list[str]=Field(min_length=1)
    subject_ref:str|None=None
    time_scope:list[float]|None=Field(None,min_length=2,max_length=2)
    visual_status:State='unknown'
    track_status:State='unknown'
    identity_status:State='unknown'
    attribution_status:State='unknown'
    text_source_status:str='not_applicable'
    temporal_alignment_status:str='not_applicable'
    knowledge_rule_version:str
    final_claim_status:State
