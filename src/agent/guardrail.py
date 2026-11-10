"""
Guardrail 智能体模块：后端硬编码防幻觉护栏与证据等级兜底校验
"""

from src.schema import ArbiterOutput, ClaimStatus, EvidenceLevel

# 禁止在 E2 及以下低证据等级下直接宣称的心理状态关键词
COGNITIVE_OVERREACH_KEYWORDS = ["走神", "不专注", "分心", "心不在焉", "学不会", "完全掌握", "积极投入"]

class GuardrailAgent:
    def validate_context(self,claim,registry):
        from src.context.analysis import guard
        return guard(claim,registry)

    def validate_atomic(self,claim,registry):
        from src.agent.review_claims import guard_atomic
        return guard_atomic(claim,registry)

    def validate(self, arbiter_output: ArbiterOutput) -> ArbiterOutput:
        """
        后置护栏拦截与兜底修正
        """
        res = arbiter_output.model_copy(deep=True)

        # 截图留存和模型置信度不能充当独立复核记录。
        review = res.verification_details
        valid_review = bool(
            review and review.reviewed_action and review.has_action_review(review.reviewed_action, res.event_id)
            and review.review_ref in res.support_refs
        )
        if res.evidence_level in {EvidenceLevel.E2, EvidenceLevel.E3, EvidenceLevel.E4} and not valid_review:
            res.evidence_level = EvidenceLevel.E1
            res.status = ClaimStatus.UNCERTAIN
            res.claim = "[护栏拦截] 缺少带引用的独立动作复核，原声明未经证实。"
        if res.evidence_level == EvidenceLevel.E4 and review.reviewer_kind != "human":
            res.evidence_level = EvidenceLevel.E2
        # 当前复核契约只记录视觉动作复核，尚不能证明跨模态解释。
        if res.evidence_level == EvidenceLevel.E3:
            res.evidence_level = EvidenceLevel.E2
            res.status = ClaimStatus.UNCERTAIN
            res.claim = "[护栏拦截] 仅有动作复核记录，缺少独立跨模态核验，教学解释保持存疑。"
        if res.status == ClaimStatus.CONTRADICTED and not (valid_review and review.review_status == "rejected"):
            res.status = ClaimStatus.UNCERTAIN
            res.evidence_level = EvidenceLevel.E1
            res.claim = "[护栏拦截] 缺少明确反证，不能驳回初判。"
        if res.status == ClaimStatus.SUPPORTED and res.evidence_level in {EvidenceLevel.E0, EvidenceLevel.E1}:
            res.status = ClaimStatus.UNCERTAIN
            res.claim = "[护栏拦截] 单模型预测尚未独立复核，不能声明已证实。"

        # 铁律 1: 声明支持但无支持引用，强制降级为 E0 / Unknown
        if not res.support_refs:
            res.status = ClaimStatus.UNKNOWN
            res.evidence_level = EvidenceLevel.E0
            res.claim += " [护栏拦截: 结论缺乏引用支撑，已强制降级为未知 E0]"

        # 铁律 2: ICAP 认知边界护栏 - 证据等级为 E1/E2 时，严禁直接给心理状态下定论
        if res.evidence_level in [EvidenceLevel.E0, EvidenceLevel.E1, EvidenceLevel.E2]:
            for kw in COGNITIVE_OVERREACH_KEYWORDS:
                if kw in res.claim:
                    res.claim = res.claim.replace(kw, f"外显动作状态")
                    res.claim += " [护栏拦截: E2及以下证据严禁跨级推断内在心理状态]"
                    if res.status == ClaimStatus.SUPPORTED:
                        res.status = ClaimStatus.UNKNOWN

        return res
