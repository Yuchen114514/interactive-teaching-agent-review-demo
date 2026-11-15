import type { AgentChatRequestBody, AgentStreamEvent } from '../api/types';
import type { AgentDraftImage, useAgentWorkspaceStore } from '../stores/agentWorkspace';
import { captureAgentAccount } from './agentAccountScope';
import { clearLocalAgentRuns, hasLocalAgentRun, registerLocalAgentRun, unregisterLocalAgentRun } from './agentRunRegistry';

type Workspace = ReturnType<typeof useAgentWorkspaceStore>;
type Run = {
  id: string;
  userId: string;
  token: string;
  sessionId: string | null;
  initialViewGeneration: number;
  controller: AbortController;
  scope: ReturnType<typeof captureAgentAccount>;
  frame: number | null;
  delta: string;
  deltaTargetId?: string;
  followupId?: string;
  redirected: boolean;
  stopped: boolean;
  serverAccepted: boolean;
  accepted?: () => void;
};
type Dependencies = {
  workspace: Workspace;
  stream: (token: string, body: AgentChatRequestBody, onEvent: (event: AgentStreamEvent) => void, signal: AbortSignal) => Promise<void>;
  onSettled?: (token: string, sessionId: string | null, redirected: boolean, stopped: boolean, localAssistantId: string) => Promise<void>;
  onRejected?: (message: string) => void;
  schedule?: (callback: () => void) => number;
  cancel?: (id: number) => void;
};
let idSequence = 0;
const makeId = (prefix: string) => `${prefix}-${Date.now()}-${++idSequence}`;
const key = (sessionId: string | null) => sessionId || 'draft';

/** Requests belong to sessions, never to mounted chat components. */
export class AgentBackgroundRuns {
  private runs = new Map<string, Run>();
  private settling = new Set<string>();
  private schedule: (callback: () => void) => number;
  private cancel: (id: number) => void;
  constructor(private dependencies: Dependencies) {
    this.schedule = dependencies.schedule ?? (callback => window.setTimeout(callback, 32));
    this.cancel = dependencies.cancel ?? (id => window.clearTimeout(id));
  }

  reset() {
    for (const run of this.runs.values()) {
      run.accepted = undefined;
      run.controller.abort();
      if (run.frame !== null) this.cancel(run.frame);
      run.delta = '';
    }
    this.runs.clear();
    this.settling.clear();
    clearLocalAgentRuns();
  }

  stop(sessionId: string | null) {
    for (const run of this.runs.values()) {
      if (run.sessionId !== sessionId) continue;
      run.stopped = true;
      this.flush(run);
      if (run.serverAccepted) {
        const targetId = run.followupId || run.id;
        this.dependencies.workspace.updateMessage(run.sessionId, targetId, {terminalState: 'stopped'});
        this.dependencies.workspace.appendAssistantDelta(run.sessionId, targetId, '\n\n已请求停止本轮生成。');
      }
      run.controller.abort();
    }
  }

  private flush(run: Run) {
    if (run.frame !== null) this.cancel(run.frame);
    run.frame = null;
    const delta = run.delta;
    run.delta = '';
    if (!delta || !run.scope.isCurrent() || run.redirected) return;
    const targetId = run.deltaTargetId || run.id;
    this.dependencies.workspace.appendAssistantDelta(run.sessionId, targetId, delta);
    this.dependencies.workspace.updateMessage(run.sessionId, targetId, { pending: false });
    if (this.dependencies.workspace.currentSessionId === run.sessionId) this.dependencies.workspace.requestScrollToBottom();
  }

