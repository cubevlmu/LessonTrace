"""
前端 WebUI 专属数据导出与桥接接口 (为前端组员预留)
组员可直接调用此类或导入生成的 JSON 文件，无需理解内部复杂 Agent 编排逻辑。
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from src.db import ClassroomDB

class WebUIBridge:
    def __init__(self, db_path: str = "data/classroom.db"):
        self.db = ClassroomDB(db_path)

    def get_lesson_overview(self, lesson_id: str) -> Dict[str, Any]:
        """获取课堂总体复盘概览数据"""
        events = self.db.get_all_events(lesson_id)
        arbitrations = self.db.get_all_arbitrations(lesson_id)

        # 统计四大状态分布
        status_counts = {"supported": 0, "uncertain": 0, "contradicted": 0, "unknown": 0}
        conflict_counts = {}
        for arb in arbitrations:
            st = arb.get("status", "unknown")
            status_counts[st] = status_counts.get(st, 0) + 1
            ct = arb.get("conflict_type", "none")
            conflict_counts[ct] = conflict_counts.get(ct, 0) + 1

        return {
            "lesson_id": lesson_id,
            "total_events": len(events),
            "total_claims": len(arbitrations),
            "status_distribution": status_counts,
            "conflict_distribution": conflict_counts,
            # 仅是输出状态占比；没有人工真值时不能当作准确率。
            "abstention_rate": round(status_counts["unknown"] / max(1, len(arbitrations)), 3),
            "supported_rate": round(status_counts["supported"] / max(1, len(arbitrations)), 3),
            "corrected_rate": round(status_counts["contradicted"] / max(1, len(arbitrations)), 3)
        }

    def get_timeline_events(self, lesson_id: str) -> List[Dict[str, Any]]:
        """获取按时间轴排序的事件与裁决卡片数据列表"""
        events = self.db.get_all_events(lesson_id)
        arbitrations = {a["event_id"]: a for a in self.db.get_all_arbitrations(lesson_id)}

        timeline = []
        for e in events:
            arb = arbitrations.get(e.event_id, {})
            timeline.append({
                "event_id": e.event_id,
                "t_start": e.t_start,
                "t_end": e.t_end,
                "duration": round(e.t_end - e.t_start, 2),
                "track_id": e.track_id,
                "action": e.observable_action,
                "confidence": e.confidence,
                "bbox": e.bbox,
                "raw_video": e.raw_evidence_ref,
                "modality": e.modality,
                "source_kind": "model_prediction" if e.modality == "visual" else e.modality,
                # 裁决信息
                "claim": arb.get("claim", "尚未裁决"),
                "status": arb.get("status", "unknown"),
                "evidence_level": arb.get("evidence_level", "E1"),
                "conflict_type": arb.get("conflict_type", "none"),
                "crop_image": arb.get("verification_details", {}).get("crop_image_path", ""),
                "support_refs": arb.get("support_refs", []),
                "verification_details": arb.get("verification_details", {})
            })
        return timeline

    def export_full_report_json(self, lesson_id: str, output_file: str = "data/webui_report.json") -> str:
        """一键导出给前端使用的标准化 JSON 报表"""
        overview = self.get_lesson_overview(lesson_id)
        timeline = self.get_timeline_events(lesson_id)

        data = {
            "meta": overview,
            "timeline": timeline
        }
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"[+] WebUI 完整报告已导出至: {output_file}")
        return output_file


    def get_region_report(self, video_id=None):
        """Export original region annotations and append-only reviews separately."""
        from src.evidence_db import EvidenceDB
        db = EvidenceDB(self.db.db_path)
        return {
            'report_kind': 'region_evidence_replay',
            'scope': 'observable_action_only_not_cognitive_state',
            'source_kinds': ['dataset_annotation', 'human_review', 'vlm_visual_review'],
            'events': [db.detail(e['event_id']) for e in db.events(video_id=video_id)],
        }

if __name__ == "__main__":
    bridge = WebUIBridge()
    bridge.export_full_report_json("cls_demo_01")
