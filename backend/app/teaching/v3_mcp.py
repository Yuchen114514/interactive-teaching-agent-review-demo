from __future__ import annotations

import json
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Any, ClassVar

from app.mcp_http_tools import PostOnlyMCPToolkit
from app.teaching.v3_contracts import MCPRequestBlock


PROBLEM_TOOL_HINTS = ("problem", "题目", "question")
DETAIL_TOOL_HINTS = ("detail", "info", "get", "详情", "信息")
PROBLEM_ID_KEYS = ("problemId", "problem_id", "id")
PREFERRED_PROBLEM_DETAIL_TOOLS = ("get_problem_info", "getProblemInfo", "get_problem_detail", "problem_detail")
PROBLEM_LIST_TOOL_HINTS = ("list", "search", "submissions", "status", "contest", "class", "列表", "搜索", "提交")
PREFERRED_USER_INFO_TOOLS = ("get_user_info", "getCurrentUserInfo", "get_me", "current_user")
PREFERRED_USER_STATS_TOOLS = ("get_user_submission_stats", "getUserSubmissionStats", "get_user_weekly_submissions")
PREFERRED_SUBMISSION_DETAIL_TOOLS = ("get_submission_detail", "getSubmissionDetail")
MCP_RESOURCE_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "problem_search": ("search_problems",),
    "problem_submissions": ("get_problem_submissions",),
    "contest_list": ("list_contests",),
    "contest_info": ("get_contest_exam_info",),
    "contest_problems_and_status": ("get_contest_exam_problems_and_status",),
    "contest_rank_list": ("get_contest_exam_rank_list",),
    "contest_submissions": ("get_contest_exam_submissions",),
    "class_info": ("get_class_info",),
    "class_list": ("get_class_list",),
    "my_class_list": ("get_my_class_list",),
    "class_contest_list": ("get_class_contest_list",),
    "class_problem_list": ("get_class_problem_list",),
}
PREFERRED_SUBMIT_WITH_FEEDBACK_TOOLS = (
    "submit_code_and_get_judge_feedback",
    "submitCodeAndGetJudgeFeedback",
    "submit_and_get_judge_feedback",
    "submitAndGetJudgeFeedback",
)
PREFERRED_SUBMIT_CODE_TOOLS = (
    "submit_code",
    "submitCode",
)


@dataclass(frozen=True)
class V3MCPToolResult:
    tool_name: str
    status: str
    latency_ms: int
    raw_text: str
    parsed_payload: Any | None
    request_arguments: dict[str, Any]
    error_type: str | None = None
    error_message: str | None = None


