"""Semantic scene validation and deterministic, data-structure-aware SVG layout."""
from __future__ import annotations

import html
import math
import re
from collections import defaultdict
from typing import Any

STRUCTURES = {
    "array", "matrix", "dp_table", "linked_list", "doubly_linked_list", "circular_list",
    "stack", "monotonic_stack", "queue", "deque", "monotonic_queue", "heap",
    "priority_queue", "tree", "bst", "avl_tree", "red_black_tree", "trie",
    "segment_tree", "graph", "directed_graph", "weighted_graph", "dag", "union_find",
    "rod", "multi",
}
TREE_KINDS = {"tree", "bst", "avl_tree", "red_black_tree", "heap", "priority_queue", "trie", "segment_tree", "union_find"}
GRAPH_KINDS = {"graph", "directed_graph", "weighted_graph", "dag"}
LINEAR_KINDS = {"stack", "monotonic_stack", "queue", "deque", "monotonic_queue",
                "linked_list", "doubly_linked_list", "circular_list"}
CELL_KINDS = {"matrix", "dp_table"}


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def normalize_scene(scene: dict) -> dict:
    """Wrap legacy frame-level items in one container without modifying the input."""
    result = {**scene}
    result["frames"] = []
    for frame in scene.get("frames", []):
        if not isinstance(frame, dict):
            result["frames"].append(frame)
            continue
        current = {**frame}
        if "containers" not in current:
            kind = scene.get("structure", "array")
            current["containers"] = [{
                "id": "primary", "kind": kind, "label": scene.get("title", kind),
                "items": current.get("items", []), "edges": current.get("edges", []),
                "pointers": current.get("pointers", []),
            }]
        result["frames"].append(current)
    return result


def _tree_info(items: list[dict]) -> tuple[list[str], dict[str, list[dict]], dict[str, dict]]:
    by_id = {i["id"]: i for i in items}
    children: dict[str, list[dict]] = defaultdict(list)
    roots = []
    for item in items:
        parent = item.get("parent")
        if parent and parent != item["id"]:
            children[parent].append(item)
        else:
            roots.append(item["id"])
    return roots, children, by_id


def _tree_invariants(container: dict) -> list[str]:
    kind = container["kind"]
    items = container["items"]
    if not items:
        return []
    roots, children, by_id = _tree_info(items)
    errors = []
    if len(roots) != 1:
        return [f"{kind} 需要唯一根节点"]
    root = roots[0]
    visited: set[str] = set()
    visiting: set[str] = set()

    def walk(node: str) -> tuple[int, set[int]]:
        if node in visiting:
            errors.append(f"{kind} 存在父指针环")
            return 0, {0}
        if node in visited:
            errors.append(f"{kind} 存在重复父子引用")
            return 0, {0}
        visiting.add(node)
        offspring = children.get(node, [])
        sides = [i.get("side") for i in offspring if i.get("side")]
        if kind in {"bst", "avl_tree", "red_black_tree", "heap", "priority_queue", "segment_tree"} and len(sides) != len(set(sides)):
            errors.append(f"{kind} 同一父节点出现重复的左右侧")
        heights = []
        black_heights = []
        for child in offspring:
            child_height, paths = walk(child["id"])
            heights.append(child_height)
            black_heights.extend(paths)
            if kind == "red_black_tree" and by_id[node].get("color") == "red" and child.get("color") == "red":
                errors.append("红黑树出现相邻红节点")
        if kind in {"avl_tree", "red_black_tree"}:
            for side in ("left", "right"):
                if not any(i.get("side") == side for i in offspring):
                    heights.append(0)
                    black_heights.append(1)
        height = 1 + (max(heights) if heights else 0)
        if kind == "avl_tree":
            side_heights = {i.get("side"): _subtree_height(i["id"], children, set()) for i in offspring}
            if abs(side_heights.get("left", 0) - side_heights.get("right", 0)) > 1:
                errors.append(f"AVL 节点 {by_id[node].get('label')} 失衡")
            stated = by_id[node].get("height")
            if stated is not None and stated != height:
                errors.append(f"AVL 节点 {by_id[node].get('label')} 高度应为 {height}")
        if kind == "red_black_tree":
            if node == root and by_id[node].get("color") != "black":
                errors.append("红黑树根节点必须为黑色")
            if by_id[node].get("color") not in {"red", "black"}:
                errors.append("红黑树节点缺少 red/black 颜色")
            if len(set(black_heights)) > 1:
                errors.append("红黑树各路径黑高不一致")
        visiting.remove(node)
        visited.add(node)
        return height, {h + (1 if by_id[node].get("color") == "black" else 0) for h in black_heights} if black_heights else {2 if by_id[node].get("color") == "black" else 1}

    walk(root)
    if len(visited) != len(items):
        errors.append(f"{kind} 存在与根不相连的节点")
    return errors


