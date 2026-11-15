from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from app.diagram_protocol import DiagramInlinePayloadModel, DiagramSceneModel, DiagramTeachingBundleModel


DIAGRAM_SCENE_OPEN_PATTERN = re.compile(r"<diagram_scene>\s*", re.IGNORECASE)
DIAGRAM_SCENE_CLOSE_PATTERN = re.compile(r"\s*</diagram_scene>", re.IGNORECASE)
LINEAR_ITEM_KINDS = {"array", "linked_list", "stack", "queue", "deque", "matrix", "table"}
TREE_LIKE_KINDS = {"tree", "heap", "segment_tree", "trie", "union_find", "fenwick"}


def plan_diagram_teaching_bundle(
    *,
    user_message: str,
    assistant_message: str,
) -> dict[str, Any] | None:
    del user_message
    parsed = extract_model_diagram_scene(assistant_message)
    if parsed is None:
        return None

    scene, preface, followup = parsed
    payload = DiagramInlinePayloadModel(
        scene_id=scene.scene_id,
        default_step_id=scene.default_step_id,
        default_step_title=_default_step_title(scene),
        title=scene.title,
        diagram_kind=scene.diagram_kind,
        teaching_goal=scene.teaching_goal,
        step_count=len(scene.steps),
        inline_caption=_default_step_title(scene),
    )
    bundle = DiagramTeachingBundleModel(
        preface=preface,
        inline_payload=payload,
        followup=followup,
        scene=scene,
    )
    return bundle.model_dump(by_alias=True)


def extract_model_diagram_scene(assistant_message: str) -> tuple[DiagramSceneModel, str, str] | None:
    extracted = _extract_first_scene_object(assistant_message)
    if extracted is None:
        return None

    raw_scene, block_start, block_end = extracted
    preface = assistant_message[:block_start].strip()
    followup = _clean_followup(assistant_message[block_end:])
    if not preface:
        return None

    if not isinstance(raw_scene, dict):
        return None
    raw_scene = _normalize_model_scene({**raw_scene})
    raw_scene.setdefault("scene_id", _stable_scene_id(raw_scene))

    try:
        scene = DiagramSceneModel.model_validate(raw_scene)
    except ValidationError:
        return None

    return scene, preface, followup


def _extract_first_scene_object(assistant_message: str) -> tuple[dict[str, Any], int, int] | None:
    open_match = DIAGRAM_SCENE_OPEN_PATTERN.search(assistant_message)
    if open_match is None:
        return None
    decoder = json.JSONDecoder()
    try:
        raw_scene, json_end_offset = decoder.raw_decode(assistant_message[open_match.end() :])
    except json.JSONDecodeError:
        return None
    block_end = open_match.end() + json_end_offset
    close_match = DIAGRAM_SCENE_CLOSE_PATTERN.match(assistant_message[block_end:])
    if close_match is not None:
        block_end += close_match.end()
    return raw_scene, open_match.start(), block_end


def _clean_followup(raw_followup: str) -> str:
    followup = DIAGRAM_SCENE_CLOSE_PATTERN.sub("", raw_followup, count=1).strip()
    if not followup:
        return ""
    lines = [line.strip() for line in followup.splitlines() if line.strip()]
    if lines and all(re.fullmatch(r"</[a-zA-Z_][\w-]*>", line) for line in lines):
        return ""
    return followup


def _default_step_title(scene: DiagramSceneModel) -> str | None:
    if scene.default_step_id:
        for step in scene.steps:
            if step.step_id == scene.default_step_id:
                return step.title
    return scene.steps[0].title if scene.steps else None


def _stable_scene_id(raw_scene: dict[str, Any]) -> str:
    seed = json.dumps(raw_scene, ensure_ascii=False, sort_keys=True)
    return f"scene-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def _normalize_model_scene(raw_scene: dict[str, Any]) -> dict[str, Any]:
    diagram_kind = str(raw_scene.get("diagram_kind") or "custom")
    raw_scene["layout"] = _normalize_layout(raw_scene.get("layout"), diagram_kind)
    steps = raw_scene.get("steps")
    if isinstance(steps, list):
        raw_scene["steps"] = [
            _normalize_model_step(step, diagram_kind, raw_scene["layout"])
            for step in steps
            if isinstance(step, dict)
        ]
    return raw_scene


def _normalize_layout(layout: Any, diagram_kind: str) -> str:
    if layout == "vertical":
        if diagram_kind in {"stack", "queue", "deque"}:
            return diagram_kind
        if diagram_kind in TREE_LIKE_KINDS:
            return "tree"
        return "freeform"
    if layout in {
        "freeform",
        "array",
        "linked_list",
        "stack",
        "queue",
        "deque",
        "tree",
        "graph",
        "matrix",
        "table",
        "flow",
    }:
        return str(layout)
    if diagram_kind in {"array", "linked_list", "stack", "queue", "deque", "matrix", "table", "tree", "graph", "flow"}:
        return diagram_kind
    if diagram_kind in TREE_LIKE_KINDS:
        return "tree"
    return "freeform"


