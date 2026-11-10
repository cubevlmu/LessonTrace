"""裁决模型候选与独立复核；置信度、任务预期和裁剪成功均不构成动作证实。"""

from pathlib import Path
from typing import Optional
from src.schema import (
    ClassroomEvent, TeachingContext, ConflictType, EvidenceLevel,
    ClaimStatus, VerificationDetails, ArbiterOutput,
)


class ArbiterAgent:
    def arbitrate_fusion(self,candidate,canonical):
        from src.agent.fusion.reasoning import adjudicate
        return adjudicate(candidate,canonical)

    def arbitrate_context(self,claim,risks):
        from src.context.analysis import arbitrate
        return arbitrate(claim,risks)

    def arbitrate_atomic(self,claim,bundle,risks,registry,query):
        from src.agent.review_claims import arbitrate_claim
        return arbitrate_claim(claim,bundle,risks,registry,query)

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def arbitrate(self, event: ClassroomEvent, context: Optional[TeachingContext],
                  conflict_type: ConflictType,
                  verification: Optional[VerificationDetails]) -> ArbiterOutput:
        action = event.observable_action
        reviewed = bool(verification and verification.has_action_review(action, event.event_id))
        refs = [event.event_id]
        status = ClaimStatus.UNCERTAIN
        level = EvidenceLevel.E1
        claim = (
            f"模型在 {event.t_start}s~{event.t_end}s 提示候选动作 [{action}] "
            f"(置信度 {event.confidence:.2f})；尚无独立复核，不能视为已证实事实。"
        )

        if event.modality == "simulated":
            status, level, refs = ClaimStatus.UNKNOWN, EvidenceLevel.E0, []
            claim = "模拟事件仅用于流程演示，不构成真实课堂证据。"
        elif not event.raw_evidence_ref or not Path(event.raw_evidence_ref).is_file():
            status, level, refs = ClaimStatus.UNKNOWN, EvidenceLevel.E0, []
            claim = f"候选动作 [{action}] 的原始视频不可访问，无法核验，主动判定为未知。"
        elif conflict_type == ConflictType.SPATIO_TEMPORAL_CONFLICT:
            claim = "目标时空归属存在疑点，尚未完成轨迹复核，不能归因给原学生。"
        elif reviewed:
            refs.append(verification.review_ref)
            level = EvidenceLevel.E4 if verification.reviewer_kind == "human" else EvidenceLevel.E2
            if verification.review_status == "rejected":
                status = ClaimStatus.CONTRADICTED
                claim = f"独立复核记录驳回候选动作 [{action}]；未据此推断其他动作或心理状态。"
            else:
                status = ClaimStatus.SUPPORTED
                claim = f"独立复核记录支持 {event.t_start}s~{event.t_end}s 的外显动作 [{action}]。"

        # 教学语境只能约束解释，不能独立验证视觉预测，更不能证明心理状态。
        if level != EvidenceLevel.E0 and conflict_type == ConflictType.PEDAGOGICAL_CONTEXT_MISSING:
            if status != ClaimStatus.CONTRADICTED:
                status = ClaimStatus.UNKNOWN
                claim += " 缺少教学任务语境，认知状态无法推断，主动判定为未知。"
        elif level != EvidenceLevel.E0 and conflict_type == ConflictType.CROSS_MODAL_CONTRADICTION:
            if status != ClaimStatus.CONTRADICTED:
                status = ClaimStatus.UNCERTAIN
                claim += " 候选动作与任务预期存在差异，需结合语境复核，不能直接认定异常。"
            if context:
                refs.append(context.context_id)

        return ArbiterOutput(
            event_id=event.event_id, lesson_id=event.lesson_id, claim=claim,
            status=status, evidence_level=level, support_refs=refs,
            conflict_type=conflict_type, verification_details=verification,
        )
