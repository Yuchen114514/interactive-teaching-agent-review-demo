<template>
  <section class="conversation-stage">
    <div class="composer-message-underlay" aria-hidden="true" />
    <AgentOverlayScroll class-name="chat-timeline" @viewport="setScrollViewport">
      <div
        class="chat-content-visibility"
        :class="{ 'is-switching-session': sessionSwitching || !sessionReadyForPaint }"
      >
        <button v-if="hasEarlierHistory" class="history-load-earlier" type="button" :disabled="loadingEarlierHistory || workspace.currentSessionRunning" @click="loadEarlierHistory">
          {{ loadingEarlierHistory ? '正在加载…' : '加载更早的消息' }}
        </button>
        <div v-if="showConversationSkeleton" class="conversation-skeleton" aria-hidden="true">
          <div class="conversation-skeleton-row assistant">
            <i class="agent-skeleton-line skeleton-avatar" />
            <span>
              <i class="agent-skeleton-line skeleton-bubble wide" />
              <i class="agent-skeleton-line skeleton-bubble medium" />
            </span>
          </div>
          <div class="conversation-skeleton-row user">
            <span>
              <i class="agent-skeleton-line skeleton-bubble compact" />
            </span>
          </div>
          <div class="conversation-skeleton-row assistant">
            <i class="agent-skeleton-line skeleton-avatar" />
            <span>
              <i class="agent-skeleton-line skeleton-bubble full" />
              <i class="agent-skeleton-line skeleton-bubble short" />
            </span>
          </div>
          <div class="conversation-bottom-spacer" aria-hidden="true" />
        </div>
        <div v-if="showEmptyConversation && workspace.problemRedirect" key="problem-redirect" class="empty-conversation problem-redirect-card">
          <div class="empty-orb"><AgentIcon name="doc" :size="24" /></div>
          <h1>{{ workspace.problemRedirect.problemTitle }}</h1>
          <p>检测到这是另一道题目。当前会话保持不变，问题不会写入当前题目的上下文。</p>
          <button class="problem-redirect-action" type="button" @click="carrySessionIntoProblem">{{ workspace.problemRedirect.actionLabel }}</button>
        </div>
        <div v-else-if="showEmptyConversation" key="empty" class="empty-conversation">
          <div class="empty-orb">
            <AgentIcon name="brain" :size="28" />
          </div>
          <h1>开始一次答疑</h1>
          <p>输入题目、截图或追问，我会把对话、题目工作区和板书放在同一个工作台里。</p>
        </div>
        <TransitionGroup
          v-else-if="renderedMessages.length"
          :key="messageListKey"
          name="message-motion"
          tag="div"
          class="message-motion-list"
          :css="!sessionSwitching && sessionReadyForPaint && !messageDeleteMotionSuppressed"
        >
          <article
            v-for="message in renderedMessages"
            :key="message.id"
            :ref="(element) => setMessageElement(message, element)"
            class="chat-message"
            :class="[
              message.role === 'user' ? 'user' : message.role === 'system' ? 'system' : 'assistant',
              highlightedMessageId === message.id ? 'is-highlighted' : '',
            ]"
          >
            <div
              class="message-stack"
              :class="{ 'is-editing': editingMessage?.id === message.id }"
            >
              <div v-if="message.images?.length" class="message-image-strip">
                <AgentAuthenticatedImage
                  v-for="image in message.images"
                  :key="image.image_id || image.url"
                  class="message-image-thumb"
                  :token="token"
                  :src="image.thumbnail_url || image.url"
                  :original-src="image.url"
                  :filename="image.filename"
                  alt="上传的图片"
                >
                  <span>{{ image.filename || "图片" }}</span>
                </AgentAuthenticatedImage>
              </div>
              <div
                class="message-bubble"
                :class="{ 'is-editing': editingMessage?.id === message.id, 'is-typing': message.pending && !message.content }"
              >
                <div
                  v-if="editingMessage?.id === message.id"
                  class="inline-message-editor-shell"
                >
                  <textarea
                    v-model="editingMessage.text"
                    class="inline-message-editor"
                    rows="4"
                    @keydown.esc.prevent="cancelEdit"
                    @keydown.enter.meta.prevent="submitEdit"
                    @keydown.enter.ctrl.prevent="submitEdit"
                  />
                  <div class="inline-message-editor-actions">
                    <button type="button" aria-label="取消编辑" @click="cancelEdit">
                      <AgentIcon name="x" :size="13" />
                    </button>
                    <button
                      class="inline-message-editor-submit"
                      type="button"
                      aria-label="发送编辑"
                      :disabled="!editingMessageCanSubmit"
                      @click="submitEdit"
                    >
                      <AgentIcon name="send" :size="13" />
                    </button>
                  </div>
                </div>
                <template v-else-if="message.role === 'assistant'">
                  <AgentMessageRenderer
                    v-if="message.content"
                    :streaming="workspace.currentSessionRunning && message.id === messages[messages.length - 1]?.id"
                    :content="message.content"
                  />
                </template>
                <AgentMessageText
                  v-else
                  :content="message.content"
                  :expandable="message.role === 'user'"
                />
                <div v-if="message.pending && !message.content" class="typing-dots" role="status" aria-label="正在回复">
                  <i /><i /><i />
                </div>
              </div>
              <Transition name="inline-diagram-replace" mode="out-in">
                <AgentDiagramLoading
                  v-if="message.role === 'assistant' && message.mainVisibleComplete && message.diagramStatus === 'running'"
                  key="diagram-loading"
                />
                <div
                  v-else-if="inlineDiagramMap.get(message.id)?.length"
                  key="diagram-cards"
                  class="inline-diagram-cards"
                >
                  <AgentDiagramPanel
                    v-for="diagram in inlineDiagramMap.get(message.id) || []"
                    :key="diagram.scene_id || diagram.diagram_scene_id"
                    :diagram="diagram"
                    inline
                  />
                </div>
              </Transition>
              <div
                v-if="message.role === 'assistant' && message.diagramStatus === 'failed'"
                class="float-section inline-diagram-failed"
                role="status"
              >图示生成失败，请稍后重试</div>
              <div
                v-for="continuation in message.continuations || []"
                :key="continuation.id"
                :ref="(element) => setMessageElement(continuation, element)"
                class="message-bubble response-continuation"
                :class="{ 'is-typing': continuation.pending && !continuation.content }"
              >
                <AgentMessageRenderer
                  v-if="continuation.content"
                  :streaming="workspace.currentSessionRunning && continuation.id === messages[messages.length - 1]?.id"
                  :content="continuation.content"
                />
                <div v-if="continuation.pending && !continuation.content" class="typing-dots" role="status" aria-label="正在回复">
                  <i /><i /><i />
                </div>
              </div>
              <div v-if="message.messageId && !isSharedDemo" class="message-actions">
                <button
                  v-if="message.role === 'user' && editingMessage?.id !== message.id"
                  type="button"
                  aria-label="编辑重发"
                  @click="startEdit(message)"
                >
                  <AgentIcon name="pencil" :size="13" />
                </button>
                <button class="message-delete-action" type="button" aria-label="删除消息" @click="message.role === 'assistant' ? deleteResponseGroup(message) : deleteMessage(message.messageId)">
                  <AgentIcon name="trash" :size="13" />
                </button>
              </div>
            </div>
          </article>
          <div key="conversation-bottom-spacer" class="conversation-bottom-spacer" aria-hidden="true" />
        </TransitionGroup>
        <div v-else class="conversation-loading-guard" aria-hidden="true" />
      </div>
    </AgentOverlayScroll>

    <AgentComposer
      :disabled="!token || isSharedDemo"
      :running="workspace.currentSessionRunning"
      :initial-draft="workspace.composerDraft"
      @send="send"
      @stop="stop"
    >
      <template #below-composer>
        <div v-if="isSharedDemo" class="shared-demo-note">Read-only example · Create a new chat to try it yourself.</div>
        <div v-if="workspace.problemRedirect && !showEmptyConversation" class="problem-redirect-inline">
          <div class="problem-redirect-inline-copy">
            <AgentIcon name="doc" :size="16" />
            <span>{{ workspace.problemRedirect.problemTitle }}：本轮问题未写入当前会话</span>
          </div>
          <button class="problem-redirect-action" type="button" @click="carrySessionIntoProblem">{{ workspace.problemRedirect.actionLabel }}</button>
        </div>
      </template>
    </AgentComposer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import AgentIcon from "./AgentIcon.vue";