def _subtree_height(node: str, children: dict[str, list[dict]], seen: set[str]) -> int:
    if node in seen:
        return 0
    seen.add(node)
    return 1 + max((_subtree_height(i["id"], children, seen.copy()) for i in children.get(node, [])), default=0)


def _linear_transition(before: dict, after: dict) -> list[str]:
    kind = after.get("kind")
    if kind not in LINEAR_KINDS or before.get("kind") != kind:
        return []
    old = [item["id"] for item in before.get("items", []) if isinstance(item, dict) and "id" in item]
    new = [item["id"] for item in after.get("items", []) if isinstance(item, dict) and "id" in item]
    if old == new:
        return []
    added = set(new) - set(old)
    removed = set(old) - set(new)
    if len(added) + len(removed) != 1:
        return []  # A key frame may summarize more than one operation.
    if added:
        if kind in {"stack", "monotonic_stack"} and new[:-1] != old:
            return ["入栈节点必须放在栈顶"]
        if kind in {"queue", "monotonic_queue"} and new[:-1] != old:
            return ["入队节点必须放在队尾"]
        if kind == "deque" and new[1:] != old and new[:-1] != old:
            return ["双端队列只能从两端插入"]
    if removed:
        if kind in {"stack", "monotonic_stack"} and old[:-1] != new:
            return ["出栈节点必须来自栈顶"]
        if kind in {"queue", "monotonic_queue"} and old[1:] != new:
            return ["出队节点必须来自队首"]
        if kind == "deque" and old[1:] != new and old[:-1] != new:
            return ["双端队列只能从两端弹出"]
    return []


def _transfer_errors(frame: dict, current: dict[str, dict], previous: dict[str, dict]) -> list[str]:
    transfers = frame.get("transfers", [])
    if not isinstance(transfers, list):
        return ["transfers 必须是数组"]
    errors = []
    for index, transfer in enumerate(transfers):
        prefix = f"跨结构转移 {index + 1}"
        if not isinstance(transfer, dict) or transfer.get("kind") not in {"copy", "move"}:
            errors.append(prefix + "类型必须是 copy 或 move")
            continue
        source, target = transfer.get("from"), transfer.get("to")
        if not isinstance(source, dict) or not isinstance(target, dict):
            errors.append(prefix + "缺少源或目标")
            continue
        sc, si = source.get("container"), source.get("item")
        tc, ti = target.get("container"), target.get("item")
        if sc == tc or sc not in current or tc not in current:
            errors.append(prefix + "必须连接两个当前可见的不同容器")
            continue
        source_state = current if transfer["kind"] == "copy" else previous
        source_item = next((item for item in source_state.get(sc, {}).get("items", []) if item.get("id") == si), None)
        target_item = next((item for item in current[tc].get("items", []) if item.get("id") == ti), None)
        if source_item is None or target_item is None:
            errors.append(prefix + "源或目标节点不存在于相应状态")
            continue
        if source_item.get("value", source_item.get("label")) != target_item.get("value", target_item.get("label")):
            errors.append(prefix + "源与目标的值不一致")
        if transfer["kind"] == "move" and any(item.get("id") == si for item in current[sc].get("items", [])):
            errors.append(prefix + "移动后源节点仍在原容器")
        if transfer["kind"] == "copy" and previous and any(item.get("id") == ti for item in previous.get(tc, {}).get("items", [])):
            errors.append(prefix + "复制目标不是新节点")
    return errors


