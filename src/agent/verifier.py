"""
Verifier 智能体模块：基于 OpenCV 的本地主动取证工具库
按需触发局部高精度区域裁剪 (ROI Crop with 20% expansion) 与关键帧溯源
"""

import os
import cv2
from pathlib import Path
from typing import Optional
from src.schema import ClassroomEvent, ConflictType, VerificationDetails

class VerifierAgent:
    def __init__(self, output_dir: str = "outputs/evidence_crops"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def verify_event(
        self,
        event: ClassroomEvent,
        conflict_type: ConflictType,
        critic_reason: str
    ) -> VerificationDetails:
        """
        根据冲突类型执行针对性的局部取证
        """
        if conflict_type == ConflictType.NONE:
            return VerificationDetails(tool_called="none", critic_flag="none", notes="常规事实无需二次取证")

        video_path = Path(event.raw_evidence_ref)
        if not video_path.exists():
            return VerificationDetails(
                tool_called="file_checker",
                critic_flag=conflict_type.value,
                notes=f"原始视频文件不存在 ({video_path})，跳过局部抽帧取证"
            )

        # 执行针对性工具调用
        if conflict_type == ConflictType.VISUAL_AMBIGUITY_CONFLICT:
            crop_path = self._crop_student_roi(event, video_path, expand_ratio=0.20)
            notes = "局部截图已留存，动作尚未复核；不能仅凭低置信度驳回初判。" if crop_path else "局部截图提取失败，动作尚未复核。"
            return VerificationDetails(
                tool_called="crop_student_roi",
                crop_image_path=crop_path,
                critic_flag=critic_reason,
                notes=notes
            )

        elif conflict_type in [ConflictType.PEDAGOGICAL_CONTEXT_MISSING, ConflictType.CROSS_MODAL_CONTRADICTION]:
            crop_path = self._crop_student_roi(event, video_path, expand_ratio=0.20)
            return VerificationDetails(
                tool_called="crop_student_roi_with_context",
                crop_image_path=crop_path,
                critic_flag=critic_reason,
                notes="已留存候选动作截图，等待语境及动作复核。" if crop_path else "截图提取失败，无法补充视觉证据。"
            )

        elif conflict_type == ConflictType.SPATIO_TEMPORAL_CONFLICT:
            crop_path = self._crop_student_roi(event, video_path, expand_ratio=0.30)
            return VerificationDetails(
                tool_called="crop_student_roi",
                crop_image_path=crop_path,
                critic_flag=critic_reason,
                notes="仅尝试留存截图；尚未执行时间窗扩展或轨迹比对，不能确认 ID 跳变。"
            )

        return VerificationDetails(tool_called="default_logger", critic_flag=critic_reason)

    def _crop_student_roi(
        self,
        event: ClassroomEvent,
        video_path: Path,
        expand_ratio: float = 0.20
    ) -> str:
        """
        从原视频中提取事件中间时刻的关键帧，并按照边界框外扩指定比例进行裁剪保存
        """
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        
        # 定位至事件发生中间时刻
        mid_time = (event.t_start + event.t_end) / 2.0
        target_frame = int(mid_time * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return ""

        h, w, _ = frame.shape
        x1, y1, x2, y2 = event.bbox
        bw = x2 - x1
        bh = y2 - y1

        # 外扩指定比例像素，以获得肢体与课桌周边上下文
        dx = int(bw * expand_ratio)
        dy = int(bh * expand_ratio)
        crop_x1 = max(0, x1 - dx)
        crop_y1 = max(0, y1 - dy)
        crop_x2 = min(w, x2 + dx)
        crop_y2 = min(h, y2 + dy)

        if bw <= 0 or bh <= 0 or crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            return ""

        crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
        crop_filename = f"verify_{event.event_id}.jpg"
        save_path = self.output_dir / crop_filename
        if not cv2.imwrite(str(save_path), crop):
            return ""

        return str(save_path)
