"""Bounded, deterministic full traces for algorithms that need more than key frames."""
from __future__ import annotations

from collections import deque
from typing import Any

from .scene_core import validate_scene

MAX_TRACE_FRAMES = 256


def _nodes(spec: dict) -> list[str]:
    nodes = spec.get("nodes", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 50 or len(nodes) != len(set(nodes)) or not all(isinstance(n, str) and n for n in nodes):
        raise ValueError("nodes 必须是 1 至 50 个互异字符串")
    return nodes


def _frame_limit(count: int) -> None:
    if count > MAX_TRACE_FRAMES:
        raise ValueError(f"完整过程需要 {count} 帧，超过 {MAX_TRACE_FRAMES} 帧上限；未生成截断结果")


def _rod_containers(rods: list[str], stacks: dict[str, list[int]], *, english: bool = False) -> list[dict]:
    return [{"id": rod, "kind": "rod", "label": f"Peg {rod}" if english else f"{rod} 杆", "items": [
        {"id": f"disk-{size}", "label": str(size), "size": size} for size in stacks[rod]
    ]} for rod in rods]


def _hanoi(spec: dict) -> dict:
    rods = spec.get("rods", [])
    n = spec.get("disks")
    source, target = spec.get("source"), spec.get("target")
    english = spec.get("language") == "en"
    if not isinstance(rods, list) or len(rods) < 3 or len(rods) > 12 or len(rods) != len(set(rods)) or not all(isinstance(r, str) and r for r in rods):
        raise ValueError("汉诺塔需要 3 至 12 根不同的杆")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 50 or source not in rods or target not in rods or source == target:
        raise ValueError("盘片数或起终杆无效")
    spare = [rod for rod in rods if rod not in {source, target}]
    direct = n <= len(rods) - 1
    _frame_limit(2*n if direct else 2**n)
    moves: list[tuple[int, str, str]] = []
    if direct:
        for size, rod in zip(range(1, n), spare):
            moves.append((size, source, rod))
        moves.append((n, source, target))
        for size, rod in reversed(list(zip(range(1, n), spare))):
            moves.append((size, rod, target))
    else:
        def recurse(size: int, start: str, end: str, auxiliary: str) -> None:
            if not size:
                return
            recurse(size-1, start, auxiliary, end)
            moves.append((size, start, end))
            recurse(size-1, auxiliary, end, start)
        recurse(n, source, target, spare[0])
    stacks = {rod: list(range(n, 0, -1)) if rod == source else [] for rod in rods}
    first_note = (
        f"Convention: move from peg {source} to peg {target}; {n} disks start on peg {source}."
        if english and spec.get("conventional_endpoints") else
        f"{n} disks start on peg {source}." if english else
        f"约定从 {source} 杆移到 {target} 杆；{n} 个盘片位于 {source} 杆" if spec.get("conventional_endpoints") else
        f"{n} 个盘片位于 {source} 杆"
    )
    frames = [{"id": "f0", "title": "Initial state" if english else "初始状态", "note": first_note,
               "containers": _rod_containers(rods, stacks, english=english), "operation": {"kind": "initialize", "target": source}}]
    for index, (size, start, end) in enumerate(moves, 1):
        if not stacks[start] or stacks[start][-1] != size or (stacks[end] and stacks[end][-1] < size):
            raise ValueError("轨迹生成器产生了非法盘片移动")
        stacks[start].pop(); stacks[end].append(size)
        frames.append({"id": f"f{index}", "title": f"Move {index}: disk {size} {start}→{end}" if english else f"第 {index} 步：{size} 号盘 {start}→{end}",
                       "note": f"Move disk {size} from {start} to {end}." if english else f"移动 {size} 号盘，{start} → {end}",
                       "containers": _rod_containers(rods, stacks, english=english),
                       "operation": {"kind": "move", "target": f"disk-{size}", "source": start, "destination": end}})
    return {"structure": "multi", "title": "Tower of Hanoi: complete legal sequence" if english else "汉诺塔完整合法过程", "algorithm": "hanoi", "frames": frames}


def _dag(spec: dict) -> dict:
    nodes = _nodes(spec)
    english = spec.get("language") == "en"
    raw_edges = spec.get("edges", [])
    if not isinstance(raw_edges, list) or any(not isinstance(e, list) or len(e) != 2 or e[0] not in nodes or e[1] not in nodes or e[0] == e[1] for e in raw_edges):
        raise ValueError("DAG 边列表无效")
    _frame_limit(len(nodes) + 1)
    edges = [tuple(e) for e in raw_edges]
    indegree = {node: 0 for node in nodes}
    outgoing = {node: [] for node in nodes}
    for a, b in edges:
        indegree[b] += 1; outgoing[a].append(b)
    ready = deque(sorted(node for node in nodes if indegree[node] == 0))
    order: list[str] = []
    frames = []
    def snapshot(index: int, title: str, transfers: list[dict] | None = None, operation: dict | None = None) -> None:
        frames.append({"id": f"f{index}", "title": title, "note": ("Visited: " if english else "已完成：") + (" → ".join(order) if order else ("none" if english else "无")),
                       "visited_order": order.copy(), "containers": [
                           {"id": "dag", "kind": "dag", "label": "Directed acyclic graph" if english else "有向无环图", "items": [
                               {"id": f"node-{node}", "label": node, "role": "visited" if node in order else "pending"} for node in nodes],
                            "edges": [{"from": f"node-{a}", "to": f"node-{b}"} for a,b in edges]},
                           {"id": "ready", "kind": "queue", "label": "Ready queue (indegree 0)" if english else "入度为 0 的待访问队列", "items": [
                               {"id": f"ready-{node}", "label": node} for node in ready]}
                       ], "operation": operation or {"kind": "initialize", "target": "ready"},
                       "transfers": transfers or []})
    snapshot(0, "Initial indegrees and ready queue" if english else "初始入度与就绪队列")
    while ready:
        node = ready.popleft()
        order.append(node)
        newly_ready = []
        for neighbor in sorted(outgoing[node]):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                ready.append(neighbor)
                newly_ready.append(neighbor)
        transfers = [{"kind": "copy", "from": {"container": "dag", "item": f"node-{x}"},
                      "to": {"container": "ready", "item": f"ready-{x}"}, "label": f"Enqueue {x}" if english else f"{x} 入队"} for x in newly_ready]
        snapshot(len(order), f"Visit {node}" if english else f"访问 {node}", transfers, {"kind": "visit", "target": f"node-{node}"})
    if len(order) != len(nodes):
        raise ValueError("输入图有环，无法完成 DAG 拓扑遍历")
    return {"structure": "multi", "title": "Complete DAG topological traversal" if english else "DAG 完整拓扑遍历", "algorithm": "dag_topological", "frames": frames}


def _prim_edges(spec: dict, nodes: list[str]) -> list[dict]:
    edges = spec.get("edges", [])
    if not isinstance(edges, list) or not edges:
        raise ValueError("Prim 需要非空加权边列表")
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("from") not in nodes or edge.get("to") not in nodes or edge["from"] == edge["to"] or not isinstance(edge.get("weight"), (int, float)) or isinstance(edge["weight"], bool):
            raise ValueError("Prim 加权边无效")
    return edges


def _prim(spec: dict) -> dict:
    nodes = _nodes(spec)
    english = spec.get("language") == "en"
    start = spec.get("start")
    if start not in nodes:
        raise ValueError("Prim 起点不在节点列表中")
    edges = _prim_edges(spec, nodes)
    _frame_limit(len(nodes))
    visited = [start]
    selected: list[int] = []
    frames = []
    def snapshot(index: int, title: str, operation: dict) -> None:
        weight = sum(edges[i]["weight"] for i in selected)
        note = f"Selected {len(selected)} edges; total weight {weight:g}" if english else f"已选 {len(selected)} 条边；当前权重 {weight:g}"
        frames.append({"id": f"f{index}", "title": title, "note": note,
                       "visited_order": visited.copy(), "selected_edges": selected.copy(),
                       "containers": [{"id": "graph", "kind": "weighted_graph", "label": "Prim minimum spanning tree" if english else "Prim 最小生成树", "items": [
                           {"id": f"node-{node}", "label": node, "role": "visited" if node in visited else "pending"} for node in nodes],
                           "edges": [{"from": f"node-{e['from']}", "to": f"node-{e['to']}", "label": str(e['weight']), "selected": i in selected} for i,e in enumerate(edges)]}],
                       "operation": operation})
    snapshot(0, f"Start at {start}" if english else f"从 {start} 开始", {"kind": "initialize", "target": f"node-{start}"})
    while len(visited) < len(nodes):
        candidates = []
        for index, edge in enumerate(edges):
            a, b = edge["from"], edge["to"]
            if (a in visited) != (b in visited):
                candidates.append((edge["weight"], min(a,b), max(a,b), index))
        if not candidates:
            raise ValueError("图不连通，Prim 无法生成覆盖所有节点的树")
        _, _, _, index = min(candidates)
        edge = edges[index]
        newcomer = edge["to"] if edge["from"] in visited else edge["from"]
        visited.append(newcomer); selected.append(index)
        title = f"Select edge {edge['from']}—{edge['to']} ({edge['weight']:g})" if english else f"选边 {edge['from']}—{edge['to']}（{edge['weight']:g}）"
        snapshot(len(selected), title, {"kind": "select_edge", "target": f"node-{newcomer}"})
    return {"structure": "weighted_graph", "title": "Complete Prim minimum spanning tree" if english else "Prim 完整最小生成树", "algorithm": "prim",
            "total_weight": sum(edges[i]["weight"] for i in selected), "frames": frames}


def generate_trace(spec: dict) -> dict:
    algorithm = spec.get("algorithm")
    if algorithm == "hanoi":
        scene = _hanoi(spec)
    elif algorithm == "dag_topological":
        scene = _dag(spec)
    elif algorithm == "prim":
        scene = _prim(spec)
    else:
        raise ValueError(f"不支持的完整过程算法：{algorithm}")
    note = spec.get("case_note")
    if isinstance(note, str) and note.strip() and scene.get("frames"):
        origin = spec.get("case_origin")
        english = spec.get("language") == "en"
        prefix = ("Illustrative example: " if english else "教学示例：") if origin == "illustrative" else (
            "Example assumptions: " if english else "示例约定：") if origin == "mixed" else ""
        scene["frames"][0]["note"] = (prefix + note.strip()[:200] + " " + str(scene["frames"][0].get("note") or "")).strip()
    return scene


def _snapshot(scene: dict) -> list[dict[str, Any]]:
    """Canonical semantic state for frame-by-frame comparison, independent of prose/layout."""
    result = []
    for frame in scene.get("frames", []):
        result.append({
            "containers": [{"id": c.get("id"), "kind": c.get("kind"),
                            "items": [(i.get("id"), i.get("label"), i.get("size"), i.get("role")) for i in c.get("items", [])],
                            "edges": [(e.get("from"), e.get("to"), e.get("label"), e.get("selected")) for e in c.get("edges", [])]}
                           for c in frame.get("containers", [])],
            "visited_order": frame.get("visited_order"), "selected_edges": frame.get("selected_edges"),
            "operation": frame.get("operation"),
        })
    return result


def _audit_hanoi(scene: dict, spec: dict) -> list[str]:
    rods = spec["rods"]
    frames = scene.get("frames", [])
    errors = []
    def stacks(frame: dict) -> dict[str, list[int]]:
        return {c.get("id"): [i.get("size") for i in c.get("items", [])] for c in frame.get("containers", []) if c.get("kind") == "rod"}
    if not frames:
        return ["缺少汉诺塔状态"]
    initial = stacks(frames[0]); final = stacks(frames[-1])
    if initial.get(spec["source"]) != list(range(spec["disks"], 0, -1)):
        errors.append("汉诺塔初始盘片不正确")
    if final.get(spec["target"]) != list(range(spec["disks"], 0, -1)) or any(final.get(rod) for rod in rods if rod != spec["target"]):
        errors.append("汉诺塔未全部搬到目标杆")
    for index in range(1, len(frames)):
        before, after = stacks(frames[index-1]), stacks(frames[index])
        old_owner = {size: rod for rod, values in before.items() for size in values}
        new_owner = {size: rod for rod, values in after.items() for size in values}
        moved = [size for size in old_owner if old_owner.get(size) != new_owner.get(size)]
        if len(old_owner) != spec["disks"] or old_owner.keys() != new_owner.keys() or len(moved) != 1:
            errors.append(f"第 {index+1} 帧不是一次完整的合法盘片移动")
            continue
        size = moved[0]; src = old_owner[size]; dst = new_owner[size]
        if before[src][-1] != size or after[dst][-1] != size or (before[dst] and before[dst][-1] < size):
            errors.append(f"第 {index+1} 帧违反汉诺塔顶部或大小规则")
    return errors


def _audit_dag(scene: dict, spec: dict) -> list[str]:
    frames = scene.get("frames", [])
    nodes = spec["nodes"]
    if not frames:
        return ["缺少 DAG 状态"]
    errors = []
    final_order = frames[-1].get("visited_order", [])
    if len(final_order) != len(nodes) or set(final_order) != set(nodes):
        errors.append("DAG 遍历未覆盖全部节点且仅访问一次")
    else:
        position = {node: index for index, node in enumerate(final_order)}
        if any(position[a] >= position[b] for a,b in spec["edges"]):
            errors.append("DAG 遍历顺序违反有向边先后关系")
    for index, frame in enumerate(frames):
        order = frame.get("visited_order", [])
        if len(order) != index or order != final_order[:index]:
            errors.append(f"第 {index+1} 帧 DAG 访问进度不连续")
    return errors


def _kruskal_weight(nodes: list[str], edges: list[dict]) -> float:
    parent = {node: node for node in nodes}
    def root(node: str) -> str:
        while parent[node] != node:
            node = parent[node]
        return node
    total = 0.0; count = 0
    for edge in sorted(edges, key=lambda e: e["weight"]):
        a,b = root(edge["from"]),root(edge["to"])
        if a != b:
            parent[a] = b; total += edge["weight"]; count += 1
    if count != len(nodes)-1:
        raise ValueError("图不连通")
    return total


def _audit_prim(scene: dict, spec: dict) -> list[str]:
    frames = scene.get("frames", [])
    if not frames:
        return ["缺少 Prim 状态"]
    nodes, edges = spec["nodes"], spec["edges"]
    errors = []
    if frames[0].get("visited_order") != [spec["start"]] or frames[0].get("selected_edges") != []:
        errors.append("Prim 初始状态不正确")
    for index in range(1, len(frames)):
        before, after = frames[index-1], frames[index]
        visited = before.get("visited_order", []); order = after.get("visited_order", [])
        previous = before.get("selected_edges", []); selected = after.get("selected_edges", [])
        if len(order) != len(visited)+1 or order[:len(visited)] != visited or len(selected) != len(previous)+1 or selected[:-1] != previous:
            errors.append(f"第 {index+1} 帧 Prim 状态未推进一步")
            continue
        edge_id = selected[-1]
        if not isinstance(edge_id, int) or not 0 <= edge_id < len(edges):
            errors.append(f"第 {index+1} 帧 Prim 选择了无效边")
            continue
        edge = edges[edge_id]
        if (edge["from"] in visited) == (edge["to"] in visited) or order[-1] not in {edge["from"],edge["to"]}:
            errors.append(f"第 {index+1} 帧 Prim 边未跨越当前割")
        cut_min = min((e["weight"] for e in edges if (e["from"] in visited) != (e["to"] in visited)), default=None)
        if edge["weight"] != cut_min:
            errors.append(f"第 {index+1} 帧 Prim 未选最小割边")
    final = frames[-1]
    if set(final.get("visited_order", [])) != set(nodes) or len(final.get("selected_edges", [])) != len(nodes)-1:
        errors.append("Prim 未生成完整生成树")
    try:
        minimum = _kruskal_weight(nodes, edges)
        chosen = sum(edges[i]["weight"] for i in final.get("selected_edges", []) if isinstance(i, int) and 0 <= i < len(edges))
        if chosen != minimum or scene.get("total_weight") != minimum:
            errors.append("Prim 最终权重不是最小生成树权重")
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def verify_trace(scene: dict, spec: dict) -> list[str]:
    errors = validate_scene(scene, max_frames=MAX_TRACE_FRAMES)
    try:
        expected = generate_trace(spec)
    except ValueError as exc:
        return errors + [str(exc)]
    observed, canonical = _snapshot(scene), _snapshot(expected)
    if len(observed) != len(canonical):
        errors.append(f"完整过程应有 {len(canonical)} 帧，实际 {len(observed)} 帧")
    for index, (actual, wanted) in enumerate(zip(observed, canonical)):
        if actual != wanted:
            errors.append(f"第 {index + 1} 帧与算法的确定性状态不一致")
    if scene.get("algorithm") != spec.get("algorithm"):
        errors.append("算法类型与请求不一致")
    if spec.get("algorithm") == "prim" and scene.get("total_weight") != expected.get("total_weight"):
        errors.append("Prim 总权重不一致")
    audit = {"hanoi": _audit_hanoi, "dag_topological": _audit_dag, "prim": _audit_prim}[spec["algorithm"]]
    errors.extend(audit(scene, spec))
    return list(dict.fromkeys(errors))
