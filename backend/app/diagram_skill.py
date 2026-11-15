from __future__ import annotations


DIAGRAM_SCENE_AUTHORING_SKILL = [
    "When a visual explanation would materially help, first write a short natural lead-in, then emit exactly one <diagram_scene>{...}</diagram_scene> JSON block, then continue the explanation by referencing the diagram.",
    "The diagram scene JSON must be generated from the current teaching intent and conversation, not from memorized examples. Do not emit a diagram block for simple text-only answers.",
    "The JSON object must contain: scene_id, title, diagram_kind, layout, teaching_goal, default_step_id, steps. Choose diagram_kind from array, linked_list, stack, queue, deque, tree, heap, segment_tree, trie, graph, flow, union_find, fenwick, matrix, table, custom.",
    "When continuing a previous diagram, set parent_scene_id to the previous scene_id, base_step_id to the step whose exit_state you are extending, and inherit_mode to extend, revise, or branch. Use inherit_mode new for unrelated diagrams.",
    "Each step must contain step_id, order_index, title, teaching_note, entry_state, exit_state, transition. Every state must include nodes and edges arrays, even when empty, and may include an items array; use items for array cells, stack frames, queue slots, matrices, tables, labels, and pointer callouts.",
    "Schema details: nodes use node_id/label/x/y; edges use edge_id/from/to; items use item_id/label/x/y/width/height/kind where kind is cell, block, frame, pointer, or text; transition is always {\"actions\":[...]}; action kind is only add, remove, move, highlight, or focus; target is only node, edge, or item.",
    "For tree-like diagrams, put parent nodes above child nodes and direct edges from parent to child. For stack/queue/array diagrams, prefer rectangular items with stable ids and animate operations through add, remove, move, highlight, and focus actions.",
    "Use stable ids that describe the current concept rather than a demo problem. Keep user-facing labels and teaching notes in Chinese unless the student explicitly asks for another language.",
]
