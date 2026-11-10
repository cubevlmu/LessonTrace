"""
核心数据契约与 Pydantic 模型定义
严格对齐 docs/agent.md 与 docs/code_artifact.md 中的 Schema 规范
"""

from enum import Enum
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

class ConflictType(str, Enum):
    NONE = "none"
    PEDAGOGICAL_CONTEXT_MISSING = "pedagogical_context_missing"  # 缺乏教学语境约束 (如未说明随堂练习却判低头)
    VISUAL_AMBIGUITY_CONFLICT = "visual_ambiguity_conflict"      # 相似动作视觉歧义 (如托腮被置信度中等误检为举手)
    SPATIO_TEMPORAL_CONFLICT = "spatio_temporal_conflict"        # 时空与实体混淆 (如 ID 跳变、坐标瞬移)
    CROSS_MODAL_CONTRADICTION = "cross_modal_contradiction"      # 音视频/教案与动作矛盾 (如全班抬头看演示某生持续低头)

class EvidenceLevel(str, Enum):
    E0 = "E0"  # 无可定位证据 / 幻觉拦截
    E1 = "E1"  # 单模型弱预测 (未经复核或低置信度)
    E2 = "E2"  # 原始音视频可定位外显事实 (带时间戳、bbox与局部抽帧)
    E3 = "E3"  # 跨模态互证教学解释 (视觉动作 + 讲稿/教学任务印证)
    E4 = "E4"  # 教师/人工复核黄金记录

class ClaimStatus(str, Enum):
    SUPPORTED = "supported"        # 证据充分支持
    UNCERTAIN = "uncertain"        # 存在部分疑点/置信度不足
    CONTRADICTED = "contradicted"  # 证据反驳初判 (已纠错)
    UNKNOWN = "unknown"            # 证据不足主动拒答 (ICAP理论边界)

class ClassroomEvent(BaseModel):
    """感知层输出并存入 SQLite 的结构化事件契约"""
    event_id: str = Field(..., description="事件唯一标识符")
    lesson_id: str = Field(..., description="课堂编号")
    t_start: float = Field(..., description="开始时间 (秒)")
    t_end: float = Field(..., description="结束时间 (秒)")
    source_id: str = Field(default="camera_01", description="视频采集源标识")
    modality: str = Field(default="visual", description="感知模态 (visual / audio)")
    track_id: str = Field(..., description="匿名学生跟踪编号 (例如 student_12)")
    bbox: List[int] = Field(..., description="目标边界框 [x1, y1, x2, y2]")
    observable_action: str = Field(..., description="外显动作类别")
    confidence: float = Field(..., description="模型检测置信度 (0.0 ~ 1.0)")
    raw_evidence_ref: str = Field(..., description="原始视频相对路径或切片索引")

class TeachingContext(BaseModel):
    """教学任务与语境约束契约 (来源于教案或讲稿 ASR 转写)"""
    context_id: str
    lesson_id: str
    t_start: float
    t_end: float
    teaching_stage: str = Field(..., description="教学环节 (导入 / 讲授 / 课堂练习 / 互动提问 / 总结)")
    teacher_speech: Optional[str] = Field(default="", description="教师同期说话转写内容")
    expected_student_action: Optional[str] = Field(
        default=None,
        description="本阶段预期学生活动 (如 focus / writing / reading / hand_raising)"
    )

class VerificationDetails(BaseModel):
    """取证细节与工具调用结果"""
    tool_called: str = Field(default="none", description="被调用的取证工具名")
    crop_image_path: Optional[str] = Field(default="", description="局部取证图像存储路径")
    critic_flag: str = Field(default="", description="冲突审查原因标记")
    notes: Optional[str] = Field(default="", description="取证补充说明")
    review_status: Literal["not_reviewed", "confirmed", "rejected"] = "not_reviewed"
    reviewer_kind: Optional[Literal["human", "visual_model"]] = None
    review_ref: str = Field(default="", description="独立复核记录引用；截图路径本身不代表复核结果")
    reviewed_action: str = Field(default="", description="复核所针对的原始动作标签")
    reviewed_event_id: str = Field(default="", description="复核绑定的事件，避免跨学生或时间窗误用记录")

    def has_action_review(self, action: str, event_id: str) -> bool:
        return bool(
            self.review_status in {"confirmed", "rejected"}
            and self.reviewer_kind and self.review_ref.strip()
            and self.reviewed_action == action
            and self.reviewed_event_id == event_id
        )