import AgentComposer from "./AgentComposer.vue";
import AgentOverlayScroll from "./AgentOverlayScroll.vue";
import AgentMessageRenderer from "./AgentMessageRenderer.vue";
import AgentAuthenticatedImage from "./AgentAuthenticatedImage.vue";
import AgentMessageText from "./AgentMessageText.vue";
import AgentDiagramPanel from "./AgentDiagramPanel.vue";
import AgentDiagramLoading from "./AgentDiagramLoading.vue";
import { createAgentSession, deleteAgentMessage, deleteAgentResponseGroup, deleteAgentTurnsFromMessage, loadAgentHistoryPage } from "../api/agentApi";
import type { AgentHistoryMessage } from "../api/types";
import { useAgentRouteContext } from "../composables/useAgentRouteContext";
import { buildAgentChatRequest } from "../utils/agentChatRequest";
import { useAgentWorkspaceBootstrap } from "../composables/useAgentWorkspaceBootstrap";
import { useAgentRunsStore } from "../stores/agentRuns";
import { captureAgentAccount } from "../utils/agentAccountScope";
import { normalizeAgentMessageRole } from "../utils/agentMessageRole";
import { buildInlineDiagramMap } from "../utils/agentInlineDiagram";
import { groupAgentResponseMessages, type AgentResponseGroup } from "../utils/agentResponseGroups";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { writeCachedHistory } from "../utils/agentDataCache";
import { useAccodingStore } from "@/stores";
import { useAgentLayoutStore } from "../stores/agentLayout";

