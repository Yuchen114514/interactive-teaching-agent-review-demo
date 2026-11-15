import { defineStore } from "pinia";
import { markRaw } from "vue";
import { ElMessage } from "element-plus";
import { AgentBackgroundRuns } from "../utils/agentBackgroundRuns";
import { captureAgentAccount } from "../utils/agentAccountScope";
import { hasLocalAgentRun } from "../utils/agentRunRegistry";
import { useAgentWorkspaceStore, type AgentUiMessage } from "./agentWorkspace";
import { useAgentWorkspaceBundle } from "../composables/useAgentWorkspaceBundle";
import { useAgentWorkspaceBootstrap } from "../composables/useAgentWorkspaceBootstrap";
import { useAgentRunRecovery } from "../composables/useAgentRunRecovery";
import {
  listAgentSessionRuns,
  loadAgentHistoryPage,
  stopAgentSessionRuns,
  streamAgentChat,
} from "../api/agentApi";
import {
  isAgentSessionCacheAvailable,
  writeCachedHistory,
} from "../utils/agentDataCache";
import { normalizeAgentMessageRole } from "../utils/agentMessageRole";
import {
  terminalAgentStatus,
  terminateAgentRecovery,
} from "../utils/agentRecoveryTermination";

export const useAgentRunsStore = defineStore("agentRuns", () => {
  const workspace = useAgentWorkspaceStore();
  const { loadBundle } = useAgentWorkspaceBundle();
  const { refreshSessions } = useAgentWorkspaceBootstrap();
  const { recoverRuns, startRunPolling, stopPolling } = useAgentRunRecovery();
  const reconciliations = new Map<string, number>();
  let reconciliationSequence = 0;
  const terminalSnapshots = new Map<string, AgentUiMessage[]>();
  const stopOperations = new Map<string, number>();
  let stopSequence = 0;

  function rememberTerminalFeedback(
    sessionId: string,
    localAssistantId?: string,
  ) {
    const current = workspace.messagesBySession[sessionId] || [];
    const candidates = current.filter(
      (message) =>
        message.terminalState &&
        message.messageId == null,
    );
    if (!candidates.length) return;
    const byId = new Map(
      (terminalSnapshots.get(sessionId) || []).map((message) => [
        message.id,
        message,
      ]),
    );
    candidates.forEach((message) =>
      byId.set(message.id, { ...message, pending: false }),
    );
    terminalSnapshots.delete(sessionId);
    terminalSnapshots.set(sessionId, [...byId.values()].slice(-8));
    while (terminalSnapshots.size > 24)
      terminalSnapshots.delete(terminalSnapshots.keys().next().value!);
  }

  async function reconcile(
    token: string,
    sessionId: string,
    localAssistantId?: string,
  ) {
    const scope = captureAgentAccount(token);
    if (
      !scope.isCurrent() ||
      hasLocalAgentRun(sessionId) ||
      !isAgentSessionCacheAvailable(token, sessionId)
    )
      return;
    rememberTerminalFeedback(sessionId, localAssistantId);
    const feedback = terminalSnapshots.get(sessionId) || [];
    const sequence = ++reconciliationSequence;
    reconciliations.set(sessionId, sequence);
    const ownsResult = () =>
      scope.isCurrent() &&
      reconciliations.get(sessionId) === sequence &&
      !hasLocalAgentRun(sessionId) &&
      isAgentSessionCacheAvailable(token, sessionId);
    try {
      const [page, serverRuns] = await Promise.all([
        loadAgentHistoryPage(token, sessionId),
        feedback.some((message) => message.runId)
          ? listAgentSessionRuns(token, sessionId).catch(() => [])
          : Promise.resolve([]),
      ]);
      if (!ownsResult()) return;
      const current = workspace.messagesBySession[sessionId] || [];
      const claimedLocalIds = new Set<string>();
      const history: AgentUiMessage[] = page.messages.map((message) => ({
        id:
          message.message_id != null
            ? `message-${message.message_id}`
            : `history-${sessionId}-${message.turn_index}-${message.role}`,
        messageId: message.message_id,
        role: normalizeAgentMessageRole(message.role),
        content: String(message.content || ""),
        images: message.images || [],
        turnIndex: message.turn_index,
        pending: false,
      }));
      for (const persisted of history) {
        const local = current.find((message) =>
          !claimedLocalIds.has(message.id) && message.role === persisted.role && (
            (message.messageId != null && message.messageId === persisted.messageId) ||
            (message.messageId == null && !message.terminalState && message.content === persisted.content)
          ),
        );
        if (!local) continue;
        claimedLocalIds.add(local.id);
        persisted.id = local.id;
        persisted.sourceAssistantId = local.sourceAssistantId;
        persisted.mainVisibleComplete = local.mainVisibleComplete;
        persisted.diagramStatus = local.diagramStatus;
        persisted.diagramSceneIds = local.diagramSceneIds;
      }
      // Streaming text can differ slightly from the stored answer. Preserve the
      // active bubble's identity when its turn is otherwise unambiguous.
      if (localAssistantId && !claimedLocalIds.has(localAssistantId)) {
        const local = current.find((message) => message.id === localAssistantId);
        const latestTurn = history[history.length - 1]?.turnIndex;
        const persisted = [...history].reverse().find((message) =>
          message.role === "assistant" && message.turnIndex === latestTurn &&
          !current.some((item) => item.messageId === message.messageId),
        );
        if (local && persisted && !local.terminalState) {
          persisted.id = local.id;
          claimedLocalIds.add(local.id);
        }
      }
      const persistedIds = new Set(
        history.map((message) => message.messageId).filter((id) => id != null),
      );
      const retained = feedback
        .filter((message) => {
          const serverRun = serverRuns.find(
            (run) => run.run_id === message.runId,
          );
          if (
            serverRun?.assistant_message_id != null &&
            persistedIds.has(serverRun.assistant_message_id)
          ) {
            const persisted = history.find(
              (item) => item.messageId === serverRun.assistant_message_id,
            )!;
            if (
              message.terminalState === "stopped" &&
              serverRun.status === "stopped"
            ) {
              persisted.terminalState = "stopped";
              persisted.content = `${persisted.content}\n\n本轮生成已停止。`;
            } else if (
              message.terminalState === "failed" &&
              serverRun.status === "failed"
            )
              persisted.terminalState = "failed";
            return false;
          }
          if (
            message.turnIndex != null &&
            history.some(
              (item) =>
                item.role === "assistant" &&
                item.turnIndex === message.turnIndex,
            )
          )
            return false;
          return true;
        })
        .map((message) => {
          const serverRun = serverRuns.find(
            (run) => run.run_id === message.runId,
          );
          return message.terminalState === "stopped" &&
            serverRun?.status === "stopped"
            ? {
                ...message,
                content: message.content.replace(
                  /已请求停止本轮生成。\s*$/,
                  "本轮生成已停止。",
                ),
              }
            : message;
        });
      const merged = [...history];
      for (const message of retained) {
        let turnIndex = message.turnIndex;
        if (turnIndex == null) {
          const position = current.findIndex((item) => item.id === message.id);
          for (let index = position - 1; index >= 0; index--) {
            const anchor = current[index];
            if (anchor.role !== "user") continue;
            turnIndex = history.find((item) => item.id === anchor.id ||
              (anchor.messageId != null && item.messageId === anchor.messageId))?.turnIndex;
            break;
          }
        }
        let lastSameOrEarlier = -1;
        if (turnIndex != null) {
          for (let index = merged.length - 1; index >= 0; index--) {
            const itemTurn = merged[index].turnIndex;
            if (itemTurn != null && itemTurn <= turnIndex) {
              lastSameOrEarlier = index;
              break;
            }
          }
        }
        merged.splice(lastSameOrEarlier < 0 ? merged.length : lastSameOrEarlier + 1, 0,
          turnIndex == null ? message : { ...message, turnIndex });
      }
      workspace.setMessages(sessionId, merged);
      workspace.setHistoryPageState(sessionId, {
        page: 1,
        hasMore: page.hasMore,
        total: page.meta.count,
      });
      terminalSnapshots.delete(sessionId);
      await writeCachedHistory(token, sessionId, page.messages);
      if (ownsResult())
        await loadBundle(token, sessionId, { background: true });
    } finally {
      if (reconciliations.get(sessionId) === sequence)
        reconciliations.delete(sessionId);
    }
  }

  function clearLocalPlaceholder(sessionId: string, localAssistantId?: string) {
    if (!localAssistantId) return;
    workspace.setMessages(
      sessionId,
      (workspace.messagesBySession[sessionId] || []).filter(
        (message) => message.id !== localAssistantId,
      ),
    );
  }

  function clearTerminalTracking(sessionId: string, localAssistantId?: string) {
    stopOperations.delete(sessionId);
    reconciliations.delete(sessionId);
    terminalSnapshots.delete(sessionId);
    if (localAssistantId) clearLocalPlaceholder(sessionId, localAssistantId);
  }

  const runtime = markRaw(
    new AgentBackgroundRuns({
      workspace,
      stream: streamAgentChat,
      onRejected: (message) => ElMessage.warning(message),
      async onSettled(token, sessionId, redirected, stopped, localAssistantId) {
        const scope = captureAgentAccount(token);
        if (!scope.isCurrent()) return;
        if (
          sessionId &&
          !redirected &&
          !stopped &&
          !hasLocalAgentRun(sessionId)
        ) {
          rememberTerminalFeedback(sessionId, localAssistantId);
          try {
            const running = await recoverRuns(token, sessionId);
            if (!scope.isCurrent()) return;
            if (running > 0) {
              clearLocalPlaceholder(sessionId, localAssistantId);
              startRunPolling(token, sessionId, {
                onSettled: () => reconcile(token, sessionId, localAssistantId),
                onTerminalError: () =>
                  clearTerminalTracking(sessionId, localAssistantId),
              });
            } else await reconcile(token, sessionId, localAssistantId);
          } catch (error) {
            if (!scope.isCurrent()) return;
            if (terminalAgentStatus(error)) {
              stopPolling(sessionId);
              clearTerminalTracking(sessionId, localAssistantId);
              terminateAgentRecovery(
                workspace,
                token,
                sessionId,
                error,
                (message) => ElMessage.warning(message),
                scope.isCurrent,
              );
              return;
            }
            if (scope.isCurrent()) {
              workspace.setRunCount(
                sessionId,
                Math.max(1, workspace.runningCountsBySession[sessionId] || 0),
              );
              startRunPolling(token, sessionId, {
                onSettled: () => reconcile(token, sessionId, localAssistantId),
                onTerminalError: () =>
                  clearTerminalTracking(sessionId, localAssistantId),
              });
            }
          }
        }
        if (scope.isCurrent())
          await refreshSessions(token, { background: true }).catch(
            () => undefined,
          );
        if (
          scope.isCurrent() &&
          sessionId &&
          stopped &&
          stopOperations.has(sessionId)
        ) {
          workspace.setRunCount(
            sessionId,
            Math.max(1, workspace.runningCountsBySession[sessionId] || 0),
          );
        }
      },
    }),
  );

  function reset() {
    runtime.reset();
    stopPolling();
    reconciliations.clear();
    terminalSnapshots.clear();
    stopOperations.clear();
  }

  async function stop(sessionId: string | null, token: string) {
    const scope = captureAgentAccount(token);
    if (!scope.isCurrent()) return;
    const sequence = ++stopSequence;
    if (sessionId) stopOperations.set(sessionId, sequence);
    const ownsStop = () =>
      scope.isCurrent() &&
      !!sessionId &&
      stopOperations.get(sessionId) === sequence;
    // Cancel both queued and in-flight recovery before the stop request can race it.
    if (sessionId) stopPolling(sessionId);
    if (sessionId) reconciliations.delete(sessionId);
    runtime.stop(sessionId);
    if (!sessionId) return;
    rememberTerminalFeedback(sessionId);
    const resume = () => {
      workspace.setRunCount(
        sessionId,
        Math.max(1, workspace.runningCountsBySession[sessionId] || 0),
      );
      startRunPolling(token, sessionId, {
        onSettled: async () => {
          if (!ownsStop()) return;
          await reconcile(token, sessionId);
          if (ownsStop()) {
            stopOperations.delete(sessionId);
            workspace.setRunCount(sessionId, 0);
          }
        },
        onTerminalError: () => clearTerminalTracking(sessionId),
      });
    };
    try {
      await stopAgentSessionRuns(token, sessionId);
      if (ownsStop()) resume();
    } catch (error) {
      if (!scope.isCurrent()) throw error;
      if (terminalAgentStatus(error)) {
        clearTerminalTracking(sessionId);
        terminateAgentRecovery(
          workspace,
          token,
          sessionId,
          error,
          (message) => ElMessage.warning(message),
          scope.isCurrent,
        );
        throw error;
      }
      if (ownsStop()) {
        terminalSnapshots.delete(sessionId);
        const messages = workspace.messagesBySession[sessionId] || [];
        workspace.setMessages(
          sessionId,
          messages.map((message) =>
            message.terminalState === "stopped"
              ? {
                  ...message,
                  terminalState: undefined,
                  content: message.content.replace(
                    /(?:已请求停止本轮生成。|本轮生成已停止。)\s*$/,
                    "停止尚未确认，正在核对后台任务。",
                  ),
                }
              : message,
          ),
        );
        resume();
      }
      throw error;
    }
  }
  return { start: runtime.start.bind(runtime), stop, reset };
});
