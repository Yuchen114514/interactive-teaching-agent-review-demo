import { loadAgentRunEvents, listAgentSessionRuns } from "../api/agentApi";
import type { AgentTurnRunSummary } from "../api/types";
import {
  useAgentWorkspaceStore,
  type AgentUiMessage,
} from "../stores/agentWorkspace";
import { captureAgentAccount } from "../utils/agentAccountScope";
import { hasLocalAgentRun } from "../utils/agentRunRegistry";
import { ElMessage } from "element-plus";
import {
  terminalAgentStatus,
  terminateAgentRecovery,
} from "../utils/agentRecoveryTermination";

const pollingStops = new Map<string, () => void>();
interface ReplayState {
  cursor: number;
  content: string;
  visibleResponse?: string;
  diagramStatus?: 'running' | 'ready' | 'failed';
  diagramSceneIds: string[];
  sessionId: string;
  lastUsed: number;
  isCurrent: () => boolean;
}
const replayStates = new Map<string, ReplayState>();
const MAX_RECOVERY_TEXT = 200_000;

function recoveryMessage(
  run: AgentTurnRunSummary,
  replay: ReplayState,
): AgentUiMessage {
  return {
    id: `run-recovery-${run.run_id}`,
    runId: run.run_id,
    role: "assistant",
    content:
      replay.visibleResponse ||
      replay.content ||
      run.response_preview ||
      run.error_message ||
      "",
    pending: run.status === "running" || run.status === "cancel_requested",
    turnIndex: run.turn_index,
    messageId: run.assistant_message_id ?? undefined,
    mainVisibleComplete: Boolean(replay.visibleResponse),
    diagramStatus: replay.diagramStatus,
    diagramSceneIds: replay.diagramSceneIds,
    images: [],
  };
}

