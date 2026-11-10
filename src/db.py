"""
SQLite 证据库管理与数据持久化模块
"""

import sqlite3
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from src.schema import ClassroomEvent, TeachingContext, ArbiterOutput, ConflictType, EvidenceLevel, ClaimStatus

class ClassroomDB:
    def __init__(self, db_path: str = "data/classroom.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. 结构化事件表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                lesson_id TEXT,
                t_start REAL,
                t_end REAL,
                source_id TEXT,
                modality TEXT,
                track_id TEXT,
                bbox TEXT,
                observable_action TEXT,
                confidence REAL,
                raw_evidence_ref TEXT
            )
            """)

            # 2. 教学任务语境表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS teaching_context (
                context_id TEXT PRIMARY KEY,
                lesson_id TEXT,
                t_start REAL,
                t_end REAL,
                teaching_stage TEXT,
                teacher_speech TEXT,
                expected_student_action TEXT
            )
            """)

            # 3. 智能体最终裁决输出表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS arbitrations (
                event_id TEXT PRIMARY KEY,
                lesson_id TEXT,
                claim TEXT,
                status TEXT,
                evidence_level TEXT,
                support_refs TEXT,
                conflict_type TEXT,
                verification_details TEXT
            )
            """)
            conn.commit()

    def insert_event(self, event: ClassroomEvent):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.lesson_id,
                event.t_start,
                event.t_end,
                event.source_id,
                event.modality,
                event.track_id,
                json.dumps(event.bbox),
                event.observable_action,
                event.confidence,
                event.raw_evidence_ref
            ))
            conn.commit()

    def insert_context(self, ctx: TeachingContext):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO teaching_context VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ctx.context_id,
                ctx.lesson_id,
                ctx.t_start,
                ctx.t_end,
                ctx.teaching_stage,
                ctx.teacher_speech,
                ctx.expected_student_action
            ))
            conn.commit()

    def insert_arbitration(self, res: ArbiterOutput):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO arbitrations VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                res.event_id,
                res.lesson_id,
                res.claim,
                res.status.value,
                res.evidence_level.value,
                json.dumps(res.support_refs),
                res.conflict_type.value,
                json.dumps(res.verification_details.model_dump() if res.verification_details else {})
            ))
            conn.commit()

    def get_event(self, event_id: str) -> Optional[ClassroomEvent]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM events WHERE event_id = ?", (event_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return ClassroomEvent(
                event_id=row[0],
                lesson_id=row[1],
                t_start=row[2],
                t_end=row[3],
                source_id=row[4],
                modality=row[5],
                track_id=row[6],
                bbox=json.loads(row[7]),
                observable_action=row[8],
                confidence=row[9],
                raw_evidence_ref=row[10]
            )

    def get_events_by_window(self, lesson_id: str, t_start: float, t_end: float) -> List[ClassroomEvent]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM events 
            WHERE lesson_id = ? AND NOT (t_end < ? OR t_start > ?)
            ORDER BY t_start ASC
            """, (lesson_id, t_start, t_end))
            rows = cursor.fetchall()
            return [
                ClassroomEvent(
                    event_id=r[0],
                    lesson_id=r[1],
                    t_start=r[2],
                    t_end=r[3],
                    source_id=r[4],
                    modality=r[5],
                    track_id=r[6],
                    bbox=json.loads(r[7]),
                    observable_action=r[8],
                    confidence=r[9],
                    raw_evidence_ref=r[10]
                ) for r in rows
            ]

    def get_context_at_time(self, lesson_id: str, timestamp: float) -> Optional[TeachingContext]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM teaching_context
            WHERE lesson_id = ? AND t_start <= ? AND t_end >= ?
            LIMIT 1
            """, (lesson_id, timestamp, timestamp))
            row = cursor.fetchone()
            if not row:
                return None
            return TeachingContext(
                context_id=row[0],
                lesson_id=row[1],
                t_start=row[2],
                t_end=row[3],
                teaching_stage=row[4],
                teacher_speech=row[5],
                expected_student_action=row[6]
            )

    def get_all_events(self, lesson_id: Optional[str] = None) -> List[ClassroomEvent]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if lesson_id:
                cursor.execute("SELECT * FROM events WHERE lesson_id = ? ORDER BY t_start ASC", (lesson_id,))
            else:
                cursor.execute("SELECT * FROM events ORDER BY t_start ASC")
            rows = cursor.fetchall()
            return [
                ClassroomEvent(
                    event_id=r[0],
                    lesson_id=r[1],
                    t_start=r[2],
                    t_end=r[3],
                    source_id=r[4],
                    modality=r[5],
                    track_id=r[6],
                    bbox=json.loads(r[7]),
                    observable_action=r[8],
                    confidence=r[9],
                    raw_evidence_ref=r[10]
                ) for r in rows
            ]

    def get_all_arbitrations(self, lesson_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if lesson_id:
                cursor.execute("SELECT * FROM arbitrations WHERE lesson_id = ?", (lesson_id,))
            else:
                cursor.execute("SELECT * FROM arbitrations")
            rows = cursor.fetchall()
            results = []
            for r in rows:
                results.append({
                    "event_id": r[0],
                    "lesson_id": r[1],
                    "claim": r[2],
                    "status": r[3],
                    "evidence_level": r[4],
                    "support_refs": json.loads(r[5]),
                    "conflict_type": r[6],
                    "verification_details": json.loads(r[7]) if r[7] else {}
                })
            return results