const store = useAccodingStore();
const layout = useAgentLayoutStore();
const workspace = useAgentWorkspaceStore();
const { context: routeContext } = useAgentRouteContext();
const { switchSession: loadSession } = useAgentWorkspaceBootstrap();
const runs = useAgentRunsStore();
const scrollRef = ref<HTMLElement | null>(null);
const loadingEarlierHistory = ref(false);
const editingMessage = ref<{ id: string; messageId: number; text: string } | null>(null);
const highlightedMessageId = ref<string | null>(null);
const sessionSwitching = ref(false);
const sessionReadyForPaint = ref(false);
const messageDeleteMotionSuppressed = ref(false);
const messageElements = new Map<string, HTMLElement>();
let handledScrollToBottomRequest = 0;
let sessionSwitchTimer: number | null = null;

const token = computed(() => store.token);
const isSharedDemo = computed(() => workspace.sessions.some(
  (session) => session.session_id === workspace.currentSessionId && session.is_shared_demo,
));
const messages = computed(() => workspace.currentMessages);
const renderedMessages = computed(() => groupAgentResponseMessages(messages.value));
const inlineDiagramMap = computed(() => buildInlineDiagramMap(workspace.diagrams, messages.value));
const showEmptyConversation = computed(() =>
  !messages.value.length &&
  !sessionSwitching.value &&
  sessionReadyForPaint.value &&
  !workspace.historyLoading,
);
const showConversationSkeleton = computed(() => workspace.historyLoading && !messages.value.length);
const messageListKey = computed(() => `messages-${workspace.currentSessionId || "draft"}`);
const hasEarlierHistory = computed(() => Boolean(workspace.currentSessionId && workspace.historyPagesBySession[workspace.currentSessionId]?.hasMore));
let disposed = false;