export function useAgentRunRecovery() {
  const workspace = useAgentWorkspaceStore();

  async function recoverRuns(
    token: string,
    sessionId: string,
    options: { signal?: AbortSignal; isCurrent?: () => boolean } = {},
  ): Promise<number> {
    const account = captureAgentAccount(token);
    const valid = () =>
      account.isCurrent() &&
      !options.signal?.aborted &&
      (!options.isCurrent || options.isCurrent());
    const check = () => {
      if (!valid())
        throw new DOMException("Stale Agent recovery", "AbortError");
    };
    check();
    if (hasLocalAgentRun(sessionId))
      return workspace.runningCountsBySession[sessionId] || 1;
    const runs = await listAgentSessionRuns(token, sessionId, options.signal);
    check();
    if (hasLocalAgentRun(sessionId))
      return workspace.runningCountsBySession[sessionId] || 1;
    const runningRuns = runs.filter(
      (run) => run.status === "running" || run.status === "cancel_requested",
    );
    workspace.setRunCount(sessionId, runningRuns.length);
    if (!runningRuns.length) {
      for (const [id, state] of replayStates)
        if (state.sessionId === sessionId) replayStates.delete(id);
      return 0;
    }
    const recoveredMessages: AgentUiMessage[] = [];
    for (const run of runningRuns) {
      let state = replayStates.get(run.run_id);
      if (!state || !state.isCurrent()) {
        state = {
          cursor: 0,
          content: "",
          diagramSceneIds: [],
          sessionId,
          lastUsed: Date.now(),
          isCurrent: account.isCurrent,
        };
        replayStates.set(run.run_id, state);
      }
      state.lastUsed = Date.now();
      // Bound initial replay work per poll, retaining the cursor for subsequent batches.
      for (let page = 0; page < 10; page += 1) {
        const replay = await loadAgentRunEvents(
          token,
          run.run_id,
          state.cursor,
          options.signal,
        );
        check();
        if (hasLocalAgentRun(sessionId))
          return workspace.runningCountsBySession[sessionId] || 1;
        const events = replay.events || [];
        const previousCursor = state.cursor;
        for (const event of events) {
          if (event.event_index <= state.cursor) continue;
          state.cursor = event.event_index;
          if (
            event.type === "main_delta" &&
            typeof event.payload?.delta === "string"
          )
            state.content = (state.content + event.payload.delta).slice(
              0,
              MAX_RECOVERY_TEXT,
            );
          if (
            event.type === "main_block.visible_complete" &&
            typeof event.payload?.response === "string"
          )
            state.visibleResponse = event.payload.response.slice(
              0,
              MAX_RECOVERY_TEXT,
            );
          if (event.type === "diagram_status") {
            if (event.payload?.status === "running") state.diagramStatus = "running";
            if (event.payload?.status === "failed") state.diagramStatus = "failed";
            if (event.payload?.status === "skipped") state.diagramStatus = undefined;
          }
          if (event.type === "diagram_ready") {
            state.diagramStatus = "ready";
            const scene = event.payload?.scene;
            const id = typeof event.payload?.diagram_scene_id === "string" ? event.payload.diagram_scene_id :
              scene && typeof scene === "object" && typeof (scene as Record<string, unknown>).scene_id === "string" ?
                (scene as Record<string, unknown>).scene_id as string : "";
            if (id && !state.diagramSceneIds.includes(id)) state.diagramSceneIds.push(id);
          }
        }
        if (events.length < 300 || state.cursor === previousCursor) break;
      }
      recoveredMessages.push(recoveryMessage(run, state));
    }
    check();
    if (!hasLocalAgentRun(sessionId)) {
      const recoveredRunIds = new Set(runningRuns.map((run) => run.run_id));
      const recoveredMessageIds = new Set(
        runningRuns
          .map((run) => run.assistant_message_id)
          .filter((id) => id != null),
      );
      const existing = (workspace.messagesBySession[sessionId] ?? []).filter(
        (message) =>
          !message.id.startsWith("run-recovery-") &&
          (!message.runId || !recoveredRunIds.has(message.runId)) &&
          (message.messageId == null ||
            !recoveredMessageIds.has(message.messageId)),
      );
      workspace.setMessages(sessionId, [...existing, ...recoveredMessages]);
    }
    const oldest = [...replayStates.entries()].sort(
      (a, b) => a[1].lastUsed - b[1].lastUsed,
    );
    for (const [id, state] of oldest)
      if (replayStates.size > 32 || Date.now() - state.lastUsed > 10 * 60_000)
        replayStates.delete(id);
    return runningRuns.length;
  }

  function stopPolling(sessionId?: string | null) {
    if (sessionId) {
      pollingStops.get(sessionId)?.();
      pollingStops.delete(sessionId);
      for (const [id, state] of replayStates)
        if (state.sessionId === sessionId) replayStates.delete(id);
      return;
    }
    pollingStops.forEach((stop) => stop());
    pollingStops.clear();
    replayStates.clear();
  }

  function startRunPolling(
    token: string,
    sessionId: string,
    options: {
      onSettled?: () => void | Promise<void>;
      onTerminalError?: (error: unknown) => void;
    } = {},
  ) {
    pollingStops.get(sessionId)?.();
    pollingStops.delete(sessionId);
    const account = captureAgentAccount(token);
    const controller = new AbortController();
    let stopped = false;
    let timer: number | undefined;
    let failures = 0;
    const valid = () => !stopped && account.isCurrent();
    const stop = () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
    const schedule = () => {
      if (!valid()) {
        stop();
        return;
      }
      timer = window.setTimeout(
        () => {
          void tick();
        },
        Math.min(30_000, 2200 * 2 ** Math.min(failures, 4)),
      );
    };
    const tick = async () => {
      if (!valid()) return;
      try {
        const running = await recoverRuns(token, sessionId, {
          signal: controller.signal,
          isCurrent: valid,
        });
        if (!valid()) return;
        failures = 0;
        if (running > 0) {
          schedule();
          return;
        }
        await options.onSettled?.();
        if (!valid()) return;
        stop();
        if (pollingStops.get(sessionId) === stop)
          pollingStops.delete(sessionId);
      } catch (error) {
        if (!valid()) return;
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
          options.onTerminalError?.(error);
          return;
        }
        failures += 1;
        schedule();
      }
    };
    pollingStops.set(sessionId, stop);
    void tick();
  }
  return { recoverRuns, startRunPolling, stopPolling };
}
