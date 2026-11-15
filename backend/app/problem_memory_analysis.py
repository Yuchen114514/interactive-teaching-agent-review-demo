from __future__ import annotations

import json
import re
from typing import Any


PROBLEM_MEMORY_ANALYSIS_INSTRUCTIONS = [
    "你是 OJ 苏格拉底对话的题目工作记忆分析器，不直接与学生对话。",
    "你只根据当前这一轮用户消息、教师回复、最近可见历史、当前会话摘要和当前学习状态，输出结构化 JSON。",
    "你的任务是维护“当前到底在做哪道题、当前推进到哪里、当前卡点是什么”。",
    "只有已经在对话中明确出现过的题目、条件、焦点、卡点，才能进入摘要。",
    "不要编造题意，不要补充对话中从未出现过的知识点。",
    "session_summary 应该是一句简洁的当前题目描述。",
    "learning_state 应该是一句简洁的当前推进状态或当前卡点描述。",
    "如果这一轮不足以更新工作记忆，就输出 should_update=false。",
    "输出必须是单个 JSON 对象，不要带 markdown 代码块。",
]


def extract_problem_memory_analysis_json(text: str) -> dict[str, Any] | None:
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


def normalize_problem_memory_analysis_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    should_update = payload.get("should_update")
    if not isinstance(should_update, bool):
        return None

    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return None

    session_summary = str(payload.get("session_summary") or "").strip() or None
    learning_state = str(payload.get("learning_state") or "").strip() or None
    reason = str(payload.get("reason") or "").strip() or None

    return {
        "should_update": should_update,
        "session_summary": session_summary,
        "learning_state": learning_state,
        "reason": reason,
        "confidence": confidence,
    }