  private handleEvent(run: Run, event: AgentStreamEvent) {
    if (!run.scope.isCurrent() || run.controller.signal.aborted) return;
    const workspace = this.dependencies.workspace;
    if (event.kind !== 'error') run.serverAccepted = true;
    if (event.kind !== 'error' && run.accepted) {
      const accepted = run.accepted;
      run.accepted = undefined;
      accepted();
    }
    if (event.kind === 'session') {
      if (event.runId) workspace.updateMessage(run.sessionId, run.id, {runId: event.runId});
      if (run.sessionId === event.sessionId) return;
      this.flush(run);
      const previous = run.sessionId;
      workspace.transferMessages(previous, event.sessionId);
      workspace.transferRunCount(previous, event.sessionId);
      unregisterLocalAgentRun(previous);
      registerLocalAgentRun(event.sessionId);
      run.sessionId = event.sessionId;
      workspace.touchSessionActivity(event.sessionId);
      if (workspace.currentSessionId === previous && workspace.sessionViewGeneration === run.initialViewGeneration) {
        workspace.setCurrentSession(event.sessionId);
      }
      return;
    }
    if (event.kind === 'problem_redirect') {
      this.flush(run);
      run.redirected = true;
      workspace.setMessages(run.sessionId, (workspace.messagesBySession[key(run.sessionId)] || []).filter(message => message.id !== run.id && message.id !== run.userId));
      if (workspace.currentSessionId === run.sessionId) workspace.setProblemRedirect({
        problemId: event.problemId, problemTitle: event.problemTitle, message: event.message, actionLabel: event.actionLabel,
      });
      return;
    }
    if (run.redirected) return;
    if (event.kind === 'followup_start') {
      this.flush(run);
      if (!run.followupId) {
        run.followupId = makeId('assistant-followup');
        workspace.appendMessage(run.sessionId, { id: run.followupId, role: 'assistant', content: '', pending: true, sourceAssistantId: run.id });
      }
      if (workspace.currentSessionId === run.sessionId) workspace.requestScrollToBottom();
    } else if (event.kind === 'followup_delta') {
      if (!run.followupId) return;
      run.deltaTargetId = run.followupId;
      run.delta += event.delta;
      if (run.frame === null) run.frame = this.schedule(() => this.flush(run));
    } else if (event.kind === 'followup_complete') {
      if (!run.followupId) return;
      this.flush(run);
      workspace.updateMessage(run.sessionId, run.followupId, { content: event.response, pending: false, mainVisibleComplete: true });
    } else if (event.kind === 'main_delta' || event.kind === 'answer_delta' || event.kind === 'text') {
      run.deltaTargetId = run.id;
      run.delta += event.kind === 'text' ? event.text : event.delta;
      if (run.frame === null) run.frame = this.schedule(() => this.flush(run));
    } else if (event.kind === 'partial_response' || event.kind === 'main_visible_complete') {
      this.flush(run);
      const content = workspace.messagesBySession[key(run.sessionId)]?.find(message => message.id === run.id)?.content || '';
      if (event.kind === 'main_visible_complete' || !content || event.response.startsWith(content)) {
        workspace.updateMessage(run.sessionId, run.id, { content: event.response });
      }
      workspace.updateMessage(run.sessionId, run.id, { pending: false, mainVisibleComplete: event.kind === 'main_visible_complete' });
      if (workspace.currentSessionId === run.sessionId) workspace.requestScrollToBottom();
    } else if (event.kind === 'diagram_decision') {
      if (event.needDiagram) workspace.updateMessage(run.sessionId, run.id, { diagramStatus: 'running' });
    } else if (event.kind === 'diagram_status') {
      if (event.status === 'running') workspace.updateMessage(run.sessionId, run.id, { diagramStatus: 'running' });
      if (event.status === 'failed') workspace.updateMessage(run.sessionId, run.id, { diagramStatus: 'failed' });
      if (event.status === 'skipped') workspace.updateMessage(run.sessionId, run.id, { diagramStatus: undefined });
    } else if (event.kind === 'diagram_ready') {
      // Sidecars for background sessions are reconciled from their own bundle.
      if (workspace.currentSessionId === run.sessionId) workspace.upsertDiagramSummary(event.payload);
      const scene = event.payload.scene && typeof event.payload.scene === 'object'
        ? event.payload.scene as Record<string, unknown> : event.payload;
      const sceneId = typeof scene.scene_id === 'string' ? scene.scene_id :
        typeof event.payload.diagram_scene_id === 'string' ? event.payload.diagram_scene_id : '';
      const message = workspace.messagesBySession[key(run.sessionId)]?.find(item => item.id === run.id);
      workspace.updateMessage(run.sessionId, run.id, {
        diagramStatus: 'ready',
        diagramSceneIds: sceneId ? [...new Set([...(message?.diagramSceneIds || []), sceneId])] : message?.diagramSceneIds,
      });
    } else if (event.kind === 'error') {
      this.flush(run);
      const targetId = run.followupId || run.id;
      workspace.updateMessage(run.sessionId, targetId, {terminalState: 'failed'});
      workspace.appendAssistantDelta(run.sessionId, targetId, `\n\n${event.error}`);
    }
  }