class ArbiterOutput(BaseModel):
    """最终裁决输出契约 (输出给报告生成与前端证据卡片)"""
    event_id: str
    lesson_id: str
    claim: str = Field(..., description="原子声明结论")
    status: ClaimStatus = Field(..., description="裁决状态")
    evidence_level: EvidenceLevel = Field(..., description="证据审计等级")
    support_refs: List[str] = Field(default_factory=list, description="支持该结论的事件或视频片段引用")
    conflict_type: ConflictType = Field(default=ConflictType.NONE, description="识别出的冲突类型")
    verification_details: Optional[VerificationDetails] = None

# Region annotations are deliberately separate from model detections.
class RegionAnnotation(BaseModel):
    event_id: str
    video_id: str
    action: str
    t_start: float
    t_end: float
    region_xywh: List[float]
    frame_width: float
    frame_height: float
    source_kind: Literal['dataset_annotation'] = 'dataset_annotation'
    region_kind: Literal['unverified_annotation_region'] = 'unverified_annotation_region'
    excel_path: str
    excel_sha256: str
    excel_row: int
    temporal_rows: List[int] = Field(default_factory=list)
    temporal_path: str
    temporal_sha256: str
    review_todo: bool = False


class HumanRegionReview(BaseModel):
    decision: Literal['confirmed', 'rejected', 'unable_to_judge']
    box_kind: Literal['single_person', 'multi_person_region', 'unable_to_judge']
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(default='', max_length=4000)
    clip_id: str
    global_time: float = Field(allow_inf_nan=False)

