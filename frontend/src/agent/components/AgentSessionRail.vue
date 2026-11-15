<template>
  <aside
    ref="railRef"
    class="session-rail"
    :class="{
      'is-collapsed': layout.sessionRailCollapsed,
    }"
  >
    <div ref="shadowLayer" class="session-selection-shadow" aria-hidden="true" hidden>
      <div ref="shadowSurface" class="session-selection-shadow-surface" />
    </div>
    <div class="rail-head">
      <div class="session-view-switch" role="tablist" aria-label="会话与题目">
        <span class="session-view-switch-thumb" :class="{ 'is-problems': railView === 'problems' }" aria-hidden="true" />
        <button class="session-view-switch-option" :class="{ 'is-active': railView === 'sessions' }" type="button" role="tab" :aria-selected="railView === 'sessions'" @click="setRailView('sessions')">会话</button>
        <button class="session-view-switch-option" :class="{ 'is-active': railView === 'problems' }" type="button" role="tab" :aria-selected="railView === 'problems'" @click="setRailView('problems')">题目</button>
      </div>
      <Transition name="session-create-control-motion">
        <button
          v-if="railView === 'sessions' && !searchExpanded"
          class="standalone-session-create"
          type="button"
          aria-label="新建独立会话"
          @click.stop="newSession"
        >
          <AgentIcon name="plus" :size="14" />
        </button>
      </Transition>
      <label
        class="rail-search"
        :class="{ 'is-search-open': searchExpanded }"
        aria-label="搜索会话"
        @click.stop="openSearch"
      >
        <AgentIcon name="search" :size="15" />
        <input ref="railSearchInputRef" v-model="query" placeholder="搜索会话" type="search" @focus="openSearch" @keydown.esc.stop="closeSearch" />
      </label>
    </div>

    <AgentOverlayScroll class-name="session-list" @viewport="setSessionListViewport" @scroll="scheduleShadow">
      <div ref="sessionListContentRef" class="session-list-content">
        <div v-if="workspace.sessionsLoading" class="session-list-skeleton" aria-hidden="true">
          <div v-for="index in 7" :key="index" class="session-row-skeleton">
            <i class="agent-skeleton-line session-skeleton-icon" />
            <span>
              <i class="agent-skeleton-line session-skeleton-title" />
              <i class="agent-skeleton-line session-skeleton-subtitle" />
            </span>
          </div>
        </div>
        <div v-else-if="railView === 'problems' && problemFolders.length === 0" class="session-state">暂无题目</div>
        <div v-else-if="railView === 'problems'" class="session-folder-list">
          <section v-for="folder in problemFolders" :key="`problem-${folder.problemId}`" class="session-folder">
            <div class="session-folder-heading">
              <button class="session-folder-toggle" type="button" :aria-expanded="isFolderExpanded(folder.problemId)" @click="toggleFolder(folder.problemId)">
                <AgentIcon :name="isFolderExpanded(folder.problemId) ? 'chevron-down' : 'chevron-right'" :size="11" />
                <span><strong>{{ problemFolderTitle(folder.problemId, folder.problemTitle) }}</strong><small>{{ folder.sessions.length }} 个会话</small></span>
              </button>
              <button class="session-folder-create" type="button" :aria-label="`在${problemFolderTitle(folder.problemId)}中新建会话`" @click="createProblemSession(folder.problemId)">
                <AgentIcon name="plus" :size="12" />
              </button>
            </div>
            <TransitionGroup v-if="isFolderExpanded(folder.problemId)" name="session-row-motion" tag="div" class="session-list-motion session-folder-children">
              <div
                v-for="session in folder.sessions"
                :key="session.session_id"
                class="session-row-shell"
                :ref="(element) => setSessionRowRef(session.session_id, element)"
                :class="{
                  'is-exiting': isSessionRowExiting(session.session_id),
                  'is-selected': session.session_id === workspace.currentSessionId,
                }"
                :style="sessionRowExitStyle(session.session_id)"
              >
              <div
                v-if="editingSessionId === session.session_id"
                class="session-row is-renaming"
                :class="{
                  'is-selected': session.session_id === workspace.currentSessionId,
                }"
              >
                <span class="session-inline-editor" @click.stop>
                  <input
                    :ref="setRenameInputRef"
                    v-model="editingTitle"
                    @keydown.enter.prevent="submitRename(session.session_id)"
                    @keydown.esc.prevent="cancelRename"
                    @blur="submitRename(session.session_id)"
                  />
                </span>
              </div>
              <button
                v-else
                class="session-row"
                :class="{
                  'is-selected': session.session_id === workspace.currentSessionId,
                }"
                type="button"
                @click="selectSession(session.session_id)"
              >
                <span>
                  <strong
                    class="session-title-text"
                    :class="{ 'is-title-updated': renamedSessionId === session.session_id }"
                  >
                    {{ sessionTitle(session) }}
                  </strong>
                  <small v-if="session.is_shared_demo" class="session-demo-badge">Demo</small>
                </span>
              </button>
              <button v-if="!session.is_shared_demo" class="session-rename-button" type="button" aria-label="重命名会话" @click.stop="startRename(session)">
                <AgentIcon name="pencil" :size="10" />
              </button>
              <button v-if="!session.is_shared_demo" class="session-delete-button" type="button" aria-label="删除会话" @click.stop="deleteSession(session.session_id)">
                <AgentIcon name="trash" :size="10" />
              </button>
              </div>
            </TransitionGroup>
            <button v-if="isFolderExpanded(folder.problemId) && folder.sessions.length === 0" class="session-folder-empty" type="button" @click="createProblemSession(folder.problemId)">新建此题会话</button>
          </section>
        </div>
        <div v-else class="session-flat-list">
          <section class="standalone-session-section">
            <TransitionGroup name="session-row-motion" tag="div" class="session-list-motion">
              <div
                v-for="session in flatSessions"
                :key="session.session_id"
                class="session-row-shell"
                :ref="(element) => setSessionRowRef(session.session_id, element)"
                :class="{
                  'is-exiting': isSessionRowExiting(session.session_id),
                  'is-selected': session.session_id === workspace.currentSessionId,
                }"
                :style="sessionRowExitStyle(session.session_id)"
              >
                <div v-if="editingSessionId === session.session_id" class="session-row is-renaming" :class="{ 'is-selected': session.session_id === workspace.currentSessionId }">
                  <span class="session-inline-editor" @click.stop><input :ref="setRenameInputRef" v-model="editingTitle" @keydown.enter.prevent="submitRename(session.session_id)" @keydown.esc.prevent="cancelRename" @blur="submitRename(session.session_id)" /></span>
                </div>
                <button v-else class="session-row" :class="{ 'is-selected': session.session_id === workspace.currentSessionId }" type="button" @click="selectSession(session.session_id)">
                  <span><strong class="session-title-text" :class="{ 'is-title-updated': renamedSessionId === session.session_id }">{{ sessionTitle(session) }}</strong><small v-if="session.is_shared_demo" class="session-demo-badge">Demo</small></span>
                </button>
                <button v-if="!session.is_shared_demo" class="session-rename-button" type="button" aria-label="重命名会话" @click.stop="startRename(session)"><AgentIcon name="pencil" :size="10" /></button>
                <button v-if="!session.is_shared_demo" class="session-delete-button" type="button" aria-label="删除会话" @click.stop="deleteSession(session.session_id)"><AgentIcon name="trash" :size="10" /></button>
              </div>
            </TransitionGroup>
            <div v-if="filteredSessions.length === 0" class="session-state">暂无会话</div>
          </section>
        </div>
      </div>
    </AgentOverlayScroll>
  </aside>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import AgentIcon from "./AgentIcon.vue";
