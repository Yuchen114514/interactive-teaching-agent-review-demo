from __future__ import annotations

import json
import re
from typing import Any


BOARDWORK_ANALYSIS_INSTRUCTIONS = [
    "你是苏格拉底题解白板分析器，不直接与学生对话。",
    "你只根据当前这一轮用户回答、教师追问、以及现有白板节点，输出结构化 JSON。",
    "如果本轮没有新的稳定认知，就输出 noop。",
    "如果当前输入还只是寒暄、准备提问、或尚未进入 OJ 题目/知识点，也输出 noop。",
    "只有学生已经显式说出、或已经在教师追问下形成共识的内容，才能进入 summary_points。",
    "不要把完整解法、完整证明、完整代码写进 summary_points。",
    "coach_note 必须是一句短追问，只保留最后那个真正推动学生思考的问题，不要保留铺垫、举例和表扬。",
    "输出必须是单个 JSON 对象，不要带 markdown 代码块。",
]


def extract_boardwork_analysis_json(text: str) -> dict[str, Any] | None:
    normalized = (text or "").strip()
    if not normalized:
        return None

    try:
        payload = json.loads(normalized)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", normalized)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def normalize_boardwork_analysis_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    action_type = payload.get("action_type")
    if action_type not in {"noop", "annotate", "refine", "advance"}:
        return None

    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return None

    summary_points = payload.get("summary_points") or []
    normalized_points: list[dict[str, Any]] = []
    if isinstance(summary_points, list):
        for point in summary_points:
            if not isinstance(point, dict):
                continue
            text = str(point.get("text") or "").strip()
            source_turns = point.get("source_turns") or []
            if not text or not isinstance(source_turns, list):
                continue
            normalized_points.append(
                {
                    "text": text,
                    "source_turns": [int(turn) for turn in source_turns if isinstance(turn, int)],
                    "source_kind": str(point.get("source_kind") or "shared_consensus"),
                }
            )

    coach_note = payload.get("coach_note")
    normalized_coach: dict[str, Any] | None = None
    if isinstance(coach_note, dict):
        text = str(coach_note.get("text") or "").strip()
        source_turns = coach_note.get("source_turns") or []
        if text and isinstance(source_turns, list):
            normalized_coach = {
                "text": text,
                "source_turns": [int(turn) for turn in source_turns if isinstance(turn, int)],
                "source_kind": str(coach_note.get("source_kind") or "teacher_prompt"),
            }

    return {
        "action_type": action_type,
        "target_node_title": str(payload.get("target_node_title") or "").strip(),
        "summary_points": normalized_points,
        "coach_note": normalized_coach,
        "promotion_reason": str(payload.get("promotion_reason") or "").strip() or None,
        "confidence": confidence,
    }
