from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.teaching.agent_providers import TeachingAgentProviderUnavailableError
from app.teaching.v3_contracts import (
    KnowledgeDepositHintBlock,
    MainIntentStateBlock,
    MCPRequestBlock,
    VisualNeedBlock,
)

MAIN_DIALOG_AGENT_NAME = "MainDialogAgent"
MAIN_DIALOG_CAPABILITY = "main_dialog_turn"
STANDARD_MATH_MARKDOWN_RULE = (
    "Math format policy: for every mathematical expression in visible_reply and knowledge_deposit_hint.candidate_summary, "
    "use standard Markdown math: inline formulas must be wrapped as $...$, display formulas as $$...$$. "
    "Do not use \\(...\\), \\[...\\], bare multi-line formulas, or ad-hoc line-broken variables such as d[i] on separate lines. "
    "Keep normal prose outside math delimiters."
)


@dataclass(frozen=True)
class MainDialogResult:
    intent_state: MainIntentStateBlock
    visible_reply: str
    visual_need: VisualNeedBlock
    knowledge_deposit_hint: KnowledgeDepositHintBlock
    mcp_request: MCPRequestBlock
    raw_output: dict[str, Any]


class MainDialogAgent:
    def __init__(
        self,
        *,
        provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
    ) -> None:
        self.provider = provider

    def run(self, context: dict[str, Any]) -> MainDialogResult:
        if self.provider is None:
            raise _provider_error("provider_missing")

        context = _context_with_guidance_contract(context)
        raw_output = self.provider(
            {
                "capability": MAIN_DIALOG_CAPABILITY,
                "context": context,
                "contract": _main_dialog_contract(context=context),
            }
        )
        if not isinstance(raw_output, dict):
            raise _provider_error("provider_output_rejected", {"reason": "not_object"})

        intent_state, visible_reply, visual_need, knowledge_hint, mcp_request = _parse_main_dialog_blocks(raw_output)

        intent_state, visible_reply, visual_need, knowledge_hint, mcp_request = _finalize_main_dialog_blocks(
            intent_state=intent_state,
            visible_reply=visible_reply,
            visual_need=visual_need,
            knowledge_hint=knowledge_hint,
            mcp_request=mcp_request,
            context=context,
        )

        if not visible_reply:
            raise _provider_error("provider_output_rejected", {"reason": "empty_visible_reply"})

        return MainDialogResult(
            intent_state=intent_state,
            visible_reply=visible_reply,
            visual_need=visual_need,
            knowledge_deposit_hint=knowledge_hint,
            mcp_request=mcp_request,
            raw_output=raw_output,
        )


def _provider_error(outcome: str, details: dict[str, Any] | None = None) -> TeachingAgentProviderUnavailableError:
    return TeachingAgentProviderUnavailableError(
        agent_name=MAIN_DIALOG_AGENT_NAME,
        capability=MAIN_DIALOG_CAPABILITY,
        outcome=outcome,
        details=details,
    )


def _parse_main_dialog_blocks(
    raw_output: dict[str, Any],
) -> tuple[MainIntentStateBlock, str, VisualNeedBlock, KnowledgeDepositHintBlock, MCPRequestBlock]:
    normalized_output = _normalize_raw_output(raw_output)
    try:
        intent_state = MainIntentStateBlock.model_validate(normalized_output.get("intent_state"))
        visible_reply = _normalize_visible_reply(normalized_output.get("visible_reply"))
        visual_need = VisualNeedBlock.model_validate(normalized_output.get("visual_need") or {})
        knowledge_hint = KnowledgeDepositHintBlock.model_validate(
            normalized_output.get("knowledge_deposit_hint") or {}
        )
        mcp_request = MCPRequestBlock.model_validate(normalized_output.get("mcp_request") or {})
    except (TypeError, ValidationError, ValueError) as exc:
        raise _provider_error(
            "provider_output_rejected",
            {
                "reason": type(exc).__name__,
                "errors": exc.errors() if isinstance(exc, ValidationError) else [],
                "raw_keys": sorted(str(key) for key in raw_output.keys()),
                "raw_output_preview": _preview_raw_output(raw_output),
            },
        ) from exc
    return intent_state, visible_reply, visual_need, knowledge_hint, mcp_request