import AgentOverlayScroll from "./AgentOverlayScroll.vue";
import { useSessionSelectionShadow } from "../composables/useSessionSelectionShadow";
import {
  deleteAgentSession,
  renameAgentSession,
} from "../api/agentApi";
import type { AgentSessionSummary } from "../api/types";
import { useAgentRouteContext } from "../composables/useAgentRouteContext";
import { useAgentWorkspaceBootstrap } from "../composables/useAgentWorkspaceBootstrap";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { useAgentLayoutStore } from "../stores/agentLayout";
import { groupProblemFolderSessions } from "../utils/problemFolderSessions";
import { removeCachedSession, writeCachedSessions } from "../utils/agentDataCache";
import { captureAgentAccount } from "../utils/agentAccountScope";
import { agentDraftCache } from "../utils/agentDraftCache";
import { mergeRenderedSessions, sessionIds } from "../utils/sessionRenderOrder";
import { mergeRenamedSessionInPlace, shouldSubmitSessionRename } from "../utils/sessionRename";
import { sessionActivityTimeValue } from "../utils/sessionActivity";
import { useAccodingStore } from "@/stores";
import { createAgentSession } from "../api/agentApi";

const store = useAccodingStore();
const workspace = useAgentWorkspaceStore();
const layout = useAgentLayoutStore();
const {
  switchSession: loadSession,
} = useAgentWorkspaceBootstrap();
const { context } = useAgentRouteContext();
const query = ref("");
const sessionListRef = ref<HTMLElement | null>(null);
const railRef = ref<HTMLElement | null>(null);
const sessionListContentRef = ref<HTMLElement | null>(null);
const editingSessionId = ref<string | null>(null);
const editingTitle = ref("");
const editingOriginalTitle = ref("");
const renameInputRef = ref<HTMLInputElement | null>(null);
const renamedSessionId = ref<string | null>(null);
const expandedFolderIds = ref<Set<number>>(new Set());
const railView = computed(() => layout.sessionRailView);
const searchExpanded = ref(false);
const railSearchInputRef = ref<HTMLInputElement | null>(null);
const exitingSessionIds = ref<Set<string>>(new Set());
const exitingSessionHeights = ref<Record<string, number>>({});
const renderedSessions = ref<AgentSessionSummary[]>([]);
let renamedTitleTimer: number | null = null;
let alive = true;
let renameGeneration = 0;
let renameRequestSequence = 0;
const renameRequestVersions = new Map<string, number>();
const pendingRenameKeys = new Set<string>();
const pendingDeletes = new Set<string>();
const exitVersions = new Map<string, number>();
let exitSequence = 0;
const SESSION_ROW_EXIT_MOTION_MS = 320;
const sessionRowElements = new Map<string, HTMLElement>();
const { shadowLayer, shadowSurface, scheduleShadow } = useSessionSelectionShadow(
  railRef, sessionListRef, sessionListContentRef,
  () => sessionRowElements.get(workspace.currentSessionId ?? ""),
);
const currentProblemId = computed(() => context.value.problem_id);
function setRailView(view: "sessions" | "problems") {
  layout.setSessionRailView(view);
}
const sessionGroups = computed(() => groupProblemFolderSessions(renderedSessions.value, { currentProblemId: currentProblemId.value }));
const problemFolders = computed(() => sessionGroups.value.problemFolders);
const standaloneSessions = computed(() => sessionGroups.value.standaloneSessions);
const flatSessions = computed(() => [...renderedSessions.value].sort((left, right) => {
  if (left.is_shared_demo !== right.is_shared_demo) return left.is_shared_demo ? -1 : 1;
  return sessionActivityTimeValue(right) - sessionActivityTimeValue(left);
}));
function setSessionListViewport(element: HTMLElement | null) {
  sessionListRef.value = element;
}

