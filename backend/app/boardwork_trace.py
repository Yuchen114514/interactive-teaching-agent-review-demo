from __future__ import annotations

import re
from typing import Any


def _summary_lines(summary: str) -> list[str]:
    normalized = (summary or "").strip()
    if not normalized:
        return []
    lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if line:
            lines.append(line)
    return lines


def normalize_board_point_text(text: str) -> str:
    normalized = " ".join((text or "").strip().split())
    normalized = normalized.replace("**", "")
    replacements = (
        ("师生共识", ""),
        ("教师已确认", ""),
        ("教师确认", ""),
        ("教师引导", ""),
        ("学生明确提出", ""),
        ("学生明确", ""),
        ("学生提出", ""),
        ("学生确认", ""),
        ("学生指出", ""),
        ("学生给出", ""),
        ("学生识别出", ""),
        ("学生拓展了解题策略，确认了", ""),
        ("学生拓展了解题策略", ""),
        ("学生已经掌握了", ""),
        ("学生已经", ""),
        ("经教师确认", ""),
        ("并声称", ""),
        ("当前数组状态为", "当前状态为"),
        ("当前状态是", "当前状态为"),
    )
    for source, target in replacements:
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"^(学生|教师|双方)\s*", "", normalized)
    normalized = normalized.strip(" ：:，,。.!！?")
    if normalized and normalized[-1] not in "。？！?!":
        normalized += "。"
    return normalized


def _normalize_summary_points(
    points: list[dict[str, Any]] | None,
    *,
    fallback_turn: int,
    fallback_source_kind: str,
) -> list[dict[str, Any]]:
    normalized_points: list[dict[str, Any]] = []
    for point in points or []:
        if not isinstance(point, dict):
            continue
        text = normalize_board_point_text(str(point.get("text") or ""))
        if not text:
            continue
        source_turns = [
            int(turn)
            for turn in (point.get("source_turns") or [])
            if isinstance(turn, int)
        ] or [fallback_turn]
        normalized_points.append(
            {
                "text": text,
                "source_turns": source_turns,
                "source_kind": str(point.get("source_kind") or fallback_source_kind),
            }
        )
    return normalized_points


def render_trace_summary(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    for point in payload.get("summary_points", []) or []:
        if not isinstance(point, dict):
            continue
        text = normalize_board_point_text(str(point.get("text") or ""))
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(text)
    return "\n".join(f"- {line}" for line in lines)


def build_trace_payload(
    *,
    summary: str,
    source_turn_index: int,
    coach_note: str | None = None,
    promotion_reason: str | None = None,
    summary_source_kind: str = "student_inference",
    coach_source_kind: str = "teacher_prompt",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary_points": _normalize_summary_points(
            [
                {
                    "text": point,
                    "source_turns": [source_turn_index],
                    "source_kind": summary_source_kind,
                }
                for point in _summary_lines(summary)
            ],
            fallback_turn=source_turn_index,
            fallback_source_kind=summary_source_kind,
        ),
        "promotion_reason": promotion_reason,
    }
    if coach_note:
        payload["coach_note_source"] = {
            "text": coach_note,
            "source_turns": [source_turn_index],
            "source_kind": coach_source_kind,
        }
    return payload


def merge_trace_payload(
    *,
    existing_payload: dict[str, Any] | None,
    summary: str,
    source_turn_index: int,
    coach_note: str | None = None,
    promotion_reason: str | None = None,
    summary_source_kind: str = "student_inference",
    coach_source_kind: str = "teacher_prompt",
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(existing_payload or {})
    existing_points = {
        normalize_board_point_text(str(point.get("text") or "")): dict(point)
        for point in payload.get("summary_points", [])
        if isinstance(point, dict) and point.get("text")
    }
    merged_points: list[dict[str, Any]] = list(existing_points.values())
    for point in _normalize_summary_points(
        [
            {
                "text": point_text,
                "source_turns": [source_turn_index],
                "source_kind": summary_source_kind,
            }
            for point_text in _summary_lines(summary)
        ],
        fallback_turn=source_turn_index,
        fallback_source_kind=summary_source_kind,
    ):
        point_text = point["text"]
        if point_text in existing_points:
            continue
        existing_points[point_text] = point
        merged_points.append(point)

    payload["summary_points"] = merged_points
    if coach_note:
        payload["coach_note_source"] = {
            "text": coach_note,
            "source_turns": [source_turn_index],
            "source_kind": coach_source_kind,
        }
    elif "coach_note_source" in payload and not coach_note:
        payload.pop("coach_note_source", None)
    if promotion_reason is not None:
        payload["promotion_reason"] = promotion_reason
    return payload
