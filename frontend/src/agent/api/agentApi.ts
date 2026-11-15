import { parseAgentSseBuffer } from "./sse";
import type {
  AgentChatRequestBody,
  AgentBlackboardEntry,
  AgentDiagramSummary,
  AgentHistoryMessage,
  AgentSessionSummary,
  AgentSessionGroupsResponse,
  AgentStreamEvent,
  AgentTurnRunEventsResponse,
  AgentTurnRunSummary,
  AgentUploadedImage,
  AgentUserPreferences,
  AgentMascotSkinPreference,
  AgentFontSizePreference,
  AgentRuntimeInfo,
  AgentWorkspaceSummary,
} from "./types";
import { resolveAgentApiBaseUrl } from "../config";
import { isEnglishDemo, notifyDemoAuthExpired } from "../../en-demo/mode";

type RequestOptions = {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
};

export class AgentApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "AgentApiError";
    this.status = status;
    this.code = code;
  }
}

type AgentHistoryResponse = {
  history?: AgentHistoryMessage[];
  messages?: AgentHistoryMessage[];
  data?: AgentHistoryMessage[];
  meta?: {
    count?: number;
    max_message_id?: number | null;
    last_message_at?: string | number | null;
  };
};

type AgentListResponse<T> =
  | T[]
  | {
      data?: T[];
      items?: T[];
      workspaces?: T[];
      blackboard?: T[];
      diagrams?: T[];
      scenes?: T[];
    };

function agentBaseUrl(): string {
  return resolveAgentApiBaseUrl();
}

export function buildAgentJsonHeaders(token: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-Accoding-Session": token,
    Authorization: token,
  };
}

function buildAgentAuthHeaders(token: string): Record<string, string> {
  return {
    "X-Accoding-Session": token,
    Authorization: token,
  };
}

async function parseResponseError(
  response: Response,
): Promise<{ message: string; code?: string }> {
  try {
    const payload = await response.clone().json();
    const message =
      payload?.error?.message ||
      payload?.message ||
      payload?.detail ||
      `HTTP ${response.status}: ${response.statusText}`;
    const code = payload?.error?.code || payload?.code;
    return { message, code };
  } catch {
    return { message: `HTTP ${response.status}: ${response.statusText}` };
  }
}

async function throwAgentResponseError(
  response: Response,
  path: string,
  token?: string,
): Promise<never> {
  const parsed = await parseResponseError(response);
  if (isEnglishDemo && response.status === 401 && token) {
    notifyDemoAuthExpired(token);
    throw new AgentApiError("Your demo session expired. Please retry in a moment.", 401, "AUTH_EXPIRED");
  }
  const missingV3Endpoint =
    response.status === 404 &&
    (path.startsWith("/v2/") || path.startsWith("/v3/"));
  throw new AgentApiError(
    missingV3Endpoint
      ? "当前 Agent 后端缺少新版 v2/v3 接口，请将新版工作台接入 v3 Agent 后端。"
      : parsed.message,
    response.status,
    missingV3Endpoint ? "AGENT_BACKEND_INCOMPATIBLE" : parsed.code,
  );
}

function requestDeadline(signal?: AbortSignal, timeoutMs = 20_000) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  let timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    reset(milliseconds: number) {
      clearTimeout(timer);
      timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, milliseconds);
    },
    error(error: unknown) {
      return timedOut
        ? new AgentApiError(
            "Agent 请求超时，请检查网络后重试。",
            408,
            "AGENT_REQUEST_TIMEOUT",
          )
        : error;
    },
    dispose() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    },
  };
}

async function requestAgentJson<T>(
  path: string,
  token: string,
  options: RequestOptions = {},
): Promise<T> {
  const deadline = requestDeadline(options.signal, options.timeoutMs);
  try {
    const response = await fetch(`${agentBaseUrl()}${path}`, {
      method: options.method ?? "GET",
      headers: buildAgentJsonHeaders(token),
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: deadline.signal,
    });
    if (!response.ok) await throwAgentResponseError(response, path, token);
    return (await response.json()) as T;
  } catch (error) {
    throw deadline.error(error);
  } finally {
    deadline.dispose();
  }
}

