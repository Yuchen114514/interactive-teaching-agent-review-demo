from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any


class DiagramAgent:
    def __init__(
        self,
        *,
        provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> None:
        self.provider = provider

    def run(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        visual_need = ((context.get("main_blocks") or {}).get("visual_need") or {})
        if not isinstance(visual_need, dict) or not visual_need.get("need_diagram"):
            return [{"type": "diagram_status", "status": "skipped"}]
        if self.provider is None:
            fallback = _fallback_scene(context, visual_need)
            if not isinstance(fallback, dict):
                return [{"type": "diagram_status", "status": "skipped", "reason": "no_concrete_visual_fallback"}]
            scene = _with_narration_metadata(_normalize_scene(fallback, context=context, visual_need=visual_need))
            return [
                {
                    "type": "diagram_ready",
                    "diagram_goal": visual_need.get("diagram_goal"),
                    "scene": scene,
                },
                {"type": "diagram_status", "status": "completed"},
            ]

        raw_output = self.provider(
            {
                "capability": "diagram_handoff",
                "visual_need": visual_need,
                "data_structure_scene_contract": _data_structure_scene_contract(),
                "request_context": _request_context(context, visual_need),
                "context": context,
            }
        )
        scene = raw_output.get("scene") if isinstance(raw_output, dict) else None
        if not isinstance(scene, dict) and isinstance(raw_output, dict):
            scene = _compile_data_structure_scene(raw_output.get("data_structure_scene"), context=context, visual_need=visual_need)
        if not isinstance(scene, dict):
            return [{"type": "diagram_status", "status": "skipped", "reason": "missing_scene"}]
        if _is_meta_teaching_strategy_scene(scene):
            return [{"type": "diagram_status", "status": "skipped", "reason": "meta_teaching_strategy_scene"}]
        if _is_ambiguous_animation(scene):
            return [{"type": "diagram_status", "status": "skipped", "reason": "ambiguous_animation"}]
        scene = _with_narration_metadata(_normalize_scene(scene, context=context, visual_need=visual_need))
        return [
            {
                "type": "diagram_ready",
                "diagram_goal": visual_need.get("diagram_goal"),
                "scene": scene,
            },
            {"type": "diagram_status", "status": "completed"},
        ]


def _is_ambiguous_animation(scene: dict[str, Any]) -> bool:
    steps = scene.get("steps")
    if not isinstance(steps, list) or not steps:
        return True
    return any(_is_ambiguous_step(step) for step in steps if isinstance(step, dict))


def _data_structure_scene_contract() -> dict[str, Any]:
    return {
        "output_field": "data_structure_scene",
        "renderer_owns_layout": True,
        "allowed_structures": [
            "array",
            "stack",
            "monotonic_stack",
            "queue",
            "deque",
            "linked_list",
            "heap",
            "tree",
            "graph",
            "matrix",
            "dp_table",
            "union_find",
        ],
        "required_top_level_fields": ["structure", "teaching_focus", "invariant", "frames"],
        "frame_fields": ["frame_id", "title", "containers", "pointers", "annotations", "operation"],
        "container_fields": ["id", "kind", "label", "items", "edges"],
        "item_fields": ["id", "label", "value", "role", "row", "col", "parent", "next"],
        "edge_fields": ["id", "from", "to", "label", "role"],
        "rules": [
            "Act like a teacher drawing on a blackboard for the student: show concrete example states and data-structure changes, not the tutor's teaching plan.",
            "Do not output freeform flowchart coordinates for data-structure teaching.",
            "Do not draw meta teaching strategy diagrams such as understand problem -> derive idea -> write code.",
            "Describe semantic state: containers, items, pointers, invariant, operation, annotations.",
            "For graph containers, put semantic edges on container.edges with from/to item ids.",
            "For linked_list containers, put next on each item when the pointer matters.",
            "For dp_table or matrix containers, put row/col on each cell item.",
            "For union_find or parent-pointer trees, put parent on each node item.",
            "Use stable item ids across frames so the renderer can animate add/remove/highlight.",
            "The renderer decides coordinates, spacing, typography, and camera bounds.",
        ],
    }


def _is_meta_teaching_strategy_scene(scene: dict[str, Any]) -> bool:
    text = _scene_text(scene)
    if not text:
        return False
    compact = text.replace(" ", "")
    meta_tokens = (
        "教学策略",
        "讲解策略",
        "讲解流程",
        "解题流程",
        "理解题意",
        "分析题意",
        "推导策略",
        "选择算法",
        "完整解法",
        "代码实现",
        "复杂度分析",
    )
    concrete_tokens = (
        "栈",
        "队列",
        "数组",
        "链表",
        "堆",
        "树",
        "图",
        "矩阵",
        "DP",
        "dp",
        "并查集",
        "parent",
        "next",
        "front",
        "rear",
        "top",
        "差分",
        "指针",
        "节点",
        "下标",
    )
    meta_count = sum(1 for token in meta_tokens if token in compact)
    concrete_count = sum(1 for token in concrete_tokens if token in compact)
    if meta_count >= 2 and concrete_count == 0:
        return True
    if meta_count >= 3 and concrete_count <= 1:
        return True
    return False


def _scene_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_scene_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_scene_text(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def _compile_data_structure_scene(
    spec: Any,
    *,
    context: dict[str, Any],
    visual_need: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(spec, dict):
        return None
    frames = spec.get("frames")
    if not isinstance(frames, list) or not frames or not all(isinstance(frame, dict) for frame in frames):
        return None

    structure = _safe_structure(spec.get("structure"))
    title = str(spec.get("title") or visual_need.get("diagram_goal") or _structure_title(structure))
    invariant = str(spec.get("invariant") or "")
    teaching_focus = str(spec.get("teaching_focus") or visual_need.get("visual_focus") or title)
    typed_frames = [frame for frame in frames if isinstance(frame, dict)]
    steps: list[dict[str, Any]] = []
    if len(typed_frames) == 1:
        frame = typed_frames[0]
        state = _compile_data_structure_frame(frame, structure=structure, invariant=invariant)
        steps.append(
            {
                "step_id": _safe_id(frame.get("frame_id"), "observe_state"),
                "order_index": 0,
                "title": str(frame.get("title") or teaching_focus),
                "teaching_note": _frame_note(frame, invariant=invariant, teaching_focus=teaching_focus),
                "entry_state": state,
                "exit_state": state,
                "transition": {"actions": _actions_from_operation(frame.get("operation"), structure=structure)},
            }
        )
    else:
        for index, (before, after) in enumerate(zip(typed_frames, typed_frames[1:])):
            operation = after.get("operation") if isinstance(after.get("operation"), dict) else before.get("operation")
            steps.append(
                {
                    "step_id": f"{_safe_id(before.get('frame_id'), f'frame_{index}')}_to_{_safe_id(after.get('frame_id'), f'frame_{index + 1}')}",
                    "order_index": index,
                    "title": str(after.get("title") or before.get("title") or teaching_focus),
                    "teaching_note": _frame_note(after, invariant=invariant, teaching_focus=teaching_focus),
                    "entry_state": _compile_data_structure_frame(before, structure=structure, invariant=invariant),
                    "exit_state": _compile_data_structure_frame(after, structure=structure, invariant=invariant),
                    "transition": {"actions": _actions_from_operation(operation, structure=structure)},
                }
            )

    return {
        "scene_id": str(spec.get("scene_id") or f"v3-ds-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}"),
        "title": title,
        "diagram_kind": _layout_for_structure(structure),
        "layout": _layout_for_structure(structure),
        "teaching_goal": teaching_focus,
        "default_step_id": steps[0]["step_id"] if steps else None,
        "data_structure": {
            "structure": structure,
            "invariant": invariant,
            "teaching_focus": teaching_focus,
        },
        "steps": steps,
    }


def _compile_data_structure_frame(frame: dict[str, Any], *, structure: str, invariant: str) -> dict[str, list[dict[str, Any]]]:
    containers = frame.get("containers") if isinstance(frame.get("containers"), list) else []
    pointers = frame.get("pointers") if isinstance(frame.get("pointers"), list) else []
    annotations = frame.get("annotations") if isinstance(frame.get("annotations"), list) else []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    if invariant:
        items.append(
            {
                "item_id": "invariant",
                "label": f"不变量：{invariant}",
                "x": -150,
                "y": -58,
                "width": 360,
                "height": 32,
                "kind": "text",
                "state": "muted",
            }
        )

    for container_index, container in enumerate(containers):
        if not isinstance(container, dict):
            continue
        container_kind = _safe_structure(container.get("kind"))
        if container_kind == "graph" or structure == "graph":
            graph_nodes, graph_edges = _compile_graph_container(container, index=container_index)
            nodes.extend(graph_nodes)
            edges.extend(graph_edges)
        elif container_kind == "linked_list" or structure == "linked_list":
            list_nodes, list_edges = _compile_linked_list_container(container, index=container_index)
            nodes.extend(list_nodes)
            edges.extend(list_edges)
        elif container_kind in {"matrix", "dp_table"} or structure in {"matrix", "dp_table"}:
            items.extend(_compile_table_container(container, index=container_index))
        elif container_kind in {"heap", "tree", "union_find"} or structure in {"heap", "tree", "union_find"}:
            tree_nodes, tree_edges = _compile_tree_container(container, index=container_index)
            nodes.extend(tree_nodes)
            edges.extend(tree_edges)
        elif container_kind in {"stack", "monotonic_stack"} or structure in {"stack", "monotonic_stack"}:
            items.extend(_compile_stack_container(container, index=container_index))
        else:
            items.extend(_compile_linear_container(container, index=container_index))

    for pointer_index, pointer in enumerate(pointers):
        if isinstance(pointer, dict):
            items.append(_compile_pointer(pointer, index=pointer_index))

    for annotation_index, annotation in enumerate(annotations):
        if isinstance(annotation, dict):
            items.append(_compile_annotation(annotation, index=annotation_index))

    return {"nodes": nodes, "edges": edges, "items": items}


def _compile_stack_container(container: dict[str, Any], *, index: int) -> list[dict[str, Any]]:
    raw_items = container.get("items") if isinstance(container.get("items"), list) else []
    cells = [item for item in raw_items if isinstance(item, dict)]
    base_x = -120 + index * 210
    frame_y = 54
    cell_height = 34
    cell_gap = 8
    frame_height = max(112, len(cells) * (cell_height + cell_gap) + 34)
    compiled: list[dict[str, Any]] = [
        {
            "item_id": _safe_id(container.get("id"), f"container_{index}"),
            "label": str(container.get("label") or "栈"),
            "x": base_x,
            "y": frame_y,
            "width": 162,
            "height": frame_height,
            "kind": "frame",
        }
    ]
    bottom_y = frame_y + frame_height - cell_height - 16
    for item_index, item in enumerate(cells):
        y = bottom_y - item_index * (cell_height + cell_gap)
        compiled.append(
            {
                "item_id": _safe_id(item.get("id"), f"item_{item_index}"),
                "label": _item_label(item),
                "x": base_x + 14,
                "y": y,
                "width": 134,
                "height": cell_height,
                "kind": "cell",
                "state": "highlighted" if str(item.get("role") or "").lower() in {"top", "current", "focus"} else "default",
            }
        )
    return compiled


def _compile_tree_container(container: dict[str, Any], *, index: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_items = container.get("items") if isinstance(container.get("items"), list) else []
    tree_items = [item for item in raw_items if isinstance(item, dict)]
    if any("parent" in item for item in tree_items):
        return _compile_parent_tree_container(container, index=index)
    base_x = index * 320
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for item_index, item in enumerate(tree_items):
        level = (item_index + 1).bit_length() - 1
        first_index = (1 << level) - 1
        position = item_index - first_index
        nodes_in_level = 1 << level
        spread = max(86, 260 / nodes_in_level)
        x = base_x + (position - (nodes_in_level - 1) / 2) * spread
        y = level * 92
        item_id = _safe_id(item.get("id"), f"node_{item_index}")
        nodes.append(
            {
                "node_id": item_id,
                "label": _item_label(item),
                "x": x,
                "y": y,
                "shape": "circle",
                "width": 44,
                "height": 44,
                "state": "highlighted" if str(item.get("role") or "").lower() in {"root", "top", "focus", "current"} else "default",
            }
        )
        if item_index > 0:
            parent_index = (item_index - 1) // 2
            if parent_index < len(tree_items):
                parent_id = _safe_id(tree_items[parent_index].get("id"), f"node_{parent_index}")
                edges.append(
                    {
                        "edge_id": f"{parent_id}_to_{item_id}",
                        "from": parent_id,
                        "to": item_id,
                        "label": "",
                    }
                )
    return nodes, edges


def _compile_parent_tree_container(container: dict[str, Any], *, index: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_items = container.get("items") if isinstance(container.get("items"), list) else []
    tree_items = [item for item in raw_items if isinstance(item, dict)]
    item_ids = [_safe_id(item.get("id"), f"node_{item_index}") for item_index, item in enumerate(tree_items)]
    item_by_id = dict(zip(item_ids, tree_items, strict=False))
    depths: dict[str, int] = {}

    def depth_of(item_id: str, seen: set[str] | None = None) -> int:
        if item_id in depths:
            return depths[item_id]
        seen = set() if seen is None else seen
        if item_id in seen:
            depths[item_id] = 0
            return 0
        seen.add(item_id)
        parent = item_by_id[item_id].get("parent")
        parent_id = _safe_id(parent, "") if parent is not None else None
        if not parent_id or parent_id not in item_by_id:
            depths[item_id] = 0
        else:
            depths[item_id] = depth_of(parent_id, seen) + 1
        return depths[item_id]

    for item_id in item_ids:
        depth_of(item_id)

    by_depth: dict[int, list[str]] = {}
    for item_id in item_ids:
        by_depth.setdefault(depths[item_id], []).append(item_id)

    base_x = index * 340
    nodes: list[dict[str, Any]] = []
    for depth, ids_at_depth in sorted(by_depth.items()):
        spread = max(88, 250 / max(1, len(ids_at_depth)))
        for position, item_id in enumerate(ids_at_depth):
            item = item_by_id[item_id]
            role = str(item.get("role") or "").lower()
            nodes.append(
                {
                    "node_id": item_id,
                    "label": _item_label(item),
                    "x": base_x + (position - (len(ids_at_depth) - 1) / 2) * spread,
                    "y": depth * 88,
                    "shape": "circle",
                    "width": 44,
                    "height": 44,
                    "state": "highlighted" if role in {"root", "top", "focus", "current"} else "default",
                }
            )

    edges: list[dict[str, Any]] = []
    for item_id, item in zip(item_ids, tree_items, strict=False):
        parent = item.get("parent")
        if parent is None:
            continue
        parent_id = _safe_id(parent, "")
        if parent_id in item_by_id:
            edges.append({"edge_id": f"{parent_id}_to_{item_id}", "from": parent_id, "to": item_id, "label": "parent"})
    return nodes, edges


def _compile_graph_container(container: dict[str, Any], *, index: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_items = container.get("items") if isinstance(container.get("items"), list) else []
    graph_items = [item for item in raw_items if isinstance(item, dict)]
    base_x = index * 340
    radius_x = 128
    radius_y = 78
    nodes: list[dict[str, Any]] = []
    for item_index, item in enumerate(graph_items):
        angle = -math.pi / 2 + item_index * (2 * math.pi / max(1, len(graph_items)))
        role = str(item.get("role") or "").lower()
        nodes.append(
            {
                "node_id": _safe_id(item.get("id"), f"node_{item_index}"),
                "label": _item_label(item),
                "x": base_x + radius_x * math.cos(angle),
                "y": 54 + radius_y * math.sin(angle),
                "shape": "circle",
                "width": 44,
                "height": 44,
                "state": "highlighted" if role in {"current", "focus", "front", "visited"} else "default",
            }
        )

    raw_edges = container.get("edges") if isinstance(container.get("edges"), list) else []
    edges = [
        {
            "edge_id": _safe_id(edge.get("id"), f"{_safe_id(edge.get('from'), 'from')}_to_{_safe_id(edge.get('to'), 'to')}"),
            "from": _safe_id(edge.get("from"), "from"),
            "to": _safe_id(edge.get("to"), "to"),
            "label": str(edge.get("label") or ""),
            "state": "highlighted" if str(edge.get("role") or "").lower() in {"focus", "current"} else "default",
        }
        for edge in raw_edges
        if isinstance(edge, dict) and edge.get("from") is not None and edge.get("to") is not None
    ]
    return nodes, edges


def _compile_linked_list_container(container: dict[str, Any], *, index: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_items = container.get("items") if isinstance(container.get("items"), list) else []
    list_items = [item for item in raw_items if isinstance(item, dict)]
    base_x = -150
    base_y = 60 + index * 92
    nodes: list[dict[str, Any]] = []
    for item_index, item in enumerate(list_items):
        role = str(item.get("role") or "").lower()
        nodes.append(
            {
                "node_id": _safe_id(item.get("id"), f"node_{item_index}"),
                "label": _item_label(item),
                "x": base_x + item_index * 112,
                "y": base_y,
                "shape": "rounded_rect",
                "width": 74,
                "height": 38,
                "state": "highlighted" if role in {"current", "focus", "front", "rear"} else "default",
            }
        )

    item_ids = {_safe_id(item.get("id"), f"node_{item_index}") for item_index, item in enumerate(list_items)}
    edges: list[dict[str, Any]] = []
    for item_index, item in enumerate(list_items):
        item_id = _safe_id(item.get("id"), f"node_{item_index}")
        next_id = _safe_id(item.get("next"), "") if item.get("next") is not None else ""
        if next_id and next_id in item_ids:
            edges.append({"edge_id": f"{item_id}_to_{next_id}", "from": item_id, "to": next_id, "label": "next"})
        elif item_index + 1 < len(list_items):
            next_item_id = _safe_id(list_items[item_index + 1].get("id"), f"node_{item_index + 1}")
            edges.append({"edge_id": f"{item_id}_to_{next_item_id}", "from": item_id, "to": next_item_id, "label": "next"})
    return nodes, edges


def _compile_table_container(container: dict[str, Any], *, index: int) -> list[dict[str, Any]]:
    raw_items = container.get("items") if isinstance(container.get("items"), list) else []
    table_items = [item for item in raw_items if isinstance(item, dict)]
    cell_width = 58
    cell_height = 36
    gap = 4
    rows = [_safe_int(item.get("row"), fallback=item_index // 4) for item_index, item in enumerate(table_items)]
    cols = [_safe_int(item.get("col"), fallback=item_index % 4) for item_index, item in enumerate(table_items)]
    min_row = min(rows, default=0)
    min_col = min(cols, default=0)
    max_row = max(rows, default=0)
    max_col = max(cols, default=0)
    base_x = -130
    base_y = 40 + index * 124
    frame_width = (max_col - min_col + 1) * (cell_width + gap) + 18
    frame_height = (max_row - min_row + 1) * (cell_height + gap) + 36
    compiled: list[dict[str, Any]] = [
        {
            "item_id": _safe_id(container.get("id"), f"table_{index}"),
            "label": str(container.get("label") or "表"),
            "x": base_x - 10,
            "y": base_y - 28,
            "width": frame_width,
            "height": frame_height,
            "kind": "frame",
            "state": "muted",
        }
    ]
    for item_index, item in enumerate(table_items):
        row = _safe_int(item.get("row"), fallback=item_index // 4)
        col = _safe_int(item.get("col"), fallback=item_index % 4)
        role = str(item.get("role") or "").lower()
        compiled.append(
            {
                "item_id": _safe_id(item.get("id"), f"cell_{item_index}"),
                "label": _item_label(item),
                "x": base_x + (col - min_col) * (cell_width + gap),
                "y": base_y + (row - min_row) * (cell_height + gap),
                "width": cell_width,
                "height": cell_height,
                "kind": "cell",
                "state": "highlighted" if role in {"current", "focus", "front", "rear"} else "default",
            }
        )
    return compiled


def _compile_linear_container(container: dict[str, Any], *, index: int) -> list[dict[str, Any]]:
    raw_items = container.get("items") if isinstance(container.get("items"), list) else []
    cells = [item for item in raw_items if isinstance(item, dict)]
    base_x = -180
    base_y = 52 + index * 82
    compiled: list[dict[str, Any]] = []
    for item_index, item in enumerate(cells):
        compiled.append(
            {
                "item_id": _safe_id(item.get("id"), f"item_{item_index}"),
                "label": _item_label(item),
                "x": base_x + item_index * 92,
                "y": base_y,
                "width": 82,
                "height": 34,
                "kind": "cell",
                "state": "highlighted" if str(item.get("role") or "").lower() in {"current", "focus", "front", "rear"} else "default",
            }
        )
    return compiled


def _compile_pointer(pointer: dict[str, Any], *, index: int) -> dict[str, Any]:
    return {
        "item_id": _safe_id(pointer.get("id"), f"pointer_{index}"),
        "label": str(pointer.get("label") or pointer.get("target") or "指针"),
        "x": 92 + index * 126,
        "y": 182,
        "width": 118,
        "height": 32,
        "kind": "pointer",
        "state": "highlighted",
    }


def _compile_annotation(annotation: dict[str, Any], *, index: int) -> dict[str, Any]:
    return {
        "item_id": _safe_id(annotation.get("id"), f"annotation_{index}"),
        "label": str(annotation.get("label") or annotation.get("text") or "说明"),
        "x": 86 + index * 132,
        "y": 78,
        "width": 118,
        "height": 32,
        "kind": "cell",
        "state": "highlighted",
    }


def _actions_from_operation(operation: Any, *, structure: str = "array") -> list[dict[str, Any]]:
    if not isinstance(operation, dict):
        return [{"kind": "highlight", "target": "item", "target_id": "invariant"}]
    kind = str(operation.get("kind") or operation.get("type") or "highlight")
    target = _safe_id(operation.get("target") or operation.get("item") or operation.get("id"), "current")
    result = operation.get("result")
    action_target = _operation_target_for_structure(structure)
    actions = [{"kind": _action_kind_for_operation(kind), "target": action_target, "target_id": target}]
    added = operation.get("added")
    if isinstance(added, list):
        actions.extend(
            {"kind": "add", "target": action_target, "target_id": _safe_id(item_id, "added")}
            for item_id in added
        )
    if result:
        actions.append({"kind": "highlight", "target": "item", "target_id": _safe_id(result, "result")})
    return actions


def _action_kind_for_operation(kind: str) -> str:
    if kind in {"pop", "remove", "delete", "dequeue"}:
        return "remove"
    if kind in {"push", "add", "enqueue", "insert"}:
        return "add"
    if kind in {"move", "swap"}:
        return "move"
    return "highlight"


def _operation_target_for_structure(structure: str) -> str:
    if structure in {"graph", "tree", "heap", "linked_list", "union_find"}:
        return "node"
    return "item"


def _safe_int(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _frame_note(frame: dict[str, Any], *, invariant: str, teaching_focus: str) -> str:
    operation = frame.get("operation") if isinstance(frame.get("operation"), dict) else {}
    result = operation.get("result") if isinstance(operation, dict) else None
    if result:
        return f"{teaching_focus}；本步结果是 {result}。"
    if invariant:
        return f"{teaching_focus}；观察这一步如何维护不变量：{invariant}。"
    return teaching_focus


def _item_label(item: dict[str, Any]) -> str:
    label = str(item.get("label") or item.get("id") or "item")
    value = item.get("value")
    if value is None or str(value) in label:
        return label
    return f"{label}, val={value}"


def _safe_structure(value: Any) -> str:
    raw = str(value or "array").strip()
    aliases = {
        "monotonic stack": "monotonic_stack",
        "dp": "dp_table",
        "table": "dp_table",
        "priority_queue": "heap",
        "priority queue": "heap",
    }
    return aliases.get(raw, raw)


def _layout_for_structure(structure: str) -> str:
    if structure in {"monotonic_stack", "stack"}:
        return "stack"
    if structure in {"queue", "deque", "array", "linked_list"}:
        return "array" if structure == "array" else structure
    if structure in {"heap", "tree", "union_find"}:
        return "tree"
    if structure == "dp_table":
        return "table"
    if structure == "graph":
        return "graph"
    return "freeform"


def _structure_title(structure: str) -> str:
    titles = {
        "monotonic_stack": "单调栈状态变化",
        "stack": "栈状态变化",
        "queue": "队列状态变化",
        "deque": "双端队列状态变化",
        "heap": "堆状态变化",
        "tree": "树结构状态变化",
        "graph": "图结构状态变化",
        "dp_table": "DP 状态表",
    }
    return titles.get(structure, "数据结构状态变化")


def _safe_id(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip()
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in raw)
    return safe or fallback


def _fallback_scene(context: dict[str, Any], visual_need: dict[str, Any]) -> dict[str, Any] | None:
    user_message = str(context.get("user_message") or "")
    main_blocks = context.get("main_blocks") if isinstance(context.get("main_blocks"), dict) else {}
    visible_reply = str(main_blocks.get("visible_reply") or "")
    problem_context = context.get("problem_context") if isinstance(context.get("problem_context"), dict) else {}
    summary = str(problem_context.get("problem_summary") or "")
    source_text = f"{user_message}{visible_reply}{summary}"
    is_monotonic_stack = any(token in source_text for token in ("单调栈", "栈顶", "入栈", "出栈", "右侧第一个更大"))
    is_heap = any(token in source_text for token in ("堆", "优先队列", "heap", "priority queue"))
    is_union_find = any(token in source_text for token in ("并查集", "union find", "disjoint set", "路径压缩", "parent[", "parent 指针"))
    is_tree = any(token in source_text for token in ("二叉树", "根节点", "左右子树", "DFS", "LCA"))
    is_graph = any(token in source_text for token in ("图结构", "无向图", "有向图", "邻接表", "最短路", "BFS", "Dijkstra", "拓扑"))
    is_suffix_difference = any(token in source_text for token in ("后缀", "差分", "相邻差值", "数组b", "Coolbreeze"))
    is_house = any(token in source_text for token in ("房屋", "环形", "偷窃", "首尾"))
    is_hero = any(token in source_text for token in ("英雄", "体力", "回家", "T_i", "S_i", "等待消耗"))
    is_binary_search = any(token in source_text for token in ("二分查找", "binary search", "low", "mid", "high"))
    is_max_subarray = any(
        token in source_text
        for token in (
            "最大连续子数组",
            "最大子数组",
            "连续子数组和",
            "Kadane",
            "kadane",
            "dp[i]=max",
            "以 i 结尾",
            "以i结尾",
        )
    )
    if is_monotonic_stack:
        compiled = _compile_data_structure_scene(
            {
                "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
                "structure": "monotonic_stack",
                "title": visual_need.get("diagram_goal") or "单调栈状态变化",
                "teaching_focus": "当前元素与栈顶比较，弹出更小栈顶并确定答案，再入栈维护单调性",
                "invariant": "栈内元素值从栈底到栈顶单调递减",
                "frames": [
                    {
                        "frame_id": "before_compare",
                        "title": "比较当前元素和栈顶",
                        "containers": [
                            {
                                "id": "stack",
                                "kind": "stack",
                                "label": "单调栈",
                                "items": [
                                    {"id": "idx1", "label": "idx=1", "value": "3"},
                                    {"id": "idx3", "label": "idx=3", "value": "5", "role": "top"},
                                ],
                            }
                        ],
                        "pointers": [{"id": "current_x", "label": "当前 x=7"}],
                    },
                    {
                        "frame_id": "after_pop",
                        "title": "弹出栈顶并记录答案",
                        "operation": {"kind": "pop", "target": "idx3", "added": ["current_x"], "result": "ans[3]=7"},
                        "containers": [
                            {
                                "id": "stack",
                                "kind": "stack",
                                "label": "单调栈",
                                "items": [
                                    {"id": "idx1", "label": "idx=1", "value": "3"},
                                    {"id": "current_x", "label": "x 入栈", "value": "7", "role": "top"},
                                ],
                            }
                        ],
                        "annotations": [
                            {"id": "popped_idx3", "label": "弹出 idx=3"},
                            {"id": "ans_3", "label": "ans[3]=7"},
                        ],
                    },
                ],
            },
            context=context,
            visual_need=visual_need,
        )
        if compiled is not None:
            return compiled
        title = "单调栈状态变化"
        nodes = [
            {"node_id": "scan", "label": "扫描当前元素 x", "x": -30, "y": 0, "shape": "pill", "width": 126, "height": 36},
            {"node_id": "top", "label": "比较栈顶 top", "x": 190, "y": 0, "shape": "pill", "width": 120, "height": 36, "state": "highlighted"},
            {"node_id": "answer", "label": "弹出并确定答案", "x": 190, "y": 136, "shape": "pill", "width": 140, "height": 36, "state": "highlighted"},
        ]
        edges = [
            {"edge_id": "scan_to_top", "from": "scan", "to": "top", "label": "比较"},
            {"edge_id": "top_to_answer", "from": "top", "to": "answer", "label": "x更大"},
        ]
        entry_items = [
            {"item_id": "stack_frame", "label": "单调栈", "x": -92, "y": 88, "width": 150, "height": 136, "kind": "frame"},
            {"item_id": "stack_low", "label": "idx=1,val=3", "x": -74, "y": 176, "width": 118, "height": 32, "kind": "cell"},
            {"item_id": "stack_top", "label": "栈顶 idx=3,val=5", "x": -74, "y": 132, "width": 142, "height": 32, "kind": "cell", "state": "highlighted"},
            {"item_id": "current_x", "label": "当前 x=7", "x": 124, "y": 184, "width": 112, "height": 32, "kind": "cell", "state": "highlighted"},
        ]
        exit_items = [
            {"item_id": "stack_frame", "label": "单调栈", "x": -92, "y": 88, "width": 150, "height": 136, "kind": "frame"},
            {"item_id": "stack_low", "label": "idx=1,val=3", "x": -74, "y": 176, "width": 118, "height": 32, "kind": "cell"},
            {"item_id": "popped_top", "label": "弹出 idx=3", "x": 124, "y": 132, "width": 112, "height": 32, "kind": "cell", "state": "highlighted"},
            {"item_id": "answer_set", "label": "ans[3]=7", "x": 124, "y": 82, "width": 108, "height": 32, "kind": "cell", "state": "highlighted"},
            {"item_id": "push_x", "label": "x 入栈", "x": -74, "y": 132, "width": 118, "height": 32, "kind": "cell", "state": "highlighted"},
        ]
        note = "复杂数据结构图要看状态变化：当前元素先和栈顶比较，若它更大就弹出栈顶并确定答案，最后把当前元素入栈维持单调性。"
        layout = "stack"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": "top"},
            {"kind": "remove", "target": "item", "target_id": "stack_top"},
            {"kind": "add", "target": "item", "target_id": "push_x"},
            {"kind": "highlight", "target": "item", "target_id": "answer_set"},
        ]
    elif is_heap:
        compiled = _compile_data_structure_scene(
            {
                "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
                "structure": "heap",
                "title": visual_need.get("diagram_goal") or "堆顶与下滤调整",
                "teaching_focus": "弹出堆顶后，用树形结构观察下滤如何恢复堆序",
                "invariant": "每个父节点满足堆序关系",
                "frames": [
                    {
                        "frame_id": "before_pop",
                        "title": "弹出前的堆",
                        "containers": [
                            {
                                "id": "heap",
                                "kind": "heap",
                                "label": "堆",
                                "items": [
                                    {"id": "h0", "label": "堆顶 1", "role": "top"},
                                    {"id": "h1", "label": "4"},
                                    {"id": "h2", "label": "3"},
                                    {"id": "h3", "label": "8"},
                                ],
                            }
                        ],
                    },
                    {
                        "frame_id": "after_pop",
                        "title": "下滤恢复堆序",
                        "operation": {"kind": "remove", "target": "h0", "result": "弹出堆顶"},
                        "containers": [
                            {
                                "id": "heap",
                                "kind": "heap",
                                "label": "堆",
                                "items": [
                                    {"id": "h2", "label": "3", "role": "focus"},
                                    {"id": "h1", "label": "4"},
                                    {"id": "h3", "label": "8"},
                                ],
                            }
                        ],
                        "annotations": [{"id": "restore_heap", "label": "下滤恢复堆序"}],
                    },
                ],
            },
            context=context,
            visual_need=visual_need,
        )
        if compiled is not None:
            return compiled
        title = "堆顶与候选集合"
        nodes = [
            {"node_id": "candidates", "label": "候选元素集合", "x": 0, "y": 0},
            {"node_id": "heap_top", "label": "堆顶最优候选", "x": 180, "y": 0, "state": "highlighted"},
            {"node_id": "update", "label": "弹出/加入后重排", "x": 90, "y": 120, "state": "highlighted"},
        ]
        edges = [
            {"edge_id": "candidates_to_top", "from": "candidates", "to": "heap_top", "label": "维护"},
            {"edge_id": "top_to_update", "from": "heap_top", "to": "update", "label": "取出后更新"},
        ]
        entry_items = exit_items = []
        note = "堆图要突出堆顶代表当前最优候选，以及每次弹出或加入后局部调整恢复堆序。"
        layout = "tree"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": "heap_top"},
            {"kind": "move", "target": "node", "target_id": "update"},
        ]
    elif is_union_find:
        compiled = _compile_data_structure_scene(
            {
                "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
                "structure": "union_find",
                "title": visual_need.get("diagram_goal") or "并查集路径压缩",
                "teaching_focus": "沿 parent 指针找到根后，把路径上的节点直接挂到根",
                "invariant": "parent 指针最终指向集合代表元",
                "frames": [
                    {
                        "frame_id": "before_compress",
                        "title": "压缩前沿 parent 指针向上找根",
                        "containers": [
                            {
                                "id": "uf",
                                "kind": "union_find",
                                "label": "parent 森林",
                                "items": [
                                    {"id": "root", "label": "1", "parent": None, "role": "root"},
                                    {"id": "mid", "label": "2", "parent": "root"},
                                    {"id": "leaf", "label": "3", "parent": "mid", "role": "current"},
                                ],
                            }
                        ],
                    },
                    {
                        "frame_id": "after_compress",
                        "title": "路径压缩后直接指向根",
                        "operation": {"kind": "move", "target": "leaf", "result": "parent[3]=1"},
                        "containers": [
                            {
                                "id": "uf",
                                "kind": "union_find",
                                "label": "parent 森林",
                                "items": [
                                    {"id": "root", "label": "1", "parent": None, "role": "root"},
                                    {"id": "mid", "label": "2", "parent": "root"},
                                    {"id": "leaf", "label": "3", "parent": "root", "role": "current"},
                                ],
                            }
                        ],
                        "annotations": [{"id": "compressed", "label": "后续 find(3) 少走中间节点"}],
                    },
                ],
            },
            context=context,
            visual_need=visual_need,
        )
        if compiled is not None:
            return compiled
        title = "并查集路径压缩"
        nodes = [
            {"node_id": "root", "label": "根节点", "x": 90, "y": 0, "state": "highlighted"},
            {"node_id": "mid", "label": "中间父节点", "x": 40, "y": 105},
            {"node_id": "leaf", "label": "查询节点", "x": 140, "y": 105, "state": "highlighted"},
        ]
        edges = [
            {"edge_id": "root_to_mid", "from": "root", "to": "mid", "label": "parent"},
            {"edge_id": "root_to_leaf", "from": "root", "to": "leaf", "label": "压缩后"},
        ]
        entry_items = exit_items = []
        note = "路径压缩图要突出 parent 指针变化：查询节点先沿链找到根，随后直接挂到根，减少后续查询路径长度。"
        layout = "tree"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": "root"},
            {"kind": "move", "target": "node", "target_id": "leaf"},
        ]
    elif is_tree:
        title = "树结构递归关系"
        nodes = [
            {"node_id": "root", "label": "根/当前节点", "x": 90, "y": 0, "state": "highlighted"},
            {"node_id": "left", "label": "左子树状态", "x": 0, "y": 110},
            {"node_id": "right", "label": "右子树状态", "x": 180, "y": 110},
        ]
        edges = [
            {"edge_id": "root_left", "from": "root", "to": "left", "label": "递归"},
            {"edge_id": "root_right", "from": "root", "to": "right", "label": "递归"},
        ]
        entry_items = exit_items = []
        note = "树图要让用户看到当前节点如何从左右子树收集信息，再合成这一层的返回值。"
        layout = "tree"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": "root"},
            {"kind": "highlight", "target": "node", "target_id": "left"},
            {"kind": "highlight", "target": "node", "target_id": "right"},
        ]
    elif is_graph:
        title = "图搜索的 frontier 扩展"
        nodes = [
            {"node_id": "frontier", "label": "当前队列/边界", "x": 0, "y": 0, "state": "highlighted"},
            {"node_id": "visited", "label": "已访问集合", "x": 180, "y": 0},
            {"node_id": "next", "label": "下一层候选", "x": 90, "y": 120, "state": "highlighted"},
        ]
        edges = [
            {"edge_id": "frontier_to_next", "from": "frontier", "to": "next", "label": "扩展邻居"},
            {"edge_id": "next_to_visited", "from": "next", "to": "visited", "label": "标记"},
        ]
        entry_items = exit_items = []
        note = "图结构讲解要突出 frontier、visited 和下一层候选，避免只画抽象箭头。"
        layout = "graph"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": "frontier"},
            {"kind": "add", "target": "node", "target_id": "next"},
            {"kind": "highlight", "target": "node", "target_id": "visited"},
        ]
    elif is_suffix_difference:
        compiled = _compile_data_structure_scene(
            {
                "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
                "structure": "array",
                "title": visual_need.get("diagram_goal") or "后缀操作影响差分",
                "teaching_focus": "用目标数组和差分数组观察：一次后缀操作只改变起点处的差分值",
                "invariant": "后缀整体同增同减时，起点之后的相邻差会互相抵消",
                "frames": [
                    {
                        "frame_id": "before_suffix",
                        "title": "先把目标数组 b 转成差分 d",
                        "containers": [
                            {
                                "id": "target_b",
                                "kind": "array",
                                "label": "目标数组 b",
                                "items": [
                                    {"id": "b0", "label": "b[0]", "value": "2"},
                                    {"id": "b1", "label": "b[1]", "value": "3"},
                                    {"id": "b2", "label": "b[2]", "value": "3"},
                                    {"id": "b3", "label": "b[3]", "value": "1"},
                                ],
                            },
                            {
                                "id": "diff_d",
                                "kind": "array",
                                "label": "差分 d",
                                "items": [
                                    {"id": "d0", "label": "d[0]", "value": "2"},
                                    {"id": "d1", "label": "d[1]", "value": "1", "role": "focus"},
                                    {"id": "d2", "label": "d[2]", "value": "0"},
                                    {"id": "d3", "label": "d[3]", "value": "-2"},
                                ],
                            },
                        ],
                        "annotations": [{"id": "formula", "label": "d[i]=b[i]-b[i-1]"}],
                    },
                    {
                        "frame_id": "after_suffix",
                        "title": "一次后缀 +/-1 只改一个差分起点",
                        "operation": {"kind": "highlight", "target": "d1", "result": "只改变 d[1]"},
                        "containers": [
                            {
                                "id": "target_b",
                                "kind": "array",
                                "label": "从 i=1 开始的后缀",
                                "items": [
                                    {"id": "b0", "label": "b[0]", "value": "2"},
                                    {"id": "b1", "label": "b[1]+1", "value": "4", "role": "focus"},
                                    {"id": "b2", "label": "b[2]+1", "value": "4", "role": "focus"},
                                    {"id": "b3", "label": "b[3]+1", "value": "2", "role": "focus"},
                                ],
                            },
                            {
                                "id": "diff_d",
                                "kind": "array",
                                "label": "差分 d",
                                "items": [
                                    {"id": "d0", "label": "d[0]", "value": "2"},
                                    {"id": "d1", "label": "d[1]+1", "value": "2", "role": "focus"},
                                    {"id": "d2", "label": "d[2]", "value": "0"},
                                    {"id": "d3", "label": "d[3]", "value": "-2"},
                                ],
                            },
                        ],
                        "annotations": [{"id": "answer", "label": "次数累加 |d[i]|"}],
                    },
                ],
            },
            context=context,
            visual_need=visual_need,
        )
        if compiled is not None:
            return compiled
        title = "后缀操作影响差分"
        nodes = [
            {"node_id": "target_array", "label": "目标数组 b", "x": 0, "y": 0},
            {"node_id": "difference", "label": "差分 d[i]=b[i]-b[i-1]", "x": 180, "y": 0, "state": "highlighted"},
            {"node_id": "operation", "label": "一次后缀+/-1只改变一个d[i]", "x": 90, "y": 120, "state": "highlighted"},
        ]
        edges = [
            {"edge_id": "array_to_diff", "from": "target_array", "to": "difference", "label": "转成差分"},
            {"edge_id": "op_to_diff", "from": "operation", "to": "difference", "label": "匹配一次操作"},
        ]
        note = "后缀操作让相邻位置的共同变化互相抵消，只剩起点处的差分改变，所以总次数是各差分绝对值之和。"
        entry_items = exit_items = []
        layout = "flow"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": nodes[0]["node_id"]},
            {"kind": "highlight", "target": "node", "target_id": nodes[-1]["node_id"]},
        ]
    elif is_max_subarray:
        sample = _extract_example_array(user_message) or _extract_example_array(source_text) or [-2, 1, -3, 4, -1]
        sample = sample[:8]
        dp_values, best_values = _max_subarray_states(sample)
        focus_index = max(range(len(best_values)), key=lambda idx: best_values[idx]) if best_values else 0
        frames = []
        for frame_index in range(min(len(sample), 5)):
            role_index = frame_index
            annotations = [
                {
                    "id": f"choice_{frame_index}",
                    "label": _max_subarray_choice_note(sample, dp_values, frame_index),
                },
                {
                    "id": f"best_note_{frame_index}",
                    "label": f"当前全局 best={best_values[frame_index]}",
                },
            ]
            frames.append(
                {
                    "frame_id": f"scan_{frame_index}",
                    "title": f"扫到下标 {frame_index}：比较另起一段还是接上前一段",
                    "operation": {"kind": "highlight", "target": f"dp_{frame_index}"},
                    "containers": [
                        {
                            "id": "kadane_table",
                            "kind": "dp_table",
                            "label": "样例数组与状态表",
                            "items": _max_subarray_table_items(
                                sample,
                                dp_values,
                                best_values,
                                active_index=role_index,
                                answer_index=focus_index,
                            ),
                        }
                    ],
                    "annotations": annotations,
                }
            )
        compiled = _compile_data_structure_scene(
            {
                "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
                "structure": "dp_table",
                "title": visual_need.get("diagram_goal") or "最大连续子数组和状态表",
                "teaching_focus": "像板书一样按下标扫描：dp[i] 表示以 i 结尾的最大连续和，best 记录扫到目前的最大答案",
                "invariant": "dp[i]=max(nums[i], dp[i-1]+nums[i])，best=max(best, dp[i])",
                "frames": frames,
            },
            context=context,
            visual_need=visual_need,
        )
        if compiled is not None:
            return compiled
        return None
    elif is_binary_search:
        parsed = _binary_search_example(source_text)
        if parsed is None:
            return None
        nums, target = parsed
        if _is_lower_bound_search(source_text):
            frames = _lower_bound_frames(nums, target)
        else:
            frames = _binary_search_frames(nums, target)
        compiled = _compile_data_structure_scene(
            {
                "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
                "structure": "array",
                "title": visual_need.get("diagram_goal") or "二分查找边界变化",
                "teaching_focus": f"观察 low、mid、high 如何把目标 {target} 缩到命中位置",
                "invariant": "每轮比较 nums[mid] 与 target，只保留仍可能包含目标值的有序区间",
                "frames": frames,
            },
            context=context,
            visual_need=visual_need,
        )
        if compiled is not None:
            return compiled
        return None
    elif is_house:
        title = "环形首尾相邻约束"
        nodes = [
            {"node_id": "house_0", "label": "第1间", "x": 0, "y": 0, "state": "highlighted"},
            {"node_id": "house_1", "label": "中间房屋", "x": 120, "y": 0},
            {"node_id": "house_last", "label": "最后一间", "x": 240, "y": 0, "state": "highlighted"},
        ]
        edges = [
            {"edge_id": "adjacent_0_1", "from": "house_0", "to": "house_1", "label": "相邻"},
            {"edge_id": "adjacent_last_0", "from": "house_last", "to": "house_0", "label": "环形相邻"},
        ]
        note = "第一间和最后一间在环上相邻，所以不能同时选择，需要拆成两个线性问题。"
        entry_items = exit_items = []
        layout = "flow"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": nodes[0]["node_id"]},
            {"kind": "highlight", "target": "node", "target_id": nodes[-1]["node_id"]},
        ]
    elif is_hero:
        compiled = _compile_data_structure_scene(
            {
                "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
                "structure": "array",
                "title": visual_need.get("diagram_goal") or "两个英雄相邻交换比较",
                "teaching_focus": "把两个相邻英雄的两种顺序摆在黑板上，只比较另一个人多等待的代价",
                "invariant": "相邻交换只影响这两个英雄之间的等待贡献",
                "frames": [
                    {
                        "frame_id": "a_before_b",
                        "title": "顺序 A -> B：B 多等 A 的回家时间",
                        "containers": [
                            {
                                "id": "order_ab",
                                "kind": "array",
                                "label": "队列顺序 A在前",
                                "items": [
                                    {"id": "A", "label": "A在前", "value": "T_A,S_A", "role": "focus"},
                                    {"id": "B", "label": "B在后", "value": "T_B,S_B"},
                                ],
                            },
                            {
                                "id": "cost_ab",
                                "kind": "array",
                                "label": "新增等待代价",
                                "items": [
                                    {"id": "cost_ab", "label": "B 等 A", "value": "T_A*S_B", "role": "focus"},
                                ],
                            },
                        ],
                        "annotations": [{"id": "compare_ab", "label": "A在前代价 = T_A*S_B"}],
                    },
                    {
                        "frame_id": "b_before_a",
                        "title": "顺序 B -> A：A 多等 B 的回家时间",
                        "operation": {"kind": "swap", "target": "A", "result": "比较两项更小者"},
                        "containers": [
                            {
                                "id": "order_ba",
                                "kind": "array",
                                "label": "队列顺序 B在前",
                                "items": [
                                    {"id": "B", "label": "B在前", "value": "T_B,S_B", "role": "focus"},
                                    {"id": "A", "label": "A在后", "value": "T_A,S_A"},
                                ],
                            },
                            {
                                "id": "cost_ba",
                                "kind": "array",
                                "label": "新增等待代价",
                                "items": [
                                    {"id": "cost_ba", "label": "A 等 B", "value": "T_B*S_A", "role": "focus"},
                                ],
                            },
                        ],
                        "annotations": [{"id": "rule", "label": "若 T_A*S_B < T_B*S_A，则 A 应在 B 前"}],
                    },
                ],
            },
            context=context,
            visual_need=visual_need,
        )
        if compiled is not None:
            return compiled
        title = "两个英雄相邻交换比较"
        nodes = [
            {"node_id": "hero_a", "label": "英雄A: T_A,S_A", "x": 0, "y": 0, "state": "highlighted"},
            {"node_id": "hero_b", "label": "英雄B: T_B,S_B", "x": 180, "y": 0, "state": "highlighted"},
            {"node_id": "rule", "label": "比较 T_A*S_B 与 T_B*S_A", "x": 90, "y": 110},
        ]
        edges = [
            {"edge_id": "a_waits_b", "from": "hero_a", "to": "rule", "label": "B等待A"},
            {"edge_id": "b_waits_a", "from": "hero_b", "to": "rule", "label": "A等待B"},
        ]
        note = "只比较相邻两个英雄的两种顺序，选择等待消耗更小的一种。"
        entry_items = exit_items = []
        layout = "flow"
        transition_actions = [
            {"kind": "highlight", "target": "node", "target_id": nodes[0]["node_id"]},
            {"kind": "highlight", "target": "node", "target_id": nodes[-1]["node_id"]},
        ]
    else:
        return None
    return {
        "scene_id": f"v3-fallback-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}",
        "title": visual_need.get("diagram_goal") or title,
        "diagram_kind": layout,
        "layout": layout,
        "teaching_goal": visual_need.get("diagram_goal") or title,
        "steps": [
            {
                "step_id": "observe_constraint",
                "order_index": 0,
                "title": title,
                "teaching_note": note,
                "entry_state": {"nodes": nodes, "edges": edges, "items": entry_items},
                "exit_state": {"nodes": nodes, "edges": edges, "items": exit_items},
                "transition": {
                    "actions": transition_actions,
                },
            }
        ],
    }


def _extract_example_array(source_text: str) -> list[int]:
    sample_match = re.search(r"(?:样例|示例|nums\s*=?)[:：\s\[]*([\s\S]*?)(?:输出|答案|最优|$)", source_text)
    candidate = sample_match.group(1) if sample_match else source_text
    candidate = re.split(r"(?:输出|答案|最优|解释|请讲|画图|定义|初始|到\s*-?\d+\s*时)", candidate, maxsplit=1)[0]
    values = [int(value) for value in re.findall(r"-?\d+", candidate)]
    if len(values) >= 2:
        return values
    return []


def _max_subarray_states(nums: list[int]) -> tuple[list[int], list[int]]:
    if not nums:
        return [], []
    dp_values = [nums[0]]
    best_values = [nums[0]]
    for value in nums[1:]:
        dp_values.append(max(value, dp_values[-1] + value))
        best_values.append(max(best_values[-1], dp_values[-1]))
    return dp_values, best_values


def _max_subarray_table_items(
    nums: list[int],
    dp_values: list[int],
    best_values: list[int],
    *,
    active_index: int,
    answer_index: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    headers = ("i", "nums[i]", "dp[i]", "best")
    for row, label in enumerate(headers):
        items.append({"id": f"row_{row}", "label": label, "row": row, "col": 0})
    for col, value in enumerate(nums, start=1):
        index = col - 1
        role = "focus" if index == active_index else ""
        answer_role = "focus" if index == answer_index and best_values[index] == best_values[answer_index] else role
        dp_label = f"dp[{index}]={dp_values[index]}" if role else str(dp_values[index])
        best_label = f"best={best_values[index]}" if answer_role else str(best_values[index])
        items.extend(
            [
                {"id": f"idx_{index}", "label": str(index), "row": 0, "col": col, "role": role},
                {"id": f"num_{index}", "label": str(value), "row": 1, "col": col, "role": role},
                {"id": f"dp_{index}", "label": dp_label, "row": 2, "col": col, "role": role},
                {"id": f"best_{index}", "label": best_label, "row": 3, "col": col, "role": answer_role},
            ]
        )
    return items


def _max_subarray_choice_note(nums: list[int], dp_values: list[int], index: int) -> str:
    if index <= 0:
        return f"第一个数只能自己开头：dp[0]={dp_values[0]}"
    extend = dp_values[index - 1] + nums[index]
    restart = nums[index]
    if restart >= extend:
        return f"nums[{index}]={nums[index]}，重新开始比接上 {extend} 更好"
    return f"nums[{index}]={nums[index]}，接上前一段得到 {extend} 更好"


def _binary_search_example(source_text: str) -> tuple[list[int], int] | None:
    arrays = re.findall(r"\[([^\[\]]+)\]", source_text)
    nums: list[int] = []
    for raw_array in arrays:
        values = [int(value) for value in re.findall(r"-?\d+", raw_array)]
        if len(values) >= 2:
            nums = values
            break
    if len(nums) < 2:
        return None

    lower_bound_match = re.search(r"(?:大于等于|大于或等于|不小于|>=|≥)\s*(-?\d+)", source_text)
    target_match = lower_bound_match or re.search(
        r"(?:target|目标值?|查找|寻找|找)\s*[=:：]?\s*(-?\d+)",
        source_text,
        flags=re.IGNORECASE,
    )
    target = int(target_match.group(1)) if target_match else nums[len(nums) // 2]
    return nums[:10], target


def _is_lower_bound_search(source_text: str) -> bool:
    compact = re.sub(r"\s+", "", source_text).lower()
    return any(
        marker in compact
        for marker in (
            "第一个大于等于",
            "第一个大于或等于",
            "第一个不小于",
            "第一个>=",
            "第一个≥",
            "lower_bound",
            "lower bound",
        )
    )


def _binary_search_frames(nums: list[int], target: int) -> list[dict[str, Any]]:
    low = 0
    high = len(nums) - 1
    frames: list[dict[str, Any]] = []
    found_index: int | None = None
    for _ in range(6):
        if low > high:
            break
        mid = (low + high) // 2
        value = nums[mid]
        relation = "命中目标" if value == target else ("mid 值偏小，下一轮右移 low" if value < target else "mid 值偏大，下一轮左移 high")
        frames.append(
            {
                "frame_id": f"bounds_{low}_{mid}_{high}",
                "title": f"low={low}, mid={mid}, high={high}：比较 nums[mid]={value}",
                "operation": {
                    "kind": "highlight",
                    "target": f"mid_{mid}",
                    "added": [f"low_{low}", f"mid_{mid}", f"high_{high}"],
                    "result": relation,
                },
                "containers": [
                    {
                        "id": "search_array",
                        "kind": "array",
                        "label": "有序数组",
                        "items": _binary_search_array_items(nums, low=low, mid=mid, high=high),
                    }
                ],
                "pointers": [
                    {"id": f"low_{low}", "label": f"low={low}", "target": f"idx_{low}"},
                    {"id": f"mid_{mid}", "label": f"mid={mid}", "target": f"idx_{mid}"},
                    {"id": f"high_{high}", "label": f"high={high}", "target": f"idx_{high}"},
                ],
                "annotations": [
                    {"id": f"target_{target}", "label": f"target={target}"},
                    {"id": f"decision_{len(frames)}", "label": relation},
                ],
            }
        )
        if value == target:
            found_index = mid
            break
        if value < target:
            low = mid + 1
        else:
            high = mid - 1
    if found_index is not None:
        items = _binary_search_array_items(nums, low=found_index, mid=found_index, high=found_index)
        annotation = f"在下标 {found_index} 找到目标值 {target}。"
        target_id = f"idx_{found_index}"
    else:
        items = _binary_search_array_items(nums, low=0, mid=0, high=len(nums) - 1)
        annotation = f"数组中不存在目标值 {target}。"
        target_id = ""
    frames.append(
        {
            "frame_id": f"search_result_{found_index if found_index is not None else 'missing'}",
            "title": annotation,
            "operation": ({"kind": "highlight", "target": target_id} if target_id else {"kind": "highlight"}),
            "containers": [
                {
                    "id": "search_array",
                    "kind": "array",
                    "label": "有序数组",
                    "items": items,
                }
            ],
            "pointers": (
                [{"id": f"answer_{found_index}", "label": f"答案下标={found_index}", "target": target_id}]
                if found_index is not None
                else []
            ),
            "annotations": [{"id": "search_result", "label": annotation}],
        }
    )
    return frames


def _lower_bound_frames(nums: list[int], target: int) -> list[dict[str, Any]]:
    low = 0
    high = len(nums) - 1
    candidate: int | None = None
    frames: list[dict[str, Any]] = []
    while low <= high:
        probe_low, probe_high = low, high
        mid = (low + high) // 2
        value = nums[mid]
        if value >= target:
            candidate = mid
            high = mid - 1
            decision = f"nums[{mid}]={value} >= {target}，記下候選並繼續向左找"
        else:
            low = mid + 1
            decision = f"nums[{mid}]={value} < {target}，答案只能在右側"
        frames.append(
            {
                "frame_id": f"lower_bound_{probe_low}_{mid}_{probe_high}",
                "title": f"比較 nums[{mid}]={value} 與 {target}",
                "operation": {
                    "kind": "highlight",
                    "target": f"idx_{mid}",
                    "added": [f"low_{probe_low}", f"mid_{mid}", f"high_{probe_high}"],
                    "result": decision,
                },
                "containers": [
                    {
                        "id": "search_array",
                        "kind": "array",
                        "label": "有序数组",
                        "items": _binary_search_array_items(
                            nums,
                            low=probe_low,
                            mid=mid,
                            high=probe_high,
                        ),
                    }
                ],
                "pointers": [
                    {"id": f"low_{probe_low}", "label": f"low={probe_low}", "target": f"idx_{probe_low}"},
                    {"id": f"mid_{mid}", "label": f"mid={mid}", "target": f"idx_{mid}"},
                    {"id": f"high_{probe_high}", "label": f"high={probe_high}", "target": f"idx_{probe_high}"},
                ],
                "annotations": [
                    {"id": f"target_{target}", "label": f"寻找第一个 >= {target}"},
                    {"id": f"decision_{len(frames)}", "label": decision},
                ],
            }
        )

    if candidate is None:
        result_title = f"没有元素大于等于 {target}"
        result_items = _binary_search_array_items(nums, low=0, mid=0, high=len(nums) - 1)
        result_pointers: list[dict[str, Any]] = []
        result_operation = {"kind": "highlight"}
    else:
        result_title = f"第一个大于等于 {target} 的位置是下标 {candidate}"
        result_items = _binary_search_array_items(nums, low=candidate, mid=candidate, high=candidate)
        result_pointers = [
            {"id": f"answer_{candidate}", "label": f"答案下标={candidate}", "target": f"idx_{candidate}"}
        ]
        result_operation = {"kind": "highlight", "target": f"idx_{candidate}"}
    frames.append(
        {
            "frame_id": f"lower_bound_result_{candidate if candidate is not None else 'missing'}",
            "title": result_title,
            "operation": result_operation,
            "containers": [
                {"id": "search_array", "kind": "array", "label": "有序数组", "items": result_items}
            ],
            "pointers": result_pointers,
            "annotations": [{"id": "lower_bound_result", "label": result_title}],
        }
    )
    return frames


def _binary_search_array_items(nums: list[int], *, low: int, mid: int, high: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, value in enumerate(nums):
        roles: list[str] = []
        if index == low:
            roles.append(f"low={low}")
        if index == mid:
            roles.append(f"mid={mid}")
        if index == high:
            roles.append(f"high={high}")
        role = "focus" if roles else ""
        label = f"{index}:{value}" + (f" ({', '.join(roles)})" if roles else "")
        items.append({"id": f"idx_{index}", "label": label, "value": str(value), "role": role})
    return items


def _request_context(context: dict[str, Any], visual_need: dict[str, Any]) -> dict[str, Any]:
    main_blocks = context.get("main_blocks") if isinstance(context.get("main_blocks"), dict) else {}
    plan_state = context.get("plan_state") if isinstance(context.get("plan_state"), dict) else {}
    return {
        "tutor_needs_diagram": True,
        "latest_user_message": context.get("user_message"),
        "visible_reply": main_blocks.get("visible_reply"),
        "diagram_goal": visual_need.get("diagram_goal"),
        "visual_focus": visual_need.get("visual_focus"),
        "current_step_key": plan_state.get("current_step_key"),
        "plan_state": plan_state,
    }


def _is_ambiguous_step(step: dict[str, Any]) -> bool:
    note = str(step.get("teaching_note") or "").strip()
    transition = step.get("transition") if isinstance(step.get("transition"), dict) else {}
    actions = transition.get("actions") if isinstance(transition, dict) else []
    entry_state = step.get("entry_state") if isinstance(step.get("entry_state"), dict) else {}
    exit_state = step.get("exit_state") if isinstance(step.get("exit_state"), dict) else {}
    visible_count = _visible_element_count(entry_state) + _visible_element_count(exit_state)
    return not note or not isinstance(actions, list) or len(actions) == 0 or visible_count == 0


def _visible_element_count(state: dict[str, Any]) -> int:
    return sum(
        len(value)
        for key in ("nodes", "edges", "items")
        if isinstance((value := state.get(key)), list)
    )


def _with_narration_metadata(scene: dict[str, Any]) -> dict[str, Any]:
    steps = scene.get("steps")
    enhanced = dict(scene)
    enhanced["presentation_style"] = "guided_animation"
    enhanced["narration_hint"] = "先看起点，再看变化，最后说明结果为什么成立。"
    if isinstance(steps, list):
        enhanced["steps"] = [
            _with_step_narration(step) if isinstance(step, dict) else step
            for step in steps
        ]
    return enhanced


def _normalize_scene(
    scene: dict[str, Any],
    *,
    context: dict[str, Any],
    visual_need: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(scene)
    steps = normalized.get("steps") if isinstance(normalized.get("steps"), list) else []
    first_step = next((step for step in steps if isinstance(step, dict)), {})
    normalized.setdefault("scene_id", f"v3-diagram-{context.get('workspace_id') or 'workspace'}-{context.get('source_turn_index') or 0}")
    normalized.setdefault("session_id", str(context.get("session_uuid") or ""))
    normalized.setdefault("workspace_id", context.get("workspace_id"))
    normalized.setdefault("teaching_step_id", None)
    normalized.setdefault("parent_scene_id", None)
    normalized.setdefault("base_step_id", None)
    normalized.setdefault("inherit_mode", "new")
    normalized.setdefault("title", visual_need.get("diagram_goal") or "图示讲解")
    normalized["layout"] = _safe_layout(normalized.get("layout"))
    normalized.setdefault("teaching_goal", visual_need.get("diagram_goal") or normalized.get("title") or "图示讲解")
    normalized.setdefault("default_step_id", first_step.get("step_id") if isinstance(first_step, dict) else None)
    normalized.setdefault("status", "verified")
    normalized.setdefault("source_turn_index", int(context.get("source_turn_index") or 0))
    normalized["steps"] = [
        _normalize_step(step, index=index)
        for index, step in enumerate(steps)
        if isinstance(step, dict)
    ]
    return normalized


def _normalize_step(step: dict[str, Any], *, index: int) -> dict[str, Any]:
    normalized = dict(step)
    normalized.setdefault("order_index", index)
    transition = normalized.get("transition") if isinstance(normalized.get("transition"), dict) else {}
    actions = transition.get("actions") if isinstance(transition.get("actions"), list) else []
    normalized["transition"] = {
        **transition,
        "actions": [_normalize_action(action) for action in actions if isinstance(action, dict)],
    }
    return normalized


def _normalize_action(action: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(action)
    normalized["kind"] = str(normalized.get("kind") or normalized.get("action_kind") or "focus")
    normalized["target_id"] = str(
        normalized.get("target_id")
        or normalized.get("item_id")
        or normalized.get("node_id")
        or normalized.get("edge_id")
        or "current"
    )
    return normalized


def _safe_layout(value: Any) -> str:
    raw = str(value or "freeform")
    if raw == "horizontal_flow":
        return "flow"
    allowed = {"freeform", "array", "linked_list", "stack", "queue", "deque", "tree", "graph", "matrix", "table", "flow"}
    return raw if raw in allowed else "freeform"


def _with_step_narration(step: dict[str, Any]) -> dict[str, Any]:
    enhanced = dict(step)
    transition = step.get("transition") if isinstance(step.get("transition"), dict) else {}
    actions = transition.get("actions") if isinstance(transition, dict) else []
    enhanced["narration"] = {
        "watch": str(step.get("title") or "先看当前高亮区域"),
        "change": _describe_actions(actions if isinstance(actions, list) else []),
        "why": str(step.get("teaching_note") or "这一步说明当前状态如何变成下一状态。"),
    }
    return enhanced


def _describe_actions(actions: list[Any]) -> str:
    descriptions = [_describe_action(action) for action in actions if isinstance(action, dict)]
    return "，".join(description for description in descriptions if description) or "观察高亮区域的变化"


def _describe_action(action: dict[str, Any]) -> str:
    target_id = str(action.get("target_id") or "当前元素")
    label = _human_target_label(target_id)
    kind = action.get("kind")
    if kind == "highlight":
        return f"强调 {label}"
    if kind == "focus":
        return f"聚焦 {label}"
    if kind == "add":
        return f"加入 {label}"
    if kind == "remove":
        return f"移出 {label}"
    if kind == "move":
        return f"移动 {label}"
    return f"观察 {label}"


def _human_target_label(target_id: str) -> str:
    return target_id.replace("_", " ").replace("-", " ")