def _finalize_main_dialog_blocks(
    *,
    intent_state: MainIntentStateBlock,
    visible_reply: str,
    visual_need: VisualNeedBlock,
    knowledge_hint: KnowledgeDepositHintBlock,
    mcp_request: MCPRequestBlock,
    context: dict[str, Any],
) -> tuple[MainIntentStateBlock, str, VisualNeedBlock, KnowledgeDepositHintBlock, MCPRequestBlock]:
    visible_reply_had_diagram_source = _contains_diagram_source_block(visible_reply)
    visible_reply = _strip_diagram_source_blocks(visible_reply)
    visible_reply = _repair_visible_reply(visible_reply, context)
    if visible_reply_had_diagram_source:
        visual_need = _force_visual_need(
            visual_need,
            diagram_goal=_diagram_goal_from_context(context),
        )
    if visual_need.need_diagram and not visual_need.diagram_goal:
        visual_need = visual_need.model_copy(update={"diagram_goal": _diagram_goal_from_context(context)})
    visible_reply = _strip_diagram_promises(visible_reply)
    if intent_state.intent != "non_problem":
        visible_reply = _enforce_guidance_visible_reply(visible_reply, context)
        visible_reply = _enforce_direct_visible_reply(visible_reply, context)
    should_prefetch_explicit_problem = intent_state.intent != "non_problem" or (
        mcp_request.need_mcp and mcp_request.resource == "problem_detail"
    )
    if _context_has_explicit_problem_id(context) and should_prefetch_explicit_problem:
        problem_id = _explicit_problem_id(context)
        if mcp_request.resource == "submit_code_feedback" and mcp_request.need_mcp:
            mcp_request = mcp_request.model_copy(update={"problem_id": problem_id})
        elif not mcp_request.need_mcp or mcp_request.resource in {"none", "problem_detail"}:
            mcp_request = mcp_request.model_copy(
                update={
                    "need_mcp": True,
                    "resource": "problem_detail",
                    "problem_id": problem_id,
                    "reason": mcp_request.reason or "explicit_problem_id",
                }
            )
    if mcp_request.need_mcp and mcp_request.resource == "submit_code_feedback":
        mcp_request = _complete_model_selected_submission(mcp_request, context)
    clarification = _mcp_request_clarification(
        mcp_request,
        response_language=str(context.get("response_language") or "zh"),
    )
    if clarification:
        visible_reply = clarification
        mcp_request = MCPRequestBlock()
    return intent_state, visible_reply, visual_need, knowledge_hint, mcp_request


def _normalize_raw_output(raw_output: dict[str, Any]) -> dict[str, Any]:
    intent_source = raw_output.get("intent_state")
    if not isinstance(intent_source, dict) and any(
        key in raw_output for key in ("intent", "turn_class", "class", "kind", "type", "plan_signal")
    ):
        intent_source = raw_output
    return {
        **raw_output,
        "intent_state": _normalize_intent_state(intent_source),
        "visual_need": _normalize_visual_need(raw_output.get("visual_need")),
        "knowledge_deposit_hint": _normalize_knowledge_deposit_hint(
            raw_output.get("knowledge_deposit_hint")
        ),
        "mcp_request": _normalize_mcp_request(raw_output.get("mcp_request")),
    }


def _normalize_intent_state(value: Any) -> dict[str, Any]:
    raw = _as_object(value)
    intent = _normalize_intent(
        raw.get("intent"),
        raw.get("turn_class"),
        raw.get("class"),
        raw.get("kind"),
        raw.get("type"),
    )
    normalized = dict(raw)
    normalized["intent"] = intent
    normalized.setdefault("confidence", 0.7)
    normalized.setdefault("state_action", "none")
    normalized.setdefault("should_start_new_workspace", intent == "new_problem")
    normalized["plan_signal"] = _normalize_plan_signal(raw.get("plan_signal"), intent=intent)
    normalized.setdefault("target_step_key", None)
    normalized.setdefault("off_plan_reason", None)
    normalized.setdefault("reason", "")
    return normalized


def _normalize_visual_need(value: Any) -> dict[str, Any]:
    raw = _as_object(value)
    raw_type = str(raw.get("type") or raw.get("kind") or "").strip().lower()
    inferred_need = raw_type in {"diagram", "visual", "chart", "image"} or bool(raw.get("diagram_goal"))
    need_diagram = raw.get("need_diagram")
    if not isinstance(need_diagram, bool):
        need_diagram = inferred_need
    diagram_goal = raw.get("diagram_goal") or raw.get("description") or raw.get("goal") or raw.get("content")
    visual_focus = raw.get("visual_focus") or raw.get("focus")
    normalized = dict(raw)
    normalized["need_diagram"] = need_diagram
    normalized["diagram_goal"] = diagram_goal if isinstance(diagram_goal, str) else None
    normalized["visual_focus"] = visual_focus if isinstance(visual_focus, str) else None
    suggested_case = raw.get("suggested_case")
    normalized["suggested_case"] = suggested_case[:300] if isinstance(suggested_case, str) and suggested_case.strip() else None
    normalized["priority"] = raw.get("priority") if raw.get("priority") in {"none", "low", "normal", "high"} else ("normal" if need_diagram else "none")
    return normalized


