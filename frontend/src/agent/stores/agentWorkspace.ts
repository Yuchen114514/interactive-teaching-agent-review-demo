import { defineStore } from "pinia";
import type {
  AgentBlackboardEntry,
  AgentDiagramSummary,
  AgentMessageImage,
  AgentSessionSummary,
  AgentWorkspaceSummary,
} from "../api/types";
import { filterDeletedMessage, filterDeletedMessages } from "../utils/messageDeletion";
import { consumeScrollIntent, requestScrollIntent } from "../utils/scrollIntent";
import { sameStableValue } from "../utils/workspaceStableUpdate";

export type GuidanceDepth = "socratic" | "coached" | "minimal" | "direct";

export interface AgentUiMessage {
  id: string;
  runId?: string;
  terminalState?: 'failed' | 'stopped';
  messageId?: number;
  role: "user" | "assistant" | "system";
  content: string;
  pending?: boolean;
  mainVisibleComplete?: boolean;
  diagramStatus?: 'running' | 'ready' | 'failed';
  diagramSceneIds?: string[];
  sourceAssistantId?: string;
  turnIndex?: number;
  images?: AgentMessageImage[];
}

export interface AgentDraftImage {
  image_id: string;
  url: string;
  thumbnail_url?: string;
  filename?: string;
  previewUrl?: string;
  size?: number;
}