  async start(token: string, body: AgentChatRequestBody, images: AgentDraftImage[] = [], onAccepted?: () => void): Promise<boolean> {
    const workspace = this.dependencies.workspace;
    const sessionId = body.session_id || null;
    const scope = captureAgentAccount(token);
    if (!scope.isCurrent() || !body.message.trim() || this.settling.has(key(sessionId)) || hasLocalAgentRun(sessionId) || workspace.runningCountsBySession[key(sessionId)] > 0) return false;
    const run: Run = {
      id: makeId('assistant'), userId: makeId('user'), token, sessionId,
      initialViewGeneration: workspace.sessionViewGeneration, controller: new AbortController(),
      scope, frame: null, delta: '', redirected: false, stopped: false, serverAccepted: false, accepted: onAccepted,
    };
    this.runs.set(run.id, run);
    registerLocalAgentRun(sessionId);
    workspace.incrementRun(sessionId);
    workspace.appendMessage(sessionId, {
      id: run.userId, role: 'user', content: body.message,
      images: images.map(({image_id, url, thumbnail_url, filename}) => ({image_id, url, thumbnail_url, filename})),
    });
    workspace.appendMessage(sessionId, {id: run.id, role: 'assistant', content: '', pending: true, mainVisibleComplete: false});
    if (sessionId) workspace.touchSessionActivity(sessionId, {preview: body.message});
    workspace.requestScrollToBottom();
    try {
      await this.dependencies.stream(token, {...body, images: body.images ? [...body.images] : undefined}, event => this.handleEvent(run, event), run.controller.signal);
    } catch (error) {
      if (scope.isCurrent() && !run.controller.signal.aborted && !run.redirected) {
        this.flush(run);
        if (!run.serverAccepted) {
          workspace.setMessages(run.sessionId, (workspace.messagesBySession[key(run.sessionId)] || []).filter(message => message.id !== run.id && message.id !== run.userId));
          this.dependencies.onRejected?.(`发送失败，草稿已保留：${error instanceof Error ? error.message : '网络异常'}`);
        } else {
          workspace.appendAssistantDelta(run.sessionId, run.id, `\n\n请求中断，正在核对后台任务：${error instanceof Error ? error.message : '网络异常'}`);
        }
      }
    } finally {
      this.flush(run);
      run.accepted = undefined;
      if (this.runs.get(run.id) === run) {
        this.runs.delete(run.id);
        unregisterLocalAgentRun(run.sessionId);
      }
      if (scope.isCurrent()) {
        if (run.stopped && !run.serverAccepted) workspace.setMessages(run.sessionId, (workspace.messagesBySession[key(run.sessionId)] || []).filter(message => message.id !== run.id && message.id !== run.userId));
        this.settling.add(key(run.sessionId));
        workspace.setSessionChecking?.(run.sessionId, true);
        workspace.decrementRun(run.sessionId);
        if (!run.redirected) workspace.updateMessage(run.sessionId, run.id, {pending: false, mainVisibleComplete: true});
        if (run.followupId) workspace.updateMessage(run.sessionId, run.followupId, {pending: false});
        try {
          await this.dependencies.onSettled?.(token, run.sessionId, run.redirected, run.stopped, run.id).catch(() => undefined);
        } finally {
          this.settling.delete(key(run.sessionId));
          if (scope.isCurrent()) workspace.setSessionChecking?.(run.sessionId, false);
        }
      }
    }
    return true;
  }
}