def _normalize_model_step(raw_step: dict[str, Any], diagram_kind: str, layout: str) -> dict[str, Any]:
    step = {**raw_step}
    transform_nodes_to_items = diagram_kind in LINEAR_ITEM_KINDS
    entry_state, entry_id_kind = _normalize_state(
        step.get("entry_state"),
        diagram_kind=diagram_kind,
        layout=layout,
        transform_nodes_to_items=transform_nodes_to_items,
    )
    exit_state, exit_id_kind = _normalize_state(
        step.get("exit_state"),
        diagram_kind=diagram_kind,
        layout=layout,
        transform_nodes_to_items=transform_nodes_to_items,
    )
    step["entry_state"] = entry_state
    step["exit_state"] = exit_state
    id_kind = {**entry_id_kind, **exit_id_kind}
    step["transition"] = _normalize_transition(step.get("transition"), id_kind)
    return step


def _normalize_state(
    raw_state: Any,
    *,
    diagram_kind: str,
    layout: str,
    transform_nodes_to_items: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    state = raw_state if isinstance(raw_state, dict) else {}
    raw_nodes = [node for node in state.get("nodes", []) if isinstance(node, dict)]
    raw_edges = [edge for edge in state.get("edges", []) if isinstance(edge, dict)]
    raw_items = [item for item in state.get("items", []) if isinstance(item, dict)]
    id_kind: dict[str, str] = {}

    if transform_nodes_to_items and raw_nodes and any("x" not in node or "y" not in node for node in raw_nodes):
        items = [_node_like_to_item(node, index, len(raw_nodes), diagram_kind) for index, node in enumerate(raw_nodes)]
        normalized_items = [_normalize_item(item, index) for index, item in enumerate([*items, *raw_items])]
        for item in normalized_items:
            id_kind[item["item_id"]] = "item"
        return {"nodes": [], "edges": [], "items": normalized_items}, id_kind

    normalized_nodes = _normalize_nodes(raw_nodes, raw_edges, diagram_kind, layout)
    for node in normalized_nodes:
        id_kind[node["node_id"]] = "node"
    normalized_edges = _normalize_edges(raw_edges)
    for edge in normalized_edges:
        id_kind[edge["edge_id"]] = "edge"
    normalized_items = [_normalize_item(item, index) for index, item in enumerate(raw_items)]
    for item in normalized_items:
        id_kind[item["item_id"]] = "item"
    return {"nodes": normalized_nodes, "edges": normalized_edges, "items": normalized_items}, id_kind


def _normalize_nodes(raw_nodes: list[dict[str, Any]], raw_edges: list[dict[str, Any]], diagram_kind: str, layout: str) -> list[dict[str, Any]]:
    node_ids = [str(node.get("node_id") or node.get("id") or f"node-{index}") for index, node in enumerate(raw_nodes)]
    inferred_positions = _infer_node_positions(node_ids, raw_edges, diagram_kind, layout)
    normalized: list[dict[str, Any]] = []
    for index, node in enumerate(raw_nodes):
        node_id = node_ids[index]
        x, y = inferred_positions.get(node_id, (90 + index * 72, 110))
        normalized.append(
            {
                "node_id": node_id,
                "label": str(node.get("label") or node_id),
                "x": _number_or_default(node.get("x"), x),
                "y": _number_or_default(node.get("y"), y),
                **_optional_state(node),
                **_optional_node_shape(node),
            }
        )
    return normalized


def _infer_node_positions(
    node_ids: list[str],
    raw_edges: list[dict[str, Any]],
    diagram_kind: str,
    layout: str,
) -> dict[str, tuple[float, float]]:
    if not node_ids:
        return {}
    if diagram_kind in TREE_LIKE_KINDS or layout == "tree":
        children_by_parent: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
        has_parent: set[str] = set()
        for edge in raw_edges:
            parent = edge.get("from") or edge.get("source")
            child = edge.get("to") or edge.get("target")
            if isinstance(parent, str) and isinstance(child, str) and parent in children_by_parent and child in children_by_parent:
                children_by_parent[parent].append(child)
                has_parent.add(child)
        roots = [node_id for node_id in node_ids if node_id not in has_parent] or [node_ids[0]]
        levels: dict[str, int] = {}
        queue = [(root, 0) for root in roots]
        while queue:
            node_id, level = queue.pop(0)
            if node_id in levels:
                continue
            levels[node_id] = level
            queue.extend((child, level + 1) for child in children_by_parent.get(node_id, []))
        for node_id in node_ids:
            levels.setdefault(node_id, 0)
        by_level: dict[int, list[str]] = {}
        for node_id in node_ids:
            by_level.setdefault(levels[node_id], []).append(node_id)
        positions: dict[str, tuple[float, float]] = {}
        for level, ids in by_level.items():
            spacing = 240 / max(1, len(ids))
            for index, node_id in enumerate(ids):
                positions[node_id] = (80 + spacing / 2 + index * spacing, 56 + level * 72)
        return positions

    return {node_id: (80 + index * 76, 112) for index, node_id in enumerate(node_ids)}


def _normalize_edges(raw_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, edge in enumerate(raw_edges):
        from_id = edge.get("from") or edge.get("source")
        to_id = edge.get("to") or edge.get("target")
        if not isinstance(from_id, str) or not isinstance(to_id, str):
            continue
        normalized.append(
            {
                "edge_id": str(edge.get("edge_id") or edge.get("id") or f"{from_id}-{to_id}-{index}"),
                "from": from_id,
                "to": to_id,
                **({"label": str(edge["label"])} if "label" in edge and edge["label"] is not None else {}),
                **_optional_state(edge),
            }
        )
    return normalized


def _node_like_to_item(node: dict[str, Any], index: int, total: int, diagram_kind: str) -> dict[str, Any]:
    item_id = str(node.get("item_id") or node.get("node_id") or node.get("id") or f"item-{index}")
    if diagram_kind == "stack":
        x = 158
        y = 166 - (total - index - 1) * 42
    else:
        x = 64 + index * 58
        y = 112
    return {
        "item_id": item_id,
        "label": str(node.get("label") or item_id),
        "x": x,
        "y": y,
        "kind": "cell",
        "state": "highlighted" if node.get("is_top") is True else node.get("state"),
    }


def _normalize_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    item_id = str(item.get("item_id") or item.get("id") or f"item-{index}")
    return {
        "item_id": item_id,
        "label": str(item.get("label") or item_id),
        "x": _number_or_default(item.get("x"), 64 + index * 58),
        "y": _number_or_default(item.get("y"), 112),
        "width": _number_or_default(item.get("width"), 48),
        "height": _number_or_default(item.get("height"), 34),
        "kind": item.get("kind") if item.get("kind") in {"cell", "block", "frame", "pointer", "text"} else "cell",
        **_optional_state(item),
        **({"secondary_label": str(item["secondary_label"])} if item.get("secondary_label") is not None else {}),
    }


def _normalize_transition(raw_transition: Any, id_kind: dict[str, str]) -> dict[str, Any]:
    if raw_transition is None:
        return {"actions": []}
    if not isinstance(raw_transition, dict):
        return {"actions": []}
    raw_actions = raw_transition.get("actions")
    if not isinstance(raw_actions, list):
        raw_actions = [raw_transition] if raw_transition.get("action") or raw_transition.get("kind") else []

    actions: list[dict[str, Any]] = []
    for index, action in enumerate(raw_actions):
        if not isinstance(action, dict):
            continue
        kind = action.get("kind") or action.get("action")
        if kind not in {"highlight", "focus", "move", "remove", "add"}:
            continue
        raw_target = action.get("target")
        target_id = action.get("target_id")
        if target_id is None and isinstance(raw_target, str) and raw_target not in {"node", "edge", "item"}:
            target_id = raw_target
            raw_target = None
        if target_id is None:
            target_id = action.get("id") or f"transition-target-{index}"
        target_id = str(target_id)
        target_kind = raw_target if raw_target in {"node", "edge", "item"} else id_kind.get(target_id)
        if kind == "focus" and id_kind.get(target_id) != target_kind:
            target_kind = None
        if kind in {"add", "remove", "highlight"} and target_kind is None:
            continue
        if kind == "move" and target_kind not in {"node", "item"}:
            continue
        normalized = {"kind": kind, "target_id": target_id}
        if target_kind is not None:
            normalized["target"] = target_kind
        for key in ("x", "y", "width", "height"):
            if action.get(key) is not None:
                normalized[key] = action[key]
        actions.append(normalized)
    return {"actions": actions}


def _number_or_default(value: Any, default: float) -> float:
    return float(value) if isinstance(value, int | float) else float(default)


def _optional_state(value: dict[str, Any]) -> dict[str, Any]:
    state = value.get("state")
    if state in {"default", "highlighted", "muted"}:
        return {"state": state}
    return {}


def _optional_node_shape(value: dict[str, Any]) -> dict[str, Any]:
    shape = value.get("shape")
    result: dict[str, Any] = {}
    if shape in {"circle", "rounded_rect", "pill"}:
        result["shape"] = shape
    for key in ("width", "height"):
        if isinstance(value.get(key), int | float):
            result[key] = float(value[key])
    return result
