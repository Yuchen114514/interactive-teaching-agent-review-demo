from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.diagram_protocol import DiagramTeachingBundleModel


TREE_LIKE_KINDS = {"tree", "heap", "segment_tree", "trie", "union_find", "fenwick"}


def verify_diagram_teaching_bundle(
    *,
    bundle: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    try:
        validated = DiagramTeachingBundleModel.model_validate(bundle)
    except ValidationError:
        return {"ok": False, "reason": "invalid_bundle"}

    if not validated.preface.strip():
        return {"ok": False, "reason": "missing_preface"}
    if validated.inline_payload.scene_id != validated.scene.scene_id:
        return {"ok": False, "reason": "scene_id_mismatch"}
    if validated.inline_payload.step_count != len(validated.scene.steps):
        return {"ok": False, "reason": "step_count_mismatch"}
    if not validated.scene.scene_id.strip():
        return {"ok": False, "reason": "missing_scene_id"}
    if not validated.scene.steps:
        return {"ok": False, "reason": "missing_steps"}
    if validated.scene.inherit_mode != "new" and not validated.scene.parent_scene_id:
        return {"ok": False, "reason": "missing_parent_scene"}

    step_ids = {step.step_id for step in validated.scene.steps}
    default_step_id = validated.inline_payload.default_step_id or validated.scene.default_step_id
    if default_step_id is not None and default_step_id not in step_ids:
        return {"ok": False, "reason": "default_step_missing"}

    for step in validated.scene.steps:
        entry_node_ids = {node.node_id for node in step.entry_state.nodes}
        entry_edge_ids = {edge.edge_id for edge in step.entry_state.edges}
        entry_item_ids = {item.item_id for item in step.entry_state.items}
        exit_node_ids = {node.node_id for node in step.exit_state.nodes}
        exit_edge_ids = {edge.edge_id for edge in step.exit_state.edges}
        exit_item_ids = {item.item_id for item in step.exit_state.items}
        known_node_ids = entry_node_ids | exit_node_ids
        known_edge_ids = entry_edge_ids | exit_edge_ids
        known_item_ids = entry_item_ids | exit_item_ids
        for edge in [*step.entry_state.edges, *step.exit_state.edges]:
            if edge.from_ not in known_node_ids or edge.to not in known_node_ids:
                return {"ok": False, "reason": "edge_endpoint_missing"}
        if not _tree_layout_is_valid(validated.scene.diagram_kind, validated.scene.layout, step):
            return {"ok": False, "reason": "invalid_tree_layout"}
        for action in step.transition.actions:
            if action.target == "node" and action.target_id not in known_node_ids:
                return {"ok": False, "reason": "transition_target_missing"}
            if action.target == "edge" and action.target_id not in known_edge_ids:
                return {"ok": False, "reason": "transition_target_missing"}
            if action.target == "item" and action.target_id not in known_item_ids:
                return {"ok": False, "reason": "transition_target_missing"}
            if action.kind in {"add", "remove", "highlight"} and action.target is None:
                return {"ok": False, "reason": "transition_target_missing"}
            if action.kind == "move" and action.target not in {"node", "item"}:
                return {"ok": False, "reason": "transition_target_missing"}

    scene = validated.scene.model_copy(
        update={
            "session_id": session_id,
            "status": "verified",
            "default_step_id": default_step_id,
            "inherit_mode": validated.scene.inherit_mode,
        }
    )
    return {"ok": True, "reason": "ok", "scene": scene.model_dump(by_alias=True)}


def _tree_layout_is_valid(diagram_kind: str, layout: str, step: Any) -> bool:
    if diagram_kind not in TREE_LIKE_KINDS and layout != "tree":
        return True

    for state in (step.entry_state, step.exit_state):
        node_by_id = {node.node_id: node for node in state.nodes}
        for edge in state.edges:
            parent = node_by_id.get(edge.from_)
            child = node_by_id.get(edge.to)
            if parent is None or child is None:
                continue
            if child.y <= parent.y:
                return False
    return True