export async function listAgentSessions(
  token: string,
): Promise<AgentSessionSummary[]> {
  const data = await requestAgentJson<
    | AgentSessionSummary[]
    | { sessions?: AgentSessionSummary[]; data?: AgentSessionSummary[] }
  >("/sessions", token);
  if (Array.isArray(data)) return data;
  if (Array.isArray(data.sessions)) return data.sessions;
  if (Array.isArray(data.data)) return data.data;
  return [];
}

export async function listAgentSessionGroups(
  token: string,
): Promise<AgentSessionGroupsResponse> {
  return requestAgentJson<AgentSessionGroupsResponse>(
    "/sessions/groups",
    token,
  );
}

export async function createAgentSession(
  token: string,
  problemId?: number,
): Promise<AgentSessionSummary> {
  return requestAgentJson<AgentSessionSummary>("/sessions", token, {
    method: "POST",
    body: problemId === undefined ? {} : { problem_id: problemId },
  });
}

export async function getAgentRuntime(
  token: string,
): Promise<AgentRuntimeInfo> {
  return requestAgentJson<AgentRuntimeInfo>("/v2/runtime", token);
}

export async function getAgentUserPreferences(
  token: string,
): Promise<AgentUserPreferences> {
  return requestAgentJson<AgentUserPreferences>(
    "/v3/users/me/preferences",
    token,
  );
}

export async function patchAgentUserPreferences(
  token: string,
  preferences: Partial<AgentUserPreferences>,
): Promise<AgentUserPreferences> {
  return requestAgentJson<AgentUserPreferences>(
    "/v3/users/me/preferences",
    token,
    {
      method: "PATCH",
      body: preferences,
    },
  );
}

export async function updateAgentUserPreferences(
  token: string,
  mascotSkin: AgentMascotSkinPreference,
  fontSize: AgentFontSizePreference,
): Promise<AgentUserPreferences> {
  return requestAgentJson<AgentUserPreferences>(
    "/v3/users/me/preferences",
    token,
    {
      method: "PATCH",
      body: { mascot_skin: mascotSkin, font_size: fontSize },
    },
  );
}

function normalizeAgentHistory(
  data: AgentHistoryResponse | AgentHistoryMessage[],
): AgentHistoryMessage[] {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data.history)) return data.history;
  if (Array.isArray(data.messages)) return data.messages;
  if (Array.isArray(data.data)) return data.data;
  return [];
}

export interface AgentHistoryPage {
  messages: AgentHistoryMessage[];
  meta: {
    count: number;
    maxMessageId: number | null;
    lastMessageAt: string | null;
  };
  hasMore: boolean;
  oldestMessageId: number | null;
  page: number;
}

export async function loadAgentHistoryPage(
  token: string,
  sessionId: string,
  options: {
    page?: number;
    limit?: number;
    afterMessageId?: number;
    signal?: AbortSignal;
    timeoutMs?: number;
  } = {},
): Promise<AgentHistoryPage> {
  const page = Math.max(1, Math.floor(options.page ?? 1));
  const limit = Math.min(300, Math.max(1, Math.floor(options.limit ?? 100)));
  const query = new URLSearchParams({
    session_id: sessionId,
    page: String(page),
    limit: String(limit),
  });
  if (options.afterMessageId !== undefined)
    query.set("after_message_id", String(options.afterMessageId));
  const data = await requestAgentJson<
    AgentHistoryResponse | AgentHistoryMessage[]
  >(`/history?${query}`, token, options);
  const messages = normalizeAgentHistory(data);
  const rawMeta = Array.isArray(data) ? undefined : data.meta;
  const ids = messages
    .map((message) => message.message_id)
    .filter((id): id is number => typeof id === "number");
  const count = rawMeta?.count ?? messages.length;
  return {
    messages,
    meta: {
      count,
      maxMessageId:
        rawMeta?.max_message_id ?? (ids.length ? Math.max(...ids) : null),
      lastMessageAt:
        rawMeta?.last_message_at == null
          ? null
          : String(rawMeta.last_message_at),
    },
    hasMore:
      rawMeta?.count !== undefined
        ? page * limit < count
        : messages.length === limit,
    oldestMessageId: ids.length ? Math.min(...ids) : null,
    page,
  };
}