async function openSearch() {
  searchExpanded.value = true;
  await nextTick();
  railSearchInputRef.value?.focus();
}

function closeSearch() {
  searchExpanded.value = false;
  railSearchInputRef.value?.blur();
}

function handleSearchOutsidePointerDown(event: PointerEvent) {
  const target = event.target;
  if (target instanceof Element && target.closest(".rail-search")) return;
  closeSearch();
}

const filteredSessions = computed(() => {
  const needle = query.value.trim().toLowerCase();
  const source = needle
    ? workspace.sessions.filter((session) =>
        sessionSearchText(session).includes(needle),
    )
    : workspace.sessions;
  return source;
});

function isFolderExpanded(problemId: number) {
  return expandedFolderIds.value.has(problemId);
}

function toggleFolder(problemId: number) {
  const next = new Set(expandedFolderIds.value);
  if (next.has(problemId)) next.delete(problemId);
  else next.add(problemId);
  expandedFolderIds.value = next;
}

function problemFolderTitle(problemId: number, fallback?: string) {
  const title = workspace.sessions.find((session) => session.problem_id === problemId)?.problem_title?.trim();
  if (title) return title;
  const workspaceTitle = [
    ...workspace.workspaces,
    ...Object.values(workspace.workspacesBySession).flat(),
  ].find((item) => item.problem_id === problemId)?.problem_title?.trim();
  if (workspaceTitle) return workspaceTitle;
  if (problemId === currentProblemId.value) {
    const currentWorkspace = workspace.selectedWorkspace;
    if (currentWorkspace?.problem_id === problemId) {
      const workspaceTitle = currentWorkspace.problem_title?.trim() || currentWorkspace.title?.trim();
      if (workspaceTitle) return workspaceTitle;
    }
    if (workspace.problemRedirect?.problemId === problemId) return workspace.problemRedirect.problemTitle;
  }
  return fallback?.trim() || `题目 ${problemId}`;
}

