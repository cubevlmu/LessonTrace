"""
LocalClassroomAgent 本地单编排器主控模块
串联 Miner -> Critic -> Verifier -> Arbiter -> Guardrail 完整闭环
"""

import os
from typing import List, Dict, Any, Optional
from src.schema import ClassroomEvent, TeachingContext, ArbiterOutput, ConflictType, VerificationDetails
from src.db import ClassroomDB
from src.agent.miner import MinerAgent
from src.agent.critic import CriticAgent
from src.agent.verifier import VerifierAgent
from src.agent.arbiter import ArbiterAgent
from src.agent.guardrail import GuardrailAgent

class LocalClassroomAgent:
    def __init__(
        self,
        db_path: str = "data/classroom.db",
        crop_output_dir: str = "outputs/evidence_crops",
        api_key: Optional[str] = None
    ):
        self.db = ClassroomDB(db_path)
        self.miner = MinerAgent(self.db)
        self.critic = CriticAgent()
        self.verifier = VerifierAgent(crop_output_dir)
        self.arbiter = ArbiterAgent(api_key)
        self.guardrail = GuardrailAgent()

    def process_single_event(
        self,
        event: ClassroomEvent,
        context: Optional[TeachingContext] = None,
        verification: Optional[VerificationDetails] = None
    ) -> ArbiterOutput:
        """
        单事件完整核验流水线
        """
        # Step 1: 若未提供语境，从库中查询对齐的语境
        if context is None:
            mid_t = (event.t_start + event.t_end) / 2.0
            context = self.db.get_context_at_time(event.lesson_id, mid_t)

        # Step 2: Critic 快速冲突打标
        conflict_type, critic_reason = self.critic.check_conflict(event, context)

        # Step 3: Verifier 按需调用本地取证工具
        verification_details = verification
        if verification_details is None and conflict_type != ConflictType.NONE:
            verification_details = self.verifier.verify_event(event, conflict_type, critic_reason)

        # Step 4: Arbiter 结构化原子裁决
        raw_output = self.arbiter.arbitrate(event, context, conflict_type, verification_details)

        # Step 5: Guardrail 兜底护栏防幻觉
        final_output = self.guardrail.validate(raw_output)

        # 存入数据库以备前端调用
        self.db.insert_arbitration(final_output)

        return final_output

    def process_lesson(self, lesson_id: str) -> List[ArbiterOutput]:
        """
        全堂课所有事件批量核验流水线
        """
        events = self.db.get_all_events(lesson_id)
        results = []
        print(f"[-] 开始批量核验课堂 [{lesson_id}]，共 {len(events)} 个视觉行为事件...")
        for evt in events:
            res = self.process_single_event(evt)
            results.append(res)
        print(f"[OK] 课堂 [{lesson_id}] 全部事件核验完成，已保存至数据库！")
        return results
