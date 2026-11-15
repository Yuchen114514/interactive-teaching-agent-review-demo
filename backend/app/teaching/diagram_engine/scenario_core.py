"""Composable situation scenes for problems without a fixed data-structure view."""
from __future__ import annotations

import html
import math
from collections import defaultdict
from typing import Any

VIEWS = {"zone", "row", "stack", "grid"}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _resolved_values(scene: dict, frame_index: int) -> dict[str, dict]:
    values: dict[str, dict] = {}
    for frame in scene.get("frames", [])[:frame_index+1]:
        for eid, fields in frame.get("values", {}).items():
            values.setdefault(eid, {}).update(fields)
    return values


def validate_scenario(scene: Any, problem_text: str = "") -> list[str]:
    if not isinstance(scene, dict) or scene.get("structure") != "scenario":
        return ["不是 scenario 场景"]
    errors = []
    entities, regions, frames = scene.get("entities"), scene.get("regions"), scene.get("frames")
    if not isinstance(entities, list) or not 1 <= len(entities) <= 50:
        return ["entities 必须有 1 至 50 项"]
    if not isinstance(regions, list) or not 1 <= len(regions) <= 12:
        return ["regions 必须有 1 至 12 项"]
    if not isinstance(frames, list) or not 1 <= len(frames) <= 6:
        return ["frames 必须有 1 至 6 帧"]
    entity_ids = [e.get("id") for e in entities if isinstance(e, dict)]
    region_ids = [r.get("id") for r in regions if isinstance(r, dict)]
    if len(entity_ids) != len(entities) or any(not isinstance(i, str) or not i for i in entity_ids) or len(set(entity_ids)) != len(entity_ids):
        errors.append("实体 ID 缺失或重复")
    if len(region_ids) != len(regions) or any(not isinstance(i, str) or not i for i in region_ids) or len(set(region_ids)) != len(region_ids):
        errors.append("区域 ID 缺失或重复")
    if any(not isinstance(e.get("label"), (str, int, float)) for e in entities if isinstance(e, dict)):
        errors.append("实体缺少可见标签")
    if any(r.get("view", "zone") not in VIEWS or not isinstance(r.get("label"), str) for r in regions if isinstance(r, dict)):
        errors.append("区域视图或标签无效")
    rules = scene.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 16:
        errors.append("规则列表无效")
        rules = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("source") not in {"problem", "assumption", "derived"}:
            errors.append("规则来源无效")
            continue
        if rule["source"] == "problem" and (not isinstance(rule.get("quote"), str) or not rule["quote"] or rule["quote"] not in problem_text):
            errors.append("题目规则来源未在原文中找到")
        constraint = rule.get("constraint")
        if constraint is not None and (not isinstance(constraint, dict) or constraint.get("kind") != "max_occupancy" or constraint.get("region") not in region_ids or not isinstance(constraint.get("max"), int) or constraint["max"] < 0):
            errors.append("规则约束格式无效")
    previous = None
    previous_values: dict[str, dict] = {}
    resolved_values: dict[str, dict] = {}
    frame_ids = set()
    for index, frame in enumerate(frames):
        prefix = f"第 {index+1} 帧"
        if not isinstance(frame, dict):
            errors.append(prefix + "不是对象"); continue
        fid = frame.get("id")
        if not isinstance(fid, str) or not fid or fid in frame_ids:
            errors.append(prefix + " ID 缺失或重复")
        frame_ids.add(fid)
        placements = frame.get("placements")
        if not isinstance(placements, dict) or set(placements) != set(entity_ids):
            errors.append(prefix + "实体归属不完整")
            continue
        if any(region not in region_ids for region in placements.values()):
            errors.append(prefix + "引用了不存在的区域")
        relations = frame.get("relations", [])
        if not isinstance(relations, list) or len(relations) > 30:
            errors.append(prefix + "关系列表无效")
        else:
            for relation in relations:
                if not isinstance(relation, dict) or relation.get("from") not in entity_ids or relation.get("to") not in entity_ids or not isinstance(relation.get("kind"), str):
                    errors.append(prefix + "关系引用无效")
        if frame.get("evidence") != "conceptual":
            errors.append(prefix + "证据级别只能标为概念示意；尚无外部执行记录")
        phases = frame.get("visual_phases", [])
        if not isinstance(phases, list) or len(phases) > 6 or any(not isinstance(p, str) for p in phases):
            errors.append(prefix + "视觉阶段无效")
        values = frame.get("values", {})
        if not isinstance(values, dict) or any(eid not in entity_ids or not isinstance(fields, dict) or any(not isinstance(key, str) or not (value is None or isinstance(value, (str, int, float, bool))) for key,value in fields.items()) for eid,fields in values.items()):
            errors.append(prefix + "实体属性更新无效")
            values = {}
        resolved_values = {eid: fields.copy() for eid,fields in resolved_values.items()}
        for eid,fields in values.items():
            resolved_values.setdefault(eid, {}).update(fields)
        if previous is not None:
            moved = {eid for eid in entity_ids if previous.get(eid) != placements.get(eid)}
            updated = {eid for eid in entity_ids if previous_values.get(eid, {}) != resolved_values.get(eid, {})}
            changed = moved | updated
            operation = frame.get("operation", {})
            if changed and (not isinstance(operation, dict) or operation.get("kind") not in {"transfer", "move", "update", "compound"} or set(operation.get("entities", [])) != changed):
                errors.append(prefix + "状态变化缺少对应的操作")
        for rule in rules:
            constraint = rule.get("constraint") if isinstance(rule, dict) else None
            if isinstance(constraint, dict) and constraint.get("kind") == "max_occupancy" and constraint.get("region") in region_ids and isinstance(constraint.get("max"), int):
                count = sum(region == constraint["region"] for region in placements.values())
                if count > constraint["max"]:
                    errors.append(prefix + f"区域 {constraint['region']} 超出声明容量")
        previous = placements
        previous_values = resolved_values
    return list(dict.fromkeys(errors))


