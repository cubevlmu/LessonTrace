"""
Miner 智能体模块：负责从证据库中按时间窗、目标学生或行为检索候选证据
"""

from typing import List, Optional, Tuple
from src.schema import ClassroomEvent, TeachingContext
from src.db import ClassroomDB

class MinerAgent:
    def retrieve_fusion(self,service,query,actor,allow_identity=False):
        from src.agent.fusion.retrieval import retrieve
        return retrieve(service,query,actor,allow_identity)

    def retrieve_context(self,request,service,actor):
        from src.context.retrieval import multimedia
        return multimedia(service,request,actor)

    def retrieve_regions(self,query):
        return self.db.search_region_evidence(query)

    def __init__(self, db: ClassroomDB):
        self.db = db

    def recall_candidates(
        self,
        lesson_id: str,
        t_start: float,
        t_end: float,
        track_id: Optional[str] = None
    ) -> List[Tuple[ClassroomEvent, Optional[TeachingContext]]]:
        """
        检索指定时间窗内的所有视觉事件，并对齐相应的教学语境
        """
        events = self.db.get_events_by_window(lesson_id, t_start, t_end)
        if track_id:
            events = [e for e in events if e.track_id == track_id]

        candidates = []
        for evt in events:
            # 查找事件发生时刻的教学语境
            ctx = self.db.get_context_at_time(lesson_id, (evt.t_start + evt.t_end) / 2)
            candidates.append((evt, ctx))

        return candidates