def _normalize_knowledge_deposit_hint(value: Any) -> dict[str, Any]:
    raw = _as_object(value)
    raw_type = str(raw.get("deposit_type") or raw.get("type") or raw.get("kind") or "").strip().lower()
    valid_deposit_types = {"concept", "mistake", "formula", "strategy", "example"}
    candidate_summary = (
        raw.get("candidate_summary")
        or raw.get("content")
        or raw.get("summary")
        or raw.get("description")
    )
    can_deposit = raw.get("can_deposit")
    if not isinstance(can_deposit, bool):
        can_deposit = raw_type in {"whiteboard", "knowledge", *valid_deposit_types} or isinstance(candidate_summary, str)
    deposit_type = raw.get("deposit_type") if raw.get("deposit_type") in valid_deposit_types else None
    if deposit_type is None and raw_type in valid_deposit_types:
        deposit_type = raw_type
    if deposit_type is None and can_deposit:
        deposit_type = "concept"
    normalized = dict(raw)
    normalized["can_deposit"] = can_deposit
    normalized["deposit_type"] = deposit_type
    normalized["candidate_summary"] = candidate_summary if isinstance(candidate_summary, str) else None
    normalized["source_step_key"] = raw.get("source_step_key") if isinstance(raw.get("source_step_key"), str) else None
    normalized.setdefault("confidence", 0.5 if can_deposit else 0.0)
    return normalized


def _normalize_mcp_request(value: Any) -> dict[str, Any]:
    raw = _as_object(value)
    valid_resources = {
        "none",
        "problem_detail",
        "problem_search",
        "problem_submissions",
        "submission_detail",
        "submit_code_feedback",
        "user_info",
        "user_stats",
        "user_weekly_submissions",
        "contest_list",
        "contest_info",
        "contest_problems_and_status",
        "contest_rank_list",
        "contest_submissions",
        "class_info",
        "class_list",
        "my_class_list",
        "class_contest_list",
        "class_problem_list",
        "platform_query",
    }
    need_mcp = raw.get("need_mcp")
    if not isinstance(need_mcp, bool):
        need_mcp = False
    resource = raw.get("resource")
    if resource not in valid_resources:
        resource = "none"
    normalized = dict(raw)
    normalized["need_mcp"] = need_mcp
    normalized["resource"] = resource
    normalized["problem_id"] = raw.get("problem_id") if isinstance(raw.get("problem_id"), int) else None
    normalized["submission_id"] = raw.get("submission_id") if isinstance(raw.get("submission_id"), int) else None
    normalized["contest_id"] = raw.get("contest_id") if isinstance(raw.get("contest_id"), int) else None
    normalized["class_id"] = raw.get("class_id") if isinstance(raw.get("class_id"), int) else None
    for field in ("title_search", "id_search", "author_search", "class_name", "course_name", "result"):
        normalized[field] = raw.get(field) if isinstance(raw.get(field), str) else None
    tag_names = raw.get("tag_names")
    normalized["tag_names"] = [tag for tag in tag_names if isinstance(tag, str)] if isinstance(tag_names, list) else []
    filters = raw.get("filters")
    normalized["filters"] = filters if isinstance(filters, dict) else {}
    normalized["language_id"] = raw.get("language_id") if isinstance(raw.get("language_id"), int) else None
    normalized["language"] = raw.get("language") if isinstance(raw.get("language"), str) else None
    normalized["code"] = raw.get("code") if isinstance(raw.get("code"), str) else None
    normalized["reason"] = raw.get("reason") if isinstance(raw.get("reason"), str) else ""
    return normalized


def _mcp_request_clarification(request: MCPRequestBlock, *, response_language: str = "zh") -> str | None:
    if not request.need_mcp:
        return None
    english = response_language == "en"
    if request.resource == "none":
        if request.reason == "unsupported_global_language_list_query":
            if english:
                return "The platform MCP does not provide a global language list. Give me a problem ID and I can check the languages available for that problem."
            return "目前 MCP 没有提供平台全局语言列表；如果你给我题目 ID，我可以查询该题支持的语言。"
        if english:
            return "Which platform information do you need: a problem, submission, contest, class, or account detail?"
        return "我还没有识别出对应的平台查询类型。你可以说明要查题目、提交记录、竞赛、班级或账号信息。"
    required_fields = {
        "problem_detail": ((request.problem_id, "题目 ID"),),
        "problem_submissions": ((request.problem_id, "题目 ID"),),
        "submission_detail": ((request.submission_id, "提交记录 ID"),),
        "contest_info": ((request.contest_id, "竞赛 ID"),),
        "contest_problems_and_status": ((request.contest_id, "竞赛 ID"),),
        "contest_rank_list": ((request.contest_id, "竞赛 ID"),),
        "contest_submissions": ((request.contest_id, "竞赛 ID"),),
        "class_info": ((request.class_id, "班级 ID"),),
        "class_contest_list": ((request.class_id, "班级 ID"),),
        "class_problem_list": ((request.class_id, "班级 ID"),),
    }
    missing = [label for value, label in required_fields.get(request.resource, ()) if value is None]
    if missing:
        if english:
            english_labels = {"题目 ID": "problem ID", "提交记录 ID": "submission ID", "竞赛 ID": "contest ID", "班级 ID": "class ID"}
            return f"Please provide the {', '.join(english_labels.get(label, label) for label in missing)} so I can look it up."
        return f"请提供{'、'.join(missing)}，我就可以继续查询。"
    if request.resource == "submit_code_feedback" and (not request.code or not (request.language or request.language_id)):
        if english:
            return "Please provide the complete code and programming language before I prepare a submission."
        return "请提供要验证的完整代码和编程语言，我再为你准备提交请求。"
    return None


