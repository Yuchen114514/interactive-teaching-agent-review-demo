import os
import asyncio
import json
import threading
from collections import defaultdict
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator
from urllib.parse import urlparse

import logging

from sqlalchemy import text
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.media import Audio, Image
from agno.models.openai.like import OpenAILike
from agno.tools import Toolkit
from agno.tools.mcp import MCPTools

try:
    from mcp import StdioServerParameters
except ImportError:
    StdioServerParameters = None

from app.agent_store import AgentStore
from app.config import get_settings
from app.database import session_context
try:
    from app.diagram_skill import DIAGRAM_SCENE_AUTHORING_SKILL
except ImportError:  # pragma: no cover - remote production may not ship diagram modules yet
    DIAGRAM_SCENE_AUTHORING_SKILL = []
from app.image_references import delete_orphaned_images, extract_local_image_id
from app.mcp_http_tools import PostOnlyMCPToolkit
from app.models import (
    AgentMessageRole,
    AgentMessageType,
    AgentMode,
    AgentSessionSource,
    ImageAsset,
)
from app.session_titles import generate_session_title
from app.schemas import GuidanceDepth, TeachingTurnRequest
from app.teaching.agent_providers import build_teaching_agent_providers
from app.teaching.orchestrator import TeachingOrchestrator
from app.teaching.taxonomy import load_taxonomy
from app.visible_history import render_visible_history_context


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")


MCP_TRIGGER_KEYWORDS = {
    "查询", "查看", "获取", "统计", "平台", "排行榜", "排名",
    "昵称", "个人信息", "个人数据", "提交数", "ac数", "ac 数",
    "本周ac", "本周 ac", "ac几道题", "ac 几道题", "ac题数", "ac 题数", "通过了几道题",
    "通过率", "提交记录", "评测记录", "课程", "班级",
    "题目数据", "题目信息", "样例", "测试点",
}


NORMAL_INSTRUCTIONS = [
    "你是 OJ（Online Judge）平台的问答助手，始终以清晰、准确的回答帮助用户。",
    "若用户问题明显偏离 OJ 学习场景，请礼貌收束，并引导到题目、代码、算法、调试、提交结果或刷题计划相关问题。",
    "当用户的问题涉及题目数据、题目信息、提交记录、个人信息或个人统计时，应主动调用可用工具进行查询，不要只靠猜测作答。",
    "在需要系统信息、提交统计或题目信息时，可调用 MCP 工具完成查询，确保内容基于可靠数据。",
    "不要凭空捏造答案；若不确定，可说明需要进一步查询。",
    "在被问及个人信息（昵称、AC 数、提交数）时，优先使用对应的 MCP 工具返回结果。",
]

SOCRATIC_INSTRUCTIONS = [
    "你是 OJ 平台苏格拉底导师：仅通过提问引导学生自主解题，不直接给答案或代码。",
    "仅处理题目分析、代码调试、算法理解与 OJ 学习直接相关的问题；若用户偏题，简短拒绝并引导回题目或代码。",
    "每轮只问一个关键认知点，等待学生回答后再推进；禁止提前给后续步骤、完整计划或解法。",
    "若学生要求直接代码，先礼貌拒绝，再追问核心逻辑第一步。",
    "不要直接肯定学生答案正确，不要替学生补全关键公式，也不要把完整做法改写成提示。",
    "在问题即将解决前，可使用普通 Markdown 选项，不要输出 JSON。",
    "选项在必要时提供，若将问题答案呈现为选项效果不佳时可不提供选项。",
    "如需给选项，请使用这种普通文本格式：问题：... 然后换行写 A. ... B. ... C. ...。",
    "Debug 先问报错与关键条件；优化先问复杂度与方案对比；除上述内容外不输出无关文本。",
]