def validate_scene(raw: Any, expected: str | None = None, max_frames: int = 6) -> list[str]:
    if not isinstance(raw, dict):
        return ["模型输出不是 JSON 对象"]
    scene = normalize_scene(raw)
    kind = scene.get("structure")
    errors = []
    if kind not in STRUCTURES:
        errors.append("structure 不在许可列表")
    frames = scene.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= max_frames:
        return errors + [f"frames 必须有 1 至 {max_frames} 帧"]
    if expected and kind != expected:
        all_containers = [c for f in frames if isinstance(f, dict) for c in f.get("containers", []) if isinstance(c, dict)]
        container_kinds = {c.get("kind") for c in all_containers}
        compatible = expected in container_kinds or (expected == "multi" and any(len(f.get("containers", [])) >= 2 for f in frames if isinstance(f, dict)))
        if expected == "bst" and kind == "tree":
            compatible = any(_bst_invariant(c.get("items", [])) for c in all_containers if c.get("kind") == "tree")
        if not compatible:
            errors.append(f"结构类型错误：期望 {expected}，得到 {kind}")
    frame_ids = set()
    previous_ids: set[str] = set()
    previous_rods: dict[str, list[dict]] | None = None
    previous_containers: dict[str, dict] = {}
    for fi, frame in enumerate(frames):
        prefix = f"第 {fi + 1} 帧"
        if not isinstance(frame, dict):
            errors.append(prefix + "不是对象")
            continue
        frame_id = frame.get("id")
        if not isinstance(frame_id, str) or not frame_id or frame_id in frame_ids:
            errors.append(prefix + " id 缺失或重复")
        frame_ids.add(frame_id)
        containers = frame.get("containers")
        if not isinstance(containers, list) or not 1 <= len(containers) <= 12:
            errors.append(prefix + " containers 应为 1 至 12 项")
            continue
        all_ids: set[str] = set()
        container_ids: set[str] = set()
        valid_containers = []
        for ci, container in enumerate(containers):
            cp = f"{prefix}容器 {ci + 1}"
            if not isinstance(container, dict):
                errors.append(cp + "不是对象")
                continue
            cid = container.get("id")
            ckind = container.get("kind")
            if not isinstance(cid, str) or not cid or cid in container_ids:
                errors.append(cp + " id 缺失或重复")
            container_ids.add(cid)
            if ckind not in STRUCTURES - {"multi"}:
                errors.append(cp + " kind 不受支持")
            items = container.get("items")
            if not isinstance(items, list) or len(items) > 50:
                errors.append(cp + " items 应为不超过 50 项的数组")
                continue
            if not items and ckind not in LINEAR_KINDS | {"rod"}:
                errors.append(cp + "缺少节点")
            ids = set()
            sizes = []
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    errors.append(cp + "存在无效节点")
                    continue
                iid = item["id"]
                if iid in all_ids:
                    errors.append(prefix + "节点 id 跨容器重复")
                all_ids.add(iid)
                ids.add(iid)
                if not isinstance(item.get("label"), (str, int, float)):
                    errors.append(cp + f"节点 {iid} 缺少可见 label")
                if ckind in CELL_KINDS and not all(isinstance(item.get(k), int) and item[k] >= 0 for k in ("row", "col")):
                    errors.append(cp + "矩阵节点缺少 row/col")
                if ckind == "rod":
                    size = item.get("size")
                    if not isinstance(size, (int, float)) or size <= 0:
                        errors.append(cp + "盘片缺少正数 size")
                    else:
                        sizes.append(size)
                if ckind == "red_black_tree" and item.get("color") not in {"red", "black"}:
                    errors.append(cp + "红黑树节点需要 red/black 颜色")
            if ckind == "rod" and sizes != sorted(sizes, reverse=True):
                errors.append(cp + "盘片须按从底到顶的尺寸降序排列")
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("next", "prev", "parent"):
                    if item.get(key) is not None and item[key] not in ids:
                        errors.append(cp + f" {key} 指向不存在节点")
            if ckind in {"linked_list", "doubly_linked_list", "circular_list"} and len(items) > 1 and not any(isinstance(i, dict) and i.get("next") for i in items):
                errors.append(cp + "链表缺少 next 关系")
            edges = container.get("edges", [])
            if not isinstance(edges, list):
                errors.append(cp + " edges 不是数组")
            else:
                for edge in edges:
                    if not isinstance(edge, dict) or edge.get("from") not in ids or edge.get("to") not in ids:
                        errors.append(cp + "存在悬空边")
            if ckind in GRAPH_KINDS and len(items) > 1 and not edges:
                errors.append(cp + "图缺少边")
            pointers = container.get("pointers", [])
            if not isinstance(pointers, list):
                errors.append(cp + " pointers 不是数组")
            else:
                for pointer in pointers:
                    if not isinstance(pointer, dict) or pointer.get("target") not in ids:
                        errors.append(cp + "存在悬空指针")
            valid_containers.append(container)
            if cid in previous_containers:
                errors.extend(prefix + " " + message for message in _linear_transition(previous_containers[cid], container))
        operation = frame.get("operation")
        if isinstance(operation, dict) and operation.get("target"):
            allowed = all_ids | container_ids | previous_ids
            if operation["target"] not in allowed:
                errors.append(prefix + "操作目标不存在于相应帧")
        errors.extend(prefix + " " + message for message in _transfer_errors(frame, {c["id"]: c for c in valid_containers}, previous_containers))
        if fi == len(frames) - 1:
            for container in valid_containers:
                final_kind = container.get("kind")
                if final_kind in {"avl_tree", "red_black_tree"}:
                    errors.extend(_tree_invariants(container))
                if final_kind in {"bst", "avl_tree", "red_black_tree"} and all(_numeric_label(i) is not None for i in container.get("items", [])):
                    if not _bst_invariant(container["items"]):
                        errors.append(prefix + " BST 左右子树顺序不正确")
                if final_kind in {"heap", "priority_queue"}:
                    errors.extend(_heap_invariant(container))
        current_rods = {c["id"]: c["items"] for c in valid_containers if c.get("kind") == "rod"}
        if previous_rods is not None and len(current_rods) == len(valid_containers) and current_rods.keys() == previous_rods.keys():
            old_owner = {item["id"]: cid for cid, items in previous_rods.items() for item in items}
            new_owner = {item["id"]: cid for cid, items in current_rods.items() for item in items}
            if old_owner.keys() != new_owner.keys():
                errors.append(prefix + "盘片集合在相邻状态间发生变化")
            moved = [iid for iid in old_owner.keys() & new_owner.keys() if old_owner[iid] != new_owner[iid]]
            if len(moved) == 1:
                disk = moved[0]
                source = previous_rods[old_owner[disk]]
                target = current_rods[new_owner[disk]]
                if source[-1]["id"] != disk or target[-1]["id"] != disk:
                    errors.append(prefix + "移动的盘片必须来自源杆顶部并落在目标杆顶部")
                old_item = next(item for item in source if item["id"] == disk)
                if old_item.get("size") != target[-1].get("size"):
                    errors.append(prefix + "盘片尺寸在移动后发生变化")
        previous_rods = current_rods if len(current_rods) == len(valid_containers) else None
        previous_containers = {c["id"]: c for c in valid_containers}
        previous_ids = all_ids
    return list(dict.fromkeys(errors))