def _as_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_intent(*values: Any) -> str:
    raw_values = [str(value or "").strip().lower() for value in values if str(value or "").strip()]
    aliases = {
        "new": "new_problem",
        "problem": "new_problem",
        "new_question": "new_problem",
        "new_problem": "new_problem",
        "followup": "continue_problem",
        "follow_up": "continue_problem",
        "continue": "continue_problem",
        "continue_problem": "continue_problem",
        "continuation": "continue_problem",
        "proceed": "continue_problem",
        "proceed_with_solution": "continue_problem",
        "solution_progress": "continue_problem",
        "request_derivation_and_rule": "continue_problem",
        "request_key_observation": "continue_problem",
        "request_hint": "continue_problem",
        "request_next_step": "continue_problem",
        "request_solution_progress": "continue_problem",
        "request_solution": "continue_problem",
        "advance": "continue_problem",
        "next_step": "continue_problem",
        "chat": "non_problem",
        "casual": "non_problem",
        "non_problem": "non_problem",
        "state": "state_change",
        "state_change": "state_change",
    }
    for intent in raw_values:
        normalized = aliases.get(intent, intent)
        if normalized in {"new_problem", "continue_problem", "non_problem", "state_change"}:
            return normalized
    return aliases.get(raw_values[0], raw_values[0]) if raw_values else ""


def _repair_visible_reply(text: str, context: dict[str, Any]) -> str:
    cleaned = text.strip()
    cleaned = _strip_visible_reply_protocol_wrappers(cleaned)
    if not cleaned:
        raise _provider_error("provider_output_rejected", {"reason": "empty_visible_reply"})
    return cleaned


def _strip_visible_reply_protocol_wrappers(text: str) -> str:
    cleaned = text.strip()
    json_match = re.fullmatch(r"\s*\{\s*['\"]visible_reply['\"]\s*:\s*(['\"])([\s\S]*)\1\s*\}\s*", cleaned)
    if json_match:
        cleaned = json_match.group(2).strip()
    label_pattern = re.compile(
        r"^(?:#{1,6}\s*)?(?:visible_reply|final_response|final_answer|assistant_response|"
        r"最终回复|最终回答|修正后回复|修正回复|可见回复)\s*[:：]\s*",
        flags=re.IGNORECASE,
    )
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = label_pattern.sub("", cleaned, count=1).strip()
    return cleaned


def _default_plan_signal(intent: str) -> str:
    if intent == "new_problem":
        return "reset_plan"
    if intent == "continue_problem":
        return "keep_current_step"
    return "none"


def _normalize_plan_signal(value: Any, *, intent: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "": _default_plan_signal(intent),
        "start": "reset_plan",
        "create": "reset_plan",
        "new": "reset_plan",
        "new_plan": "reset_plan",
        "begin": "reset_plan",
        "reset": "reset_plan",
        "continue": "keep_current_step",
        "keep": "keep_current_step",
        "same_step": "keep_current_step",
        "next": "advance_step",
        "advance": "advance_step",
        "advance_next": "advance_step",
        "complete": "complete_solution",
        "complete_solution": "complete_solution",
        "full_solution": "complete_solution",
        "finish": "complete_solution",
        "done": "complete_solution",
        "repair": "repair_plan",
    }
    normalized = aliases.get(raw, raw)
    if normalized in {"none", "keep_current_step", "advance_step", "complete_solution", "branch", "repair_plan", "reset_plan"}:
        return normalized
    return _default_plan_signal(intent)