def render_scenario(scene: dict, frame_index: int = 0) -> str:
    frame = scene["frames"][frame_index]
    regions, entities = scene["regions"], scene["entities"]
    placements = frame["placements"]
    values = _resolved_values(scene, frame_index)
    n=len(regions)
    width=960; margin=20; gap=28 if n>1 else 0; cell_h=230; footer_h=96
    available=width-2*margin; boxes={}; row=[]; cursor_y=margin
    def flush_row():
        nonlocal row,cursor_y
        if not row:return
        cell_w=(available-(len(row)-1)*gap)/len(row)
        for col,region in enumerate(row):
            boxes[region["id"]]=(margin+col*(cell_w+gap),cursor_y,cell_w,cell_h)
        cursor_y+=cell_h+gap;row=[]
    for region in regions:
        largest=max(sum(place==region["id"] for place in item.get("placements", {}).values()) for item in scene["frames"])
        wide=region.get("view")=="row" and largest>=4
        if wide:
            flush_row();boxes[region["id"]]=(margin,cursor_y,available,cell_h);cursor_y+=cell_h+gap
        else:
            row.append(region)
            if len(row)==3:flush_row()
    flush_row()
    height=cursor_y-gap+margin+footer_h
    highlighted=set((frame.get("operation") or {}).get("entities", []))
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(frame.get("title", "情境状态"))}">',
         f'<rect width="{width}" height="{height}" rx="18" fill="#0d1725"/>',
         '<defs><marker id="scenario-tip" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0 0 L9 4.5 L0 9 Z" fill="#7ddab7"/></marker></defs>']
    chip_centers={}
    for region in regions:
        x,y,w,h=boxes[region["id"]]
        out.append(f'<g data-region="{_esc(region["id"])}" transform="translate({x:.1f} {y:.1f})"><rect width="{w:.1f}" height="{h:.1f}" rx="15" fill="#152337" stroke="#31465e"/>')
        out.append(f'<text x="16" y="26" fill="#c0d4e6" font-size="14" font-family="system-ui,sans-serif">{_esc(region["label"])}</text><line x1="15" y1="38" x2="{w-15:.1f}" y2="38" stroke="#2e435a"/>')
        members=[e for e in entities if placements.get(e["id"])==region["id"]]
        view=region.get("view", "zone")
        chip_cols=1 if view=="stack" else max(1,min(3,math.floor((w-20)/100)))
        if view=="row":chip_cols=max(1,min(len(members),math.floor((w-20)/80)))
        chip_w=min(112,(w-28-(chip_cols-1)*9)/chip_cols)
        for index,entity in enumerate(members):
            col=index%chip_cols; row=index//chip_cols
            px=14+col*(chip_w+9); py=52+row*58
            if py+48>h-10:
                if index==chip_cols*math.floor((h-62)/58):
                    out.append(f'<text x="16" y="{h-15:.1f}" fill="#91a7bd" font-size="11">还有 {len(members)-index} 项</text>')
                break
            active=entity["id"] in highlighted
            chip_centers[entity["id"]]=(x+px+chip_w/2,y+py+24,chip_w/2,24)
            out.append(f'<rect data-entity="{_esc(entity["id"])}" x="{px:.1f}" y="{py:.1f}" width="{chip_w:.1f}" height="48" rx="9" fill="{"#1b5864" if active else "#263b53"}" stroke="{"#7ddab7" if active else "#607b97"}"/>')
            label=str(entity.get("label",""))
            dynamic=values.get(entity["id"], {})
            attributes={**dynamic, **{key:value for key,value in entity.get("attributes", {}).items() if key not in dynamic}}
            out.append(f'<text x="{px+chip_w/2:.1f}" y="{py+21:.1f}" text-anchor="middle" fill="#eaf3ff" font-size="13" font-family="system-ui,sans-serif">{_esc(label[:18])}</text>')
            if attributes:
                summary=" · ".join(f"{k}: {'待定' if v is None else v}" for k,v in list(attributes.items())[:2])
                out.append(f'<text x="{px+chip_w/2:.1f}" y="{py+39:.1f}" text-anchor="middle" fill="#9db7cb" font-size="9" font-family="system-ui,sans-serif">{_esc(summary[:26])}</text>')
        out.append('</g>')
    for relation in frame.get("relations", []):
        kind=relation.get("kind", "")
        start=chip_centers.get(relation.get("from")); end=chip_centers.get(relation.get("to"))
        if not start or not end:continue
        dx=end[0]-start[0];dy=end[1]-start[1]
        distance=math.hypot(dx,dy)
        if distance<1:continue
        ux=dx/distance;uy=dy/distance
        inset=24 if abs(dy)>abs(dx) else min(start[2],end[2])
        x1=start[0]+ux*inset;y1=start[1]+uy*inset
        x2=end[0]-ux*inset;y2=end[1]-uy*inset
        color=("#ef9b9b" if any(word in kind for word in ("violate", "invalid", "error", "conflict")) else
               "#d6a9f0" if kind=="mirror_of" else
               "#8fcbe7" if kind in {"parent_of", "child_of", "next", "precedes"} else "#7ddab7")
        dash=' stroke-dasharray="5 5"' if kind=="mirror_of" else ""
        out.append(f'<path data-role="scenario-relation" data-kind="{_esc(kind)}" d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
    if frame_index:
        previous=scene["frames"][frame_index-1]["placements"]
        grouped=defaultdict(list)
        for entity in entities:
            eid=entity["id"]
            if previous.get(eid)!=placements.get(eid):
                grouped[(previous.get(eid),placements.get(eid))].append(str(entity["label"]))
        for (source,target),labels in grouped.items():
            if source not in boxes or target not in boxes:continue
            sx,sy,sw,sh=boxes[source];tx,ty,tw,th=boxes[target]
            same_row=abs(sy-ty)<1
            if same_row:
                x1,x2=(sx+sw-2,tx+2) if sx<tx else (sx+2,tx+tw-2)
                y1=y2=sy+sh/2
            else:
                x1=sx+sw/2;x2=tx+tw/2
                y1,y2=(sy+sh-2,ty+2) if sy<ty else (sy+2,ty+th-2)
            out.append(f'<path data-role="scenario-transfer" d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}" fill="none" stroke="#7ddab7" stroke-width="3" marker-end="url(#scenario-tip)"/>')
            out.append(f'<text x="{(x1+x2)/2:.1f}" y="{(y1+y2)/2-9:.1f}" text-anchor="middle" fill="#9ce9ce" font-size="11">{_esc("、".join(labels[:3]))}</text>')
    footer_y=height-footer_h+18
    relations=frame.get("relations", [])
    relation_text=" · ".join(f"{next((str(e['label']) for e in entities if e['id']==r['from']),r['from'])} {r['kind']} {next((str(e['label']) for e in entities if e['id']==r['to']),r['to'])}" for r in relations[:4])
    if relation_text:
        out.append(f'<text x="{margin}" y="{footer_y}" fill="#b8cbe0" font-size="12" font-family="system-ui,sans-serif">关系：{_esc(relation_text[:120])}</text>')
    phases=frame.get("visual_phases", [])
    if phases:
        out.append(f'<text x="{margin}" y="{footer_y+24}" fill="#8fd8c2" font-size="12" font-family="system-ui,sans-serif">表现阶段：{_esc(" → ".join(phases))}</text>')
    out.append(f'<text x="{width-margin}" y="{height-20}" text-anchor="end" fill="#92a9bf" font-size="12" font-family="system-ui,sans-serif">概念示意 · 未验证执行</text>')
    out.append('</svg>')
    return ''.join(out)