def _bst_invariant(items: list[dict]) -> bool:
    if not items:
        return False
    roots, children, by_id = _tree_info(items)
    if len(roots) != 1:
        return False
    seen = set()
    def walk(node: str, lo: float, hi: float) -> bool:
        if node in seen:
            return False
        seen.add(node)
        try:
            value = float(by_id[node]["label"])
        except (ValueError, TypeError, KeyError):
            return False
        if not lo < value < hi:
            return False
        for child in children.get(node, []):
            side = child.get("side")
            if side == "left":
                if not walk(child["id"], lo, value): return False
            elif side == "right":
                if not walk(child["id"], value, hi): return False
            else:
                return False
        return True
    return walk(roots[0], -math.inf, math.inf) and len(seen) == len(items)


def _numeric_label(item: dict) -> float | None:
    try:
        return float(item.get("value", item.get("label")))
    except (ValueError, TypeError):
        return None


def _heap_invariant(container: dict) -> list[str]:
    items = container.get("items", [])
    if not items or not all(_numeric_label(item) is not None for item in items):
        return []
    roots, children, _ = _tree_info(items)
    if len(roots) != 1:
        return ["堆需要唯一根节点"]
    maximum = container.get("heap_order") == "max"
    errors = []
    for parent in items:
        for child in children.get(parent["id"], []):
            p, c = _numeric_label(parent), _numeric_label(child)
            if (p < c if maximum else p > c):
                errors.append("堆序不正确：父节点与子节点大小关系冲突")
    return errors


def layout_containers(containers: list[dict], link_gutter: bool = False) -> tuple[int, int, list[tuple[float, float, float, float]]]:
    """Bounded overview layout; repeated structures use the same policy at any count."""
    n = len(containers)
    width = 960
    margin = 20
    gap = 44 if link_gutter else 12
    rods = n >= 4 and all(c.get("kind") == "rod" for c in containers)
    if rods:
        cols = n if n <= 7 else math.ceil(n / 2)
        cell_h = 270
    elif n <= 3:
        cols = n
        cell_h = 260
    else:
        cols = 3 if n <= 9 else 4
        cell_h = 220
    cell_h = max([cell_h, *(110 + 54 * _tree_max_depth(c) for c in containers if c.get("kind") in TREE_KINDS)])
    rows = math.ceil(n / cols)
    cell_w = (width - 2 * margin - (cols - 1) * gap) / cols
    height = int(2 * margin + rows * cell_h + (rows - 1) * gap)
    boxes = []
    for index in range(n):
        row, col = divmod(index, cols)
        boxes.append((margin + col * (cell_w + gap), margin + row * (cell_h + gap), cell_w, cell_h))
    return width, height, boxes


def _tree_max_depth(container: dict) -> int:
    items = container.get("items", [])
    parent = {item["id"]: item.get("parent") for item in items if isinstance(item, dict) and "id" in item}
    for edge in container.get("edges", []):
        if isinstance(edge, dict) and edge.get("to") in parent and not parent[edge["to"]]:
            parent[edge["to"]] = edge.get("from")
    def depth(iid: str, seen: set[str]) -> int:
        predecessor = parent.get(iid)
        if not predecessor or predecessor in seen or predecessor not in parent:
            return 0
        return 1 + depth(predecessor, seen | {iid})
    return max((depth(iid, set()) for iid in parent), default=0)


def _svg_text(x: float, y: float, value: Any, size: int = 14, fill: str = "#eaf3ff", anchor: str = "middle", extra: str = "") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{fill}" font-size="{size}" font-family="-apple-system,system-ui,sans-serif" {extra}>{_escape(value)}</text>'