def _normalize_visible_reply(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("visible_reply must be a string")
    return value.strip()


def _context_with_guidance_contract(context: dict[str, Any]) -> dict[str, Any]:
    if isinstance(context.get("guidance_contract"), dict):
        return context
    workspace = context.get("workspace") if isinstance(context.get("workspace"), dict) else {}
    guidance_depth = str(workspace.get("guidance_depth") or context.get("guidance_depth") or "direct")
    return {**context, "guidance_contract": _guidance_contract(guidance_depth)}


def _guidance_contract(guidance_depth: str) -> dict[str, Any]:
    base = {
        "guidance_depth": guidance_depth,
        "code_policy": "complete_code_only_when_user_explicitly_asks",
        "visible_reply_policy": "answer_requested_scope_like_normal_model",
        "interaction_policy": "direct_answers_question_without_checkpoint_gating",
        "solution_scope_policy": "when_user_requests_solution_or_full_explanation_cover_problem_understanding_algorithm_derivation_steps_complexity_and_implementation_notes",
        "plan_completion_policy": "if_visible_reply_covers_complete_solution_emit_complete_solution_plan_signal",
    }
    if guidance_depth == "coached":
        return {
            **base,
            "code_policy": "no_complete_code",
            "visible_reply_policy": "acknowledge_user_turn_then_give_key_observation_and_one_checkpoint",
            "interaction_policy": "respond_to_user_specific_content_before_any_question",
            "max_checkpoint_questions": 1,
        }
    if guidance_depth in {"socratic", "minimal"}:
        return {
            **base,
            "code_policy": "no_complete_code",
            "visible_reply_policy": "acknowledge_user_turn_then_give_small_hint_or_one_question",
            "interaction_policy": "do_not_only_ask_questions_first_anchor_to_the_user_message",
            "max_checkpoint_questions": 1,
        }
    return base


def _enforce_guidance_visible_reply(text: str, context: dict[str, Any]) -> str:
    contract = context.get("guidance_contract") if isinstance(context.get("guidance_contract"), dict) else {}
    guidance_depth = str(contract.get("guidance_depth") or "").strip().lower()
    if contract.get("code_policy") != "no_complete_code":
        return text
    without_fences = re.sub(r"```[\s\S]*?```", "", text).strip()
    lines = [
        line
        for line in without_fences.splitlines()
        if not _looks_like_complete_code_line(line)
    ]
    cleaned = "\n".join(line for line in lines if line.strip()).strip()
    if context.get("response_language") == "en":
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if part.strip()]
        if guidance_depth == "minimal":
            return _anchor_non_direct_reply(sentences[0].strip() if sentences else "", context, guidance_depth=guidance_depth)
        if guidance_depth == "coached":
            return _anchor_non_direct_reply(" ".join(sentences[:2]), context, guidance_depth=guidance_depth)
        if guidance_depth == "socratic":
            question = next((sentence.strip() for sentence in sentences if "?" in sentence), "")
            grounding = next((sentence.strip() for sentence in sentences if "?" not in sentence), "")
            reply = " ".join(part for part in (grounding, question) if part)
            if not question and reply:
                reply += " What would you check next?"
            return _anchor_non_direct_reply(reply, context, guidance_depth=guidance_depth)
    if guidance_depth in {"socratic", "minimal"}:
        return _anchor_non_direct_reply(_socratic_visible_reply(cleaned, context), context, guidance_depth=guidance_depth)
    if guidance_depth == "coached":
        return _anchor_non_direct_reply(_coached_visible_reply(cleaned), context, guidance_depth=guidance_depth)
    return cleaned


def _enforce_direct_visible_reply(text: str, context: dict[str, Any]) -> str:
    return text


def _current_step_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    plan_state = context.get("plan_state") if isinstance(context.get("plan_state"), dict) else {}
    current_step_key = plan_state.get("current_step_key")
    step_chain = plan_state.get("step_chain")
    if not isinstance(current_step_key, str) or not isinstance(step_chain, list):
        return {}
    for step in step_chain:
        if isinstance(step, dict) and step.get("step_key") == current_step_key:
            return step
    return {}


def _socratic_visible_reply(text: str, context: dict[str, Any]) -> str:
    cleaned = _remove_solution_revealing_sentences(text)
    existing_question = _through_first_question(cleaned)
    repeated_existing_question = bool(existing_question) and _is_repeated_assistant_question(existing_question, context)
    if existing_question and not repeated_existing_question:
        return existing_question
    first_hint = "" if repeated_existing_question else _first_sentence(cleaned)
    return first_hint


