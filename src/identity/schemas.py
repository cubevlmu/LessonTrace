from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)


class Enrollment(Input):
    display_name: str = Field('',max_length=120)
    consent_record: str = Field(min_length=5,max_length=4000)
    expires_at: str
    authorized: Literal[True]
    purposes: list[Literal['manual_identity','reference_storage','video_reference_capture','face_matching']]


class VideoAuthorization(Input):
    subject_id: str
    run_id: str
    consent_record: str = Field(min_length=5,max_length=4000)
    expires_at: str
    all_visible_people_authorized: Literal[True]


class Capture(Input):
    subject_id: str
    authorization_id: str
    run_id: str
    track_id: int = Field(ge=1)
    frame_index: int = Field(ge=0)
    target_person_independently_identified: Literal[True]


class Binding(Input):
    subject_id: str
    run_id: str
    track_id: int = Field(ge=1)
    start: float
    end: float
    decision: Literal['human_confirmed','rejected','unknown']
    source_kind: Literal['participant_attestation','in_person_confirmation','authorized_roster_crosscheck','authorized_reference_comparison','other_independent_source']
    evidence_ref: str = Field(min_length=5,max_length=2000)
    reason: str = Field(min_length=5,max_length=2000)
    interval_identity_reviewed: Literal[True]
    cross_clip_evidence: str = Field('',max_length=2000)


class Change(Input):
    reason: str = Field(min_length=5,max_length=2000)


class Split(Change):
    split_at: float


class Match(Input):
    authorization_id: str
    run_id: str
    track_id: int = Field(ge=1)
    frame_indices: list[int] = Field(min_length=1,max_length=6)