function sessionKey(sessionId: string | null | undefined) {
  return sessionId?.trim() || "draft";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function diagramId(diagram: AgentDiagramSummary) {
  return diagram.scene_id || diagram.diagram_scene_id || "";
}

function diagramSummaryFromPayload(payload: Record<string, unknown>, selectedWorkspaceId: number | null): AgentDiagramSummary | null {
  const scene = asRecord(payload.scene);
  const source = scene || asRecord(payload.diagram) || asRecord(payload.summary) || payload;
  const sceneId = asString(source.scene_id) || asString(source.diagram_scene_id);
  if (!sceneId) return null;
  return {
    scene_id: sceneId,
    diagram_scene_id: sceneId,
    workspace_id: asNumber(source.workspace_id) || selectedWorkspaceId || undefined,
    title: asString(source.title) || asString(source.teaching_goal) || asString(payload.diagram_goal) || "生成图示",
    summary: asString(source.summary) || asString(source.teaching_goal) || asString(payload.diagram_goal),
    diagram_kind: asString(source.diagram_kind) || asString(source.layout) || "diagram",
    status: asString(source.status) || "ready",
    step_key: asString(source.default_step_id) || asString(source.step_key),
    source_turn_index: asNumber(source.source_turn_index) ?? asNumber(payload.source_turn_index),
    created_at: source.created_at as string | number | null | undefined,
  };
}

export const useAgentWorkspaceStore = defineStore("agentWorkspace", {
  state: () => ({
    sessionViewGeneration: 0,
    sessionAccessOrder: [] as string[],
    currentSessionId: null as string | null,
    sessions: [] as AgentSessionSummary[],
    sessionsLoading: false,
    historyLoading: false,
    messagesBySession: {} as Record<string, AgentUiMessage[]>,
    historyPagesBySession: {} as Record<string, { page: number; hasMore: boolean; total: number }>,
    runningCountsBySession: {} as Record<string, number>,
    checkingRunsBySession: {} as Record<string, boolean>,
    workspaces: [] as AgentWorkspaceSummary[],
    workspacesBySession: {} as Record<string, AgentWorkspaceSummary[]>,
    workspaceSessionLoading: {} as Record<string, boolean>,
    workspaceLoading: false,
    selectedWorkspaceId: null as number | null,
    blackboard: [] as AgentBlackboardEntry[],
    diagrams: [] as AgentDiagramSummary[],
    diagramScenes: {} as Record<string, Record<string, unknown>>,
    draftImages: [] as AgentDraftImage[],
    composerDraft: "",
    problemRedirect: null as { problemId: number | null; problemTitle: string; message: string; actionLabel: string } | null,
    requestedProblemFolderId: null as number | null,
    guidanceDepth: "socratic" as GuidanceDepth,
    scrollToBottomRequest: 0,
  }),
  getters: {
    currentMessages(state): AgentUiMessage[] {
      return state.messagesBySession[sessionKey(state.currentSessionId)] ?? [];
    },
    currentSessionRunning(state): boolean {
      return (state.runningCountsBySession[sessionKey(state.currentSessionId)] ?? 0) > 0 || Boolean(state.checkingRunsBySession[sessionKey(state.currentSessionId)]);
    },
    selectedWorkspace(state): AgentWorkspaceSummary | null {
      return state.workspaces.find((workspace) => workspace.workspace_id === state.selectedWorkspaceId) ?? state.workspaces[0] ?? null;
    },
  },
  actions: {
    retainSession(sessionId: string | null | undefined) {
      const key = sessionKey(sessionId);
      this.sessionAccessOrder = [...this.sessionAccessOrder.filter(id => id !== key), key];
      const candidates = [...this.sessionAccessOrder];
      for (const id of candidates) {
        if (this.sessionAccessOrder.length <= 24) break;
        if (id === sessionKey(this.currentSessionId) || this.runningCountsBySession[id] || this.checkingRunsBySession[id]) continue;
        delete this.messagesBySession[id];
        delete this.workspacesBySession[id];
        delete this.workspaceSessionLoading[id];
        delete this.historyPagesBySession[id];
        this.sessionAccessOrder = this.sessionAccessOrder.filter(value => value !== id);
      }
    },
    setSessions(sessions: AgentSessionSummary[]) {
      this.sessions = sessions;
    },
    upsertSession(session: AgentSessionSummary) {
      const existing = this.sessions.find((item) => item.session_id === session.session_id);
      if (!existing) {
        this.sessions = [session, ...this.sessions];
        return;
      }
      this.sessions = this.sessions.map((item) => (item.session_id === session.session_id ? { ...item, ...session } : item));
    },
    touchSessionActivity(sessionId: string, patch: Partial<AgentSessionSummary> = {}) {
      const now = new Date().toISOString();
      const current = this.sessions.find((session) => session.session_id === sessionId);
      this.upsertSession({
        ...(current ?? {}),
        ...patch,
        session_id: sessionId,
        created_at: current?.created_at ?? patch.created_at ?? now,
        updated_at: patch.updated_at ?? now,
        last_message_at: patch.last_message_at ?? patch.updated_at ?? now,
      });
    },
    removeSession(sessionId: string) {
      this.sessions = this.sessions.filter((session) => session.session_id !== sessionId);
      delete this.messagesBySession[sessionId];
      delete this.historyPagesBySession[sessionId];
      delete this.runningCountsBySession[sessionId];
      delete this.checkingRunsBySession[sessionId];
      delete this.workspacesBySession[sessionId];
      delete this.workspaceSessionLoading[sessionId];
      this.sessionAccessOrder = this.sessionAccessOrder.filter(id => id !== sessionId);
      if (this.currentSessionId === sessionId) {
        this.currentSessionId = null;
      }
    },
    setSessionsLoading(loading: boolean) {
      this.sessionsLoading = loading;
    },
    setHistoryLoading(loading: boolean) {
      this.historyLoading = loading;
    },
    setHistoryPageState(sessionId: string, state: { page: number; hasMore: boolean; total: number }) {
      this.historyPagesBySession[sessionId] = state;
    },
    setCurrentSession(sessionId: string | null) {
      if (this.currentSessionId !== sessionId) this.sessionViewGeneration += 1;
      this.currentSessionId = sessionId;
      this.retainSession(sessionId);
      const key = sessionKey(sessionId);
      if (!this.messagesBySession[key]) {
        this.messagesBySession[key] = [];
      }
    },
    transferMessages(fromSessionId: string | null | undefined, toSessionId: string | null | undefined) {
      const fromKey = sessionKey(fromSessionId);
      const toKey = sessionKey(toSessionId);
      if (fromKey === toKey || !this.messagesBySession[fromKey]?.length) return;
      this.messagesBySession[toKey] = [
        ...(this.messagesBySession[toKey] ?? []),
        ...this.messagesBySession[fromKey],
      ];
      delete this.messagesBySession[fromKey];
    },
    setMessages(sessionId: string | null | undefined, messages: AgentUiMessage[]) {
      this.messagesBySession[sessionKey(sessionId)] = messages;
      this.retainSession(sessionId);
    },
    appendMessage(sessionId: string | null | undefined, message: AgentUiMessage) {
      const key = sessionKey(sessionId);
      this.messagesBySession[key] = [...(this.messagesBySession[key] ?? []), message];
      this.retainSession(sessionId);
    },
    appendAssistantDelta(sessionId: string | null | undefined, messageId: string, delta: string) {
      const key = sessionKey(sessionId);
      this.messagesBySession[key] = (this.messagesBySession[key] ?? []).map((message) =>
        message.id === messageId ? { ...message, content: `${message.content}${delta}` } : message,
      );
    },
    updateMessage(sessionId: string | null | undefined, messageId: string, patch: Partial<AgentUiMessage>) {
      const key = sessionKey(sessionId);
      this.messagesBySession[key] = (this.messagesBySession[key] ?? []).map((message) =>
        message.id === messageId ? { ...message, ...patch } : message,
      );
    },
    deleteTurnsFromMessage(sessionId: string | null | undefined, messageId: number) {
      const key = sessionKey(sessionId);
      this.messagesBySession[key] = (this.messagesBySession[key] ?? []).filter((message) => {
        if (message.messageId === undefined) return true;
        return message.messageId < messageId;
      });
    },
    deleteMessage(sessionId: string | null | undefined, messageId: number) {
      const key = sessionKey(sessionId);
      this.messagesBySession[key] = filterDeletedMessage(this.messagesBySession[key] ?? [], messageId);
    },
    deleteResponseGroup(sessionId: string | null | undefined, messageIds: number[], sceneIds: string[]) {
      const key = sessionKey(sessionId);
      this.messagesBySession[key] = filterDeletedMessages(this.messagesBySession[key] ?? [], messageIds);
      const removedScenes = new Set(sceneIds);
      this.diagrams = this.diagrams.filter(diagram => !removedScenes.has(diagramId(diagram)));
      for (const sceneId of sceneIds) delete this.diagramScenes[sceneId];
    },
    incrementRun(sessionId: string | null | undefined) {
      const key = sessionKey(sessionId);
      this.runningCountsBySession[key] = (this.runningCountsBySession[key] ?? 0) + 1;
    },
    setRunCount(sessionId: string | null | undefined, count: number) {
      const key = sessionKey(sessionId);
      if (count > 0) {
        this.runningCountsBySession[key] = count;
      } else {
        delete this.runningCountsBySession[key];
      }
    },
    setSessionChecking(sessionId: string | null | undefined, checking: boolean) {
      const key = sessionKey(sessionId);
      if (checking) this.checkingRunsBySession[key] = true;
      else delete this.checkingRunsBySession[key];
    },
    transferRunCount(fromSessionId: string | null | undefined, toSessionId: string | null | undefined) {
      const fromKey = sessionKey(fromSessionId);
      const toKey = sessionKey(toSessionId);
      if (fromKey === toKey || !this.runningCountsBySession[fromKey]) return;
      this.runningCountsBySession[toKey] =
        (this.runningCountsBySession[toKey] ?? 0) + this.runningCountsBySession[fromKey];
      delete this.runningCountsBySession[fromKey];
    },
    decrementRun(sessionId: string | null | undefined) {
      const key = sessionKey(sessionId);
      const next = Math.max(0, (this.runningCountsBySession[key] ?? 0) - 1);
      if (next > 0) {
        this.runningCountsBySession[key] = next;
      } else {
        delete this.runningCountsBySession[key];
      }
    },
    setWorkspaceBundle(payload: {
      workspaces?: AgentWorkspaceSummary[];
      blackboard?: AgentBlackboardEntry[];
      diagrams?: AgentDiagramSummary[];
      selectedWorkspaceId?: number | null;
      sessionId?: string | null;
    }) {
      if (payload.workspaces && !sameStableValue(this.workspaces, payload.workspaces)) {
        this.workspaces = payload.workspaces;
      }
      if (payload.workspaces && payload.sessionId) {
        const current = this.workspacesBySession[payload.sessionId];
        if (!sameStableValue(current, payload.workspaces)) {
          this.workspacesBySession[payload.sessionId] = payload.workspaces;
        }
      }
      if (payload.blackboard && !sameStableValue(this.blackboard, payload.blackboard)) {
        this.blackboard = payload.blackboard;
      }
      if (payload.diagrams && !sameStableValue(this.diagrams, payload.diagrams)) {
        this.diagrams = payload.diagrams;
      }
      if (payload.selectedWorkspaceId !== undefined) this.selectedWorkspaceId = payload.selectedWorkspaceId;
    },
    setSessionWorkspaces(sessionId: string, workspaces: AgentWorkspaceSummary[]) {
      if (!sameStableValue(this.workspacesBySession[sessionId], workspaces)) {
        this.workspacesBySession[sessionId] = workspaces;
      }
      this.retainSession(sessionId);
    },
    setSessionWorkspaceLoading(sessionId: string, loading: boolean) {
      if (loading) {
        this.workspaceSessionLoading[sessionId] = true;
      } else {
        delete this.workspaceSessionLoading[sessionId];
      }
    },
    setWorkspaceLoading(loading: boolean) {
      this.workspaceLoading = loading;
    },
    setSelectedWorkspace(workspaceId: number | null) {
      this.selectedWorkspaceId = workspaceId;
    },
    setDiagramScene(sceneId: string, scene: Record<string, unknown>) {
      delete this.diagramScenes[sceneId];
      this.diagramScenes[sceneId] = scene;
      const keys = Object.keys(this.diagramScenes);
      for (const id of keys.slice(0, Math.max(0, keys.length - 32))) delete this.diagramScenes[id];
    },
    upsertDiagramSummary(payload: Record<string, unknown>) {
      const summary = diagramSummaryFromPayload(payload, this.selectedWorkspaceId);
      if (!summary) return;
      const id = diagramId(summary);
      const scene = asRecord(payload.scene);
      this.diagrams = [
        summary,
        ...this.diagrams.filter((diagram) => diagramId(diagram) !== id),
      ];
      if (scene) {
        this.setDiagramScene(id, scene);
      }
      if (summary.workspace_id && this.selectedWorkspaceId !== summary.workspace_id) {
        this.selectedWorkspaceId = summary.workspace_id;
      }
    },
    setDraftImages(images: AgentDraftImage[]) {
      const retained = new Set(images.map(image => image.previewUrl));
      for (const image of this.draftImages) {
        if (image.previewUrl?.startsWith('blob:') && !retained.has(image.previewUrl)) URL.revokeObjectURL(image.previewUrl);
      }
      this.draftImages = images;
    },
    setComposerDraft(value: string) {
      this.composerDraft = value;
    },
    setProblemRedirect(value: { problemId: number | null; problemTitle: string; message: string; actionLabel: string } | null) {
      this.problemRedirect = value;
    },
    requestProblemFolderExpansion(problemId: number | null) {
      this.requestedProblemFolderId = problemId;
    },
    clearDraftImages() {
      this.setDraftImages([]);
    },
    setGuidanceDepth(depth: GuidanceDepth) {
      this.guidanceDepth = depth;
    },
    requestScrollToBottom() {
      requestScrollIntent(this);
    },
    consumeScrollToBottomRequest(handledRequest: number) {
      return consumeScrollIntent(this, handledRequest);
    },
  },
});