async function loadEarlierHistory() {
  const requestToken = token.value;
  const sessionId = workspace.currentSessionId;
  if (!requestToken || !sessionId || loadingEarlierHistory.value || workspace.currentSessionRunning) return;
  const scope = captureAgentAccount(requestToken);
  const state = workspace.historyPagesBySession[sessionId];
  const viewport = getChatScrollElement();
  const height = viewport?.scrollHeight || 0;
  const scrollTop = viewport?.scrollTop || 0;
  loadingEarlierHistory.value = true;
  try {
    const page = await loadAgentHistoryPage(requestToken, sessionId, {page: (state?.page || 1) + 1});
    if (disposed || !scope.isCurrent() || workspace.currentSessionId !== sessionId) return;
    const current = workspace.messagesBySession[sessionId] || [];
    const existingIds = new Set(current.filter(message => message.messageId != null).map(message => message.messageId));
    const earlier = page.messages.filter(message => message.message_id == null || !existingIds.has(message.message_id)).map(message => ({
      id: message.message_id != null ? `message-${message.message_id}` : `history-${sessionId}-${message.turn_index}-${message.role}`,
      messageId: message.message_id, role: normalizeAgentMessageRole(message.role), content: String(message.content || ''),
      images: message.images || [], turnIndex: message.turn_index, pending: false,
    }));
    workspace.setMessages(sessionId, [...earlier, ...current]);
    workspace.setHistoryPageState(sessionId, {page: page.page, hasMore: page.hasMore, total: page.meta.count});
    await nextTick();
    if (!disposed && viewport && workspace.currentSessionId === sessionId) viewport.scrollTop = scrollTop + viewport.scrollHeight - height;
  } catch (error) {
    if (!disposed && scope.isCurrent()) ElMessage.warning(`加载更早消息失败：${error instanceof Error ? error.message : '网络异常'}`);
  } finally {
    if (!disposed) loadingEarlierHistory.value = false;
  }
}

const editingMessageCanSubmit = computed(() => Boolean(editingMessage.value?.text.trim()));
async function persistHistoryCache(requestToken: string, sessionId: string | null) {
  if (!requestToken || !sessionId) return;
  const history: AgentHistoryMessage[] = (workspace.messagesBySession[sessionId] || [])
    .filter(message => !message.pending && !message.terminalState)
    .map(message => ({message_id: message.messageId, role: message.role, content: message.content, turn_index: message.turnIndex, images: message.images}));
  await writeCachedHistory(requestToken, sessionId, history);
}

function setScrollViewport(element: HTMLElement | null) {
  scrollRef.value = element;
}

function getChatScrollElement() {
  const host = typeof document === "undefined"
    ? null
    : document.querySelector<HTMLElement>(".conversation-stage .chat-timeline");
  return scrollRef.value ?? host ?? null;
}

function scrollToBottomImmediately() {
  const target = getChatScrollElement();
  if (!target) return;
  target.scrollTo({
    top: target.scrollHeight,
    behavior: "auto",
  });
}

async function scrollToBottom() {
  await nextTick();
  scrollToBottomImmediately();
}

async function scrollToBottomAfterRender() {
  await nextTick();
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      scrollToBottomImmediately();
    });
  });
}

function waitForAnimationFrameOrTimeout() {
  return new Promise<void>((resolve) => {
    let settled = false;
    const timeout = window.setTimeout(finish, 80);
    function finish() {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      resolve();
    }
    window.requestAnimationFrame(finish);
  });
}

async function syncBottomOverFrames() {
  await nextTick();
  scrollToBottomImmediately();
  await waitForAnimationFrameOrTimeout();
  scrollToBottomImmediately();
  await waitForAnimationFrameOrTimeout();
  scrollToBottomImmediately();
}

async function releaseInitialConversationPaint() {
  await syncBottomOverFrames();
  if (sessionSwitching.value) return;
  sessionReadyForPaint.value = true;
}

function clearSessionSwitchTimer() {
  if (sessionSwitchTimer === null) return;
  window.clearTimeout(sessionSwitchTimer);
  sessionSwitchTimer = null;
}

function releaseSessionSwitchPaint() {
  scrollToBottomImmediately();
  sessionReadyForPaint.value = true;
  sessionSwitching.value = false;
  sessionSwitchTimer = null;
}

function finishSessionSwitchAfterBottomSync(delay = 80) {
  clearSessionSwitchTimer();
  const finish = () => {
    if (workspace.historyLoading && !messages.value.length) {
      sessionSwitchTimer = null;
      return;
    }
    releaseSessionSwitchPaint();
    void syncBottomOverFrames();
  };
  if (messages.value.length) {
    finish();
    return;
  }
  sessionSwitchTimer = window.setTimeout(finish, delay);
}

function setMessageElement(message: { id: string }, element: unknown) {
  if (element instanceof HTMLElement) {
    messageElements.set(message.id, element);
  } else {
    messageElements.delete(message.id);
  }
}

