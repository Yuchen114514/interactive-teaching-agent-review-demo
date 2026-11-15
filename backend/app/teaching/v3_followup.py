"""One bounded MainDialog continuation after a verified inline diagram."""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from app.config import Settings

MODE_INSTRUCTIONS = {
    "socratic": "只提一个与图中具体变化有关的问题；不要直接给完整解法。",
    "coached": "先解释图中的一个关键变化，再提出至多一个自然的下一步问题。",
    "minimal": "只用一两句简短文字指出图中要点，不展开完整解法。",
    "direct": "直接说明图中变化的结果与原因，不强行反问学生。",
}
ENGLISH_MODE_INSTRUCTIONS = {
    "socratic": "Ask one question about a specific change in the diagram; do not give the full solution.",
    "coached": "Explain one key change, then ask at most one natural next-step question.",
    "minimal": "Use one or two short sentences to point out the diagram's key idea.",
    "direct": "Explain the result and cause of the diagram's change without forcing a question.",
}


def scene_synopsis(scene: dict[str, Any], *, language: str = "zh") -> str:
    semantic = (scene.get("metadata_json") or {}).get("semantic_scene")
    if isinstance(semantic, dict):
        frames = semantic.get("frames")
        if isinstance(frames, list) and frames:
            last = frames[-1]
            if isinstance(last, dict):
                containers = last.get("containers")
                labels = [str(item.get("label")) for container in containers if isinstance(container, dict)
                          for item in container.get("items", []) if isinstance(item, dict)] if isinstance(containers, list) else []
                return "；".join(filter(None, [str(semantic.get("title") or ""), str(last.get("title") or ""),
                                          str(last.get("note") or ""), ("Final frame items: " if language == "en" else "末帧元素：") + "、".join(labels[:16]) if labels else ""]))[:800]
    steps = scene.get("steps")
    if isinstance(steps, list) and steps:
        last = steps[-1]
        if isinstance(last, dict):
            return "；".join(filter(None, [str(scene.get("title") or ""), str(last.get("title") or ""), str(last.get("teaching_note") or "")]))[:800]
    return str(scene.get("title") or "图示已完成")[:800]


def followup_input(*, guidance_depth: str, first_answer: str, diagram_goal: str, scene: dict[str, Any], language: str = "zh") -> dict[str, Any]:
    depth = guidance_depth if guidance_depth in MODE_INSTRUCTIONS else "direct"
    metadata = scene.get("metadata_json") or {}
    plan = metadata.get("teaching_plan") or {}
    trace_spec = metadata.get("trace_spec") or {}
    semantic_scene = metadata.get("semantic_scene") or {}
    return {
        "guidance_depth": depth,
        "language": "en" if language == "en" else "zh",
        "mode_instruction": (ENGLISH_MODE_INSTRUCTIONS if language == "en" else MODE_INSTRUCTIONS)[depth],
        "first_answer": first_answer,
        "diagram_goal": diagram_goal,
        "scene_synopsis": scene_synopsis(scene, language=language),
        "teaching_focus": str(plan.get("teaching_focus") or ""),
        "teaching_paradigm": str(plan.get("teaching_paradigm") or ""),
        "case_origin": str(trace_spec.get("case_origin") or semantic_scene.get("case_origin") or ""),
        "case_note": str(trace_spec.get("case_note") or semantic_scene.get("case_note") or ""),
        "scene_semantics": json.dumps(
            (scene.get("metadata_json") or {}).get("semantic_scene") or {"steps": scene.get("steps") or []},
            ensure_ascii=False, separators=(",", ":"),
        )[:6000],
        "scene_id": str(scene.get("scene_id") or ""),
    }


def generate_followup(payload: dict[str, Any], provider: Callable[[dict[str, Any]], str] | None) -> str:
    try:
        answer = provider(payload) if provider is not None else ""
    except Exception:
        answer = ""
    if isinstance(answer, str) and answer.strip():
        return answer.strip()[:1600]
    if payload.get("language") == "en":
        depth = payload.get("guidance_depth")
        if depth == "socratic":
            return "What change in the diagram would you examine next?"
        if depth == "coached":
            return "The diagram shows the current state. Which part should change next?"
        if depth == "minimal":
            return "The diagram shows the current state."
        return "The diagram shows the result of this step."
    synopsis = str(payload.get("scene_synopsis") or "图示展示了当前状态变化").split("；")[-1]
    depth = payload.get("guidance_depth")
    if depth == "socratic":
        return f"观察图中的{synopsis}，下一步应关注哪个状态？"
    if depth == "coached":
        return f"图中{synopsis}。接下来你会先更新哪一部分？"
    if depth == "minimal":
        return f"图中{synopsis}。"
    return f"图中{synopsis}，这就是当前步骤的结果。"


