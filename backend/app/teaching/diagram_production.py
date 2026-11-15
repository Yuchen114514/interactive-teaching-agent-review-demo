"""Verified semantic diagrams for the production Agent conversation."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

import httpx

from app.config import Settings

from app.teaching.diagram_engine.scene_core import STRUCTURES, normalize_scene, render_scene, validate_scene
from app.teaching.diagram_engine.scenario_core import render_scenario, validate_scenario
from app.teaching.diagram_engine.trace_engine import MAX_TRACE_FRAMES, generate_trace, verify_trace
from app.teaching.v3_diagram import DiagramAgent, _data_structure_scene_contract, _request_context

PRODUCER_VERSION = "semantic-v1"
RENDERER_VERSION = "svg-v1"
logger = logging.getLogger(__name__)

ROUTER_PROMPT = (
    "你是独立的算法教学绘图智能体。主智能体提出绘图目标与可选案例，但最终由你根据用户问题、题目上下文、"
    "首段回复和教学模式决定图形类别、讲解焦点、具体案例与所需帧数。只输出 JSON："
    "{\"views\":[\"结构类型\"],\"reason\":\"简短理由\",\"teaching_focus\":\"图要讲清的具体变化\","
    "\"teaching_paradigm\":\"single_state 或 comparison 或 key_steps 或 full_trace\","
    "\"trace_algorithm\":null或\"hanoi\"或\"dag_topological\"或\"prim\"}。"
    "根据概念、状态变化和教学意图选能直接表达的结构容器；同类多个容器须重复。"
    "若输入是层序数组且需要说明树，同时选 array、tree。待验证的树用 tree，不预设其满足 bst。"
    "结构可用时选结构视图；只有情境或抽象关系更能解释目标时才用通用情境图。"
    "不要按题号、题名或数字套模板，也不要把主智能体建议的案例当成必须照画的指令。"
    "若完整过程比关键帧更有教学价值，且算法受支持，选择 trace_algorithm；缺少具体数据时可以自拟"
    "一个小而有代表性的教学案例并明确标为示例。用户指定的条件不得擅自更改。"
)
TRACE_SPEC_PROMPT = (
    "你是算法教学绘图智能体的案例设计器。根据绘图目标和上下文选择能解释关键机制的具体案例；"
    "主智能体的建议仅作参考。只输出 JSON，不输出轨迹帧。"
    "汉诺塔返回 algorithm=hanoi、rods(杆名列表)、disks(盘片数)、source、target。"
    "DAG 拓扑遍历返回 algorithm=dag_topological、nodes(节点名列表)、edges([起点,终点]列表)。"
    "Prim 返回 algorithm=prim、nodes、edges({from,to,weight}对象列表)、start。"
    "额外返回 case_origin=given/mixed/illustrative 和 case_note（一句明确标注哪些条件来自题目、"
    "哪些是为讲解选取的示例）。case_origin=given 仅限案例全部条件明确出现在当前或近期的用户原话、或题目正文中；"
    "主智能体建议的数字、杆名、节点和首段回复中的自选设定都不是题目给定条件，必须标为 mixed 或 illustrative。"
    "给定的数据必须保持原样；缺失的数据可用有代表性的小案例补齐，"
    "优先使全过程在帧数上限内完整呈现。不要把自拟示例说成题目原始输入。"
    "若给定条件与完整轨迹无法兼容，返回包含 error 的 JSON 并解释，不得静默删减或伪造步骤。"
)
STRUCTURED_PROMPT = (
    "你是算法课的语义绘图规划器。只输出 JSON，不输出 SVG、坐标、代码或 Markdown。"
    "格式：{\"structure\":\"结构类型或multi\",\"title\":\"标题\",\"case_origin\":\"given/mixed/illustrative\",\"case_note\":\"案例来源说明\",\"frames\":[{\"id\":\"稳定ID\","
    "\"title\":\"本帧变化\",\"note\":\"一句解释\",\"containers\":[{\"id\":\"容器ID\",\"kind\":\"结构类型\","
    "\"label\":\"容器标题\",\"items\":[{\"id\":\"节点ID\",\"label\":\"可见值\"}],\"edges\":[]}],"
    "\"operation\":{\"kind\":\"操作\",\"target\":\"节点ID\"}}]}。"
    "每帧必须恰好包含用户给出的视图清单，顺序一致。多个容器用 multi，节点 ID 在同一帧跨容器不可重复。"
    "树节点用 parent 和 side(left/right)；图用 edges 的 from/to；栈从底到顶，队列及双端队列从 front 到 rear；"
    "杆盘从底到顶且有固定 size，盘片尺寸不可变化。跨结构复制或移动用 transfers，保持稳定 ID 和数值。"
    "优先画题目中的具体值；若没有可画的值，可自主设计一个能说明机制的小示例，"
    "在标题或首帧说明这是示例，并标注 case_origin 与 case_note。"
    "帧数与教学目标相称，关键步骤通常 1 至 3 帧；不要凭空编造题目原始输入、算法步骤或最优性证明。"
)
SCENARIO_PROMPT = (
    "你是算法教学的通用情境绘图规划器。只输出 JSON。"
    "当题目没有可用数据结构视图时，输出 structure=scenario，entities、regions、frames；"
    "每帧 placements 必须列出全部实体且 evidence=conceptual；题目规则引用 quote 必须逐字出现在题面。"
    "优先画可核实的输入与样例关系；若题意抽象、缺少具体数据，可以绘制清楚标注的概念示例，"
    "但不能把自拟示例冒充题面事实，也不能伪造求解过程。"
)

def create_semantic_diagram_provider(settings: Settings) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    if not settings.teaching_agent_providers_enabled or not settings.external_llm_api_key:
        return None

    def ask(system: str, user: str) -> dict[str, Any]:
        if getattr(settings, "demo_mode", False):
            system += ("\nThis is the English demo. Keep all student-visible titles, notes, "
                       "container labels, and explanatory text in English. Preserve input values exactly. "
                       "Keep JSON keys and structure identifiers as specified.")
        for attempt in range(2):
            try:
                response = httpx.post(
                    f"{settings.external_llm_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.external_llm_api_key}"},
                    json={"model": settings.external_llm_model,
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          "response_format": {"type": "json_object"}, "temperature": 0, "max_tokens": 8192},
                    timeout=max(45.0, settings.external_llm_timeout_seconds),
                )
                break
            except httpx.ReadTimeout:
                if attempt:
                    raise
        response.raise_for_status()
        answer = json.loads(response.json()["choices"][0]["message"]["content"] or "")
        if not isinstance(answer, dict):
            raise ValueError("绘图模型未返回 JSON 对象")
        return answer

    def provider(payload: dict[str, Any]) -> dict[str, Any]:
        context = payload.get("context") or {}
        problem = context.get("problem_context") or {}
        problem_text = str(problem.get("problem_summary") or "")[:2400] if isinstance(problem, dict) else ""
        examples = problem.get("examples") if isinstance(problem, dict) else None
        constraints = problem.get("constraints") if isinstance(problem, dict) else None
        recent = context.get("recent_messages")
        recent_excerpt = [
            {"role": item.get("role"), "content": str(item.get("content") or "")[:450]}
            for item in (recent[-4:] if isinstance(recent, list) else [])
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
        ]
        thread = context.get("problem_thread_summary") or {}
        user_message = str(context.get("user_message") or "")
        if len(user_message) > 1500:
            user_message = user_message[:900] + "\n…\n" + user_message[-590:]
        request = "\n".join(filter(None, [
            "题目：" + problem_text if problem_text else "",
            "用户提问：" + user_message,
            "首段回复：" + str((context.get("main_blocks") or {}).get("visible_reply") or "")[:700],
            "图示目标：" + str((payload.get("visual_need") or {}).get("diagram_goal") or ""),
            "讲解焦点：" + str((payload.get("visual_need") or {}).get("visual_focus") or ""),
            "主智能体建议案例（可调整）：" + str((payload.get("visual_need") or {}).get("suggested_case") or ""),
            "教学模式：" + str((context.get("guidance_contract") or {}).get("guidance_depth") or ""),
            "题目样例：" + json.dumps(examples, ensure_ascii=False)[:1100] if examples else "",
            "题目约束：" + json.dumps(constraints, ensure_ascii=False)[:600] if constraints else "",
            "近期会话：" + json.dumps(recent_excerpt, ensure_ascii=False)[:1500] if recent_excerpt else "",
            "当前疑惑：" + json.dumps(thread.get("open_confusions"), ensure_ascii=False)[:450]
            if isinstance(thread, dict) and thread.get("open_confusions") else "",
        ]))[:7500]
        route = ask(ROUTER_PROMPT + "\n可选结构：" + ", ".join(sorted(STRUCTURES - {"multi"})), request)
        views = route.get("views")
        if not isinstance(views, list) or len(views) > 12 or any(view not in STRUCTURES - {"multi"} for view in views):
            raise ValueError("绘图视图规划结果无效")
        trace_algorithm = route.get("trace_algorithm")
        if trace_algorithm in {"hanoi", "dag_topological", "prim"}:
            trace_request = request + "\n完整过程算法：" + str(trace_algorithm)
            for attempt in range(2):
                try:
                    spec = ask(TRACE_SPEC_PROMPT, trace_request)
                    if spec.get("algorithm") != trace_algorithm:
                        raise ValueError(str(spec.get("error") or "轨迹输入算法不匹配"))
                    if spec.get("case_origin") not in {"given", "mixed", "illustrative"}:
                        raise ValueError("缺少案例来源标注")
                    if not isinstance(spec.get("case_note"), str) or not spec["case_note"].strip():
                        raise ValueError("缺少案例说明")
                    spec["language"] = "en" if getattr(settings, "demo_mode", False) else "zh"
                    trace_scene = generate_trace(spec)
                    errors = verify_trace(trace_scene, spec)
                    if errors:
                        raise ValueError("；".join(errors[:3]))
                    return {"trace_spec": spec, "teaching_plan": _teaching_plan(route)}
                except (ValueError, KeyError, TypeError) as exc:
                    if attempt:
                        raise ValueError("绘图案例无法验证：" + str(exc)) from exc
                    trace_request += "\n上次案例错误：" + str(exc) + "。请重新选取或补齐可完整验证的案例；保留用户已给条件。"
        if views:
            scene = ask(STRUCTURED_PROMPT, request + "\n规划视图清单：" + json.dumps(views, ensure_ascii=False))
            scene["language"] = "en" if getattr(settings, "demo_mode", False) else "zh"
            return {"data_structure_scene": scene, "planned_views": views, "teaching_plan": _teaching_plan(route)}
        scene = ask(SCENARIO_PROMPT, request)
        scene["problem_text"] = problem_text
        scene["language"] = "en" if getattr(settings, "demo_mode", False) else "zh"
        return {"semantic_scene": scene, "planned_views": [], "teaching_plan": _teaching_plan(route)}

    return provider


def _teaching_plan(route: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(route.get(key) or "")[:300]
        for key in ("reason", "teaching_focus", "teaching_paradigm")
    }


def _candidate_scene(raw: dict[str, Any]) -> dict[str, Any] | None:
    candidate = raw.get("semantic_scene") or raw.get("data_structure_scene")
    if not isinstance(candidate, dict) and isinstance(raw.get("frames"), list):
        candidate = raw
    if not isinstance(candidate, dict):
        return None
    scene = dict(candidate)
    frames = scene.get("frames")
    if not isinstance(frames, list):
        return None
    scene["frames"] = [
        {**frame, "id": frame.get("id") or frame.get("frame_id") or f"f{index}"}
        if isinstance(frame, dict) else frame
        for index, frame in enumerate(frames)
    ]
    return normalize_scene(scene) if scene.get("structure") != "scenario" else scene


def build_verified_scene_payload(
    raw_scene: dict[str, Any], *, session_uuid: str, workspace_id: int | None,
    source_turn_index: int, diagram_goal: str = "", trace_spec: dict[str, Any] | None = None,
    teaching_plan: dict[str, str] | None = None,
) -> dict[str, Any]:
    scene = _candidate_scene(raw_scene) or raw_scene
    if trace_spec is None and scene.get("case_origin") in {"illustrative", "mixed"} and isinstance(scene.get("case_note"), str):
        frames = scene.get("frames")
        if isinstance(frames, list) and frames and isinstance(frames[0], dict):
            first = {**frames[0]}
            english = scene.get("language") == "en"
            prefix = ("Illustrative example: " if english else "教学示例：") if scene["case_origin"] == "illustrative" else (
                "Example assumptions: " if english else "示例约定：")
            first["note"] = (prefix + scene["case_note"].strip()[:200] + " " + str(first.get("note") or "")).strip()
            scene = {**scene, "frames": [first, *frames[1:]]}
    if scene.get("structure") == "scenario":
        errors = validate_scenario(scene, str(raw_scene.get("problem_text") or ""))
        renderer = render_scenario
    else:
        scene = normalize_scene(scene)
        errors = validate_scene(scene, max_frames=MAX_TRACE_FRAMES)
        renderer = render_scene
    if errors:
        raise ValueError("图示语义校验失败：" + "; ".join(errors[:5]))
    frames = scene["frames"]
    if len(json.dumps(scene, ensure_ascii=False).encode()) > 2_000_000:
        raise ValueError("图示语义数据超过大小限制")
    for index in range(len(frames)):
        svg = renderer(scene, index)
        if not svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"') or len(svg.encode()) > 2_000_000:
            raise ValueError(f"第 {index + 1} 帧无法安全渲染")
    if trace_spec is not None:
        trace_errors = verify_trace(scene, trace_spec)
        if trace_errors:
            raise ValueError("完整过程校验失败：" + "; ".join(trace_errors[:5]))
    digest = hashlib.sha256(
        json.dumps([session_uuid, source_turn_index, diagram_goal, PRODUCER_VERSION], ensure_ascii=False).encode()
    ).hexdigest()[:24]
    scene_id = f"semantic-{digest}"
    steps = [
        {
            "step_id": str(frame["id"]), "order_index": index,
            "title": str(frame.get("title") or scene.get("title") or "图示"),
            "teaching_note": str(frame.get("note") or frame.get("title") or "图示变化"),
            "entry_state": {"nodes": [], "edges": [], "items": []},
            "exit_state": {"nodes": [], "edges": [], "items": []},
            "transition": {"actions": [{"type": "focus", "target_id": str(frame["id"])}]},
        }
        for index, frame in enumerate(frames)
    ]
    return {
        "scene_id": scene_id, "session_id": session_uuid, "workspace_id": workspace_id,
        "source_turn_index": source_turn_index,
        "title": str(scene.get("title") or diagram_goal or "图示")[:255],
        "diagram_kind": str(scene.get("structure") or "custom")[:32],
        "layout": "freeform", "teaching_goal": diagram_goal or str(scene.get("focus") or scene.get("title") or "图示"),
        "default_step_id": str(frames[0]["id"]), "status": "verified", "steps": steps,
        "metadata_json": {
            "semantic_scene": scene, "render_engine": "scenario" if scene.get("structure") == "scenario" else "structured",
            "producer_version": PRODUCER_VERSION, "renderer_version": RENDERER_VERSION,
            "frame_count": len(frames),
            **({"trace_spec": trace_spec} if trace_spec is not None else {}),
            **({"teaching_plan": teaching_plan} if teaching_plan else {}),
        },
    }


def render_verified_frame(payload: dict[str, Any], index: int) -> str:
    metadata = payload.get("metadata_json") or {}
    scene = metadata.get("semantic_scene")
    if not isinstance(scene, dict):
        raise ValueError("场景没有语义帧")
    frames = scene.get("frames")
    if not isinstance(frames, list) or index < 0 or index >= len(frames):
        raise IndexError("图示帧不存在")
    if metadata.get("render_engine") == "scenario":
        return render_scenario(scene, index)
    return render_scene(scene, index)


def production_diagram_events(
    context: dict[str, Any], provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
) -> list[dict[str, Any]]:
    visual_need = ((context.get("main_blocks") or {}).get("visual_need") or {})
    if not isinstance(visual_need, dict) or not visual_need.get("need_diagram"):
        return [{"type": "diagram_status", "status": "skipped"}]
    if provider is None:
        return DiagramAgent().run(context)
    raw = provider({
        "capability": "diagram_handoff", "visual_need": visual_need,
        "data_structure_scene_contract": _data_structure_scene_contract(),
        "request_context": _request_context(context, visual_need), "context": context,
    })
    if isinstance(raw, dict):
        selected_semantic = any(key in raw for key in ("trace_spec", "data_structure_scene", "semantic_scene", "planned_views"))
        trace_spec = raw.get("trace_spec") if isinstance(raw.get("trace_spec"), dict) else None
        if trace_spec is not None:
            try:
                semantic = generate_trace(trace_spec)
                trace_errors = verify_trace(semantic, trace_spec)
                if trace_errors:
                    raise ValueError("；".join(trace_errors[:3]))
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("model-selected trace rejected (%s): %s", type(exc).__name__, str(exc)[:160])
                semantic = None
        else:
            semantic = _candidate_scene(raw)
        if semantic is not None:
            try:
                planned_views = raw.get("planned_views")
                if isinstance(planned_views, list) and planned_views:
                    for frame in semantic.get("frames", []):
                        actual = [item.get("kind") for item in frame.get("containers", []) if isinstance(item, dict)]
                        if actual != planned_views:
                            raise ValueError("绘图内容缺少预检确定的结构视图")
                payload = build_verified_scene_payload(
                    semantic, session_uuid=str(context.get("session_uuid") or ""),
                    workspace_id=context.get("workspace_id") if isinstance(context.get("workspace_id"), int) else None,
                    source_turn_index=int(context.get("source_turn_index") or 0),
                    diagram_goal=str(visual_need.get("diagram_goal") or ""),
                    trace_spec=trace_spec,
                    teaching_plan=raw.get("teaching_plan") if isinstance(raw.get("teaching_plan"), dict) else None,
                )
                return [
                    {"type": "diagram_ready", "diagram_goal": visual_need.get("diagram_goal"), "scene": payload},
                    {"type": "diagram_status", "status": "completed"},
                ]
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("semantic diagram validation rejected (%s): %s", type(exc).__name__, str(exc)[:160])
        if selected_semantic:
            return [{"type": "diagram_status", "status": "failed", "message": "图示未通过语义校验，请重新生成。"}]
    return DiagramAgent(provider=lambda _payload: raw).run(context)
