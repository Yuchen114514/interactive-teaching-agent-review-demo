from __future__ import annotations

from dataclasses import dataclass
import re

from app.models import AgentBoardNodeStatus, AgentBoardUpdateActionType


STUCK_MARKERS = (
    "不知道",
    "不会",
    "没思路",
    "没想清楚",
    "不确定",
    "还是不会",
    "还是不懂",
)

META_READINESS_MARKERS = (
    "准备好接受我的问题",
    "准备好了吗",
    "可以开始了",
    "开始吧",
    "我准备好了",
    "准备好了",
    "听懂了",
)

BOARDWORK_RESET_MARKERS = (
    "换个题",
    "换一题",
    "另一题",
    "新题",
    "重新开始",
    "重新看",
    "再看一道",
    "新的问题",
)

PROGRESS_MARKERS = (
    "状态",
    "转移",
    "边界",
    "复杂度",
    "参数",
    "终止",
    "剪枝",
    "递归",
    "循环",
    "前缀和",
    "二分",
    "单调",
    "dp",
    "dfs",
    "bfs",
    "下标",
    "位置",
    "调试",
    "样例",
    "提交",
    "思路",
    "概率",
)

CONCRETE_ENTRY_MARKERS = (
    *PROGRESS_MARKERS,
    "代码",
    "算法",
    "题解",
    "输入",
    "输出",
    "数组",
    "序列",
    "操作",
    "构造",
    "求",
    "最大化",
    "最小化",
    "乘积",
    "总和",
    "前缀和",
)

ABSTRACT_STUDY_TRANSITION_MARKERS = (
    "作业",
    "不会的题",
    "不会做的题",
    "还有题",
    "有题不会",
    "卡住了",
    "有不会的地方",
)

TOPIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("关键约束", ("约束", "限制", "条件", "必须", "不能", "至少", "至多", "可行", "不可行")),
    ("优化目标", ("目标", "最优", "最大", "最小", "尽量", "代价", "收益", "乘积")),
    ("关键性质", ("性质", "不变", "守恒", "单调", "等价", "充分", "必要", "结论", "关系", "差")),
    ("构造与策略", ("策略", "构造", "顺序", "贪心", "选择", "分配", "枚举")),
    ("状态与变量", ("状态", "维度", "dp", "dfs", "参数")),
    ("实现与调试", ("边界", "代码", "实现", "样例", "debug")),
]

TOPIC_MARKERS = tuple(
    marker
    for _title, markers in TOPIC_RULES
    for marker in markers
)

TOPIC_PRIORITY = {
    "关键约束": 4,
    "优化目标": 3,
    "关键性质": 5,
    "构造与策略": 2,
    "状态与变量": 1,
    "实现与调试": 0,
}

TITLE_ALIASES = {
    "构造策略": "构造与策略",
    "状态定义": "状态与变量",
    "边界与实现": "实现与调试",
}


@dataclass(slots=True)
class BoardNodeDraft:
    title: str
    summary: str
    status: AgentBoardNodeStatus
    coach_note: str | None = None


@dataclass(slots=True)
class BoardworkUpdate:
    action_type: AgentBoardUpdateActionType
    reason: str
    create_node: BoardNodeDraft | None = None
    update_existing: BoardNodeDraft | None = None
    coach_note: str | None = None


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().split())