def stream_followup(payload: dict[str, Any], provider: Callable[[dict[str, Any]], Any] | None):
    emitted = False
    try:
        result = provider(payload) if provider is not None else None
        chunks = [result] if isinstance(result, str) else result
        if chunks is not None:
            for chunk in chunks:
                if not isinstance(chunk, str) or not chunk:
                    continue
                emitted = True
                yield chunk
    except Exception:
        if emitted:
            return
    if not emitted:
        yield generate_followup(payload, None)


def create_followup_provider(settings: Settings) -> Callable[[dict[str, Any]], Any] | None:
    if not settings.teaching_agent_providers_enabled or not settings.external_llm_api_key:
        return None

    def provider(payload: dict[str, Any]):
        english = payload.get("language") == "en" or settings.demo_mode
        system_prompt = (
            "You are the main teaching Agent. Write only the student-visible continuation after a verified diagram. "
            "Do not call tools, delegate to other agents, request another diagram, or claim to see unverified details. "
            "Check node, edge, position, and numeric relationships against the verified scene. "
            "Continue the same answer: connect the diagram's actual change to the opening explanation, "
            "then complete the teaching move appropriate to the selected mode. If the example was invented, label it as an example. "
            "Do not repeat the first answer or restart the explanation. Use one or two natural sentences in English."
            if english else
            "你是主教学智能体。只生成图示之后承接上文的一段学生可见回复。"
            "不得再调用工具、分派其他智能体、要求重新绘图或假称看到了未验证的细节。"
            "以已校验图示的节点、边、位置和说明为事实依据；首段回复若与图示冲突，纠正而不要复述错误。"
            "写出数值关系、祖先约束或父子关系前，逐项核对图示语义数据；不要把合法关系说成违规，也不要混淆上界和下界。"
            "把图中真实发生的变化接回首段讲解，再完成当前教学模式需要的结论或下一步问题。"
            "若案例由绘图智能体自拟，明确说是示例，不要当作题目原始输入。"
            "不要重复首段回复，也不要重新开篇。只输出一两句自然的讲解或下一步问题，使用简体中文。"
        )
        user_prompt = (
            "Teaching mode: " + str(payload["guidance_depth"]) + ". " + str(payload["mode_instruction"])
            + "\nFirst answer: " + str(payload["first_answer"])
            + "\nDiagram goal: " + str(payload["diagram_goal"])
            + "\nDrawing focus: " + str(payload.get("teaching_focus") or "")
            + "\nTeaching paradigm: " + str(payload.get("teaching_paradigm") or "")
            + "\nExample origin: " + str(payload.get("case_origin") or "") + "; " + str(payload.get("case_note") or "")
            + "\nVerified diagram summary: " + str(payload["scene_synopsis"])
            + "\nDiagram semantics: " + str(payload.get("scene_semantics") or "")
            if english else
            "教学模式：" + str(payload["guidance_depth"]) + "。" + str(payload["mode_instruction"])
            + "\n首段回复：" + str(payload["first_answer"])
            + "\n图示目标：" + str(payload["diagram_goal"])
            + "\n绘图焦点：" + str(payload.get("teaching_focus") or "")
            + "\n讲解范式：" + str(payload.get("teaching_paradigm") or "")
            + "\n案例来源：" + str(payload.get("case_origin") or "") + "；" + str(payload.get("case_note") or "")
            + "\n已校验图示摘要：" + str(payload["scene_synopsis"])
            + "\n图示语义数据：" + str(payload.get("scene_semantics") or "")
        )
        with httpx.stream(
            "POST",
            f"{settings.external_llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.external_llm_api_key}"},
            json={
                "model": settings.external_llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1200,
                "stream": True,
            },
            timeout=settings.external_llm_timeout_seconds,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content

    return provider
