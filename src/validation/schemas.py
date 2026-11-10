from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Scoped(Input):
    run_id:str
    track_id:int=Field(ge=1)
    start:float=Field(ge=0)
    end:float=Field(ge=0)
    @model_validator(mode='after')
    def window(self):
        if self.end<self.start:raise ValueError('end_before_start')
        return self


class TrackReview(Scoped):
    decision:Literal['continuous','id_switch','fragmented','uncertain']
    problem_time:float|None=None
    evidence_frames:list[int]=Field(min_length=1,max_length=30)
    basis:str=Field(min_length=5,max_length=3000)
    human_review_attested:Literal[True]
    @model_validator(mode='after')
    def problem(self):
        if self.problem_time is not None and not self.start<=self.problem_time<=self.end:raise ValueError('problem_time_outside_review')
        if self.decision=='id_switch' and self.problem_time is None:raise ValueError('id_switch_requires_observed_problem_time')
        return self


Verdict=Literal['yes','no','uncertain']
class QAScore(Input):
    case_id:str=Field(min_length=1,max_length=60)
    report_id:str
    answers_the_question:Verdict
    key_facts_correct:Verdict
    key_facts_missing:str=Field(min_length=1,max_length=3000)
    irrelevant_information:Literal['none','minor','major','uncertain']
    source_support_valid:Verdict
    subject_scope_correct:Verdict
    time_scope_correct:Verdict
    uncertainty_appropriate:Verdict
    inappropriate_certainty:Verdict
    overall_usefulness:int=Field(ge=1,le=5)
    notes:str=Field(min_length=5,max_length=4000)
    human_review_attested:Literal[True]


class ActorAssessment(Input):
    candidate_id:str
    start:float
    end:float
    decision:Literal['confirmed','rejected','ambiguous','uncertain']
    behavior_visible:Verdict
    involves_multiple_people:Verdict
    evidence_time_correct:Verdict
    attribution_sufficient:Verdict
    evidence_frames:list[int]=Field(min_length=1,max_length=30)
    observable_action:str=Field(min_length=3,max_length=1000)
    basis:str=Field(min_length=5,max_length=3000)
    same_person_in_reviewed_interval:bool=False
    continuity_risks_addressed:bool=False
    human_review_attested:Literal[True]
    evaluated_binding_id:str|None=None
    error_in_confirmed_binding:Verdict='uncertain'


class IdentityAssessment(Input):
    binding_id:str
    judgment:Literal['correct','wrong','uncertain']
    basis:str=Field(min_length=5,max_length=3000)
    human_review_attested:Literal[True]


class ChainRequest(Scoped):
    subject_id:str|None=None
    action:str|None=None
    event_binding_id:str|None=None
    activity_id:str|None=None
    require_context:bool=False


class QuestionApproval(Input):
    case_id:str
    approved:bool
    expected_evidence_scope:str=Field(min_length=5,max_length=4000)
    notes:str=Field(min_length=5,max_length=3000)
    human_review_attested:Literal[True]