async function scrollToMessage(messageId: number | string) {
  const message = messages.value.find((item) => item.messageId === messageId || item.id === String(messageId));
  if (!message) {
    ElMessage.info("这条来源消息不在当前对话中");
    return;
  }
  await nextTick();
  const element = messageElements.get(message.id);
  if (!element) return;
  element.scrollIntoView({ block: "center", behavior: "smooth" });
  highlightedMessageId.value = message.id;
  window.setTimeout(() => {
    if (highlightedMessageId.value === message.id) highlightedMessageId.value = null;
  }, 1800);
}

function send(message: string, onAccepted?: () => void) {
  if (isSharedDemo.value) return;
  const requestToken = token.value;
  if (!requestToken) { ElMessage.warning("请登录后使用 Agent"); return; }
  if (workspace.currentSessionRunning) return;
  const requestSessionId = workspace.currentSessionId;
  const draftImages = [...workspace.draftImages];
  const body = buildAgentChatRequest({
    message,
    sessionId: requestSessionId,
    guidanceDepth: workspace.guidanceDepth,
    images: draftImages.map(image => image.url).filter(Boolean),
    routeContext: routeContext.value,
  });
  // Only the session service owns the async lifetime; do not await it here.
  void runs.start(requestToken, body, draftImages, () => {
    onAccepted?.();
    if (token.value === requestToken && workspace.currentSessionId === requestSessionId) workspace.clearDraftImages();
  });
}

async function carrySessionIntoProblem() {
  const redirect = workspace.problemRedirect;
  const requestToken = token.value;
  const viewGeneration = workspace.sessionViewGeneration;
  if (!redirect || !requestToken) return;
  const scope = captureAgentAccount(requestToken);
  try {
    const session = await createAgentSession(requestToken, redirect.problemId ?? undefined);
    if (!scope.isCurrent()) return;
    workspace.upsertSession(session);
    if (disposed || workspace.sessionViewGeneration !== viewGeneration) return;
    workspace.requestProblemFolderExpansion(redirect.problemId);
    workspace.setProblemRedirect(null);
    workspace.setComposerDraft(redirect.message);
    await loadSession(requestToken, session.session_id);
  } catch (error) {
    if (!disposed && scope.isCurrent()) ElMessage.warning(`创建题目会话失败：${error instanceof Error ? error.message : "未知错误"}`);
  }
}

async function stop() {
  const requestToken = token.value;
  if (!requestToken) return;
  const scope = captureAgentAccount(requestToken);
  try { await runs.stop(workspace.currentSessionId, requestToken); }
  catch (error) { if (!disposed && scope.isCurrent()) ElMessage.warning(`停止生成失败：${error instanceof Error ? error.message : "网络异常"}`); }
}

async function deleteTurns(messageId: number) {
  const requestToken = token.value;
  const sessionId = workspace.currentSessionId;
  if (!requestToken || workspace.currentSessionRunning) return;
  const scope = captureAgentAccount(requestToken);
  try {
    await deleteAgentTurnsFromMessage(requestToken, messageId);
    if (!scope.isCurrent()) return;
    workspace.deleteTurnsFromMessage(sessionId, messageId);
    await persistHistoryCache(requestToken, sessionId);
  } catch (error) {
    if (!disposed && scope.isCurrent()) ElMessage.warning(`删除消息失败：${error instanceof Error ? error.message : "未知错误"}`);
  }
}

async function suppressMessageDeleteMotion(removeMessage: () => void) {
  messageDeleteMotionSuppressed.value = true;
  removeMessage();
  await nextTick();
  window.requestAnimationFrame(() => {
    if (disposed) return;
    messageDeleteMotionSuppressed.value = false;
  });
}