def _motion_open(item: dict, label: Any = None) -> str:
    return f'<g data-motion-item="{_escape(item["id"])}" data-motion-label="{_escape(item.get("label", "") if label is None else label)}">'


def _array_label(item: dict) -> str:
    return re.sub(r"^\s*(?:下标)?\d+\s*[:：]\s*", "", str(item.get("label", "")))


def _linear_svg(kind: str, items: list[dict], w: float, h: float, focus: str | None, rod_max_size: float = 1) -> list[str]:
    parts = []
    if kind in {"linked_list", "doubly_linked_list", "circular_list"}:
        if not items:
            return [_svg_text(w/2, h/2, "空链表", 13, "#8ea6c0")]
        gap = min(112, (w - 58) / len(items))
        cell_w = min(68, gap - 26)
        start = (w - (len(items)-1)*gap)/2
        positions = {node["id"]: start + index*gap for index, node in enumerate(items)}
        cy = h/2 + 4
        for node in items:
            cx = positions[node["id"]]
            parts.append(_motion_open(node))
            parts.append(f'<rect data-item="{_escape(node["id"])}" x="{cx-cell_w/2:.1f}" y="{cy-24:.1f}" width="{cell_w:.1f}" height="48" rx="8" fill="{("#1b5864" if node["id"] == focus else "#263b53")}" stroke="#718ca8"/>')
            parts.append(_svg_text(cx, cy+5, node.get("label", ""), 14))
            parts.append('</g>')
        for node in items:
            source = positions[node["id"]]
            target_id = node.get("next")
            if target_id in positions:
                target = positions[target_id]
                if kind == "circular_list" and target < source:
                    y = cy + 48
                    parts.append(f'<path data-role="wrap-arrow" d="M{source:.1f} {cy+24:.1f} V{y:.1f} H{target:.1f} V{cy+29:.1f}" fill="none" stroke="#7dd2be" stroke-width="2"/>')
                    parts.append(f'<path d="M{target-4:.1f} {cy+33:.1f} L{target:.1f} {cy+27:.1f} L{target+4:.1f} {cy+33:.1f}" fill="none" stroke="#7dd2be" stroke-width="2"/>')
                elif target > source:
                    x1 = source + cell_w/2 + 3; x2 = target - cell_w/2 - 5
                    parts.append(f'<path data-role="next-arrow" d="M{x1:.1f} {cy-8:.1f} H{x2:.1f} m-6 -4 l6 4 -6 4" fill="none" stroke="#7dd2be" stroke-width="2"/>')
            previous_id = node.get("prev")
            if kind == "doubly_linked_list" and previous_id in positions:
                target = positions[previous_id]
                if target < source:
                    x1 = source - cell_w/2 - 3; x2 = target + cell_w/2 + 5
                    parts.append(f'<path data-role="prev-arrow" d="M{x1:.1f} {cy+9:.1f} H{x2:.1f} m6 -4 l-6 4 6 4" fill="none" stroke="#bba4e6" stroke-width="2"/>')
        return parts
    if kind in {"stack", "monotonic_stack"}:
        slot_h = min(38, (h - 86) / max(1, len(items)))
        box_w = min(124, w - 80)
        cx = w / 2
        base = h - 22
        top = base - max(1, len(items)) * slot_h - 10
        parts.append(f'<path data-role="stack-wall" d="M{cx-box_w/2-7:.1f} {top:.1f} V{base:.1f} H{cx+box_w/2+7:.1f} V{top:.1f}" fill="none" stroke="#7494b3" stroke-width="3" stroke-linecap="round"/>')
        for index, item in enumerate(items):
            y = base - (index + 1) * slot_h + 2
            active = item["id"] == focus or index == len(items) - 1
            parts.append(_motion_open(item))
            parts.append(f'<rect data-item="{_escape(item["id"])}" x="{cx-box_w/2:.1f}" y="{y:.1f}" width="{box_w:.1f}" height="{slot_h-4:.1f}" rx="8" fill="{("#1b5864" if active else "#263b53")}" stroke="{("#75dec6" if active else "#536e88")}"/>')
            parts.append(_svg_text(cx, y + slot_h / 2 + 5, item.get("label", ""), 16))
            parts.append('</g>')
        if items:
            parts.append(_svg_text(cx + box_w/2 + 16, base - len(items)*slot_h + slot_h/2 + 7, "TOP", 12, "#edc37f", "start", 'data-role="top"'))
        else:
            parts.append(_svg_text(cx, base-20, "空栈", 13, "#8ea6c0"))
        return parts
    if kind in {"queue", "deque", "monotonic_queue"}:
        track_x, track_y = 30, h / 2 - 34
        track_w = w - 60
        parts.append(f'<rect data-role="{("deque-track" if kind == "deque" else "queue-track")}" x="{track_x:.1f}" y="{track_y:.1f}" width="{track_w:.1f}" height="68" rx="11" fill="#17283b" stroke="#66809a" stroke-width="2"/>')
        slot_w = min(63, (track_w - 20) / max(1, len(items)))
        used = slot_w * len(items)
        start = w / 2 - used / 2
        for index, item in enumerate(items):
            x = start + index * slot_w
            active = item["id"] == focus or index == 0
            parts.append(_motion_open(item))
            parts.append(f'<rect data-item="{_escape(item["id"])}" x="{x+2:.1f}" y="{track_y+7:.1f}" width="{slot_w-4:.1f}" height="54" rx="8" fill="{("#1b5864" if active else "#263b53")}" stroke="{("#75dec6" if active else "#536e88")}"/>')
            parts.append(_svg_text(x + slot_w/2, track_y + 41, item.get("label", ""), 15))
            parts.append('</g>')
        parts.append(_svg_text(track_x, track_y - 9, "FRONT", 11, "#edc37f", "start", 'data-role="front"'))
        parts.append(_svg_text(track_x+track_w, track_y - 9, "REAR", 11, "#edc37f", "end", 'data-role="rear"'))
        parts.append(_svg_text(track_x+12, track_y+92, "← 出队" if kind != "deque" else "↔ 前端出入", 12, "#92aac2", "start", 'data-role="front-entry"'))
        parts.append(_svg_text(track_x+track_w-12, track_y+92, "入队 ←" if kind != "deque" else "后端出入 ↔", 12, "#92aac2", "end", 'data-role="back-entry"'))
        return parts
    if kind == "rod":
        cx = w / 2; base = h - 24
        parts.append(f'<line data-role="rod" x1="{cx:.1f}" y1="53" x2="{cx:.1f}" y2="{base:.1f}" stroke="#a2b7ca" stroke-width="5" stroke-linecap="round"/>')
        parts.append(f'<rect x="14" y="{base:.1f}" width="{w-28:.1f}" height="7" rx="3" fill="#8096ac"/>')
        slot_h = min(27, (h - 85) / max(1, len(items)))
        for index, item in enumerate(items):
            disk_w = min(w - 35, max(37, (float(item.get("size", 1)) / rod_max_size) * (w - 39)))
            y = base - (index + 1) * slot_h
            active = item["id"] == focus
            parts.append(_motion_open(item))
            parts.append(f'<rect data-item="{_escape(item["id"])}" x="{cx-disk_w/2:.1f}" y="{y:.1f}" width="{disk_w:.1f}" height="{slot_h-3:.1f}" rx="6" fill="{("#1c9a98" if active else "#3a708f")}" stroke="#8cc5d3"/>')
            parts.append(_svg_text(cx, y+slot_h/2+4, item.get("label", ""), 11))
            parts.append('</g>')
        return parts
    # Array and other linear fallback: indexed cells without pointer arrows.
    slot = min(68, (w - 35) / max(1, len(items)))
    start = w/2 - slot*len(items)/2
    for index, item in enumerate(items):
        x = start + index*slot
        active = item["id"] == focus
        label = _array_label(item)
        parts.append(_motion_open(item, label))
        parts.append(f'<rect data-item="{_escape(item["id"])}" x="{x+2:.1f}" y="{h/2-28:.1f}" width="{slot-4:.1f}" height="56" rx="9" fill="{("#1b5864" if active else "#263b53")}" stroke="#607b97"/>')
        parts.append(_svg_text(x+slot/2, h/2+6, label, 15))
        parts.append('</g>')
        parts.append(_svg_text(x+slot/2, h/2+48, index, 11, "#8ca5bd"))
    return parts


