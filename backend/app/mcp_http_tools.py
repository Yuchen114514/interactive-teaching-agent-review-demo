from __future__ import annotations

import itertools
import json
from time import monotonic
from typing import Any

import httpx

from agno.tools import Toolkit
from agno.tools.function import Function


DEFAULT_MCP_PROTOCOL_VERSION = "2025-11-25"


def _extract_sse_message_body(body: str) -> dict[str, Any]:
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise ValueError("No SSE data payload found in response body")


def _parse_mcp_response(response: httpx.Response) -> dict[str, Any] | None:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type and response.text.strip():
        return _extract_sse_message_body(response.text)
    if not response.text.strip():
        return None
    return response.json()


def _flatten_tool_result(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list) and content:
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n\n".join(part for part in parts if part)
    return json.dumps(result, ensure_ascii=False)


class PostOnlyMCPToolkit(Toolkit):
    def __init__(
        self,
        *,
        url: str,
        session_token: str | None = None,
        proxy_url: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        super().__init__(name="MCPHttpTools", auto_register=False)
        self.url = url
        self.session_token = session_token
        self.proxy_url = (proxy_url or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self.deadline_at: float | None = None
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.client: httpx.Client | None = None
        self.initialized = False
        self._request_ids = itertools.count(1)

    def _build_http_client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout_seconds,
            "follow_redirects": False,
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
            kwargs["trust_env"] = False
        return httpx.Client(**kwargs)

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_token:
            headers["X-Accoding-Session"] = self.session_token
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if self.protocol_version:
            headers["mcp-protocol-version"] = self.protocol_version
        return headers

    def _post_json(self, payload: dict[str, Any]) -> tuple[httpx.Response, dict[str, Any] | None]:
        if self.client is None:
            raise RuntimeError("MCP HTTP client is not initialized")

        timeout = self._remaining_timeout_seconds()
        response = self.client.post(self.url, headers=self._build_headers(), json=payload, timeout=timeout)
        response.raise_for_status()

        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id

        return response, _parse_mcp_response(response)

    def _next_request_id(self) -> int:
        return next(self._request_ids)

    def _remaining_timeout_seconds(self) -> float:
        if self.deadline_at is None:
            return self.timeout_seconds
        remaining = self.deadline_at - monotonic()
        if remaining <= 0:
            raise TimeoutError("MCP request deadline exceeded")
        return remaining

    def connect(self) -> None:
        if self.initialized:
            return

        self.deadline_at = monotonic() + self.timeout_seconds
        self.client = self._build_http_client()
        _, init_message = self._post_json(
            {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": DEFAULT_MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "accoding-agent",
                        "version": "0.1",
                    },
                },
            }
        )

        if not init_message or "result" not in init_message:
            raise RuntimeError("MCP initialize did not return a result payload")

        result = init_message["result"]
        self.protocol_version = str(result.get("protocolVersion") or DEFAULT_MCP_PROTOCOL_VERSION)
        self._post_json(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        self.build_tools()
        self.initialized = True

    def build_tools(self) -> None:
        _, tools_message = self._post_json(
            {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/list",
                "params": {},
            }
        )

        if not tools_message or "result" not in tools_message:
            raise RuntimeError("MCP tools/list did not return a result payload")

        self.functions.clear()
        tools = tools_message["result"].get("tools", [])
        for tool in tools:
            tool_name = tool["name"]
            entrypoint = self._make_tool_entrypoint(tool_name)
            function = Function(
                name=tool_name,
                description=tool.get("description"),
                parameters=tool.get("inputSchema") or {"type": "object", "properties": {}, "required": []},
                entrypoint=entrypoint,
                skip_entrypoint_processing=True,
            )
            self.functions[tool_name] = function

    def _make_tool_entrypoint(self, tool_name: str):
        def _call(**kwargs):
            return self.call_tool(tool_name, kwargs)

        _call.__name__ = tool_name
        return _call

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        _, tool_message = self._post_json(
            {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments or {},
                },
            }
        )

        if not tool_message:
            raise RuntimeError(f"MCP tool {tool_name} returned an empty response")
        if "error" in tool_message:
            error = tool_message["error"]
            raise RuntimeError(error.get("message") or f"MCP tool {tool_name} failed")

        return _flatten_tool_result(tool_message.get("result") or {})

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        self.initialized = False
        self.session_id = None
        self.protocol_version = None
        self.deadline_at = None

    async def __aenter__(self) -> "PostOnlyMCPToolkit":
        self.connect()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        self.close()