class AgentManager:
    def __init__(self):
        self.settings = get_settings()
        self._stop_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)

        model_id = self.settings.external_llm_model
        base_url = self.settings.external_llm_base_url
        api_key = self.settings.external_llm_api_key
        logger.info("Using External LLM: %s via %s", model_id, base_url)

        self.model = OpenAILike(
            id=model_id,
            base_url=base_url,
            api_key=api_key,
            temperature=self.settings.llm_temperature,
            timeout=self.settings.external_llm_timeout_seconds,
            max_retries=self.settings.external_llm_max_retries,
        )
        self.agno_db = self._build_agno_db()

    def _build_agno_db(self) -> SqliteDb | None:
        if self.settings.app_env == "test":
            return None
        backend_root = Path(__file__).resolve().parents[2]
        db_file = backend_root / ".agno_sessions.db"
        return SqliteDb(db_file=str(db_file))

    async def get_mcp_tools(self, session_token: str = None) -> Toolkit | MCPTools:
        logger.info(
            f"Initialized MCP with token: {session_token[:10]}..."
            if session_token
            else "No token provided"
        )
        if self.settings.mcp_mode == "stdio":
            if StdioServerParameters is None:
                raise ImportError("mcp package is required for stdio mode. Run `pip install mcp`.")
            cmd_args = list(self.settings.mcp_stdio_args)
            if session_token:
                cmd_args.extend(["--session", session_token])

            env = os.environ.copy()
            if hasattr(self.settings, "mcp_node_bin_dir") and self.settings.mcp_node_bin_dir:
                env["PATH"] = self.settings.mcp_node_bin_dir + os.pathsep + env["PATH"]

            server_params = StdioServerParameters(
                command=self.settings.mcp_stdio_command,
                args=cmd_args,
                env=env,
            )
            return MCPTools(server_params=server_params)

        return PostOnlyMCPToolkit(
            url=self.settings.mcp_http_url,
            session_token=session_token,
            proxy_url=self.settings.mcp_http_proxy,
            timeout_seconds=20.0,
        )

    async def create_agent(
        self,
        session_id: str,
        user_id: str | None = None,
        session_token: str | None = None,
        socratic: bool = False,
        problem_id: int | None = None,
        visible_history_context: str | None = None,
        request_message: str | None = None,
        source: str | None = None,
    ) -> Agent:
        use_mcp = self._should_enable_mcp(request_message or "", source=source)
        mcp_tool = await self.get_mcp_tools(session_token) if self.settings.mcp_enabled and use_mcp else None
        use_socratic_instructions = socratic
        instructions = list(SOCRATIC_INSTRUCTIONS if use_socratic_instructions else NORMAL_INSTRUCTIONS)
        instructions.extend(DIAGRAM_SCENE_AUTHORING_SKILL)
        description = (
            "You are a Socratic Tutor."
            if use_socratic_instructions
            else "You are a helpful assistant for the OJ (Online Judge) platform."
        )
        context_sections: list[str] = []
        if source or problem_id is not None:
            context_fragments: list[str] = []
            if source:
                context_fragments.append(f"source={source}")
            if problem_id is not None:
                context_fragments.append(f"problem_id={problem_id}")
            context_sections.append("当前题目上下文：" + "; ".join(context_fragments))
        if visible_history_context:
            context_sections.append(visible_history_context)
        additional_context = "\n\n".join(section for section in context_sections if section)

        return Agent(
            model=self.model,
            tools=[mcp_tool] if mcp_tool is not None else [],
            db=self.agno_db,
            add_history_to_context=self.agno_db is not None,
            num_history_messages=self.settings.max_context_messages if self.agno_db is not None else None,
            read_chat_history=self.agno_db is not None,
            store_history_messages=self.agno_db is not None,
            session_id=session_id,
            user_id=user_id,
            description=description,
            instructions=instructions,
            additional_context=additional_context,
        )

    def _should_enable_mcp(self, message: str, source: str | None = None) -> bool:
        normalized = message.strip().lower()
        if not normalized:
            return False

        if source in {
            AgentSessionSource.PROBLEM_HELP.value,
            AgentSessionSource.SUBMISSION_DEBUG.value,
            AgentSessionSource.COURSE_TUTOR.value,
            AgentSessionSource.EXAM_ASSISTANT.value,
        }:
            return True

        return any(keyword in normalized for keyword in MCP_TRIGGER_KEYWORDS)

    def _build_visible_history_context(self, session_id: str, *, socratic: bool, limit: int = 6) -> str:
        with session_context() as db:
            store = AgentStore(db)
            history = store.get_session_history(session_id)
            session = store.get_session(session_id)
            facts = store.list_session_facts(
                session.id if session is not None else None,
                limit=self.settings.max_memory_results,
            )
            recent_diagram_payloads = store.list_recent_diagram_scene_payloads(session_id, limit=3)

        recent_history = history[-limit:]
        history_context = render_visible_history_context(recent_history, limit=limit)
        sections: list[str] = []
        if session is not None and session.problem_id is not None:
            row = db.exec(
                text("SELECT title, content FROM problem WHERE id = :problem_id LIMIT 1"),
                params={"problem_id": session.problem_id},
            ).first()
            if row is not None:
                problem_title = (str(row[0]) if row[0] is not None else "").strip()
                problem_content = "\n".join(
                    line.strip() for line in (str(row[1]) if row[1] is not None else "").splitlines() if line.strip()
                )
                fragments = [f"problem_id={session.problem_id}"]
                if problem_title:
                    fragments.append(f"title={problem_title}")
                sections.append("当前题目上下文：" + "; ".join(fragments))
                if problem_content:
                    if len(problem_content) > 600:
                        problem_content = f"{problem_content[:597].rstrip()}..."
                    sections.append(f"当前题目内容摘要：{problem_content}")
        if session and session.summary:
            sections.append(f"当前会话摘要：{session.summary}")
        if socratic and facts:
            sections.append(
                "当前题目工作记忆：\n" + "\n".join(f"- {fact.content}" for fact in reversed(facts))
            )
        diagram_context = self._render_recent_diagram_context(recent_diagram_payloads)
        if diagram_context:
            sections.append(diagram_context)
        if history_context:
            sections.append(history_context)
        return "\n\n".join(sections)

    def _render_recent_diagram_context(self, scene_payloads: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for scene in scene_payloads:
            steps = scene.get("steps") or []
            if not steps:
                continue
            default_step_id = scene.get("default_step_id")
            base_step = next((step for step in steps if step.get("step_id") == default_step_id), steps[-1])
            exit_state = base_step.get("exit_state") or {}
            items = self._summarize_diagram_elements(exit_state.get("items") or [], "item_id")
            nodes = self._summarize_diagram_elements(exit_state.get("nodes") or [], "node_id")
            edges = self._summarize_diagram_edges(exit_state.get("edges") or [])
            fragments = [
                f"scene_id={scene.get('scene_id')}",
                f"kind={scene.get('diagram_kind')}",
                f"title={scene.get('title')}",
                f"base_step_id={base_step.get('step_id')}",
            ]
            if items:
                fragments.append(f"items=[{items}]")
            if nodes:
                fragments.append(f"nodes=[{nodes}]")
            if edges:
                fragments.append(f"edges=[{edges}]")
            lines.append("- " + "; ".join(fragments))
        if not lines:
            return ""
        return "最近图解状态：\n" + "\n".join(lines)

    def _summarize_diagram_elements(self, elements: list[dict[str, Any]], id_key: str) -> str:
        summaries: list[str] = []
        for element in elements[:8]:
            element_id = element.get(id_key)
            label = element.get("label")
            if not element_id or label is None:
                continue
            summaries.append(f"{element_id}:{label}")
        return ", ".join(summaries)

    def _summarize_diagram_edges(self, edges: list[dict[str, Any]]) -> str:
        summaries: list[str] = []
        for edge in edges[:8]:
            edge_id = edge.get("edge_id")
            from_id = edge.get("from")
            to_id = edge.get("to")
            label = edge.get("label")
            if not edge_id or not from_id or not to_id:
                continue
            edge_text = f"{edge_id}:{from_id}->{to_id}"
            if label:
                edge_text += f"({label})"
            summaries.append(edge_text)
        return ", ".join(summaries)

    def get_diagram_scene(self, session_id: str, scene_id: str) -> dict[str, Any] | None:
        with session_context() as db:
            store = AgentStore(db)
            history = store.get_session_history(session_id)
            stored_scene = store.get_diagram_scene_payload(session_id, scene_id)

        has_matching_card = any(
            entry.get("message_type") in {AgentMessageType.DIAGRAM_CARD.value, AgentMessageType.DIAGRAM_INLINE.value}
            and self._diagram_card_scene_id(entry.get("content")) == scene_id
            for entry in history
        )
        if not has_matching_card:
            return None

        if stored_scene is not None:
            return stored_scene

        return None

    def _diagram_card_scene_id(self, content: str | None) -> str | None:
        if not content:
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
        scene_id = payload.get("scene_id")
        return scene_id if isinstance(scene_id, str) else None

    def _stop_event_for(self, session_id: str) -> asyncio.Event:
        return self._stop_events[session_id]

    def request_stop(self, session_id: str, user_id: str) -> bool:
        del user_id
        self._stop_event_for(session_id).set()
        return True

    def _reset_stop_signal(self, session_id: str) -> None:
        self._stop_event_for(session_id).clear()

    def _record_turn_image_references(
        self,
        user_message_id: int | None,
        user_id: str | int | None,
        images: List[str] | None,
    ) -> None:
        if user_message_id is None or user_id is None or not images:
            return

        image_ids: list[str] = []
        for image in images:
            image_id = extract_local_image_id(image)
            if image_id and image_id not in image_ids:
                image_ids.append(image_id)

        if not image_ids:
            return

        with session_context() as db:
            AgentStore(db).attach_images(user_message_id, image_ids, int(user_id))
            db.commit()

    def _prepare_media(self, images: List[str] = None, audio: List[str] = None) -> Dict[str, Any]:
        media_args: Dict[str, Any] = {}

        if images:
            img_objs = []
            for img in images:
                local_image_id = extract_local_image_id(img)
                uploaded_image = self._load_uploaded_image(img) if local_image_id else None
                if uploaded_image is not None:
                    img_objs.append(uploaded_image)
                elif local_image_id:
                    logger.info("Skip deleted local image %s when preparing model context", local_image_id)
                elif img.startswith("http://") or img.startswith("https://"):
                    img_objs.append(Image(url=img))
                else:
                    img_objs.append(Image(content=img.encode("utf-8")))
            media_args["images"] = img_objs

        if audio:
            audio_objs = []
            for aud in audio:
                if aud.startswith("http://") or aud.startswith("https://"):
                    audio_objs.append(Audio(url=aud))
                else:
                    audio_objs.append(Audio(content=aud))
            media_args["audio"] = audio_objs

        return media_args

    def _guard_scope(self, message: str, socratic: bool, visible_history_context: str | None) -> str | None:
        del message, socratic, visible_history_context
        return None

    def get_scope_block_reply(self, session_id: str, message: str, socratic: bool) -> str | None:
        del session_id, message, socratic
        return None

    def _should_use_teaching_pipeline(
        self,
        *,
        socratic: bool,
        source: str | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
    ) -> bool:
        teaching_source = source not in {None, "", AgentSessionSource.WEB.value}
        return (
            socratic
            or teaching_source
            or problem_id is not None
            or submission_id is not None
            or course_id is not None
            or class_id is not None
            or exam_id is not None
        )

    def _record_regular_chat_user_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        message: str,
        socratic: bool,
        source: str | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
        images: List[str] | None = None,
    ) -> int:
        normalized_user_id = int(user_id)
        with session_context() as db:
            store = AgentStore(db)
            agent_session = store.get_or_create_session(
                session_id,
                normalized_user_id,
                AgentMode.SOCRATIC if socratic else AgentMode.NORMAL,
                source=AgentSessionSource(source) if source else None,
                problem_id=problem_id,
                submission_id=submission_id,
                course_id=course_id,
                class_id=class_id,
                exam_id=exam_id,
            )
            if agent_session.id is None:
                db.flush()
            turn_index = store.next_turn_index(agent_session.id)
            user_row = store.add_message(agent_session, AgentMessageRole.USER, message, turn_index=turn_index)
            if not agent_session.title:
                agent_session.title = generate_session_title(message)
                db.add(agent_session)
            db.commit()
            user_message_id = user_row.id

        self._record_turn_image_references(user_message_id, user_id, images)
        return turn_index

    def _record_regular_chat_assistant_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        response: str,
        socratic: bool,
        turn_index: int,
        source: str | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
    ) -> None:
        if not response:
            return
        normalized_user_id = int(user_id)
        with session_context() as db:
            store = AgentStore(db)
            agent_session = store.get_or_create_session(
                session_id,
                normalized_user_id,
                AgentMode.SOCRATIC if socratic else AgentMode.NORMAL,
                source=AgentSessionSource(source) if source else None,
                problem_id=problem_id,
                submission_id=submission_id,
                course_id=course_id,
                class_id=class_id,
                exam_id=exam_id,
            )
            store.add_message(
                agent_session,
                AgentMessageRole.ASSISTANT,
                response,
                turn_index=turn_index,
            )
            db.commit()

    def _schedule_regular_chat_assistant_turn(self, **kwargs) -> None:
        def runner() -> None:
            try:
                self._record_regular_chat_assistant_turn(**kwargs)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Background assistant turn persistence failed: %s", exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

    def _log_background_task_exception(self, task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Background assistant turn persistence failed: %s", exc)

    def _extract_stream_text(self, event: Any) -> str:
        content = getattr(event, "content", None)
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        return str(content)

    def _load_uploaded_image(self, image_ref: str) -> Optional[Image]:
        parsed = urlparse(image_ref)
        image_path = parsed.path.rstrip("/")
        prefix = "/api/agents/media/images/"
        if not image_path.startswith(prefix):
            return None

        image_id = image_path.removeprefix(prefix).strip()
        if not image_id:
            return None

        try:
            with session_context() as db:
                asset = db.get(ImageAsset, image_id)
            if asset is None:
                return None

            storage_path = Path(asset.storage_path)
            if not storage_path.exists():
                logger.warning("Uploaded image file missing for %s", image_id)
                return None

            return Image(
                content=storage_path.read_bytes(),
                mime_type=asset.content_type,
                format=storage_path.suffix.lstrip(".") or None,
            )
        except Exception as e:
            logger.warning("Failed to resolve uploaded image %s: %s", image_ref, e)
            return None

    async def arun_v2_compat(
        self,
        message: str,
        session_id: str,
        user_id: str = None,
        session_token: str = None,
        socratic: bool = False,
        source: str | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
        images: List[str] = None,
        audio: List[str] = None,
    ) -> str:
        del session_token
        self._reset_stop_signal(session_id)
        if user_id is None:
            raise ValueError("user_id is required for v2-compatible chat")

        normalized_user_id = int(user_id)
        mode = "socratic" if socratic else "normal"
        guidance_depth = GuidanceDepth.SOCRATIC if socratic else GuidanceDepth.DIRECT
        with session_context() as db:
            store = AgentStore(db)
            agent_session = store.get_or_create_session(
                session_id,
                normalized_user_id,
                AgentMode.SOCRATIC if socratic else AgentMode.NORMAL,
                source=AgentSessionSource(source) if source else None,
                problem_id=problem_id,
                submission_id=submission_id,
                course_id=course_id,
                class_id=class_id,
                exam_id=exam_id,
            )
            if agent_session.id is None:
                db.flush()
            turn_index = store.next_turn_index(agent_session.id)
            user_row = store.add_message(agent_session, AgentMessageRole.USER, message, turn_index=turn_index)
            if not agent_session.title:
                agent_session.title = generate_session_title(message)
                db.add(agent_session)
            self._record_turn_image_references(user_row.id, user_id, images)

            result = TeachingOrchestrator(
                db,
                agent_providers=build_teaching_agent_providers(self.settings),
                taxonomy=load_taxonomy(self.settings.teaching_algorithm_taxonomy_path),
                supervisor_provider_timeout_seconds=self.settings.teaching_supervisor_provider_timeout_seconds,
                optional_specialist_result_timeout_seconds=(
                    self.settings.teaching_optional_specialist_result_timeout_seconds
                ),
            ).run_turn(
                TeachingTurnRequest(
                    message=message,
                    session_id=session_id,
                    mode=mode,
                    guidance_depth=guidance_depth,
                    source=source,
                    problem_id=problem_id,
                    submission_id=submission_id,
                    course_id=course_id,
                    class_id=class_id,
                    images=images,
                    audio=audio,
                ),
                session_numeric_id=agent_session.id,
                user_id=normalized_user_id,
                turn_index=turn_index,
            )
            store.add_message(
                agent_session,
                AgentMessageRole.ASSISTANT,
                result.response,
                turn_index=turn_index,
            )
            db.commit()
            return result.response

    async def astream_v2_compat(
        self,
        message: str,
        session_id: str,
        user_id: str = None,
        session_token: str = None,
        socratic: bool = False,
        source: str | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
        images: List[str] = None,
        audio: List[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        response = await self.arun_v2_compat(
            message,
            session_id=session_id,
            user_id=user_id,
            session_token=session_token,
            socratic=socratic,
            source=source,
            problem_id=problem_id,
            submission_id=submission_id,
            course_id=course_id,
            class_id=class_id,
            exam_id=exam_id,
            images=images,
            audio=audio,
        )
        if response:
            yield {"type": "text", "content": response}

    async def arun(
        self,
        message: str,
        session_id: str,
        user_id: str = None,
        session_token: str = None,
        socratic: bool = False,
        source: str | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
        images: List[str] = None,
        audio: List[str] = None,
    ) -> str:
        if not self._should_use_teaching_pipeline(
            socratic=socratic,
            source=source,
            problem_id=problem_id,
            submission_id=submission_id,
            course_id=course_id,
            class_id=class_id,
            exam_id=exam_id,
        ):
            chunks: list[str] = []
            async for chunk in self.astream(
                message,
                session_id=session_id,
                user_id=user_id,
                session_token=session_token,
                socratic=socratic,
                source=source,
                problem_id=problem_id,
                submission_id=submission_id,
                course_id=course_id,
                class_id=class_id,
                exam_id=exam_id,
                images=images,
                audio=audio,
            ):
                if chunk.get("type") == "text":
                    chunks.append(str(chunk.get("content") or ""))
            return "".join(chunks)

        return await self.arun_v2_compat(
            message,
            session_id=session_id,
            user_id=user_id,
            session_token=session_token,
            socratic=socratic,
            source=source,
            problem_id=problem_id,
            submission_id=submission_id,
            course_id=course_id,
            class_id=class_id,
            exam_id=exam_id,
            images=images,
            audio=audio,
        )

    async def astream(
        self,
        message: str,
        session_id: str,
        user_id: str = None,
        session_token: str = None,
        socratic: bool = False,
        source: str | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
        images: List[str] = None,
        audio: List[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not self._should_use_teaching_pipeline(
            socratic=socratic,
            source=source,
            problem_id=problem_id,
            submission_id=submission_id,
            course_id=course_id,
            class_id=class_id,
            exam_id=exam_id,
        ):
            self._reset_stop_signal(session_id)
            if user_id is None:
                raise ValueError("user_id is required for regular chat")

            turn_index = self._record_regular_chat_user_turn(
                session_id=session_id,
                user_id=user_id,
                message=message,
                socratic=socratic,
                source=source,
                problem_id=problem_id,
                submission_id=submission_id,
                course_id=course_id,
                class_id=class_id,
                exam_id=exam_id,
                images=images,
            )
            visible_history_context = self._build_visible_history_context(session_id, socratic=socratic)
            agent = await self.create_agent(
                session_id,
                user_id=user_id,
                session_token=session_token,
                socratic=socratic,
                problem_id=problem_id,
                visible_history_context=visible_history_context,
                request_message=message,
                source=source,
            )
            full_response = ""
            stream = agent.arun(
                message,
                stream=True,
                user_id=user_id,
                session_id=session_id,
                **self._prepare_media(images, audio),
            )
            async for event in stream:
                if self._stop_event_for(session_id).is_set():
                    break
                incoming = self._extract_stream_text(event)
                if not incoming:
                    continue
                full_response += incoming
                yield {"type": "text", "content": incoming}

            self._schedule_regular_chat_assistant_turn(
                session_id=session_id,
                user_id=user_id,
                response=full_response,
                socratic=socratic,
                turn_index=turn_index,
                source=source,
                problem_id=problem_id,
                submission_id=submission_id,
                course_id=course_id,
                class_id=class_id,
                exam_id=exam_id,
            )
            return

        async for chunk in self.astream_v2_compat(
            message,
            session_id=session_id,
            user_id=user_id,
            session_token=session_token,
            socratic=socratic,
            source=source,
            problem_id=problem_id,
            submission_id=submission_id,
            course_id=course_id,
            class_id=class_id,
            exam_id=exam_id,
            images=images,
            audio=audio,
        ):
            yield chunk

    def get_session_history(
        self,
        session_id: str,
        limit: int = None,
        offset: int = 0,
        after_message_id: int | None = None,
    ) -> List[Dict[str, Any]]:
        if not session_id:
            return []
        with session_context() as db:
            return AgentStore(db).get_session_history(
                session_id,
                limit=limit,
                offset=offset,
                after_message_id=after_message_id,
            )

    def get_session_history_meta(self, session_id: str) -> Dict[str, Any]:
        if not session_id:
            return {
                "count": 0,
                "max_message_id": None,
                "last_message_at": None,
            }
        with session_context() as db:
            return AgentStore(db).get_session_history_meta(session_id)

    def get_user_sessions(self, user_id: str, *, showcase_uuid: str | None = None) -> List[Dict[str, Any]]:
        with session_context() as db:
            return AgentStore(db).list_user_sessions(int(user_id), showcase_uuid=showcase_uuid)

    def get_user_session_groups(self, user_id: str, *, showcase_uuid: str | None = None) -> Dict[str, Any]:
        with session_context() as db:
            return AgentStore(db).list_user_session_groups(int(user_id), showcase_uuid=showcase_uuid)

    def delete_session(self, session_id: str, user_id: str) -> bool:
        with session_context() as db:
            store = AgentStore(db)
            history = store.get_session_history(session_id)
            image_ids = []
            for entry in history:
                for image in entry.get("images", []):
                    image_ids.append(image["image_id"])
            deleted = store.delete_session(session_id, int(user_id))
            if deleted:
                delete_orphaned_images(db, image_ids)
            return deleted

    def rename_session(self, session_id: str, new_title: str, user_id: str) -> bool:
        with session_context() as db:
            renamed = AgentStore(db).rename_session(session_id, int(user_id), new_title)
            if renamed:
                db.commit()
            return renamed

    def delete_last_turn(self, session_id: str, user_id: str) -> bool:
        with session_context() as db:
            store = AgentStore(db)
            history = store.get_session_history(session_id)
            last_images = history[-2].get("images", []) if len(history) >= 2 else []
            deleted = store.undo_last_turn(session_id, int(user_id))
            if deleted:
                delete_orphaned_images(db, [image["image_id"] for image in last_images])
            return deleted

    def delete_message(self, message_id: int, user_id: str) -> bool:
        with session_context() as db:
            return AgentStore(db).delete_message(message_id, int(user_id))

    def delete_response_group(self, message_id: int, user_id: str) -> dict[str, Any]:
        with session_context() as db:
            return AgentStore(db).delete_response_group(message_id, int(user_id))

    def delete_turns_from_message(self, message_id: int, user_id: str) -> dict[str, Any]:
        with session_context() as db:
            result = AgentStore(db).delete_turns_from_message(message_id, int(user_id))
            if result.get("deleted"):
                delete_orphaned_images(db, result.get("deleted_image_ids") or [])
            return result


manager = AgentManager()