def _tree_svg(kind: str, items: list[dict], edges: list[dict], w: float, h: float, focus: str | None) -> list[str]:
    items = [dict(item) for item in items]
    by_id_for_edges = {i["id"]: i for i in items}
    for edge in edges:
        child = by_id_for_edges.get(edge.get("to"))
        if child is not None and not child.get("parent"):
            child["parent"] = edge.get("from")
    roots, children, by_id = _tree_info(items)
    if not items:
        return []
    pos: dict[str, tuple[float, float]] = {}
    if roots:
        root = roots[0]
        pos[root] = (w/2, 66)
        max_depth = _tree_max_depth({"items": items, "edges": edges})
        level_step = min(70, (h - 104) / max(1, max_depth))
        queue = [(root, 0)]
        seen = set()
        while queue:
            parent, depth = queue.pop(0)
            if parent in seen: continue
            seen.add(parent)
            px, py = pos[parent]
            offspring = children.get(parent, [])
            for index, child in enumerate(offspring):
                side = child.get("side")
                if kind == "trie" and len(offspring) == 1:
                    side = "center"
                if side not in {"left", "right"}:
                    side = "center" if len(offspring) == 1 else "left" if index < len(offspring)/2 else "right"
                shift = max(28, min(w*.23, w*.28/(depth+1)))
                pos[child["id"]] = (px + (-shift if side == "left" else shift if side == "right" else 0), py + level_step)
                queue.append((child["id"], depth+1))
    for index, item in enumerate(items):
        pos.setdefault(item["id"], (45+index*60, h-45))
    parts = []
    for item in items:
        parent = item.get("parent")
        if parent in pos and parent != item["id"]:
            x1,y1=pos[parent]; x2,y2=pos[item["id"]]
            if kind == "union_find": x1,y1,x2,y2=x2,y2,x1,y1
            parts.append(f'<line data-role="tree-edge" data-motion-edge="{_escape(parent+">"+item["id"])}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#7791a9" stroke-width="2"/>')
    for item in items:
        x,y=pos[item["id"]]
        color=item.get("color")
        fill = "#a9343e" if color == "red" else "#202a3a" if color == "black" else "#1c5862" if item["id"] == focus else "#283c54"
        stroke = "#ff9a9c" if color == "red" else "#9caabb" if color == "black" else "#7ed5c6" if item["id"] == focus else "#718ca8"
        parts.append(_motion_open(item))
        if kind == "segment_tree":
            node_w = min(max(78, len(str(item.get("label", ""))) * 6.0 + 18), max(78, w / 4.4))
            parts.append(f'<rect data-role="segment-node" data-item="{_escape(item["id"])}" x="{x-node_w/2:.1f}" y="{y-18:.1f}" width="{node_w:.1f}" height="36" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
            parts.append(_svg_text(x,y+4,item.get("label",""),10))
        else:
            parts.append(f'<circle data-item="{_escape(item["id"])}" data-color="{_escape(color or "none")}" cx="{x:.1f}" cy="{y:.1f}" r="22" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
            parts.append(_svg_text(x,y+5,item.get("label",""),13))
        if kind == "avl_tree":
            height=item.get("height")
            if height is None: height=_subtree_height(item["id"],children,set())
            parts.append(_svg_text(x+28,y-19,f"h={height}",10,"#a3b9cb","start"))
        parts.append('</g>')
    return parts


def _graph_svg(kind: str, items: list[dict], edges: list[dict], w: float, h: float, focus: str | None) -> list[str]:
    pos={}
    for index,item in enumerate(items):
        angle=-math.pi/2+2*math.pi*index/max(1,len(items))
        pos[item["id"]]=(w/2+min(w*.34,145)*math.cos(angle),h/2+min(h*.27,65)*math.sin(angle))
    parts=[]
    for edge in edges:
        a,b=edge.get("from"),edge.get("to")
        if a not in pos or b not in pos: continue
        x1,y1=pos[a];x2,y2=pos[b]
        selected = bool(edge.get("selected"))
        parts.append(f'<line data-role="{"selected-edge" if selected else "graph-edge"}" data-motion-edge="{_escape(a+">"+b)}" data-motion-selected="{str(selected).lower()}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{"#7ddab7" if selected else "#7895ad"}" stroke-width="{"4" if selected else "2"}"/>')
        if kind in {"directed_graph", "dag"} or edge.get("directed"):
            angle = math.atan2(y2-y1, x2-x1)
            tip_x, tip_y = x2-25*math.cos(angle), y2-25*math.sin(angle)
            left_x, left_y = tip_x-8*math.cos(angle-.55), tip_y-8*math.sin(angle-.55)
            right_x, right_y = tip_x-8*math.cos(angle+.55), tip_y-8*math.sin(angle+.55)
            parts.append(f'<path data-role="graph-arrow" d="M{left_x:.1f} {left_y:.1f} L{tip_x:.1f} {tip_y:.1f} L{right_x:.1f} {right_y:.1f}" fill="none" stroke="#9fc1d6" stroke-width="2"/>')
        if edge.get("label") is not None:
            distance = max(1, math.hypot(x2-x1,y2-y1))
            mx = (x1+x2)/2 - (y2-y1)/distance*12
            my = (y1+y2)/2 + (x2-x1)/distance*12
            parts.append(_svg_text(mx,my,edge["label"],11,"#a9ecd0" if selected else "#e7bf7d"))
    for item in items:
        x,y=pos[item["id"]]
        active=item["id"]==focus or item.get("role") in {"focus", "visited"}
        parts.append(_motion_open(item))
        parts.append(f'<circle data-item="{_escape(item["id"])}" cx="{x:.1f}" cy="{y:.1f}" r="22" fill="{("#1b5864" if active else "#283c54")}" stroke="#78b9ba"/>')
        parts.append(_svg_text(x,y+5,item.get("label",""),13))
        parts.append('</g>')
    return parts


def _cells_svg(items: list[dict], w: float, h: float, focus: str | None) -> list[str]:
    if not items: return []
    cols=max(int(i.get("col",0)) for i in items)+1
    rows=max(int(i.get("row",0)) for i in items)+1
    slot=min(55,(w-38)/max(1,cols),(h-66)/max(1,rows))
    start_x=(w-cols*slot)/2;start_y=47
    parts=[]
    for item in items:
        x=start_x+item["col"]*slot;y=start_y+item["row"]*slot
        active=item["id"]==focus or item.get("role")=="focus"
        parts.append(_motion_open(item))
        parts.append(f'<rect data-item="{_escape(item["id"])}" x="{x+2:.1f}" y="{y+2:.1f}" width="{slot-4:.1f}" height="{slot-4:.1f}" rx="7" fill="{("#1c5862" if active else "#283c54")}" stroke="#718ca8"/>')
        parts.append(_svg_text(x+slot/2,y+slot/2+5,item.get("label",""),13))
        parts.append(_svg_text(x+7,y+12,f"{item['row']},{item['col']}",8,"#9bb0c6","start"))
        parts.append('</g>')
    return parts


def render_scene(raw: dict, frame_index: int = 0) -> str:
    """No model-authored markup or coordinate is inserted into the SVG."""
    scene = normalize_scene(raw)
    frame = scene["frames"][frame_index]
    containers = frame["containers"]
    width,height,boxes = layout_containers(containers, link_gutter=any(f.get("transfers") for f in scene["frames"] if isinstance(f, dict)))
    rod_max_size = max((float(item.get("size", 1)) for container in containers if container.get("kind") == "rod" for item in container.get("items", []) if isinstance(item.get("size"), (int, float))), default=1)
    focus=(frame.get("operation") or {}).get("target")
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(frame.get("title","数据结构状态"))}">',
           f'<rect width="{width}" height="{height}" rx="18" fill="#0d1725"/>']
    for container,(x,y,w,h) in zip(containers,boxes):
        kind=container["kind"]
        items=container["items"]
        local_focus=focus
        for transfer in frame.get("transfers", []):
            if transfer.get("from", {}).get("container") == container["id"]:
                local_focus=transfer["from"].get("item")
            if transfer.get("to", {}).get("container") == container["id"]:
                local_focus=transfer["to"].get("item")
        parts.append(f'<g data-container="{_escape(container["id"])}" data-kind="{_escape(kind)}" transform="translate({x:.1f} {y:.1f})">')
        parts.append(f'<rect width="{w:.1f}" height="{h:.1f}" rx="15" fill="#152337" stroke="#31465e"/>')
        parts.append(_svg_text(16,25,container.get("label",kind),13,"#c0d4e6","start"))
        parts.append(f'<line x1="15" y1="35" x2="{w-15:.1f}" y2="35" stroke="#2e435a"/>')
        if kind in TREE_KINDS:
            parts.extend(_tree_svg(kind,items,container.get("edges",[]),w,h,local_focus))
        elif kind in GRAPH_KINDS:
            parts.extend(_graph_svg(kind,items,container.get("edges",[]),w,h,local_focus))
        elif kind in CELL_KINDS:
            parts.extend(_cells_svg(items,w,h,local_focus))
        else:
            parts.extend(_linear_svg(kind,items,w,h,local_focus,rod_max_size))
        # Edge keys are scoped by container, matching the client-side transition planner.
        # They are added on each graphic above with local endpoints; the container group supplies the scope.
        parts.append('</g>')
    positions = {container["id"]: box for container, box in zip(containers, boxes)}
    if frame.get("transfers"):
        parts.append('<defs><marker id="transfer-tip" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#7ddab7"/></marker></defs>')
    for transfer in frame.get("transfers", []):
        source, target = transfer.get("from", {}), transfer.get("to", {})
        if source.get("container") not in positions or target.get("container") not in positions:
            continue
        sx,sy,sw,sh = positions[source["container"]]
        tx,ty,tw,th = positions[target["container"]]
        if abs((sy+sh/2)-(ty+th/2)) < min(sh,th)/2:
            forward = sx < tx
            x1,x2 = (sx+sw-4,tx+4) if forward else (sx+4,tx+tw-4)
            y1=y2=(sy+sh/2+ty+th/2)/2
        else:
            forward = sy < ty
            x1=sx+sw/2; x2=tx+tw/2
            y1,y2=(sy+sh-4,ty+4) if forward else (sy+4,ty+th-4)
        parts.append(f'<path data-role="transfer-arrow" d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}" fill="none" stroke="#7ddab7" stroke-width="3" marker-end="url(#transfer-tip)"/>')
        parts.append(_svg_text((x1+x2)/2,(y1+y2)/2-7,transfer.get("label",transfer.get("kind","")),11,"#9ce9ce"))
    parts.append('</svg>')
    return ''.join(parts)