async function deleteMessage(messageId: number) {
  if (isSharedDemo.value) return;
  const requestToken = token.value;
  const sessionId = workspace.currentSessionId;
  if (!requestToken || workspace.currentSessionRunning) return;
  const scope = captureAgentAccount(requestToken);
  try {
    await deleteAgentMessage(requestToken, messageId);
    if (!scope.isCurrent()) return;
    if (!disposed && workspace.currentSessionId === sessionId) {
      await suppressMessageDeleteMotion(() => workspace.deleteMessage(sessionId, messageId));
    } else workspace.deleteMessage(sessionId, messageId);
    await persistHistoryCache(requestToken, sessionId);
  } catch (error) {
    if (!disposed && scope.isCurrent()) {
      messageDeleteMotionSuppressed.value = false;
      ElMessage.warning(`删除消息失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }
}

async function deleteResponseGroup(message: AgentResponseGroup) {
  if (isSharedDemo.value) return;
  const messageId = message.messageId;
  const requestToken = token.value;
  const sessionId = workspace.currentSessionId;
  if (messageId === undefined || !requestToken || workspace.currentSessionRunning) return;
  const scope = captureAgentAccount(requestToken);
  try {
    const result = await deleteAgentResponseGroup(requestToken, messageId);
    if (!scope.isCurrent()) return;
    const remove = () => workspace.deleteResponseGroup(sessionId, result.deleted_message_ids, result.deleted_scene_ids);
    if (!disposed && workspace.currentSessionId === sessionId) await suppressMessageDeleteMotion(remove);
    else remove();
    await persistHistoryCache(requestToken, sessionId);
  } catch (error) {
    if (!disposed && scope.isCurrent()) {
      messageDeleteMotionSuppressed.value = false;
      ElMessage.warning(`删除消息失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }
}

function startEdit(message: { id: string; messageId?: number; content: string }) {
  if (isSharedDemo.value) return;
  if (message.messageId === undefined || workspace.currentSessionRunning) return;
  editingMessage.value = {
    id: message.id,
    messageId: message.messageId,
    text: message.content,
  };
}

function cancelEdit() {
  editingMessage.value = null;
}

async function submitEdit() {
  if (isSharedDemo.value) return;
  const edit = editingMessage.value;
  const requestToken = token.value;
  const sessionId = workspace.currentSessionId;
  if (!edit || !requestToken || workspace.currentSessionRunning || !edit.text.trim()) return;
  const scope = captureAgentAccount(requestToken);
  try {
    await deleteAgentTurnsFromMessage(requestToken, edit.messageId);
    if (!scope.isCurrent()) return;
    workspace.deleteTurnsFromMessage(sessionId, edit.messageId);
    await persistHistoryCache(requestToken, sessionId);
    if (disposed || !scope.isCurrent() || workspace.currentSessionId !== sessionId || editingMessage.value !== edit) return;
    editingMessage.value = null;
    send(edit.text.trim());
  } catch (error) {
    if (!disposed && scope.isCurrent()) ElMessage.warning(`编辑重发失败：${error instanceof Error ? error.message : "未知错误"}`);
  }
}

defineExpose({
  scrollToMessage,
});

watch(
  [() => workspace.scrollToBottomRequest, () => layout.isOpen],
  async () => {
    if (!layout.isOpen) return;
    const request = workspace.consumeScrollToBottomRequest(handledScrollToBottomRequest);
    if (request === null) return;
    handledScrollToBottomRequest = request;
    await scrollToBottomAfterRender();
  },
);

watch(
  () => workspace.currentSessionId,
  async () => {
    loadingEarlierHistory.value = false;
    editingMessage.value = null;
    sessionSwitching.value = true;
    sessionReadyForPaint.value = false;
    clearSessionSwitchTimer();
    messageElements.clear();
    await nextTick();
    scrollToBottomImmediately();
    await waitForAnimationFrameOrTimeout();
    scrollToBottomImmediately();
    finishSessionSwitchAfterBottomSync(workspace.historyLoading ? 240 : 80);
  },
);

watch(
  () => messages.value.length,
  async () => {
    if (!sessionSwitching.value) return;
    await syncBottomOverFrames();
    if (messages.value.length || !workspace.historyLoading) finishSessionSwitchAfterBottomSync(40);
  },
  { flush: "post" },
);

watch(
  () => workspace.historyLoading,
  async (loading) => {
    if (loading || !sessionSwitching.value) return;
    await syncBottomOverFrames();
    finishSessionSwitchAfterBottomSync(40);
  },
);

onMounted(() => {
  void releaseInitialConversationPaint();
});

onBeforeUnmount(() => {
  disposed = true;
  clearSessionSwitchTimer();
});
</script>