export async function loadAgentHistory(
  token: string,
  sessionId: string,
  options: {
    afterMessageId?: number;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<AgentHistoryMessage[]> {
  return (await loadAgentHistoryPage(token, sessionId, options)).messages;
}

export async function loadAgentHistoryDelta(
  token: string,
  sessionId: string,
  afterMessageId: number,
): Promise<AgentHistoryMessage[]> {
  return loadAgentHistory(token, sessionId, { afterMessageId, limit: 300 });
}

export async function deleteAgentSession(
  token: string,
  sessionId: string,
): Promise<unknown> {
  return requestAgentJson(`/v2/sessions/${sessionId}`, token, {
    method: "DELETE",
  });
}

export async function renameAgentSession(
  token: string,
  sessionId: string,
  title: string,
): Promise<AgentSessionSummary> {
  return requestAgentJson(`/sessions/${sessionId}`, token, {
    method: "PATCH",
    body: { title },
  });
}

export async function deleteAgentTurnsFromMessage(
  token: string,
  messageId: number,
): Promise<unknown> {
  return requestAgentJson(`/messages/${messageId}/turns-from`, token, {
    method: "DELETE",
  });
}

export async function deleteAgentMessage(
  token: string,
  messageId: number,
): Promise<unknown> {
  return requestAgentJson(`/messages/${messageId}`, token, {
    method: "DELETE",
  });
}

export async function deleteAgentResponseGroup(
  token: string,
  messageId: number,
): Promise<{ success: boolean; deleted_message_ids: number[]; deleted_scene_ids: string[] }> {
  return requestAgentJson(`/messages/${messageId}/response-group`, token, {
    method: "DELETE",
  });
}

export async function stopAgentSessionRuns(
  token: string,
  sessionId: string,
): Promise<unknown> {
  return requestAgentJson(`/v3/sessions/${sessionId}/runs/stop`, token, {
    method: "POST",
  });
}

export async function listAgentSessionRuns(
  token: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<AgentTurnRunSummary[]> {
  const data = await requestAgentJson<AgentTurnRunSummary[]>(
    `/v3/sessions/${sessionId}/runs`,
    token,
    { signal },
  );
  return Array.isArray(data) ? data : [];
}

export async function loadAgentRunEvents(
  token: string,
  runId: string,
  after = 0,
  signal?: AbortSignal,
): Promise<AgentTurnRunEventsResponse> {
  const query = new URLSearchParams({
    after: String(after),
    limit: "300",
  });
  return requestAgentJson<AgentTurnRunEventsResponse>(
    `/v3/runs/${runId}/events?${query.toString()}`,
    token,
    { signal },
  );
}

function normalizeList<T>(
  data: AgentListResponse<T>,
  keys: string[] = [],
): T[] {
  if (Array.isArray(data)) return data;
  for (const key of keys) {
    const value = (data as Record<string, unknown>)[String(key)];
    if (Array.isArray(value)) return value as T[];
  }
  if (Array.isArray(data.data)) return data.data;
  if (Array.isArray(data.items)) return data.items;
  return [];
}

export async function loadAgentSessionWorkspaces(
  token: string,
  sessionId: string,
): Promise<AgentWorkspaceSummary[]> {
  const data = await requestAgentJson<AgentListResponse<AgentWorkspaceSummary>>(
    `/v2/sessions/${sessionId}/workspaces`,
    token,
  );
  return normalizeList(data, ["workspaces"]);
}

export async function loadAgentWorkspaceBlackboard(
  token: string,
  workspaceId: number,
): Promise<AgentBlackboardEntry[]> {
  const data = await requestAgentJson<AgentListResponse<AgentBlackboardEntry>>(
    `/v2/workspaces/${workspaceId}/blackboard`,
    token,
  );
  return normalizeList(data, ["blackboard"]);
}

export async function loadAgentWorkspaceDiagramSummaries(
  token: string,
  workspaceId: number,
): Promise<AgentDiagramSummary[]> {
  const data = await requestAgentJson<AgentListResponse<AgentDiagramSummary>>(
    `/v2/workspaces/${workspaceId}/diagram-scenes`,
    token,
  );
  return normalizeList(data, ["diagrams", "scenes"]);
}

export async function loadAgentDiagramScene(
  token: string,
  workspaceId: number,
  sceneId: string,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return requestAgentJson<Record<string, unknown>>(
    `/v2/workspaces/${workspaceId}/diagram-scenes/${encodeURIComponent(sceneId)}`,
    token,
    { signal },
  );
}

export async function loadAgentDiagramFrame(
  token: string,
  workspaceId: number,
  sceneId: string,
  frameIndex: number,
  signal?: AbortSignal,
): Promise<Blob> {
  if (!token || !Number.isSafeInteger(workspaceId) || workspaceId <= 0 ||
      !sceneId || !Number.isSafeInteger(frameIndex) || frameIndex < 0) {
    throw new Error('图示帧地址无效');
  }
  const path = `/v2/workspaces/${workspaceId}/diagram-scenes/${encodeURIComponent(sceneId)}/frames/${frameIndex}.svg`;
  const deadline = requestDeadline(signal, 20_000);
  try {
    const response = await fetch(`${agentBaseUrl()}${path}`, {
      headers: buildAgentAuthHeaders(token),
      signal: deadline.signal,
      credentials: 'omit',
      redirect: 'error',
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
    });
    if (!response.ok) await throwAgentResponseError(response, path, token);
    if (response.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'image/svg+xml') {
      throw new Error('图示帧不是 SVG 图片');
    }
    const blob = await response.blob();
    if (blob.size > 2_000_000) throw new Error('图示帧超过大小限制');
    return blob;
  } catch (error) {
    throw deadline.error(error);
  } finally {
    deadline.dispose();
  }
}

export async function uploadAgentImage(
  token: string,
  file: File,
  signal?: AbortSignal,
): Promise<AgentUploadedImage> {
  const formData = new FormData();
  formData.append("image", file);
  const deadline = requestDeadline(signal);
  try {
    const response = await fetch(`${agentBaseUrl()}/media/images`, {
      method: "POST",
      headers: buildAgentAuthHeaders(token),
      body: formData,
      signal: deadline.signal,
    });
    if (!response.ok) await throwAgentResponseError(response, "/media/images", token);
    return (await response.json()) as AgentUploadedImage;
  } catch (error) {
    throw deadline.error(error);
  } finally {
    deadline.dispose();
  }
}

export async function streamAgentChat(
  token: string,
  body: AgentChatRequestBody,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const deadline = requestDeadline(signal);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await fetch(`${agentBaseUrl()}/v3/chat/stream`, {
      method: "POST",
      headers: buildAgentJsonHeaders(token),
      body: JSON.stringify(body),
      signal: deadline.signal,
    });
    if (!response.ok)
      await throwAgentResponseError(response, "/v3/chat/stream", token);
    reader = response.body?.getReader();
    if (!reader) throw new Error("响应体为空，无法读取流式返回内容");
    const decoder = new TextDecoder();
    let buffer = "";
    deadline.reset(120_000);
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      deadline.reset(120_000);
      buffer += decoder.decode(value, { stream: true });
      if (buffer.length > 2 * 1024 * 1024)
        throw new AgentApiError(
          "Agent 返回的数据过大，请重试。",
          413,
          "AGENT_STREAM_BUFFER_LIMIT",
        );
      const parsed = parseAgentSseBuffer(buffer);
      buffer = parsed.remainder;
      parsed.events.forEach(onEvent);
      if (parsed.events.some((event) => event.kind === "done")) {
        await reader.cancel();
        return;
      }
    }
    if (buffer.trim())
      parseAgentSseBuffer(`${buffer}\n\n`).events.forEach(onEvent);
  } catch (error) {
    throw deadline.error(error);
  } finally {
    deadline.dispose();
    if (reader) {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  }
}