async function createProblemSession(problemId: number) {
  const token = store.token;
  const account = captureAgentAccount(token);
  const view = workspace.sessionViewGeneration;
  const valid = () => alive && account.isCurrent();
  if (!token || !valid()) return;
  try {
    const session = await createAgentSession(token, problemId);
    if (!valid()) return;
    workspace.upsertSession(session);
    expandedFolderIds.value = new Set([...expandedFolderIds.value, problemId]);
    await writeCachedSessions(token, [...workspace.sessions]);
    if (!valid() || workspace.sessionViewGeneration !== view) return;
    await loadSession(token, session.session_id);
  } catch (error) {
    if (valid()) ElMessage.warning(`新建题目会话失败：${error instanceof Error ? error.message : "未知错误"}`);
  }
}

function sessionTitle(session: AgentSessionSummary) {
  if (session.is_shared_demo) return session.problem_title?.trim() || "Shortest Path in an Unweighted Graph";
  return session.title?.trim() || session.preview?.trim() || `会话 ${session.session_id.slice(0, 8)}`;
}

function formatTime(value?: string | number) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function sessionSearchText(session: AgentSessionSummary) {
  return [session.title, session.preview, session.session_id, session.problem_id, session.problem_title]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

async function selectSession(sessionId: string) {
  if (!store.token) return;
  await loadSession(store.token, sessionId);
}

async function newSession() {
  const token = store.token;
  const account = captureAgentAccount(token);
  const view = workspace.sessionViewGeneration;
  const valid = () => alive && account.isCurrent();
  if (!token || !valid()) return;
  try {
    const session = await createAgentSession(token);
    if (!valid()) return;
    workspace.upsertSession(session);
    await writeCachedSessions(token, [...workspace.sessions]);
    if (!valid() || workspace.sessionViewGeneration !== view) return;
    await loadSession(token, session.session_id);
  } catch (error) {
    if (valid()) ElMessage.warning(`新建独立会话失败：${error instanceof Error ? error.message : "未知错误"}`);
  }
}

function setRenameInputRef(element: unknown) {
  renameInputRef.value = element instanceof HTMLInputElement ? element : null;
}

function setSessionRowRef(sessionId: string, element: unknown) {
  if (element instanceof HTMLElement) {
    sessionRowElements.set(sessionId, element);
  } else {
    sessionRowElements.delete(sessionId);
  }
}

function handleRenameOutsidePointerDown(event: PointerEvent) {
  const sessionId = editingSessionId.value;
  if (!sessionId) return;
  const target = event.target;
  if (target instanceof Element && target.closest(".session-inline-editor")) return;
  void submitRename(sessionId);
}

function startRename(session: AgentSessionSummary) {
  if (session.is_shared_demo) return;
  const version = ++renameGeneration;
  editingSessionId.value = session.session_id;
  editingTitle.value = session.title || session.preview || "";
  editingOriginalTitle.value = editingTitle.value.trim();
  void nextTick(() => {
    if (!alive || version !== renameGeneration || editingSessionId.value !== session.session_id) return;
    renameInputRef.value?.focus();
    renameInputRef.value?.select();
  });
}

function cancelRename() {
  renameGeneration += 1;
  editingSessionId.value = null;
  editingTitle.value = "";
  editingOriginalTitle.value = "";
}

function flashRenamedTitle(sessionId: string) {
  if (renamedTitleTimer !== null) {
    window.clearTimeout(renamedTitleTimer);
    renamedTitleTimer = null;
  }
  renamedSessionId.value = sessionId;
  renamedTitleTimer = window.setTimeout(() => {
    if (renamedSessionId.value === sessionId) renamedSessionId.value = null;
    renamedTitleTimer = null;
  }, 520);
}

function setSessionRowExiting(sessionId: string, exiting: boolean) {
  const next = new Set(exitingSessionIds.value);
  if (exiting) {
    next.add(sessionId);
  } else {
    next.delete(sessionId);
  }
  exitingSessionIds.value = next;
}

function isSessionRowExiting(sessionId: string) {
  return exitingSessionIds.value.has(sessionId);
}

function prepareSessionRowExitMotion(sessionId: string) {
  const element = sessionRowElements.get(sessionId);
  const measuredHeight = element ? Math.ceil(element.getBoundingClientRect().height) : 58;
  exitingSessionHeights.value = {
    ...exitingSessionHeights.value,
    [sessionId]: Math.max(1, measuredHeight),
  };
}

function clearSessionRowExitMotion(sessionId: string) {
  exitVersions.delete(sessionId);
  setSessionRowExiting(sessionId, false);
  const { [sessionId]: _removed, ...rest } = exitingSessionHeights.value;
  exitingSessionHeights.value = rest;
}

function sessionRowExitStyle(sessionId: string) {
  const height = exitingSessionHeights.value[sessionId];
  return height ? { "--session-row-motion-height": `${height}px` } : undefined;
}

function waitForSessionRowExitStartFrame() {
  return new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => resolve());
    });
  });
}

