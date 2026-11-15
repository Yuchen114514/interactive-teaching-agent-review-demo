import { ElMessage } from "element-plus";
import { nextTick } from "vue";
import {
  listAgentSessionGroups,
  listAgentSessions,
  loadAgentHistoryPage,
  loadAgentSessionWorkspaces,
} from "../api/agentApi";
import type { AgentHistoryMessage, AgentSessionSummary } from "../api/types";
import { normalizeAgentMessageRole } from "../utils/agentMessageRole";
import { useAgentRunRecovery } from "./useAgentRunRecovery";
import { useAgentWorkspaceBundle } from "./useAgentWorkspaceBundle";
import {
  useAgentWorkspaceStore,
  type AgentUiMessage,
} from "../stores/agentWorkspace";
import {
  isAgentSessionCacheAvailable,
  readCachedHistory,
  readCachedSessionWorkspaces,
  readCachedSessions,
  writeCachedHistory,
  writeCachedSessionWorkspaces,
  writeCachedSessions,
} from "../utils/agentDataCache";
import { sortSessionsByRecentActivity } from "../utils/sessionActivity";
import { captureAgentAccount } from "../utils/agentAccountScope";
import { hasLocalAgentRun } from "../utils/agentRunRegistry";
import {
  terminalAgentStatus,
  terminateAgentRecovery,
} from "../utils/agentRecoveryTermination";

const IMMEDIATE_WORKSPACE_PREFETCH_LIMIT = 5;
const DEFERRED_WORKSPACE_PREFETCH_BATCH_SIZE = 4;
const WORKSPACE_PREFETCH_LIMIT = 20;
let sessionSwitchSequence = 0;
let sessionsRefreshSequence = 0;
let prefetchSequence = 0;
let bootstrapTask: {
  token: string;
  isCurrent: () => boolean;
  promise: Promise<void>;
} | null = null;
const idleStops = new Set<() => void>();

function cancelPrefetch() {
  prefetchSequence += 1;
  idleStops.forEach((stop) => stop());
  idleStops.clear();
}
export function cancelAgentWorkspaceBootstrap() {
  sessionSwitchSequence += 1;
  sessionsRefreshSequence += 1;
  cancelPrefetch();
  bootstrapTask = null;
}

function normalizeHistoryMessage(message: AgentHistoryMessage): AgentUiMessage {
  const role = normalizeAgentMessageRole(message.role);
  return {
    id: message.message_id
      ? `message-${message.message_id}`
      : `${role}-${message.turn_index ?? Date.now()}`,
    messageId: message.message_id,
    role,
    content: String(message.content || ""),
    turnIndex: message.turn_index,
    images: message.images || [],
    pending: false,
  };
}
async function loadSessionsWithProblemTitles(
  token: string,
): Promise<AgentSessionSummary[]> {
  const [sessions, groups] = await Promise.all([
    listAgentSessions(token),
    listAgentSessionGroups(token).catch(() => null),
  ]);
  if (!groups) return sessions;
  const titles = new Map(
    groups.problem_groups.map((group) => [
      group.problem_id,
      group.problem_title.trim(),
    ]),
  );
  return sessions.map((session) =>
    session.problem_title?.trim() || session.problem_id == null
      ? session
      : {
          ...session,
          problem_title:
            titles.get(session.problem_id) || session.problem_title,
        },
  );
}
function runWhenIdle(callback: () => void) {
  if (typeof window === "undefined") return;
  let stop: () => void;
  const run = () => {
    idleStops.delete(stop);
    callback();
  };
  if (window.requestIdleCallback) {
    const id = window.requestIdleCallback(run, { timeout: 1200 });
    stop = () => window.cancelIdleCallback?.(id);
  } else {
    const id = window.setTimeout(run, 180);
    stop = () => window.clearTimeout(id);
  }
  idleStops.add(stop);
}