def _clip_summary(text: str, limit: int = 72) -> str:
    normalized = _normalize(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _to_board_summary(text: str, limit: int = 120) -> str:
    normalized = _normalize(text)
    return _clip_summary(normalized, limit)


def _default_coach_note(topic_title: str) -> str:
    topic_title = canonicalize_board_topic_title(topic_title)
    defaults = {
        "关键约束": "这里最关键的限制条件到底是什么？",
        "优化目标": "为什么这里要先这样定义目标？",
        "关键性质": "这个性质是怎么得到的？",
        "构造与策略": "下一步该按什么顺序把策略落下来？",
        "状态与变量": "这里还缺哪一个关键变量或状态维度？",
        "实现与调试": "先用哪个最小样例检查实现？",
    }
    return defaults.get(topic_title, "先回到当前节点：你还需要确认哪一步？")


def _sanitize_coach_note(text: str, topic_title: str) -> str:
    topic_title = canonicalize_board_topic_title(topic_title)
    normalized = _normalize(text)
    forbidden_fragments = (
        "正确的应该是",
        "完全正确",
        "答案是",
        "应该是",
        "公式正确",
        "太棒了",
        "👏",
        "请把你的代码贴出来",
        "我来帮你逐行检查",
        "你的思路方向是对的",
        "非常棒",
        "很好",
        "你已经想到了",
    )
    for fragment in forbidden_fragments:
        normalized = normalized.replace(fragment, "")

    if "问题：" in normalized:
        normalized = normalized.split("问题：", 1)[1].strip()
    elif "问题:" in normalized:
        normalized = normalized.split("问题:", 1)[1].strip()
    elif "问题就变成：" in normalized:
        normalized = normalized.split("问题就变成：", 1)[1].strip()
    elif "问题就变成:" in normalized:
        normalized = normalized.split("问题就变成:", 1)[1].strip()
    elif "换句话说：" in normalized:
        normalized = normalized.split("换句话说：", 1)[1].strip()
    elif "换句话说:" in normalized:
        normalized = normalized.split("换句话说:", 1)[1].strip()

    question_matches = re.findall(r"[^。！？!?]*[？?]", normalized)
    if question_matches:
        normalized = question_matches[-1].strip()

    normalized = normalized.replace("**", "")
    normalized = normalized.strip(" ：:，,。.!！?")
    if not normalized:
        return _default_coach_note(topic_title)
    explanation_markers = ("**", "1.", "2.", "举个例子", "这意味着", "现在我们", "具体来说", ">")
    if len(normalized) > 40 and any(marker in normalized for marker in explanation_markers):
        return _default_coach_note(topic_title)
    if "？" not in normalized and "?" not in normalized:
        return _default_coach_note(topic_title)
    return _clip_summary(normalized, 40)


def sanitize_coach_note_text(text: str, topic_title: str) -> str:
    return _sanitize_coach_note(text, topic_title)


def canonicalize_board_topic_title(title: str) -> str:
    normalized = _normalize(title)
    if not normalized:
        return "当前关键问题"
    return TITLE_ALIASES.get(normalized, normalized)


def _summary_lines(summary: str) -> list[str]:
    normalized = (summary or "").strip()
    if not normalized:
        return []
    lines = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        lines.append(line)
    if not lines and normalized:
        lines.append(normalized)
    return lines


def _format_summary(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines if line.strip())


def _extract_summary_points(text: str, topic_title: str) -> list[str]:
    topic_title = canonicalize_board_topic_title(topic_title)
    normalized = _normalize(text)
    if not normalized:
        return []
    if topic_title == "当前关键问题" and len(normalized) < 16:
        return []
    normalized = re.sub(r"(我应该先从哪里想|怎么办|怎么做|怎么想|对吗).*?[？?]?$", "", normalized).strip()
    normalized = normalized.strip(" ，,。.!！?？")
    if not normalized:
        return []
    point = _clip_summary(normalized.rstrip("。.!！"), 110)
    if point and point[-1] not in "。.!！":
        point = f"{point}。"
    return [point] if point else []


def _merge_summary(existing_summary: str, incoming_text: str, topic_title: str) -> str:
    topic_title = canonicalize_board_topic_title(topic_title)
    merged = _summary_lines(existing_summary)
    for point in _extract_summary_points(incoming_text, topic_title):
        if point not in merged:
            merged.append(point)
    return _format_summary(merged[:7])


def _looks_stuck(user_message: str) -> bool:
    normalized = _normalize(user_message)
    if any(marker in normalized for marker in PROGRESS_MARKERS + TOPIC_MARKERS):
        return False
    if len(normalized) <= 12:
        return True
    return any(marker in normalized for marker in STUCK_MARKERS)


def _is_boardworthy_input(user_message: str) -> bool:
    normalized = _normalize(user_message)
    lowered = normalized.lower()
    if not normalized:
        return False
    if normalized in META_READINESS_MARKERS:
        return False
    if any(marker in normalized for marker in META_READINESS_MARKERS):
        return False
    if any(marker in normalized for marker in ABSTRACT_STUDY_TRANSITION_MARKERS) and not any(
        marker in normalized for marker in CONCRETE_ENTRY_MARKERS + TOPIC_MARKERS
    ):
        return False
    if any(marker in lowered for marker in CONCRETE_ENTRY_MARKERS + TOPIC_MARKERS):
        return True
    if "这道题" in normalized or "题目" in normalized or "代码" in normalized or "算法" in normalized:
        return True
    if len(normalized) >= 40 and any(
        token in normalized for token in ("输入", "输出", "概率", "数组", "操作", "求", "最大化", "最小化")
    ):
        return True
    if "```" in normalized:
        return True
    return False


def _looks_like_progress(user_message: str) -> bool:
    normalized = _normalize(user_message).lower()
    if len(normalized) >= 24:
        return True
    return any(marker in normalized for marker in PROGRESS_MARKERS + TOPIC_MARKERS)


def _infer_topic_title(text: str) -> str:
    normalized = _normalize(text).lower()
    best_title = "当前关键问题"
    best_score = 0
    best_priority = -1
    for title, markers in TOPIC_RULES:
        score = sum(1 for marker in markers if marker in normalized)
        if score == 0:
            continue
        priority = TOPIC_PRIORITY.get(title, 0)
        if score > best_score or (score == best_score and priority > best_priority):
            best_title = canonicalize_board_topic_title(title)
            best_score = score
            best_priority = priority
    return best_title


def _matches_topic(text: str, title: str) -> bool:
    normalized = _normalize(text).lower()
    title = canonicalize_board_topic_title(title)
    for topic_title, markers in TOPIC_RULES:
        if topic_title != title:
            continue
        return any(marker in normalized for marker in markers)
    return False


def detect_boardwork_reset(user_message: str) -> str | None:
    normalized = _normalize(user_message)
    if not normalized:
        return None
    if not any(marker in normalized for marker in BOARDWORK_RESET_MARKERS):
        return None
    inferred_title = _infer_topic_title(normalized)
    return inferred_title if inferred_title != "当前关键问题" else "当前题目白板"


def is_boardworthy_input(user_message: str) -> bool:
    return _is_boardworthy_input(user_message)


def infer_board_topic_title(text: str) -> str:
    return _infer_topic_title(text)


def build_boardwork_update(
    *,
    user_message: str,
    assistant_message: str,
    existing_nodes: list[dict],
    turn_index: int,
) -> BoardworkUpdate:
    del turn_index
    normalized_user = _normalize(user_message)
    normalized_assistant = _normalize(assistant_message)

    if not existing_nodes:
        if not _is_boardworthy_input(normalized_user):
            return BoardworkUpdate(
                action_type=AgentBoardUpdateActionType.NOOP,
                reason="当前输入还不是 OJ 题目或知识点，不应启动题目白板。",
            )
        topic_title = _infer_topic_title(normalized_user or normalized_assistant)
        initial_summary = _merge_summary("", normalized_user, topic_title)
        if not initial_summary:
            return BoardworkUpdate(
                action_type=AgentBoardUpdateActionType.NOOP,
                reason="当前输入虽然进入学习话题，但还没有形成可沉淀的板书要点。",
            )
        return BoardworkUpdate(
            action_type=AgentBoardUpdateActionType.ADVANCE,
            reason="首次苏格拉底回合，建立当前辅导范围的首个板书节点。",
            create_node=BoardNodeDraft(
                title=topic_title,
                summary=initial_summary,
                status=AgentBoardNodeStatus.EXPLORING,
                coach_note=_sanitize_coach_note(normalized_assistant, topic_title) if normalized_assistant else None,
            ),
        )

    active_node = next((node for node in existing_nodes if node.get("is_active")), existing_nodes[-1])
    inferred_title = _infer_topic_title(normalized_user or normalized_assistant)
    active_title = canonicalize_board_topic_title(active_node.get("title") or "当前关键问题")
    active_status = active_node.get("status")
    active_points = _summary_lines(active_node.get("summary") or "")
    topic_shift_ready = (
        (active_status in {AgentBoardNodeStatus.FORMED, AgentBoardNodeStatus.IMPLEMENTED} and len(active_points) >= 2)
        or (len(active_points) >= 4 and _matches_topic(normalized_user, inferred_title))
    )
    if (
        topic_shift_ready
        and inferred_title != active_title
        and inferred_title != "当前关键问题"
        and _looks_like_progress(normalized_user)
        and (
            not _matches_topic(normalized_user, active_title)
            or TOPIC_PRIORITY.get(inferred_title, 0) > TOPIC_PRIORITY.get(active_title, 0)
        )
    ):
        return BoardworkUpdate(
            action_type=AgentBoardUpdateActionType.ADVANCE,
            reason="当前对话已经进入新的关键知识点，开启新板书节点。",
            create_node=BoardNodeDraft(
                title=inferred_title,
                summary=_merge_summary("", normalized_user, inferred_title),
                status=AgentBoardNodeStatus.EXPLORING,
                coach_note=_sanitize_coach_note(normalized_assistant, inferred_title) if normalized_assistant else None,
            ),
        )

    if _looks_stuck(normalized_user):
        follow_up = (
            _sanitize_coach_note(normalized_assistant, active_title)
            if normalized_assistant
            else "先缩小问题范围，再确认当前节点的关键判断。"
        )
        return BoardworkUpdate(
            action_type=AgentBoardUpdateActionType.ANNOTATE,
            reason="学生还没有形成新的稳定认知，只追加极小提示。",
            coach_note=follow_up,
        )

    if _looks_like_progress(normalized_user):
        refined_summary = _merge_summary(active_node.get("summary") or "", normalized_user, active_title)
        return BoardworkUpdate(
            action_type=AgentBoardUpdateActionType.REFINE,
            reason=(
                "学生给出了可复述、可继续实现的局部认知，更新当前节点摘要。"
                if refined_summary != (active_node.get("summary") or "").strip()
                else "当前回答延续了同一知识点，保持板书正文并更新追问。"
            ),
            update_existing=BoardNodeDraft(
                title=active_title,
                summary=refined_summary,
                status=AgentBoardNodeStatus.FORMED,
                coach_note=_sanitize_coach_note(normalized_assistant, active_title) if normalized_assistant else None,
            ),
        )

    return BoardworkUpdate(
        action_type=AgentBoardUpdateActionType.NOOP,
        reason="本轮没有明显推进，保持板书正文不变。",
    )