function waitForSessionRowExitMotion() {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, SESSION_ROW_EXIT_MOTION_MS);
  });
}

async function startSessionRowExit(sessionId: string, valid: () => boolean = () => alive) {
  if (!valid()) return null;
  if (isSessionRowExiting(sessionId)) return exitVersions.get(sessionId) ?? null;
  const version = ++exitSequence;
  exitVersions.set(sessionId, version);
  prepareSessionRowExitMotion(sessionId);
  await nextTick();
  if (!valid() || exitVersions.get(sessionId) !== version) return null;
  await waitForSessionRowExitStartFrame();
  if (!valid() || exitVersions.get(sessionId) !== version) return null;
  setSessionRowExiting(sessionId, true);
  return version;
}

function finishSessionRowExit(sessionId: string, version?: number) {
  if (!alive || (version !== undefined && exitVersions.get(sessionId) !== version)) return;
  clearSessionRowExitMotion(sessionId);
  if (filteredSessions.value.some((session) => session.session_id === sessionId)) {
    syncRenderedSessions(filteredSessions.value);
    return;
  }
  renderedSessions.value = renderedSessions.value.filter((session) => session.session_id !== sessionId);
}

function syncRenderedSessions(nextSessions: AgentSessionSummary[]) {
  const account = captureAgentAccount(store.token);
  const valid = () => alive && account.isCurrent();
  const nextIds = sessionIds(nextSessions);
  const currentSessions = renderedSessions.value;
  const retainExitingIds = new Set(exitingSessionIds.value);
  for (const session of currentSessions) {
    if (nextIds.has(session.session_id) && !pendingDeletes.has(session.session_id) && exitVersions.has(session.session_id)) {
      clearSessionRowExitMotion(session.session_id);
    }
  }
  const leavingSessions = currentSessions.filter(
    (session) => !nextIds.has(session.session_id) && !isSessionRowExiting(session.session_id),
  );
  for (const session of leavingSessions) {
    prepareSessionRowExitMotion(session.session_id);
    retainExitingIds.add(session.session_id);
  }
  renderedSessions.value = mergeRenderedSessions(renderedSessions.value, nextSessions, retainExitingIds);
  for (const session of leavingSessions) {
    void (async () => {
      const version = await startSessionRowExit(session.session_id, valid);
      if (version === null || !valid()) return;
      await waitForSessionRowExitMotion();
      if (valid()) finishSessionRowExit(session.session_id, version);
    })();
  }
}

async function submitRename(sessionId: string) {
  if (editingSessionId.value !== sessionId) return;
  const token = store.token;
  const account = captureAgentAccount(token);
  const editorVersion = renameGeneration;
  const valid = () => alive && account.isCurrent();
  const title = editingTitle.value.trim();
  if (!token || !title || !shouldSubmitSessionRename(title, editingOriginalTitle.value)) {
    cancelRename();
    return;
  }
  const key = `${sessionId}:${editorVersion}:${title}`;
  if (pendingRenameKeys.has(key) || !valid()) return;
  pendingRenameKeys.add(key);
  const requestVersion = ++renameRequestSequence;
  renameRequestVersions.set(sessionId, requestVersion);
  try {
    const renamed = await renameAgentSession(token, sessionId, title);
    if (!valid() || renameRequestVersions.get(sessionId) !== requestVersion) return;
    const current = workspace.sessions.find((session) => session.session_id === sessionId);
    if (!current) return;
    workspace.upsertSession(mergeRenamedSessionInPlace(current, renamed));
    await writeCachedSessions(token, [...workspace.sessions]);
    if (!valid() || renameRequestVersions.get(sessionId) !== requestVersion) return;
    if (editingSessionId.value === sessionId && renameGeneration === editorVersion && editingTitle.value.trim() === title) cancelRename();
    flashRenamedTitle(sessionId);
  } catch (error) {
    if (valid()) ElMessage.warning(`重命名失败：${error instanceof Error ? error.message : "未知错误"}`);
  } finally {
    pendingRenameKeys.delete(key);
    if (renameRequestVersions.get(sessionId) === requestVersion) renameRequestVersions.delete(sessionId);
  }
}

