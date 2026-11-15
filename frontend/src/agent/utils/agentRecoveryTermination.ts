import type { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { removeCachedSession } from "./agentDataCache";

type Workspace = ReturnType<typeof useAgentWorkspaceStore>;

export function terminalAgentStatus(error: unknown): 401 | 403 | 404 | null {
  const status =
    error && typeof error === "object" && "status" in error
      ? (error as { status?: unknown }).status
      : undefined;
  return status === 401 || status === 403 || status === 404 ? status : null;
}

/** Authentication/missing-session errors are terminal, never a running task. */
export function terminateAgentRecovery(
  workspace: Workspace,
  token: string,
  sessionId: string,
  error: unknown,
  notify: (message: string) => void,
  isCurrent: () => boolean,
) {
  const status = terminalAgentStatus(error);
  if (!status) return false;
  if (!isCurrent()) return true;
  workspace.setRunCount(sessionId, 0);
  workspace.setSessionChecking?.(sessionId, false);
  if (workspace.messagesBySession[sessionId])
    workspace.setMessages(
      sessionId,
      workspace.messagesBySession[sessionId].filter(
        (message) => !message.id.startsWith("run-recovery-"),
      ),
    );
  if (status === 403 || status === 404) {
    // Mark deletion before any awaited persistence can allow a stale restore.
    void removeCachedSession(token, sessionId);
    const selected = workspace.currentSessionId === sessionId;
    workspace.removeSession?.(sessionId);
    if (selected) {
      workspace.setHistoryLoading(false);
      workspace.setWorkspaceLoading(false);
      workspace.setWorkspaceBundle({
        workspaces: [],
        blackboard: [],
        diagrams: [],
        selectedWorkspaceId: null,
      });
    }
  }
  notify(
    status === 401
      ? "登录状态已失效，请重新登录后使用 Agent。"
      : status === 403
        ? "当前账号无权访问这条会话，请切换其他会话。"
        : "会话不存在或已被删除，请切换其他会话。",
  );
  return true;
}
