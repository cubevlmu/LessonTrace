from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

TimeReference=Literal['clip_local','dataset_global','lesson_elapsed','wall_clock','unknown']
SourceType=Literal['course_info','learning_goals','teacher_plan','activity_schedule','teacher_observation','assignment','student_work','graded_work','assessment_result','learning_material','classroom_memory','other_material','rubric','teacher_note']


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class MaterialScope(Input):
    run_id:str|None=None
    classroom_id:str|None=None
    subject_id:str|None=None
    track_id:int|None=Field(None,ge=1)


class DocumentInput(Input):
    title:str=Field(min_length=1,max_length=200)
    source_type:SourceType
    text:str=Field(min_length=1,max_length=500000)
    is_example:bool=False
    scope:MaterialScope|None=None


class ActivityInput(Input):
    source_passage_id:str
    activity_type:Literal['group_discussion','independent_practice','lecture','phone_quiz','assignment','other']
    description:str=Field(min_length=1,max_length=2000)
    start:float|str|None=None
    end:float|str|None=None
    time_reference:TimeReference='unknown'
    status:Literal['planned','teacher_reported_actual']='planned'
    action_claims:dict[str,Literal['expected','reported_visible','reported_absent']]=Field(default_factory=dict)
    supersedes_activity:str|None=None


class AlignmentInput(Input):
    document_id:str
    lesson_id:str=Field(min_length=1,max_length=120)
    clip_id:str
    time_reference:TimeReference
    text_anchor:float|str
    video_global_anchor:float
    basis:str=Field(min_length=5,max_length=2000)
    confirmation:Literal[True]
    supersedes:str|None=None


class Reason(Input):
    reason:str=Field(min_length=5,max_length=2000)


class Share(Input):
    actor:str=Field(min_length=1,max_length=120)
    allow:bool


class Search(Input):
    query:str=Field('',max_length=300)
    document_id:str|None=None
    source_type:SourceType|None=None
    lesson_id:str|None=None
    action:str|None=None
    run_id:str|None=None
    start:float|None=None
    end:float|None=None
    limit:int=Field(30,ge=1,le=100)


class Question(Input):
    lesson_id:str|None=None
    document_ids:list[str]=Field(default_factory=list,max_length=10)
    run_id:str
    question:str=Field(min_length=1,max_length=1000)
    intent:Literal['arrangement','relation','person']|None=None
    track_id:int|None=Field(None,ge=1)
    start:float|None=None
    end:float|None=None
    include_identity:bool=False
    roster_subject_id:str|None=None
    material_mode:Literal['linked','selected','none']='linked'
    use_classroom_memory:bool=True