def _anchor_non_direct_reply(text: str, context: dict[str, Any], *, guidance_depth: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    if not _looks_like_question_only(cleaned):
        return cleaned
    anchor = _followup_anchor_sentence(context, guidance_depth=guidance_depth)
    if not anchor or cleaned.startswith(anchor):
        return cleaned
    return f"{anchor}{cleaned}"


def _looks_like_question_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    question_count = stripped.count("？") + stripped.count("?")
    if question_count == 0:
        return False
    sentence_count = sum(1 for char in stripped if char in "。！？.!?")
    return question_count >= max(1, sentence_count)


def _followup_anchor_sentence(context: dict[str, Any], *, guidance_depth: str) -> str:
    message = str(context.get("user_message") or "").strip()
    if not message:
        return ""
    if context.get("response_language") == "en":
        question = message.rsplit("Student question:", 1)[-1].strip()
        focus = re.sub(r"\s+", " ", question).strip(" .!?")[:60]
        return f"Your question is about “{focus}”. " if focus else "Let's focus on this step. "
    quoted_focus = _compact_user_focus(message)
    if guidance_depth in {"socratic", "minimal", "coached"}:
        return f"你问的是{quoted_focus}。"
    return ""


def _compact_user_focus(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", message).strip(" 。！？!?")
    for prefix in ("这里我想慢一点，", "这里我想慢一点,", "我想慢一点，", "我想慢一点,", "为什么"):
        cleaned = cleaned.removeprefix(prefix)
    if len(cleaned) > 44:
        cleaned = f"{cleaned[:43].rstrip()}…"
    return cleaned or "当前这个卡点"


def _coached_visible_reply(text: str) -> str:
    sentences = _sentences(text)
    if len(sentences) <= 2:
        return text
    return "".join(sentences[:2]).strip()


def _remove_solution_revealing_sentences(text: str) -> str:
    forbidden_tokens = (
        "答案就是",
        "最终答案",
        "最少次数就是",
        "完整思路",
        "代码如下",
        "实现如下",
        "以下是",
        "程序如下",
        "完整代码",
        "C语言程序",
    )
    kept = [
        sentence
        for sentence in _sentences(text)
        if not any(token in sentence for token in forbidden_tokens)
    ]
    return "".join(kept).strip()


def _first_sentence(text: str) -> str:
    sentences = _sentences(text)
    return sentences[0].strip() if sentences else ""


def _through_first_question(text: str) -> str:
    match = re.search(r"^([\s\S]*?[？?])", text)
    return match.group(1).strip() if match else ""


def _sentences(text: str) -> list[str]:
    return [
        match.group(0)
        for match in re.finditer(r"[^。！？!?]+[。！？!?]?", text)
        if match.group(0).strip()
    ]


def _is_repeated_assistant_question(question: str, context: dict[str, Any]) -> bool:
    normalized_question = _normalize_question_for_repeat(question)
    if not normalized_question:
        return False
    recent_messages = context.get("recent_messages")
    if not isinstance(recent_messages, list):
        return False
    for message in recent_messages[-6:]:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "")
        if _normalize_question_for_repeat(content) == normalized_question:
            return True
    return False


def _normalize_question_for_repeat(text: str) -> str:
    return re.sub(r"\s+", "", text.strip().rstrip("？?。.!！"))


def _looks_like_complete_code_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    code_patterns = [
        r"^(def|class)\s+\w+",
        r"^#include\b",
        r"^using\s+namespace\b",
        r"^(int|long|void|bool|string|auto)\s+\w+\s*\(",
        r"^(for|while|if)\s*\(",
        r"^return\b",
        r"^(cin|cout)\b",
        r"^\w+\s*=\s*.+;$",
    ]
    return any(re.search(pattern, stripped) for pattern in code_patterns)


def _contains_diagram_source_block(text: str) -> bool:
    return bool(re.search(r"<\s*diagram_scene\b", text, flags=re.IGNORECASE))


def _strip_diagram_source_blocks(text: str) -> str:
    return re.sub(
        r"<\s*diagram_scene\b[^>]*>[\s\S]*?<\s*/\s*diagram_scene\s*>",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def _diagram_goal_from_context(context: dict[str, Any]) -> str:
    problem_context = context.get("problem_context") if isinstance(context.get("problem_context"), dict) else {}
    summary = problem_context.get("problem_summary")
    english = context.get("response_language") == "en"
    if isinstance(summary, str) and summary.strip():
        if english:
            return f"Use a diagram to explain the key change in this problem: {summary[:80]}"
        return f"用图解释当前题目的关键状态变化：{summary[:80]}"
    if english:
        return "Use a diagram to explain the key change in the current step"
    return "用图解释当前步骤的关键状态变化"


def _explicit_problem_id(context: dict[str, Any]) -> int | None:
    problem_context = context.get("problem_context") if isinstance(context.get("problem_context"), dict) else {}
    value = problem_context.get("problem_id")
    return value if isinstance(value, int) and value > 0 else None


def _context_has_explicit_problem_id(context: dict[str, Any]) -> bool:
    return _explicit_problem_id(context) is not None


def _context_has_ready_problem_text(context: dict[str, Any]) -> bool:
    problem_context = context.get("problem_context") if isinstance(context.get("problem_context"), dict) else {}
    return bool(problem_context.get("problem_id")) and bool(str(problem_context.get("problem_summary") or "").strip())


def _force_visual_need(visual_need: VisualNeedBlock, *, diagram_goal: str) -> VisualNeedBlock:
    if visual_need.need_diagram:
        return visual_need.model_copy(update={"diagram_goal": visual_need.diagram_goal or diagram_goal})
    return visual_need.model_copy(
        update={
            "need_diagram": True,
            "diagram_goal": visual_need.diagram_goal or diagram_goal,
            "visual_focus": visual_need.visual_focus or "当前用户要求图示讲解的关键变化",
            "priority": "normal",
        }
    )


def _complete_model_selected_submission(request: MCPRequestBlock, context: dict[str, Any]) -> MCPRequestBlock:
    """Copy exact code data only after the model has selected the submission tool."""
    message = str(context.get("user_message") or "")
    code_match = re.search(r"```([^\r\n`]*)\r?\n([\s\S]*?)```", message)
    if code_match is None:
        return request
    language = code_match.group(1).strip() or None
    code = code_match.group(2).strip()
    return request.model_copy(update={
        "problem_id": request.problem_id or _explicit_problem_id(context),
        "language": request.language or language,
        "code": code or request.code,
    })


def _strip_diagram_promises(text: str) -> str:
    cleaned = text
    patterns = [
        r"我会画图说明[。,.，]?\s*",
        r"我会生成图示[。,.，]?\s*",
        r"稍后我会画图[。,.，]?\s*",
        r"图示正在生成[，,。.]?(?:将展示|会展示|稍后展示)?[^。！？!?]*[。！？!?]?",
        r"图会在(?:右侧|下方|稍后)[^。！？!?]*[。！？!?]?",
        r"我会在(?:右侧|下方|稍后)[^。！？!?]*展示图[^。！？!?]*[。！？!?]?",
        r"(?:注[:：]?\s*)?因无法直接生成图示[^。！？!?]*[。！？!?]?",
        r"(?:注[:：]?\s*)?无法直接生成图示[^。！？!?]*[。！？!?]?",
        r"(?:注[:：]?\s*)?(?:这里只能|只能)用文字描述[^。！？!?]*[。！？!?]?",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned)
    return cleaned.strip()


def _main_dialog_contract(
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = {
        "output": {
            "intent_state": "MainIntentStateBlock",
            "visible_reply": "string",
            "visual_need": "VisualNeedBlock optional for non_problem",
            "knowledge_deposit_hint": "KnowledgeDepositHintBlock optional for non_problem",
            "mcp_request": "MCPRequestBlock; set need_mcp=true when platform data should be queried",
        },
        "rules": [
            "MainDialogAgent owns all user-visible natural language.",
            "Do not hard-code problem-specific templates.",
            "Respect context.guidance_contract.",
            "Before explaining a concrete sample, verify each stated numerical comparison and structural relation against the given input; do not infer a violation from a valid comparison. For ordered trees, check inherited ancestor bounds as well as direct parent-child relations.",
            "Read context.learning_progress.recent_turns and context.recent_messages to decide what the student has already answered. These are conversation evidence, not preclassified user intents. Do not repeat answered questions.",
            "When guidance is direct, answer the requested scope in one turn with the core conclusion and necessary details; do not gate progress with one checkpoint at a time.",
            "For direct guidance, if the user asks for a solution, full explanation, full idea, or direct walkthrough, provide 完整题意理解, 关键观察, 完整解法步骤, 推导/正确性理由, 复杂度, and implementation notes; include complete code only when the user explicitly asks for code.",
            "If the latest direct user request explicitly asks for code, visible_reply must include the actual complete solution code in a fenced Markdown code block with the requested language. Do not replace code with a description such as 'use a custom comparator' or 'code idea'.",
            "For direct guidance that visibly covers the complete solution flow, use plan_signal=complete_solution so the plan sidecar can mark the whole chain covered.",
            "Classify only user turn state: new_problem, continue_problem, non_problem, state_change.",
            "For new_problem or continue_problem when there is no reliable active problem summary, set intent_state.problem_summary to a concise summary of the actual problem using the current turn and recent messages. Do not infer that a quoted problem is the user's intent without considering the whole request.",
            "If the user asks for platform-backed information such as problem statement by id, submission details, personal user info/id, personal stats, ranking, or test data, set mcp_request.need_mcp=true and choose the matching resource.",
            "Available MCP resource routing: problem_search, problem_detail, problem_submissions, submission_detail, submit_code_feedback, user_info, user_stats, user_weekly_submissions, contest_list, contest_info, contest_problems_and_status, contest_rank_list, contest_submissions, class_info, class_list, my_class_list, class_contest_list, class_problem_list, platform_query.",
            "Use the matching request fields: problem_id, submission_id, contest_id, class_id, title_search, id_search, author_search, tag_names, class_name, course_name, result, and filters. Extract identifiers and filters from the user's request when present.",
            "When the user explicitly asks to submit supplied code to the judge and get feedback, use resource=submit_code_feedback and include problem_id, language_id, and the exact user-provided code; never submit code unless the user explicitly requested submission.",
            "If the platform-backed request is asking how to solve/explain a problem id and the problem text is available in context.problem_context, classify it as a problem-teaching turn, not as non_problem.",
            "For platform/tool requests, do not teach the user how to call MCP, do not ask Socratic questions about MCP command syntax, and do not claim a tool result before the tool has been executed by the runtime.",
            "For requests about the current logged-in user's id/profile/email/school/student id, use mcp_request.resource=user_info.",
            "If context.problem_context.problem_id is present and the user asks about the current platform problem, set mcp_request.resource=problem_detail with that problem_id.",
            "An active problem context must not replace a different explicit platform request such as submission_detail, contest_rank_list, user_info, or class_list.",
            "Casual greetings or small talk, including a user saying they want to chat before doing problems, must be non_problem with should_start_new_workspace=false and plan_signal=none.",
            "If the latest user message is unrelated to the active problem, classify non_problem and do not advance the plan.",
            "For non_problem, still write a natural reply to the user and gently invite a learning question when appropriate. Do not block the turn or replace it with a fixed tutoring response.",
            "For direct guidance, give the core conclusion first and do not respond mainly with a question.",
            "For coached, socratic, and minimal guidance, do not output complete solution code, code fences, function definitions, or final implementation.",
            "For coached, socratic, and minimal guidance, 先回应用户刚才的具体发言 before asking or hinting; 不要只反问, do not sound like a question-only machine.",
            "For coached guidance, briefly acknowledge the user's current point, give a key observation, then at most one next-step checkpoint instead of a full solution.",
            "For socratic guidance, briefly reflect what the user said, give only enough grounding to make the question meaningful, then ask one guiding question; do not reveal the full solution.",
            "For minimal guidance, briefly anchor to the user's point and give the smallest useful hint; a question is optional, not mandatory.",
            "If the user previously received a direct full walkthrough and then switches to a smaller guidance depth to ask about a middle step, keep the full-flow history as covered but set target_step_key/plan_signal to the relevant middle step rather than the final step.",
            "Do not promise diagram or whiteboard work in visible_reply unless the corresponding block requests it. Any explicit request to draw a figure (including 画一张图/画一幅图/绘制/示意图) requires visual_need.need_diagram=true.",
            "Decide when a diagram materially helps: spatial relationships, changes across states, an algorithm invariant, or a student's specific confusion are good reasons. If the student says they cannot picture a transformation (cannot picture), strongly prefer a before-and-after diagram even without the word diagram. Do not draw by default for every named data structure or for answers already clear in text. Explicit drawing requests always set visual_need.need_diagram=true.",
            "A standalone algorithm or data-structure concept question is a teaching turn even without an attached OJ problem. If a diagram helps, set visual_need.need_diagram=true and a concrete diagram_goal; non_problem is reserved for casual chat or unrelated requests.",
            "When drawing, set a concrete diagram_goal and visual_focus describing what the picture should clarify. You may offer a suggested_case when a particular example helps, but the drawing agent chooses the final category and case; do not prescribe an unrequested number of objects or claim a hypothetical example is the problem input.",
            "When visual_need.need_diagram=true, write visible_reply as the opening of one continuous explanation: establish the rule or question that the upcoming diagram will make visible. Leave observations that depend on the actual diagram, and the next teaching question, for the post-diagram continuation. Respect the selected guidance depth in both parts.",
            "knowledge_deposit_hint is for whiteboard memory, not a conversation abstract. Set can_deposit=true only when this turn produced a reusable conclusion, rule, formula, method, boundary condition, common mistake, or worked mini-example.",
            "knowledge_deposit_hint.candidate_summary must state the learned takeaway itself as a compact 可复用结论, preferably with the decisive condition/action. 不要写成 topic titles such as '影响分析', '过程总结', '题目讲解', '思路梳理', or '<topic>分析'.",
            "Compare the current takeaway with context.whiteboard_refs. If it only repeats or paraphrases an existing whiteboard conclusion, set knowledge_deposit_hint.can_deposit=false. If it adds a reusable insight, candidate_summary must state that incremental insight without restarting with the problem name or recapping earlier cards. Keep the language consistent with context.response_language.",
            STANDARD_MATH_MARKDOWN_RULE,
        ],
    }
    if context and context.get("response_language") == "en":
        contract["rules"].append(
            "English demo: visible_reply and all student-facing text MUST be in English for all four guidance depths. "
            "Socratic: one brief grounding sentence and one guiding question; no full solution. "
            "Coached: acknowledge the student's point, give one key observation, then at most one checkpoint. "
            "Minimal: one short useful hint with no full solution. Direct: answer the requested scope fully."
        )
    return contract


def _preview_raw_output(raw_output: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for key, value in raw_output.items():
        if isinstance(value, str):
            preview[key] = value[:240]
        elif isinstance(value, dict):
            preview[key] = {
                str(nested_key): (nested_value[:120] if isinstance(nested_value, str) else nested_value)
                for nested_key, nested_value in value.items()
            }
        else:
            preview[key] = value
    return preview