export function useAgentWorkspaceBootstrap() {
  const workspace = useAgentWorkspaceStore();
  const { loadBundle } = useAgentWorkspaceBundle();
  const { recoverRuns, startRunPolling, stopPolling } = useAgentRunRecovery();

  async function restoreCachedSession(
    token: string,
    sessionId: string,
    isCurrent: () => boolean,
  ) {
    const [history, workspaces] = await Promise.all([
      readCachedHistory(token, sessionId),
      readCachedSessionWorkspaces(token, sessionId),
    ]);
    if (!isCurrent()) return;
    if (
      history?.data?.length &&
      !workspace.messagesBySession[sessionId]?.length &&
      !hasLocalAgentRun(sessionId)
    )
      workspace.setMessages(
        sessionId,
        history.data.map(normalizeHistoryMessage),
      );
    if (workspaces?.data)
      workspace.setSessionWorkspaces(sessionId, workspaces.data);
  }

  async function ensureSessionWorkspaces(token: string, sessionId: string) {
    const account = captureAgentAccount(token);
    if (
      !account.isCurrent() ||
      !isAgentSessionCacheAvailable(token, sessionId) ||
      workspace.workspacesBySession[sessionId] ||
      workspace.workspaceSessionLoading[sessionId]
    )
      return;
    workspace.setSessionWorkspaceLoading(sessionId, true);
    try {
      const cached = await readCachedSessionWorkspaces(token, sessionId);
      if (!account.isCurrent()) return;
      if (cached?.data) {
        workspace.setSessionWorkspaces(sessionId, cached.data);
        return;
      }
      const workspaces = await loadAgentSessionWorkspaces(token, sessionId);
      if (!account.isCurrent()) return;
      await writeCachedSessionWorkspaces(token, sessionId, workspaces);
      if (account.isCurrent() && isAgentSessionCacheAvailable(token, sessionId))
        workspace.setSessionWorkspaces(sessionId, workspaces);
    } catch {
      /* A failed prefetch remains retryable. */
    } finally {
      if (account.isCurrent())
        workspace.setSessionWorkspaceLoading(sessionId, false);
    }
  }

  async function preloadVisibleSessionWorkspaces(
    token: string,
    sessions: AgentSessionSummary[],
  ) {
    cancelPrefetch();
    const sequence = prefetchSequence;
    const account = captureAgentAccount(token);
    const pending = sessions.slice(0, WORKSPACE_PREFETCH_LIMIT);
    const valid = () => account.isCurrent() && sequence === prefetchSequence;
    await Promise.allSettled(
      pending
        .slice(0, IMMEDIATE_WORKSPACE_PREFETCH_LIMIT)
        .map((session) => ensureSessionWorkspaces(token, session.session_id)),
    );
    let index = IMMEDIATE_WORKSPACE_PREFETCH_LIMIT;
    const loadNextBatch = () => {
      if (!valid()) return;
      const batch = pending.slice(
        index,
        index + DEFERRED_WORKSPACE_PREFETCH_BATCH_SIZE,
      );
      index += batch.length;
      void Promise.allSettled(
        batch.map((session) =>
          ensureSessionWorkspaces(token, session.session_id),
        ),
      ).finally(() => {
        if (valid() && index < pending.length) runWhenIdle(loadNextBatch);
      });
    };
    if (valid() && index < pending.length) runWhenIdle(loadNextBatch);
  }

  async function recoverSessionRuns(
    token: string,
    sessionId: string,
    sequence: number,
  ) {
    const account = captureAgentAccount(token);
    if (!account.isCurrent() || hasLocalAgentRun(sessionId)) return;
    const options = {
      onSettled: async () => {
        if (!account.isCurrent() || hasLocalAgentRun(sessionId)) return;
        const [page] = await Promise.all([
          loadAgentHistoryPage(token, sessionId),
          loadBundle(token, sessionId),
        ]);
        if (!account.isCurrent() || hasLocalAgentRun(sessionId)) return;
        workspace.setMessages(
          sessionId,
          page.messages.map(normalizeHistoryMessage),
        );
        workspace.setHistoryPageState(sessionId, {
          page: page.page,
          hasMore: page.hasMore,
          total: page.meta.count,
        });
        await writeCachedHistory(token, sessionId, page.messages);
        if (workspace.currentSessionId === sessionId)
          workspace.requestScrollToBottom();
      },
    };
    try {
      const running = await recoverRuns(token, sessionId);
      if (
        !account.isCurrent() ||
        sequence !== sessionSwitchSequence ||
        hasLocalAgentRun(sessionId)
      )
        return;
      if (running > 0) startRunPolling(token, sessionId, options);
    } catch (error) {
      if (!account.isCurrent()) return;
      if (terminalAgentStatus(error)) {
        stopPolling(sessionId);
        terminateAgentRecovery(
          workspace,
          token,
          sessionId,
          error,
          (message) => ElMessage.warning(message),
          account.isCurrent,
        );
        return;
      }
      // Unknown network state is retried; it is not treated as a completed run.
      if (account.isCurrent()) startRunPolling(token, sessionId, options);
    }
  }

  async function refreshSessions(
    token: string,
    options: { background?: boolean } = {},
  ): Promise<AgentSessionSummary[]> {
    const account = captureAgentAccount(token);
    const sequence = ++sessionsRefreshSequence;
    const ownsRefresh = () =>
      account.isCurrent() && sequence === sessionsRefreshSequence;
    if (!ownsRefresh()) return [];
    const showLoading = !options.background || workspace.sessions.length === 0;
    workspace.setSessionsLoading(showLoading);
    try {
      const sessions = sortSessionsByRecentActivity(
        await loadSessionsWithProblemTitles(token),
      );
      if (!ownsRefresh()) return [];
      workspace.setSessions(sessions);
      await writeCachedSessions(token, sessions);
      if (ownsRefresh()) void preloadVisibleSessionWorkspaces(token, sessions);
      return sessions;
    } catch (error) {
      if (ownsRefresh())
        ElMessage.warning(
          `加载会话失败：${error instanceof Error ? error.message : "未知错误"}`,
        );
      return [];
    } finally {
      if (showLoading && ownsRefresh()) workspace.setSessionsLoading(false);
    }
  }

  async function switchSession(token: string, sessionId: string) {
    const account = captureAgentAccount(token);
    const sequence = ++sessionSwitchSequence;
    const isCurrent = () =>
      account.isCurrent() &&
      sequence === sessionSwitchSequence &&
      isAgentSessionCacheAvailable(token, sessionId);
    if (!isCurrent()) return;
    await restoreCachedSession(token, sessionId, isCurrent);
    if (!isCurrent()) return;
    const hasCachedMessages = Boolean(
      workspace.messagesBySession[sessionId]?.length,
    );
    workspace.setHistoryLoading(!hasCachedMessages);
    workspace.setCurrentSession(sessionId);
    workspace.clearDraftImages();
    void loadBundle(token, sessionId, { background: hasCachedMessages }).catch(
      () => undefined,
    );
    try {
      // Reconcile the entire current page: append-only watermarks cannot detect deletions.
      const page = await loadAgentHistoryPage(token, sessionId);
      if (!isCurrent()) return;
      if (!hasLocalAgentRun(sessionId)) {
        workspace.setMessages(
          sessionId,
          page.messages.map(normalizeHistoryMessage),
        );
        workspace.setHistoryPageState(sessionId, {
          page: page.page,
          hasMore: page.hasMore,
          total: page.meta.count,
        });
        await writeCachedHistory(token, sessionId, page.messages);
      }
      if (!isCurrent()) return;
      workspace.setHistoryLoading(false);
      await nextTick();
      if (isCurrent()) workspace.requestScrollToBottom();
      void recoverSessionRuns(token, sessionId, sequence);
      return page;
    } catch (error) {
      if (isCurrent())
        ElMessage.warning(
          `加载历史失败：${error instanceof Error ? error.message : "未知错误"}`,
        );
    } finally {
      if (isCurrent()) workspace.setHistoryLoading(false);
    }
  }

  async function bootstrapAgentWorkspace(token: string) {
    const account = captureAgentAccount(token);
    if (!account.isCurrent()) return;
    if (bootstrapTask?.token === token && bootstrapTask.isCurrent())
      return bootstrapTask.promise;
    const task = {
      token,
      isCurrent: account.isCurrent,
      promise: Promise.resolve(),
    };
    task.promise = (async () => {
      const cached = await readCachedSessions(token);
      if (!account.isCurrent()) return;
      if (cached?.data?.length)
        workspace.setSessions(sortSessionsByRecentActivity(cached.data));
      const cachedId =
        workspace.currentSessionId || cached?.data?.[0]?.session_id;
      if (cachedId)
        await restoreCachedSession(token, cachedId, account.isCurrent);
      const sessions = await refreshSessions(token, {
        background: Boolean(cached?.data?.length),
      });
      if (!account.isCurrent()) return;
      const current = workspace.currentSessionId;
      const availableSessions = workspace.sessions;
      const firstSessionId = availableSessions.some(
        (session) => session.session_id === current,
      )
        ? current
        : availableSessions[0]?.session_id;
      if (firstSessionId) await switchSession(token, firstSessionId);
      else {
        workspace.setCurrentSession(null);
        workspace.setMessages(null, []);
        await loadBundle(token, null);
      }
    })().finally(() => {
      if (bootstrapTask === task) bootstrapTask = null;
    });
    bootstrapTask = task;
    return task.promise;
  }

  function newSession(token: string) {
    if (!captureAgentAccount(token).isCurrent() || hasLocalAgentRun(null))
      return;
    sessionSwitchSequence += 1;
    workspace.setCurrentSession(null);
    workspace.setHistoryLoading(false);
    workspace.setMessages(null, []);
    workspace.clearDraftImages();
    void loadBundle(token, null);
  }
  return {
    bootstrapAgentWorkspace,
    ensureSessionWorkspaces,
    newSession,
    refreshSessions,
    switchSession,
  };
}
