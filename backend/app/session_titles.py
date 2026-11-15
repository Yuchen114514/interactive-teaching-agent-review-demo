import re


DEFAULT_SESSION_TITLE = "New Chat"
MAX_TITLE_LENGTH = 40
CLAUSE_BREAK_MARKERS = ("但是", "不过", "而且", "并且", "然后", "另外")
LOW_SIGNAL_MESSAGES = {
    "ok",
    "okay",
    "好的",
    "好",
    "谢谢",
    "thanks",
    "thank you",
    "收到",
}

PREFIX_PATTERNS = [
    r"^(帮我看看|帮我|请问一下|请问|我想问一下|我想问|我想知道|能不能帮我|能不能|请你帮我|请你|麻烦你)\s*",
    r"^(如何在|如何把|如何将|如何)\s*",
    r"^(写一份|写一个|给我一份|给我一个)\s*",
    r"^(关于)\s*",
]


def _normalize_message(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", (message or "").strip())
    while True:
        previous = cleaned
        cleaned = cleaned.lstrip(" \t\r\n,，。！？!?.；;：:\"'()[]{}")
        for pattern in PREFIX_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, count=1).strip()
        if cleaned == previous:
            break
    cleaned = cleaned.strip(" \t\r\n,，。！？!?.；;：:\"'()[]{}")
    return cleaned


def _truncate_title(title: str) -> str:
    for marker in CLAUSE_BREAK_MARKERS:
        marker_index = title.find(marker)
        if marker_index >= 12:
            return title[:marker_index].rstrip(" \t\r\n,，。！？!?.；;：:\"'")

    if len(title) <= MAX_TITLE_LENGTH:
        return title

    truncated = title[:MAX_TITLE_LENGTH].rstrip()
    last_space = truncated.rfind(" ")
    if last_space >= 12:
        truncated = truncated[:last_space]
    return truncated.rstrip(" \t\r\n,，。！？!?.；;：:\"'")


def generate_session_title(user_message: str) -> str:
    cleaned = _normalize_message(user_message)
    if not cleaned:
        return DEFAULT_SESSION_TITLE

    low_signal_key = re.sub(r"[\W_]+", "", cleaned, flags=re.UNICODE).lower()
    if cleaned.lower() in LOW_SIGNAL_MESSAGES or low_signal_key in {"好", "好的", "谢谢", "好的谢谢", "thankyou", "thanks"}:
        return DEFAULT_SESSION_TITLE

    title = _truncate_title(cleaned)
    return title or DEFAULT_SESSION_TITLE


def display_session_title(title: str, preview: str) -> str:
    del preview
    return title or DEFAULT_SESSION_TITLE