async function deleteSession(sessionId: string) {
  const token = store.token;
  const account = captureAgentAccount(token);
  const valid = () => alive && account.isCurrent();
  if (!token || !valid() || workspace.runningCountsBySession[sessionId] || workspace.checkingRunsBySession?.[sessionId] || isSessionRowExiting(sessionId) || pendingDeletes.has(sessionId)) return;
  const selectedSessionId = workspace.currentSessionId;
  const view = workspace.sessionViewGeneration;
  const visibleSessions = filteredSessions.value;
  const currentIndex = visibleSessions.findIndex((session) => session.session_id === sessionId);
  const nextSession = visibleSessions[currentIndex + 1] ?? visibleSessions[currentIndex - 1];
  pendingDeletes.add(sessionId);
  const version = await startSessionRowExit(sessionId, valid);
  if (!valid() || version === null) {
    if (account.isCurrent()) pendingDeletes.delete(sessionId);
    return;
  }
  try {
    await Promise.all([
      deleteAgentSession(token, sessionId),
      waitForSessionRowExitMotion(),
    ]);
    if (!valid()) return;
    const followSelection = selectedSessionId === sessionId && workspace.currentSessionId === selectedSessionId && workspace.sessionViewGeneration === view;
    workspace.removeSession(sessionId);
    const followView = workspace.sessionViewGeneration;
    finishSessionRowExit(sessionId, version);
    await removeCachedSession(token, sessionId);
    if (!valid()) return;
    await agentDraftCache.clearSession(token, sessionId);
    if (!valid()) return;
    await writeCachedSessions(token, [...workspace.sessions]);
    if (!valid() || !followSelection || workspace.currentSessionId !== null || workspace.sessionViewGeneration !== followView) return;
    if (nextSession?.session_id && workspace.sessions.some(session => session.session_id === nextSession.session_id)) {
      await loadSession(token, nextSession.session_id);
    } else {
      await newSession();
    }
  } catch (error) {
    if (valid()) {
      if (exitVersions.get(sessionId) === version) clearSessionRowExitMotion(sessionId);
      ElMessage.warning(`删除会话失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  } finally {
    if (account.isCurrent()) pendingDeletes.delete(sessionId);
  }
}

watch(
  filteredSessions,
  (nextSessions) => {
    syncRenderedSessions(nextSessions);
  },
  { immediate: true },
);

watch(currentProblemId, (problemId) => {
  if (problemId === undefined) return;
  expandedFolderIds.value = new Set([...expandedFolderIds.value, problemId]);
}, { immediate: true });

watch(() => workspace.requestedProblemFolderId, (problemId) => {
  if (problemId === null) return;
  expandedFolderIds.value = new Set([...expandedFolderIds.value, problemId]);
  workspace.requestProblemFolderExpansion(null);
});

watch(() => store.token, () => {
  cancelRename();
  if (renamedTitleTimer !== null) window.clearTimeout(renamedTitleTimer);
  renamedTitleTimer = null;
  renamedSessionId.value = null;
  pendingDeletes.clear();
  renameRequestVersions.clear();
  pendingRenameKeys.clear();
  for (const sessionId of [...exitVersions.keys()]) clearSessionRowExitMotion(sessionId);
  renderedSessions.value = [];
  query.value = "";
  closeSearch();
});

onMounted(async () => {
  document.addEventListener("pointerdown", handleRenameOutsidePointerDown, true);
  document.addEventListener("pointerdown", handleSearchOutsidePointerDown, true);
});

onBeforeUnmount(() => {
  alive = false;
  renameGeneration += 1;
  document.removeEventListener("pointerdown", handleRenameOutsidePointerDown, true);
  document.removeEventListener("pointerdown", handleSearchOutsidePointerDown, true);
  if (renamedTitleTimer !== null) window.clearTimeout(renamedTitleTimer);
});
</script>