class VisualReviewRequest(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    clip_id: str
    frame_count: int = Field(default=8, ge=6, le=12)


class VisualObservation(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    action: str = Field(min_length=1, max_length=1000)
    evidence_refs: List[str] = Field(min_length=1)


class VisualObservations(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    observed_actions: List[VisualObservation]
    limitations: List[str]


class VisualCounterEvidence(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    target_claim: str
    fact: str = Field(min_length=1, max_length=1000)
    kind: Literal['direct_incompatible_fact', 'absence_in_samples', 'ambiguous']
    evidence_refs: List[str] = Field(min_length=1)
    subject_scope: Literal['entire_region', 'one_unverified_person', 'unknown']
    temporal_scope: Literal['sampled_instants', 'entire_interval']


class SupportAnchor(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True,'allow_inf_nan':False}
    frame_id: str
    subject_ref: str = Field(min_length=1,max_length=200)
    box_xyxy_normalized: List[float] = Field(min_length=4,max_length=4)


class VisualSupportFact(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True,'allow_inf_nan':False}
    observed_action: str = Field(min_length=1,max_length=1000)
    subject_scope: Literal['one_unverified_person','multiple_unverified_people','region_event','unknown']
    subject_ref: str = Field(min_length=1,max_length=200)
    temporal_scope: Literal['sampled_instants','local_sequence','entire_interval']
    global_time_window: List[float] = Field(min_length=2,max_length=2)
    evidence_refs: List[str] = Field(min_length=1)
    relation_to_target: Literal['supports','unrelated','contradicts','uncertain']
    view: Literal['roi','full','unknown']
    spatial_anchors: List[SupportAnchor] = Field(default_factory=list)
    context_refs: List[str] = Field(default_factory=list)


class VisualJudgment(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    decision: Literal['confirmed', 'rejected', 'uncertain']
    reason: str = Field(min_length=1, max_length=4000)
    evidence_refs: List[str] = Field(min_length=1)
    limitations: List[str]
    counter_evidence: List[VisualCounterEvidence] = Field(default_factory=list)
    support_type: Literal['direct_observation', 'temporal_change', 'unclear'] = 'unclear'
    target_claim: Optional[str] = None
    support_facts: List[VisualSupportFact] = Field(default_factory=list)


class AlignedVisualJudgment(VisualJudgment):
    """Required for new model requests; old responses still use VisualJudgment."""
    target_claim: str = Field(min_length=1,max_length=100)
    support_facts: List[VisualSupportFact]


class VisualReviewResult(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    review_id: str
    event_id: str
    clip_id: str
    reviewer_kind: Literal['visual_model'] = 'visual_model'
    source_kind: Literal['vlm_visual_review'] = 'vlm_visual_review'
    model_id: str
    prompt_version: str
    target_claim: str
    observed_actions: List[VisualObservation] = Field(default_factory=list)
    decision: Literal['confirmed', 'rejected', 'uncertain', 'error']
    reason: str
    evidence_refs: List[str] = Field(default_factory=list)
    sampled_global_times: List[float] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    availability: Literal['available', 'unavailable', 'not_checked']
    raw_model_decision: Optional[Literal['confirmed', 'rejected', 'uncertain']] = None
    final_decision: Optional[Literal['confirmed', 'rejected', 'uncertain', 'error']] = None
    decision_source: str = 'legacy_visual_review'
    safety_override: bool = False
    override_reason: List[str] = Field(default_factory=list)
    evidence_scope: str = 'region_action_exists_during_interval_no_person_identity'
    sampling_strategy: Dict[str, Any] = Field(default_factory=dict)
    temporal_coverage: Dict[str, Any] = Field(default_factory=dict)
    additional_evidence_requested: bool = False
    semantic_guardrail_violations: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_sufficiency: List[Dict[str, Any]] = Field(default_factory=list)
    support_facts: List[VisualSupportFact] = Field(default_factory=list)
    alignment_checks: Dict[str, Any] = Field(default_factory=dict)


ReviewSource = Literal['dataset_annotation','model_prediction','human_review','vlm_visual_review']


class EvidenceQuery(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    video_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,100}$')
    start: float = Field(ge=0,allow_inf_nan=False)
    end: float = Field(ge=0,allow_inf_nan=False)
    actions: List[str] = Field(default_factory=list,max_length=13)
    source_kinds: List[ReviewSource] = Field(default_factory=list)
    human_status: Optional[Literal['confirmed','rejected','unable_to_judge','not_independently_reviewed']] = None
    vlm_status: Optional[Literal['confirmed','rejected','uncertain','error','unreviewed']] = None
    intent: Literal['overview','action_check','review_todo','interpretation','unsupported'] = 'overview'
    limit: int = Field(default=20,ge=1,le=30)

    @model_validator(mode='after')
    def bounded_window(self):
        if self.start>self.end or self.end-self.start>3600:raise ValueError('invalid_or_excessive_query_window')
        return self


class ReviewQuestionRequest(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    video_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,100}$')
    question: str = Field(min_length=1,max_length=1000)
    start: Optional[float] = Field(default=None,ge=0,allow_inf_nan=False)
    end: Optional[float] = Field(default=None,ge=0,allow_inf_nan=False)
    actions: List[str] = Field(default_factory=list,max_length=13)
    source_kinds: List[ReviewSource] = Field(default_factory=list)
    human_status: Optional[Literal['confirmed','rejected','unable_to_judge','not_independently_reviewed']] = None
    vlm_status: Optional[Literal['confirmed','rejected','uncertain','error','unreviewed']] = None
    limit: int = Field(default=20,ge=1,le=30)
    allow_verification: bool = True
    max_auto_reviews: int = Field(default=2,ge=0,le=2)
    max_model_calls: int = Field(default=8,ge=0,le=8)
    budget_seconds: float = Field(default=120,ge=1,le=240,allow_inf_nan=False)


class AtomicClaim(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    claim_id: str
    claim_type: Literal['dataset_claim','observable_claim','interpretation_claim']
    event_id: Optional[str] = None
    subject_scope: str = 'unverified_region_no_student_identity'
    action: str
    global_time_window: List[float] = Field(min_length=2,max_length=2)
    source_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    verification_status: ClaimStatus = ClaimStatus.UNKNOWN
    missing_evidence: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    conclusion_source: str = 'not_adjudicated'
    adjudication_reason: str = ''
    eligible_for_report: bool = False
    basis: Dict[str,Any] = Field(default_factory=dict)

    @field_validator('global_time_window')
    @classmethod
    def valid_claim_window(cls,value):
        import math
        if not all(math.isfinite(t) and t>=0 for t in value) or value[0]>value[1]:raise ValueError('invalid_claim_window')
        return value


class ReviewRisk(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    event_id: Optional[str] = None
    risk_type: Literal['unverified_dataset_annotation','human_model_conflict','model_round_disagreement',
                       'region_subject_ambiguity','dynamic_temporal_insufficiency','cognitive_overreach',
                       'unreachable_evidence','missing_video_coverage','unsupported_confirmation']
    reason: str
    resolvable_by_visual_review: bool = False
    source_refs: List[str] = Field(default_factory=list)
