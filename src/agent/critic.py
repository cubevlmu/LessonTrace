"""
Critic 智能体模块：基于确定性硬规则的跨模态冲突检测与拦截器 (零 Token 开销)
对标 SENSE、MAVE 与 CMC-Bench 的 4 类受控冲突分类学
"""

from typing import Optional, Tuple
from src.schema import ClassroomEvent, TeachingContext, ConflictType

class CriticAgent:
    def review_fusion(self,retrieved,query):
        from src.agent.fusion.reasoning import reason
        return reason(retrieved,query)

    def review_context(self,bundle,request):
        from src.context.analysis import risks
        return risks(bundle,request)

    def review_region_evidence(self,bundle,query,registry):
        from src.agent.review_claims import critic_bundle
        return critic_bundle(bundle,query,registry)

    def check_conflict(
        self,
        event: ClassroomEvent,
        context: Optional[TeachingContext] = None
    ) -> Tuple[ConflictType, str]:
        """
        快速判定事件是否存在冲突，返回 (ConflictType, 冲突触发原因说明)
        """
        action = event.observable_action.lower()
        conf = event.confidence

        # 规则 1: 视觉动作歧义冲突 (Visual Ambiguity Conflict)
        # 例如：举手检测置信度处于中低区间 (0.35 ~ 0.65)，在教室中极易与托腮、摸头、扶眼镜混淆
        if action == "hand_raising" and conf < 0.65:
            return (
                ConflictType.VISUAL_AMBIGUITY_CONFLICT,
                f"举手动作检测置信度偏低 ({conf:.2f} < 0.65)，疑似托腮、摸头或动作歧义，需触发局部高清截图复核"
            )
        if conf < 0.55:
            return (
                ConflictType.VISUAL_AMBIGUITY_CONFLICT,
                f"动作检测置信度偏低 ({conf:.2f} < 0.55)，可能存在分类歧义或误检"
            )

        # 规则 2: 时空与实体混淆冲突 (Spatio-Temporal / Entity Conflict)
        # 例如：边界框坐标异常 (宽度或高度过小/超界)，或者长宽比不合常理
        x1, y1, x2, y2 = event.bbox
        w, h = x2 - x1, y2 - y1
        if w <= 10 or h <= 10 or w > 1200 or h > 700:
            return (
                ConflictType.SPATIO_TEMPORAL_CONFLICT,
                f"边界框尺寸异常 ({w}x{h})，疑似跟踪漂移或实体 ID 跳变"
            )

        # 规则 3: 教学语境缺失冲突 (Pedagogical Context Missing)
        # 例如：学生外显动作为低头/趴睡 (head_down/sleep) 或长时间书写 (writing/homework)，但当前时间窗完全缺失教案/讲稿语境
        if action in ["head_down", "sleep", "writing", "homework"]:
            if context is None or not context.teaching_stage:
                return (
                    ConflictType.PEDAGOGICAL_CONTEXT_MISSING,
                    f"学生表现为 {action}，但当前时间窗无教案/任务语境约束，不可直接推断认知专注度"
                )

        # 规则 4: 音视跨模态矛盾 (Cross-Modal Contradiction)
        # 例如：教师明确在进行集中讲解/看黑板演示 (expected: focus)，但该学生持续低头或玩手机
        if context and context.expected_student_action:
            exp_action = context.expected_student_action.lower()
            if exp_action == "focus" and action in ["head_down", "sleep", "playphone"]:
                return (
                    ConflictType.CROSS_MODAL_CONTRADICTION,
                    f"教学设计要求全班抬头注意板书/屏幕演示 (expected: {exp_action})，但检测到该生行为为 ({action})，构成音视跨模态矛盾"
                )

        return ConflictType.NONE, "无显著冲突，证据事实正常"
