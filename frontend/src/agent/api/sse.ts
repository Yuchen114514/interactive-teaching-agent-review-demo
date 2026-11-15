import type { AgentStreamEvent } from "./types";

type RawStreamPayload = {
  type?: string;
  session_id?: unknown;
  run_id?: unknown;
  stage?: unknown;
  message?: unknown;
  agent?: unknown;
  heartbeat?: unknown;
  model?: unknown;
  provider_backend?: unknown;
  delta?: unknown;
  text?: unknown;
  response?: unknown;
  status?: unknown;
  resource?: unknown;
  entry?: unknown;
  error?: unknown;
  final?: unknown;
  problem_id?: unknown;
  problem_title?: unknown;
  action_label?: unknown;
  need_diagram?: unknown;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

export function parseAgentSseBuffer(buffer: string): {
  events: AgentStreamEvent[];
  remainder: string;
} {
  const chunks = buffer.split(/\r?\n\r?\n/);
  const completeChunks = chunks.slice(0, -1);
  const remainder = chunks.length > 0 ? chunks[chunks.length - 1] : "";
  const events: AgentStreamEvent[] = [];

  for (const chunk of completeChunks) {
    const dataLines = chunk
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.startsWith("data:"));

    for (const line of dataLines) {
      const payloadText = line.slice(5).trim();
      if (!payloadText) continue;
      if (payloadText === "[DONE]") {
        events.push({ kind: "done" });
        continue;
      }

      let parsed: RawStreamPayload;
      try {
        parsed = JSON.parse(payloadText) as RawStreamPayload;
      } catch (error) {
        events.push({
          kind: "error",
          error:
            error instanceof Error ? error.message : "Invalid stream payload",
        });
        continue;
      }

      const payload = asRecord(parsed);
      const sessionId = asString(parsed.session_id);
      const stage = asString(parsed.stage);
      const message = asString(parsed.message);
      const delta = asString(parsed.delta);
      const text = asString(parsed.text);
      const response = asString(parsed.response);
      const status = asString(parsed.status);

      if (parsed.type === "problem_redirect") {
        const parsedProblemId = Number(parsed.problem_id);
        const problemId =
          Number.isInteger(parsedProblemId) && parsedProblemId > 0
            ? parsedProblemId
            : null;
        events.push({
          kind: "problem_redirect",
          problemId,
          problemTitle:
            asString(parsed.problem_title) ||
            (problemId ? `题目 ${problemId}` : "新题目"),
          message: asString(parsed.message) || "",
          actionLabel: asString(parsed.action_label) || "携带会话进入该题目",
        });
      }

      if (sessionId) {
        const runId = asString(parsed.run_id);
        events.push({
          kind: "session",
          sessionId,
          ...(runId ? { runId } : {}),
        });
      }

      if (parsed.type === "progress" && stage && message) {
        events.push({
          kind: "progress",
          stage,
          message,
          agent: asString(parsed.agent),
          heartbeat: parsed.heartbeat === true,
          model: asString(parsed.model),
          providerBackend: asString(parsed.provider_backend),
        });
      }

      if (parsed.type === "main_delta" && delta) {
        events.push({ kind: "main_delta", delta });
      }

      if (parsed.type === "answer_delta" && delta) {
        events.push({ kind: "answer_delta", delta });
      }

      if (parsed.type === "text" && text) {
        events.push({ kind: "text", text });
      }

      if (parsed.type === "partial_response" && response) {
        events.push({ kind: "partial_response", response });
      }

      if (parsed.type === "main_block.visible_complete" && response) {
        events.push({ kind: "main_visible_complete", response });
      }

      if (parsed.type === "main_block.visual_need") {
        events.push({ kind: "diagram_decision", needDiagram: parsed.need_diagram === true });
      }

      if (parsed.type === "main_followup.start") events.push({ kind: "followup_start" });
      if (parsed.type === "main_followup.delta" && delta) events.push({ kind: "followup_delta", delta });
      if (parsed.type === "main_followup.visible_complete" && response)
        events.push({ kind: "followup_complete", response });

      if (parsed.type === "mcp_status" && status) {
        events.push({
          kind: "mcp_status",
          status,
          resource: asString(parsed.resource),
          message: asString(parsed.message),
          agent: asString(parsed.agent),
          payload,
        });
      }

      if (parsed.type === "plan_status" && status) {
        events.push({
          kind: "plan_status",
          status,
          agent: asString(parsed.agent),
          message: asString(parsed.message),
          payload,
        });
      }

      if (parsed.type === "diagram_status" && status) {
        events.push({ kind: "diagram_status", status, payload });
      }

      if (parsed.type === "diagram_ready") {
        events.push({ kind: "diagram_ready", payload });
      }

      if (parsed.type === "whiteboard_status" && status) {
        events.push({ kind: "whiteboard_status", status, payload });
      }

      if (parsed.type === "memory_status" && status) {
        events.push({ kind: "memory_status", status, payload });
      }

      if (parsed.type === "compaction_status" && status) {
        events.push({ kind: "compaction_status", status, payload });
      }

      if (parsed.type === "whiteboard_ready") {
        events.push({ kind: "whiteboard_ready", entry: parsed.entry, payload });
      }

      if (parsed.type === "run_status" && status) {
        events.push({
          kind: "run_status",
          status,
          message: asString(parsed.message),
          payload,
        });
      }

      if (parsed.type === "final") {
        events.push({ kind: "final", response: payload });
      }

      if (parsed.type === "error" || asString(parsed.error)) {
        events.push({
          kind: "error",
          error: asString(parsed.error) ?? "Agent stream error",
        });
      }
    }
  }

  return { events, remainder };
}
