"""Independent human annotation contracts; never reuse model/human-review tables."""
import math
from typing import Literal
from pydantic import BaseModel,Field,model_validator


class StrictGold(BaseModel):
    model_config={'extra':'forbid','strict':True,'str_strip_whitespace':True}


class GoldLogin(StrictGold):
    token:str=Field(min_length=20,max_length=200)


class VisibleAction(StrictGold):
    subject:str=Field(min_length=1,max_length=300)
    action:str=Field(min_length=1,max_length=500)
    evidence_time:float=Field(ge=0,allow_inf_nan=False)


class GoldBlindReview(StrictGold):
    watched_full_window:bool
    observations:list[VisibleAction]=Field(min_length=1,max_length=30)
    people_scope:Literal['single','multiple','unknown']
    clarity:Literal['clear','ambiguous','unknown']
    occlusion:Literal['none','partial','severe','unknown']
    note:str=Field(default='',max_length=3000)


class GoldInspection(StrictGold):
    """Explicit later viewing attestation, separate from the initial blind stage."""
    watched_full_window:Literal[True]


class TargetInterval(StrictGold):
    subject:str=Field(min_length=1,max_length=300)
    start:float=Field(ge=0,allow_inf_nan=False)
    end:float=Field(ge=0,allow_inf_nan=False)

    @model_validator(mode='after')
    def ordered(self):
        if self.start>self.end:raise ValueError('invalid_target_interval')
        return self


class GoldTargetReview(StrictGold):
    decision:Literal['confirmed','rejected','uncertain']
    enough_evidence:bool
    watched_full_window:bool
    region_visibility:Literal['fully_visible','partly_occluded','unknown']
    box_accuracy:Literal['adequate','too_large','misses_target','unknown']
    timing_known:bool
    target_intervals:list[TargetInterval]=Field(default_factory=list,max_length=30)
    events_exhaustive:bool=False
    reason:str=Field(min_length=1,max_length=3000)

    @model_validator(mode='after')
    def sufficient(self):
        if self.decision!='uncertain' and not self.enough_evidence:raise ValueError('definite_decision_requires_sufficient_evidence')
        if self.decision=='rejected' and (not self.watched_full_window or self.region_visibility!='fully_visible'):
            raise ValueError('rejection_requires_full_inspection_and_visible_region')
        if self.decision=='confirmed' and self.timing_known and not self.target_intervals:raise ValueError('known_timing_requires_intervals')
        if self.decision!='confirmed' and self.target_intervals:raise ValueError('intervals_require_confirmed_target')
        if self.events_exhaustive and not self.watched_full_window:raise ValueError('exhaustive_requires_full_inspection')
        return self


class ClaimRating(StrictGold):
    claim_id:str
    correctness:Literal['correct','incorrect','partially_correct','unable_to_judge']
    unsupported:Literal['yes','no','unknown']
    evidence_consistency:Literal['matches','conflicts','unknown']
    reference_accessible:Literal['yes','no','unknown']
    timestamp_correct:Literal['yes','no','unknown']
    actual_time:float|None=Field(default=None,ge=0,allow_inf_nan=False)
    note:str=Field(default='',max_length=2000)


class GoldClaimReviews(StrictGold):
    ratings:list[ClaimRating]=Field(min_length=1,max_length=50)


class GoldResolution(StrictGold):
    review:GoldTargetReview
    method:Literal['agreement_confirmation','joint_discussion','third_person']
    rationale:str=Field(min_length=1,max_length=3000)
    recording_source_group:str|None=Field(default=None,max_length=200)
    grouping_evidence:str=Field(default='',max_length=1000)


class GoldClaimResolution(StrictGold):
    ratings:list[ClaimRating]=Field(min_length=1,max_length=50)
    method:Literal['agreement_confirmation','joint_discussion','third_person']
    rationale:str=Field(min_length=1,max_length=3000)
