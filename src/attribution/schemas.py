from typing import Literal,Annotated
from pydantic import BaseModel,ConfigDict,Field
from src.schema import AlignedVisualJudgment,VisualSupportFact,VisualObservation,VisualObservations


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Generate(Input):
    event_id:str
    run_id:str


class Verify(Input):
    candidate_id:str
    start:float
    end:float


class Human(Verify):
    decision:Literal['human_confirmed','rejected','unknown']
    evidence_frames:list[int]=Field(min_length=1,max_length=30)
    observable_action:str=Field(min_length=3,max_length=1000)
    basis:str=Field(min_length=5,max_length=2000)
    interval_inspected:Literal[True]
    same_person_in_reviewed_interval:Literal[True]
    continuity_risks_addressed:bool=False


class Revoke(Input):
    reason:str=Field(min_length=5,max_length=2000)


class ActorFact(VisualSupportFact):
    actor_role:Literal['target','other','multiple','unknown']
    observed_action:str=Field(min_length=1,max_length=240)


class ActorObservation(VisualObservation):
    action:str=Field(min_length=1,max_length=180)


class ActorObservations(VisualObservations):
    observed_actions:list[ActorObservation]=Field(max_length=2)
    limitations:list[Annotated[str,Field(max_length=180)]]=Field(max_length=2)


class ActorJudgment(AlignedVisualJudgment):
    reason:str=Field(min_length=1,max_length=360)
    support_facts:list[ActorFact]=Field(max_length=2)
    limitations:list[Annotated[str,Field(max_length=180)]]=Field(max_length=3)
    executor_relation:Literal['target','other','multiple','unknown','action_not_supported']
    target_ref:Literal['target']