class V3MCPContextProvider:
    _outage_cooldowns: ClassVar[dict[str, float]] = {}
    OUTAGE_COOLDOWN_SECONDS = 15.0

    def __init__(
        self,
        *,
        url: str,
        session_token: str,
        proxy_url: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.url = url
        self.session_token = session_token
        self.proxy_url = proxy_url
        self.timeout_seconds = timeout_seconds

    def _is_in_outage_cooldown(self) -> bool:
        expires_at = self._outage_cooldowns.get(self.url)
        if expires_at is None:
            return False
        if monotonic() < expires_at:
            return True
        self._outage_cooldowns.pop(self.url, None)
        return False

    def _record_outage(self) -> None:
        self._outage_cooldowns[self.url] = monotonic() + self.OUTAGE_COOLDOWN_SECONDS

    def _clear_outage(self) -> None:
        self._outage_cooldowns.pop(self.url, None)

    def fetch_problem(self, problem_id: int) -> dict[str, Any] | None:
        if self._is_in_outage_cooldown():
            return None
        toolkit = PostOnlyMCPToolkit(
            url=self.url,
            session_token=self.session_token,
            proxy_url=self.proxy_url,
            timeout_seconds=self.timeout_seconds,
        )
        try:
            toolkit.connect()
            tool_name = _select_problem_detail_tool(toolkit.functions)
            if tool_name is None:
                return None
            tool = toolkit.functions[tool_name]
            for key, value in _problem_argument_candidates(tool, problem_id):
                try:
                    problem = _normalize_problem_result(
                        toolkit.call_tool(tool_name, {key: value}),
                        problem_id=problem_id,
                    )
                    if problem is not None:
                        self._clear_outage()
                    return problem
                except Exception:
                    continue
            return None
        except Exception:
            self._record_outage()
            return None
        finally:
            toolkit.close()

    def execute_request(
        self,
        request: MCPRequestBlock,
        *,
        user_message: str = "",
    ) -> V3MCPToolResult:
        started_at = perf_counter()
        if self._is_in_outage_cooldown():
            return V3MCPToolResult(
                tool_name=_resource_fallback_tool_name(request.resource),
                status="failed",
                latency_ms=_elapsed_ms(started_at),
                raw_text="",
                parsed_payload=None,
                request_arguments={},
                error_type="MCPUnavailableCooldown",
                error_message="MCP 服务暂时不可用，请稍后重试。",
            )
        toolkit = PostOnlyMCPToolkit(
            url=self.url,
            session_token=self.session_token,
            proxy_url=self.proxy_url,
            timeout_seconds=self.timeout_seconds,
        )
        tool_name = ""
        arguments: dict[str, Any] = {}
        try:
            toolkit.connect()
            tool_name, arguments = _tool_call_for_mcp_request(toolkit.functions, request, user_message=user_message)
            raw_text = toolkit.call_tool(tool_name, arguments)
            parsed_payload = _parse_json_result(raw_text)
            if _payload_is_mcp_error(parsed_payload):
                return V3MCPToolResult(
                    tool_name=tool_name,
                    status="failed",
                    latency_ms=_elapsed_ms(started_at),
                    raw_text=raw_text,
                    parsed_payload=parsed_payload,
                    request_arguments=arguments,
                    error_type="MCPToolError",
                    error_message=_mcp_error_message(parsed_payload),
                )
            self._clear_outage()
            return V3MCPToolResult(
                tool_name=tool_name,
                status="completed",
                latency_ms=_elapsed_ms(started_at),
                raw_text=raw_text,
                parsed_payload=parsed_payload,
                request_arguments=arguments,
            )
        except Exception as exc:
            self._record_outage()
            return V3MCPToolResult(
                tool_name=tool_name or _resource_fallback_tool_name(request.resource),
                status="failed",
                latency_ms=_elapsed_ms(started_at),
                raw_text="",
                parsed_payload=None,
                request_arguments=arguments,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        finally:
            toolkit.close()


def render_mcp_tool_reply(
    request: MCPRequestBlock,
    result: V3MCPToolResult,
    *,
    authenticated_user_id: int | None = None,
) -> str:
    if result.status != "completed":
        message = result.error_message or "工具未返回可用结果"
        return f"MCP 工具 `{result.tool_name}` 调用失败：{message}"

    if request.resource == "user_info":
        return _render_user_info_reply(result, authenticated_user_id=authenticated_user_id)
    if request.resource == "user_stats":
        return _render_json_summary_reply("已通过 MCP 查询到你的提交统计", result)
    if request.resource == "user_weekly_submissions":
        return _render_json_summary_reply("已通过 MCP 查询到你最近 7 天的提交情况", result)
    if request.resource == "submission_detail":
        return _render_json_summary_reply("已通过 MCP 查询到提交详情", result)
    if request.resource == "problem_detail":
        return _render_problem_detail_reply(result)
    prefixes = {
        "problem_search": "已通过 MCP 搜索到题目",
        "problem_submissions": "已通过 MCP 查询到题目提交记录",
        "contest_list": "已通过 MCP 查询到竞赛列表",
        "contest_info": "已通过 MCP 查询到竞赛信息",
        "contest_problems_and_status": "已通过 MCP 查询到竞赛题目与状态",
        "contest_rank_list": "已通过 MCP 查询到竞赛排行榜",
        "contest_submissions": "已通过 MCP 查询到竞赛提交记录",
        "class_info": "已通过 MCP 查询到班级信息",
        "class_list": "已通过 MCP 查询到班级列表",
        "my_class_list": "已通过 MCP 查询到你加入的班级",
        "class_contest_list": "已通过 MCP 查询到班级考试列表",
        "class_problem_list": "已通过 MCP 查询到班级题目列表",
        "platform_query": "已通过 MCP 查询到平台信息",
    }
    return _render_json_summary_reply(prefixes.get(request.resource, "已通过 MCP 查询到平台信息"), result)


def _tool_call_for_mcp_request(
    functions: dict[str, Any],
    request: MCPRequestBlock,
    *,
    user_message: str,
) -> tuple[str, dict[str, Any]]:
    if request.resource == "problem_detail":
        if request.problem_id is None:
            raise ValueError("problem_id is required for problem_detail")
        tool_name = _select_problem_detail_tool(functions)
        if tool_name is None:
            raise ValueError("MCP problem detail tool is unavailable")
        tool = functions[tool_name]
        key, value = _problem_argument_candidates(tool, request.problem_id)[0]
        return tool_name, {key: value}
    if request.resource == "submission_detail":
        if request.submission_id is None:
            raise ValueError("submission_id is required for submission_detail")
        tool_name = _first_existing_tool(functions, PREFERRED_SUBMISSION_DETAIL_TOOLS)
        if tool_name is None:
            raise ValueError("MCP submission detail tool is unavailable")
        return tool_name, {"submissionId": str(request.submission_id)}
    if request.resource == "submit_code_feedback":
        if request.problem_id is None or not request.code or (request.language_id is None and not request.language):
            raise ValueError("problem_id, language and code are required for submit_code_feedback")
        tool_name = _select_submit_code_tool(functions)
        if tool_name is None:
            raise ValueError("MCP submit code tool is unavailable")
        return tool_name, _submit_code_arguments(functions[tool_name], request)
    if request.resource == "user_info":
        tool_name = _first_existing_tool(functions, PREFERRED_USER_INFO_TOOLS)
        if tool_name is None:
            raise ValueError("MCP user info tool is unavailable")
        return tool_name, {}
    if request.resource == "user_stats":
        if _message_asks_user_identity(user_message):
            tool_name = _first_existing_tool(functions, PREFERRED_USER_INFO_TOOLS)
        elif _message_requests_weekly_stats(user_message):
            tool_name = _first_existing_tool(functions, ("get_user_weekly_submissions",))
        else:
            tool_name = _first_existing_tool(functions, PREFERRED_USER_STATS_TOOLS)
        if tool_name is None:
            raise ValueError("MCP user stats tool is unavailable")
        return tool_name, {}
    if request.resource == "platform_query":
        if _message_asks_user_identity(user_message):
            tool_name = _first_existing_tool(functions, PREFERRED_USER_INFO_TOOLS)
        else:
            tool_name = _first_existing_tool(functions, ("get_platform_info",))
        if tool_name is None:
            raise ValueError("MCP platform query tool is unavailable")
        return tool_name, {}
    if request.resource == "user_weekly_submissions":
        tool_name = _first_existing_tool(functions, ("get_user_weekly_submissions",))
        if tool_name is None:
            raise ValueError("MCP weekly user submissions tool is unavailable")
        return tool_name, {}
    tool_candidates = MCP_RESOURCE_TOOL_NAMES.get(request.resource)
    if tool_candidates:
        tool_name = _first_existing_tool(functions, tool_candidates)
        if tool_name is None:
            raise ValueError(f"MCP tool for {request.resource} is unavailable")
        return tool_name, _schema_arguments_for_request(functions[tool_name], request)
    raise ValueError(f"MCP resource {request.resource} is not executable")


def _schema_arguments_for_request(function: Any, request: MCPRequestBlock) -> dict[str, Any]:
    parameters = getattr(function, "parameters", {}) or {}
    properties = parameters.get("properties") if isinstance(parameters, dict) else {}
    properties = properties if isinstance(properties, dict) else {}
    arguments: dict[str, Any] = {}
    for key, schema in properties.items():
        normalized = key.lower().replace("_", "").replace("-", "")
        if normalized == "problemid":
            value = request.problem_id
        elif normalized == "submissionid":
            value = request.submission_id or request.filters.get(key) or request.filters.get("submissionId")
        elif normalized == "examid":
            value = request.contest_id
        elif normalized == "classid":
            value = request.class_id
        elif normalized == "titlesearch":
            value = request.title_search
        elif normalized == "idsearch":
            value = request.id_search
        elif normalized == "authorsearch":
            value = request.author_search
        elif normalized == "tagnames":
            value = request.tag_names or None
        elif normalized == "classname":
            value = request.class_name
        elif normalized == "coursename":
            value = request.course_name
        elif normalized == "result":
            value = request.result or request.filters.get(key)
        elif normalized == "filter":
            value = request.filters or None
        else:
            value = None
        if value is None:
            continue
        value_type = schema.get("type") if isinstance(schema, dict) else None
        if value_type == "string":
            value = str(value)
        elif value_type == "integer" and isinstance(value, str) and value.isdigit():
            value = int(value)
        arguments[key] = value
    required = parameters.get("required", []) if isinstance(parameters, dict) else []
    missing = [key for key in required if key not in arguments]
    if missing:
        raise ValueError(f"MCP tool requires unavailable argument(s): {', '.join(missing)}")
    return arguments


def _first_existing_tool(functions: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in functions:
            return name
    lowered = {name.lower(): name for name in functions}
    for name in names:
        found = lowered.get(name.lower())
        if found:
            return found
    return None


def _message_asks_user_identity(message: str) -> bool:
    compact = "".join(str(message or "").lower().split())
    return any(marker in compact for marker in ("我的id", "用户id", "userid", "user_id", "个人信息", "用户信息", "邮箱", "学号", "profile"))


def _message_requests_weekly_stats(message: str) -> bool:
    compact = "".join(str(message or "").lower().split())
    return any(token in compact for token in ("最近7天", "最近七天", "本周", "最近一周", "过去一周", "weekly"))


def _resource_fallback_tool_name(resource: str) -> str:
    return {
        "problem_detail": "get_problem_info",
        "problem_search": "search_problems",
        "problem_submissions": "get_problem_submissions",
        "submission_detail": "get_submission_detail",
        "submit_code_feedback": "submit_code_and_get_judge_feedback",
        "user_info": "get_user_info",
        "user_stats": "get_user_submission_stats",
        "user_weekly_submissions": "get_user_weekly_submissions",
        "contest_list": "list_contests",
        "contest_info": "get_contest_exam_info",
        "contest_problems_and_status": "get_contest_exam_problems_and_status",
        "contest_rank_list": "get_contest_exam_rank_list",
        "contest_submissions": "get_contest_exam_submissions",
        "class_info": "get_class_info",
        "class_list": "get_class_list",
        "my_class_list": "get_my_class_list",
        "class_contest_list": "get_class_contest_list",
        "class_problem_list": "get_class_problem_list",
        "platform_query": "get_platform_info",
    }.get(resource, "mcp_tool")


def _select_submit_code_tool(functions: dict[str, Any]) -> str | None:
    combined = _first_existing_tool(functions, PREFERRED_SUBMIT_WITH_FEEDBACK_TOOLS)
    if combined:
        return combined
    scored: list[tuple[int, str]] = []
    has_feedback_tool = False
    for name, function in functions.items():
        name_lower = name.lower()
        if any(marker in name_lower for marker in ("get_", "detail", "list", "search", "详情", "列表")):
            continue
        haystack = " ".join(
            [
                name,
                str(getattr(function, "description", "") or ""),
                json.dumps(getattr(function, "parameters", {}) or {}, ensure_ascii=False),
            ]
        ).lower()
        if not any(token in haystack for token in ("submit", "提交")):
            continue
        score = 2
        if any(token in haystack for token in ("judge", "feedback", "判题", "反馈", "result", "结果")):
            score += 4
            has_feedback_tool = True
        if any(token in haystack for token in ("code", "代码", "source", "源码")):
            score += 2
        if any(token in haystack for token in ("problemid", "problem_id", "题目")):
            score += 2
        scored.append((score, name))
    if has_feedback_tool:
        scored = [entry for entry in scored if entry[0] >= 6]
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[0][1]
    preferred = _first_existing_tool(functions, PREFERRED_SUBMIT_CODE_TOOLS)
    if preferred:
        return preferred
    return None


def _submit_code_arguments(function: Any, request: MCPRequestBlock) -> dict[str, Any]:
    parameters = getattr(function, "parameters", {}) or {}
    properties = parameters.get("properties") if isinstance(parameters, dict) else {}
    properties = properties if isinstance(properties, dict) else {}
    arguments: dict[str, Any] = {}
    for key in properties:
        normalized_key = key.lower().replace("_", "")
        if "problem" in normalized_key and "id" in normalized_key:
            value = request.problem_id
        elif "language" in normalized_key or normalized_key == "lang":
            value = request.language_id if "id" in normalized_key else request.language
            schema = properties.get(key)
            enum = schema.get("enum") if isinstance(schema, dict) else None
            if value is None and isinstance(enum, list) and request.language:
                language = request.language.lower().replace("++", "pp").replace("#", "sharp").replace(" ", "")
                value = next(
                    (
                        candidate
                        for candidate in enum
                        if str(candidate).lower().replace("++", "pp").replace("#", "sharp").replace(" ", "")
                        in language
                        or language
                        in str(candidate).lower().replace("++", "pp").replace("#", "sharp").replace(" ", "")
                    ),
                    None,
                )
        elif any(token in normalized_key for token in ("code", "source", "solution", "submission", "content")):
            value = request.code
        else:
            value = None
        if value is not None:
            schema = properties.get(key)
            if isinstance(schema, dict) and schema.get("type") == "string" and "problem" in normalized_key:
                value = str(value)
            arguments[key] = value
    if not arguments:
        return {"problemId": request.problem_id, "languageId": request.language_id, "language": request.language, "code": request.code}
    required = parameters.get("required", []) if isinstance(parameters, dict) else []
    missing = [key for key in required if key not in arguments]
    if missing:
        raise ValueError(f"MCP submit tool requires unavailable argument(s): {', '.join(missing)}")
    return arguments


def _render_user_info_reply(
    result: V3MCPToolResult,
    *,
    authenticated_user_id: int | None,
) -> str:
    payload = result.parsed_payload
    flattened = _flatten_payload(payload)
    lines = ["已通过 MCP 的 `get_user_info` 查询到当前登录用户资料："]
    if authenticated_user_id is not None:
        lines.append(f"- 认证用户 ID：{authenticated_user_id}")
    fields = [
        ("邮箱", ("email", "mail")),
        ("昵称", ("nickname", "nickName", "name")),
        ("学校", ("school",)),
        ("专业", ("major",)),
        ("学号", ("student_id", "studentId")),
        ("真实姓名", ("realname", "realName")),
    ]
    for label, keys in fields:
        value = _first_text(flattened, keys)
        if value:
            lines.append(f"- {label}：{value}")
    if len(lines) == 1:
        lines.append(_compact_raw_text(result.raw_text))
    return "\n".join(lines)


def _render_problem_detail_reply(result: V3MCPToolResult) -> str:
    payload = result.parsed_payload
    flattened = _flatten_payload(payload)
    title = _first_text(flattened, ("title", "name", "problem_title", "problemTitle"))
    problem_id = _first_text(flattened, ("id", "problem_id", "problemId"))
    link = _first_text(flattened, ("link", "url"))
    content = _first_text(flattened, ("content", "description", "body", "statement", "problem_content", "problemContent"))
    lines = ["已通过 MCP 读取到题目。"]
    if title:
        lines.append(f"- 标题：{title}")
    if problem_id:
        lines.append(f"- 题目 ID：{problem_id}")
    if link:
        lines.append(f"- 链接：{link}")
    if content:
        excerpt = " ".join(content.replace("\r", "\n").split())
        if len(excerpt) > 500:
            excerpt = f"{excerpt[:497].rstrip()}..."
        lines.append("")
        lines.append(excerpt)
    if len(lines) == 1:
        lines.append(_compact_raw_text(result.raw_text))
    return "\n".join(lines)


def _render_json_summary_reply(prefix: str, result: V3MCPToolResult) -> str:
    payload = result.parsed_payload
    if payload is None:
        return f"{prefix}：\n\n{_compact_raw_text(result.raw_text)}"
    return f"{prefix}：\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"


def _compact_raw_text(text: str) -> str:
    compact = (text or "").strip()
    return compact[:1200] if compact else "工具返回为空。"


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _mcp_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "工具返回错误"
    return str(payload.get("message") or payload.get("error") or "工具返回错误")


def _select_problem_detail_tool(functions: dict[str, Any]) -> str | None:
    scored: list[tuple[int, str]] = []
    for name, function in functions.items():
        normalized_name = name.strip()
        if normalized_name in PREFERRED_PROBLEM_DETAIL_TOOLS:
            return normalized_name
        haystack = " ".join(
            [
                name,
                str(getattr(function, "description", "") or ""),
                json.dumps(getattr(function, "parameters", {}) or {}, ensure_ascii=False),
            ]
        ).lower()
        score = 0
        name_lower = normalized_name.lower()
        if any(hint in haystack for hint in PROBLEM_TOOL_HINTS):
            score += 2
        if any(hint in haystack for hint in DETAIL_TOOL_HINTS):
            score += 1
        if any(key.lower() in haystack for key in PROBLEM_ID_KEYS):
            score += 4
        if "problem" in name_lower and any(hint in name_lower for hint in ("info", "detail")):
            score += 8
        if any(hint in name_lower for hint in PROBLEM_LIST_TOOL_HINTS):
            score -= 6
        if any(key in haystack for key in ("classid", "examid", "submissionid")) and "problemid" not in haystack:
            score -= 8
        if score > 0:
            scored.append((score, name))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def _problem_argument_candidates(function: Any, problem_id: int) -> list[tuple[str, Any]]:
    parameters = getattr(function, "parameters", {}) or {}
    properties = parameters.get("properties") if isinstance(parameters, dict) else {}
    if not isinstance(properties, dict):
        properties = {}
    candidates: list[tuple[str, Any]] = []
    for key in PROBLEM_ID_KEYS:
        schema = properties.get(key)
        if isinstance(schema, dict):
            value: Any = str(problem_id) if schema.get("type") == "string" else problem_id
            candidates.append((key, value))
    for key in PROBLEM_ID_KEYS:
        value = str(problem_id) if key == "problemId" else problem_id
        if (key, value) not in candidates:
            candidates.append((key, value))
    return candidates


def _normalize_problem_result(result: str, *, problem_id: int) -> dict[str, Any] | None:
    payload = _parse_json_result(result)
    if payload is None:
        text = result.strip()
        return {"problem_id": problem_id, "title": f"题目 {problem_id}", "content": text, "source": "mcp"} if text else None
    if _payload_is_mcp_error(payload):
        return None
    flattened = _flatten_payload(payload)
    title = _first_text(flattened, ("title", "name", "problem_title", "problemTitle", "题目标题"))
    content = _first_text(flattened, ("content", "description", "body", "statement", "problem_content", "problemContent", "题面"))
    if not content:
        content = json.dumps(payload, ensure_ascii=False)
    return {
        "problem_id": int(_first_text(flattened, ("problem_id", "problemId", "id")) or problem_id),
        "title": title or f"题目 {problem_id}",
        "content": content,
        "source": "mcp",
        "raw_payload": payload if isinstance(payload, dict) else None,
    }


def _payload_is_mcp_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    error_value = payload.get("error")
    if not error_value:
        return False
    content_keys = {"content", "description", "body", "statement", "problem_content", "problemContent", "题面"}
    return not any(payload.get(key) for key in content_keys)


def _parse_json_result(result: str) -> Any | None:
    stripped = (result or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
            return parsed
        except json.JSONDecodeError:
            continue
    return None


def _flatten_payload(value: Any) -> dict[str, str]:
    flattened: dict[str, str] = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            flattened[key_text] = str(nested) if isinstance(nested, (str, int, float)) else ""
            flattened.update(_flatten_payload(nested))
    elif isinstance(value, list):
        for nested in value:
            flattened.update(_flatten_payload(nested))
    return flattened


def _first_text(payload: dict[str, str], keys: tuple[str, ...]) -> str:
    lowered = {key.lower(): value for key, value in payload.items() if value}
    for key in keys:
        value = payload.get(key) or lowered.get(key.lower())
        if value:
            return str(value).strip()
    return ""
