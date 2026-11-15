<template>
  <Transition name="agent-dock-preview">
    <div
      v-if="rightDockPreviewVisible"
      class="agent-dock-preview-frame agent-showcase-root"
      aria-hidden="true"
    />
  </Transition>
  <section
    v-show="motionPhase !== 'idle'"
    class="agent-workspace-shell agent-showcase-root"
    :class="[
      shellMotionClass,
      `agent-font-size-${preferences.fontSize}`,
      {
        'is-session-rail-collapsed': layout.sessionRailCollapsed,
        'is-context-panel-collapsed': layout.contextPanelCollapsed,
        'is-split-mode': layout.splitMode,
        'is-split-resizing': splitResizeActive,
        'is-window-dragging': windowDragActive,
        'is-window-resizing': windowResizeActive,
        'is-viewport-scaling': viewportScaleActive,
        'is-right-dock-preview-ready': rightDockPreviewVisible,
        'is-corner-close-armed': cornerCloseArmed,
        'is-split-undock-ready': splitUndockReady,
        'is-split-undock-settling': splitUndockSettling,
        'is-narrow-controls': controlsNarrow,
        'is-interacting':
          windowDragActive || windowResizeActive || splitResizeActive,
      },
    ]"
    :style="workspaceShellStyle"
    aria-label="Accoding Agent 新版工作台"
    @animationend.self="onShellAnimationEnd"
  >
    <div
      class="agent-window-drag-zone"
      aria-hidden="true"
      @pointerdown="startDrag"
    />
    <div
      v-if="layout.splitMode"
      class="agent-split-resizer"
      aria-hidden="true"
      @pointerdown="startSplitResize"
    />

    <div
      class="agent-floating-controls agent-left-controls"
      :class="{ 'has-new': layout.sessionRailCollapsed && layout.sessionRailView === 'sessions' }"
    >
      <button
        class="agent-close-button"
        type="button"
        aria-label="关闭"
        @click.stop="requestClose"
      >
        <AgentIcon name="x" :size="15" />
      </button>
      <button
        class="agent-header-button"
        type="button"
        :aria-label="layout.sessionRailCollapsed ? '展开会话栏' : '收起会话栏'"
        @click.stop="toggleSessionRail"
      >
        <AgentIcon name="sidebar" :size="17" />
      </button>
      <Transition name="new-session-control-motion">
        <button
          v-if="layout.sessionRailCollapsed && layout.sessionRailView === 'sessions'"
          class="agent-header-button agent-floating-new"
          type="button"
          aria-label="新建会话"
          :disabled="Boolean(workspace.runningCountsBySession.draft)"
          @click.stop="newSession"
        >
          <AgentIcon name="plus" :size="17" />
        </button>
      </Transition>
    </div>

    <header class="agent-floating-topbar top-bar">
      <span class="top-rail-button" aria-hidden="true">
        <AgentIcon name="sparkle" :size="17" />
      </span>
      <div class="top-title-inline">
        <p>{{ topSessionTitle }}</p>
        <span v-if="topProblemTitle" :title="topProblemTitle">{{
          topProblemTitle
        }}</span>
        <span v-if="isEnglishDemo && demoAgentReady === false" title="The model connection is being prepared">Agent connection pending</span>
      </div>
    </header>

    <div class="agent-floating-controls agent-right-controls">
      <div class="top-actions">
        <div
          ref="searchRootRef"
          class="top-message-search-wrap"
          :class="{ 'is-search-open': searchExpanded }"
          @click.stop="openSearch"
        >
          <label class="top-message-search" aria-label="搜索当前对话">
            <AgentIcon name="search" :size="15" />
            <input
              v-model="searchQuery"
              placeholder="在当前对话中搜索"
              type="search"
              @focus="openSearch"
              @keydown.esc.stop="closeSearch"
              @keydown.enter.prevent="jumpToSearchResult(0)"
            />
          </label>
          <div
            v-if="searchQuery.trim() && searchExpanded"
            class="message-search-results"
          >
            <button
              v-for="(result, index) in searchResults"
              :key="result.message.id"
              type="button"
              @click="jumpToSearchResult(index)"
            >
              <span>{{ result.message.role === "user" ? "我" : "Agent" }}</span>
              <strong v-html="highlightSnippet(result.snippet)" />
            </button>
            <button v-if="searchResults.length === 0" type="button" disabled>
              <span>无</span>
              <strong>没有匹配结果</strong>
            </button>
          </div>
        </div>
      </div>
      <Transition name="settings-control-motion">
        <button
          v-if="!controlsNarrow"
          class="agent-header-button agent-right-placeholder"
          type="button"
          aria-label="更多设置"
          @click.stop="openSettings"
        >
          <AgentIcon name="right-placeholder" :size="17" />
        </button>
      </Transition>
      <button
        class="agent-header-button agent-right-toggle"
        type="button"
        :aria-label="
          layout.contextPanelCollapsed ? '展开题目白板' : '收起题目白板'
        "
        @click.stop="toggleContextPanel"
      >
        <AgentIcon name="stack" :size="17" />
      </button>
      <button
        class="agent-header-button agent-right-extra"
        type="button"
        :aria-label="layout.splitMode ? '恢复浮窗' : '分屏模式'"
        @click.stop="toggleSplitMode"
      >
        <Transition name="agent-right-extra-icon" mode="out-in">
          <AgentIcon
            :key="layout.splitMode ? 'split' : 'float'"
            :name="layout.splitMode ? 'right-split' : 'right-utility'"
            :size="17"
          />
        </Transition>
      </button>
    </div>

    <div
      class="agent-workspace-body showcase-shell"
      :class="{
        'session-rail-collapsed': layout.sessionRailCollapsed,
        'context-panel-collapsed': layout.contextPanelCollapsed,
        'compact-overlay-mode': compactOverlayMode,
        'session-rail-overlay': sessionRailOverlay,
        'session-overlay-open':
          sessionRailOverlay && !layout.sessionRailCollapsed,
        'context-panel-overlay': contextPanelOverlay,
        'context-overlay-open':
          contextPanelOverlay && !layout.contextPanelCollapsed,
      }"
    >
      <div class="agent-control-shadows" aria-hidden="true">
        <i
          class="agent-control-shadow is-left"
          :class="{ 'has-new': layout.sessionRailCollapsed && layout.sessionRailView === 'sessions' }"
        />
        <i class="agent-control-shadow is-right" />
      </div>
        <button
          v-if="sessionRailOverlay && !layout.sessionRailCollapsed"
          class="agent-session-overlay-scrim agent-side-overlay-scrim"
          type="button"
          aria-label="收起会话栏"
          @click="layout.setSessionRailCollapsed(true, false)"
        />
        <button
          v-if="contextPanelOverlay && !layout.contextPanelCollapsed"
          class="agent-context-overlay-scrim agent-side-overlay-scrim"
          type="button"
          aria-label="收起题目白板"
          @click="layout.setContextPanelCollapsed(true, false)"
        />
        <AgentSessionRail />

        <AgentChatPanel ref="chatPanelRef" />

        <AgentContextPanel @trace="traceMessage" />
    </div>

    <Transition name="agent-settings-motion">
      <AgentSettingsPanel v-if="settingsOpen" @close="closeSettings" />
    </Transition>

    <div
      class="agent-resize-handle agent-resize-north"
      @pointerdown="startResize($event, 'n')"
    />
    <div
      class="agent-resize-handle agent-resize-east"
      @pointerdown="startResize($event, 'e')"
    />
    <div
      class="agent-resize-handle agent-resize-south"
      @pointerdown="startResize($event, 's')"
    />
    <div
      class="agent-resize-handle agent-resize-west"
      @pointerdown="startResize($event, 'w')"
    />
    <div
      class="agent-resize-handle agent-resize-nw"
      @pointerdown="startResize($event, 'nw')"
    />
    <div
      class="agent-resize-handle agent-resize-ne"
      @pointerdown="startResize($event, 'ne')"
    />
    <div
      class="agent-resize-handle agent-resize-sw"
      @pointerdown="startResize($event, 'sw')"
    />
    <div
      class="agent-resize-handle agent-resize-se"
      @pointerdown="startResize($event, 'se')"
    />
    <AgentAriaTooltip v-if="motionPhase !== 'idle'" />
  </section>
</template>

<script setup lang="ts">
import {
  computed,
  nextTick,
  ref,
  onBeforeUnmount,
  onMounted,
  watch,
} from "vue";
import AgentAriaTooltip from "./AgentAriaTooltip.vue";
import AgentIcon from "./AgentIcon.vue";
import AgentChatPanel from "./AgentChatPanel.vue";
import AgentContextPanel from "./AgentContextPanel.vue";
import AgentSessionRail from "./AgentSessionRail.vue";
import AgentSettingsPanel from "./AgentSettingsPanel.vue";
import { useAgentRunRecovery } from "../composables/useAgentRunRecovery";
import { useAgentWorkspaceBootstrap } from "../composables/useAgentWorkspaceBootstrap";
import { useAgentLayoutStore } from "../stores/agentLayout";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { useAgentPreferencesStore } from "../stores/agentPreferences";
import { acquireDragSelectionLock } from "../utils/dragSelectionLock";
import { useAccodingStore } from "@/stores";
import { isEnglishDemo } from "@/en-demo/mode";
import { translateDemoText } from "@/en-demo/englishDom";

type AgentWorkspaceMotionPhase = "idle" | "opening" | "open" | "closing";

const props = defineProps<{
  motionPhase: AgentWorkspaceMotionPhase;
  orbAnchorStyle: Record<string, string>;
}>();
const demoAgentReady = ref<boolean | null>(null);
const emit = defineEmits<{
  requestClose: [];
  motionComplete: [phase: AgentWorkspaceMotionPhase];
}>();

function onShellAnimationEnd(event: AnimationEvent) {
  // Ignore the rim pseudo-element and unrelated nested animations.
  if (event.pseudoElement) return;
  if (event.animationName === "agent-orb-shell-open" && props.motionPhase === "opening") {
    emit("motionComplete", "opening");
  } else if (event.animationName === "agent-orb-shell-close" && props.motionPhase === "closing") {
    emit("motionComplete", "closing");
  }
}

const layout = useAgentLayoutStore();
const accoding = useAccodingStore();
const workspace = useAgentWorkspaceStore();
const preferences = useAgentPreferencesStore();
const { bootstrapAgentWorkspace, newSession: startNewSession } =
  useAgentWorkspaceBootstrap();
const { stopPolling } = useAgentRunRecovery();
const chatPanelRef = ref<InstanceType<typeof AgentChatPanel> | null>(null);
const searchRootRef = ref<HTMLElement | null>(null);
const searchPinnedOpen = ref(false);
const searchQuery = ref("");
const settingsOpen = ref(false);
const viewportWidth = ref(
  typeof window === "undefined" ? 1280 : window.innerWidth,
);
const windowDragActive = ref(false);
const windowResizeActive = ref(false);
const splitResizeActive = ref(false);
const viewportScaleActive = ref(false);
const rightDockPreviewVisible = ref(false);
const cornerCloseArmed = ref(false);
const cornerCloseScale = ref(1);
const splitUndockArmed = ref(false);
const splitUndockReady = ref(false);
const splitUndockSettling = ref(false);
const splitUndockSettleOffset = ref({ x: 0, y: 0 });
const splitUndockSettleScale = ref({ x: 1, y: 1 });
const splitUndockOffset = ref({ x: 0, y: 0 });
let viewportScaleTimer: number | null = null;
let splitUndockSettleTimer: number | null = null;
let workspaceBootstrapTimer: number | null = null;
let workspaceBootstrapStarted = false;

const VIEWPORT_SCALE_RESTORE_DELAY_MS = 220;
const SPLIT_UNDOCK_SETTLE_MS = 260;
const WORKSPACE_BOOTSTRAP_AFTER_ORB_MS = 0;
const FLOAT_EDGE_TRIGGER_PX = 22;
const FLOAT_DRAG_DIRECTION_PX = 28;
const SPLIT_UNDOCK_TRIGGER_PX = 76;
const MIN_SPLIT_AGENT_WIDTH = 220;
const MIN_SPLIT_PAGE_WIDTH = 360;
const COMPACT_SIDE_DRAWER_WIDTH = 900;
const CONTEXT_PANEL_OVERLAY_MIDDLE_WIDTH = 360;
const SESSION_RAIL_WIDTH = 250;
const CONTEXT_PANEL_GRID_WIDTH = 330;

const currentSession = computed(() =>
  workspace.sessions.find(
    (session) => session.session_id === workspace.currentSessionId,
  ),
);
const topSessionTitle = computed(() => {
  if (currentSession.value?.is_shared_demo) {
    return normalizeTopbarTitle(currentSession.value.problem_title?.trim() || "Shortest Path in an Unweighted Graph");
  }
  const sessionTitle =
    currentSession.value?.title || currentSession.value?.preview;
  const title = sessionTitle || (isEnglishDemo ? "New session" : "新会话");
  return normalizeTopbarTitle(isEnglishDemo ? translateDemoText(title) : title);
});
const topProblemTitle = computed(() => {
  const workspaceTitle =
    workspace.selectedWorkspace?.problem_title ||
    workspace.selectedWorkspace?.title;
  return normalizeTopbarTitle(workspaceTitle || "");
});
const searchResults = computed(() => {
  const needle = searchQuery.value.trim().toLowerCase();
  if (!needle) return [];
  return workspace.currentMessages
    .map((message) => {
      const content = message.content || "";
      const index = content.toLowerCase().indexOf(needle);
      if (index < 0) return null;
      return {
        message,
        snippet: buildSnippet(content, index, needle.length),
      };
    })
    .filter(
      (
        item,
      ): item is {
        message: (typeof workspace.currentMessages)[number];
        snippet: string;
      } => item !== null,
    )
    .slice(0, 12);
});
const searchExpanded = computed(
  () => searchPinnedOpen.value || Boolean(searchQuery.value.trim()),
);
const splitAgentWidth = computed(() => {
  const maxAgentWidth = Math.max(
    MIN_SPLIT_AGENT_WIDTH,
    viewportWidth.value - MIN_SPLIT_PAGE_WIDTH,
  );
  return Math.round(
    clampNumber(
      viewportWidth.value * layout.splitAgentRatio,
      MIN_SPLIT_AGENT_WIDTH,
      maxAgentWidth,
    ),
  );
});
const splitLeftWidth = computed(() =>
  Math.max(0, viewportWidth.value - splitAgentWidth.value),
);
const floatingWorkspaceWidth = computed(() =>
  Math.min(layout.width, Math.max(0, viewportWidth.value - 16)),
);
const effectiveWorkspaceWidth = computed(() =>
  layout.splitMode ? splitAgentWidth.value : floatingWorkspaceWidth.value,
);
const controlsNarrow = computed(() => effectiveWorkspaceWidth.value <= 569);
const compactOverlayMode = computed(
  () =>
    viewportWidth.value <= COMPACT_SIDE_DRAWER_WIDTH ||
    effectiveWorkspaceWidth.value < COMPACT_SIDE_DRAWER_WIDTH,
);
const sessionRailOverlay = computed(() => compactOverlayMode.value);
const sidePanelsExclusive = computed(
  () => compactOverlayMode.value ||
    effectiveWorkspaceWidth.value - SESSION_RAIL_WIDTH - CONTEXT_PANEL_GRID_WIDTH <
      CONTEXT_PANEL_OVERLAY_MIDDLE_WIDTH,
);
const middleWidthWithContextPanel = computed(() => {
  const leftColumnWidth = layout.sessionRailCollapsed ? 0 : SESSION_RAIL_WIDTH;
  return (
    effectiveWorkspaceWidth.value - leftColumnWidth - CONTEXT_PANEL_GRID_WIDTH
  );
});
const contextPanelOverlay = computed(
  () =>
    compactOverlayMode.value ||
    middleWidthWithContextPanel.value < CONTEXT_PANEL_OVERLAY_MIDDLE_WIDTH,
);
const shellMotionClass = computed(() => `is-orb-${props.motionPhase}`);
const workspaceShellStyle = computed(() => {
  if (layout.splitMode) {
    const offset = splitUndockOffset.value;
    const left = splitLeftWidth.value + offset.x;
    const top = offset.y;
    return {
      width: `${splitAgentWidth.value}px`,
      height: "100dvh",
      "--agent-shell-transform": `translate3d(${left}px, ${top}px, 0)`,
      "--agent-shell-left": `${left}px`,
      "--agent-shell-top": `${top}px`,
      "--agent-split-left-width": `${splitLeftWidth.value}px`,
      "--agent-split-agent-width": `${splitAgentWidth.value}px`,
      ...props.orbAnchorStyle,
    };
  }
  const floatingLeft = splitUndockSettling.value
    ? layout.left + splitUndockSettleOffset.value.x
    : layout.left;
  const floatingTop = splitUndockSettling.value
    ? layout.top + splitUndockSettleOffset.value.y
    : layout.top;
  const floatingTransform = splitUndockSettling.value
    ? `translate3d(${floatingLeft}px, ${floatingTop}px, 0) scale(${splitUndockSettleScale.value.x}, ${splitUndockSettleScale.value.y})`
    : `translate3d(${floatingLeft}px, ${floatingTop}px, 0)`;
  return {
    width: `${layout.width}px`,
    height: `${layout.height}px`,
    "--agent-shell-transform": floatingTransform,
    "--agent-shell-left": `${floatingLeft}px`,
    "--agent-shell-top": `${floatingTop}px`,
    "--agent-corner-close-scale": cornerCloseScale.value.toFixed(3),
    ...props.orbAnchorStyle,
  };
});

let dragStart: {
  pointerId: number;
  x: number;
  y: number;
  left: number;
  top: number;
  mode: "floating" | "split";
} | null = null;

let resizeStart: {
  pointerId: number;
  x: number;
  y: number;
  left: number;
  top: number;
  width: number;
  height: number;
  direction: ResizeDirection;
} | null = null;

let splitResizeStart: {
  pointerId: number;
} | null = null;

type ResizeDirection = "n" | "e" | "s" | "w" | "nw" | "ne" | "sw" | "se";
let releaseDragSelectionLock: (() => void) | null = null;
let pendingPointerEvent: PointerEvent | null = null;
let pendingPointerFrame: number | null = null;

function lockPageSelection() {
  releaseDragSelectionLock?.();
  releaseDragSelectionLock = acquireDragSelectionLock();
}

function unlockPageSelection() {
  releaseDragSelectionLock?.();
  releaseDragSelectionLock = null;
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function normalizeTopbarTitle(value: string) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

function requestClose() {
  emit("requestClose");
}

function openSettings() {
  closeSearch();
  settingsOpen.value = true;
}

function closeSettings() {
  settingsOpen.value = false;
}

function startDrag(event: PointerEvent) {
  const target = event.target as HTMLElement | null;
  if (target?.closest("button,input,textarea,label,a")) return;
  event.preventDefault();
  if (event.currentTarget instanceof HTMLElement) {
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  dragStart = {
    pointerId: event.pointerId,
    x: event.clientX,
    y: event.clientY,
    left: layout.splitMode ? splitLeftWidth.value : layout.left,
    top: layout.splitMode ? 0 : layout.top,
    mode: layout.splitMode ? "split" : "floating",
  };
  resetDragGestureState();
  windowDragActive.value = true;
  lockPageSelection();
  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", stopPointerTracking, { once: true });
  window.addEventListener("pointercancel", stopPointerTracking, { once: true });
}

function startResize(event: PointerEvent, direction: ResizeDirection) {
  if (layout.splitMode) return;
  event.preventDefault();
  event.stopPropagation();
  if (event.currentTarget instanceof HTMLElement) {
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  resizeStart = {
    pointerId: event.pointerId,
    x: event.clientX,
    y: event.clientY,
    left: layout.left,
    top: layout.top,
    width: layout.width,
    height: layout.height,
    direction,
  };
  windowResizeActive.value = true;
  lockPageSelection();
  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", stopPointerTracking, { once: true });
  window.addEventListener("pointercancel", stopPointerTracking, { once: true });
}

function startSplitResize(event: PointerEvent) {
  event.preventDefault();
  event.stopPropagation();
  if (event.currentTarget instanceof HTMLElement) {
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  splitResizeStart = {
    pointerId: event.pointerId,
  };
  splitResizeActive.value = true;
  lockPageSelection();
  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", stopPointerTracking, { once: true });
  window.addEventListener("pointercancel", stopPointerTracking, { once: true });
}

function handlePointerMove(event: PointerEvent) {
  event.preventDefault();
  pendingPointerEvent = event;
  if (pendingPointerFrame !== null) return;
  pendingPointerFrame = window.requestAnimationFrame(flushPointerFrame);
}

function flushPointerFrame() {
  pendingPointerFrame = null;
  const event = pendingPointerEvent;
  pendingPointerEvent = null;
  if (!event) return;

  if (splitResizeStart && event.pointerId === splitResizeStart.pointerId) {
    layout.setSplitAgentRatio(
      (viewportWidth.value - event.clientX) / viewportWidth.value,
    );
    return;
  }

  if (dragStart && event.pointerId === dragStart.pointerId) {
    const deltaX = event.clientX - dragStart.x;
    const deltaY = event.clientY - dragStart.y;
    if (dragStart.mode === "split") {
      updateSplitUndockGesture(deltaX, deltaY);
      return;
    }
    const rawLeft = dragStart.left + deltaX;
    const rawTop = dragStart.top + deltaY;
    layout.setPosition(rawLeft, rawTop);
    updateFloatingDragGesture(rawLeft, rawTop, deltaX, deltaY);
  }

  if (resizeStart && event.pointerId === resizeStart.pointerId) {
    const deltaX = event.clientX - resizeStart.x;
    const deltaY = event.clientY - resizeStart.y;
    const movesWest = resizeStart.direction.includes("w");
    const movesEast = resizeStart.direction.includes("e");
    const movesNorth = resizeStart.direction.includes("n");
    const movesSouth = resizeStart.direction.includes("s");
    const nextLeft = movesWest ? resizeStart.left + deltaX : resizeStart.left;
    const nextTop = movesNorth ? resizeStart.top + deltaY : resizeStart.top;
    const nextWidth =
      resizeStart.width + (movesEast ? deltaX : 0) - (movesWest ? deltaX : 0);
    const nextHeight =
      resizeStart.height +
      (movesSouth ? deltaY : 0) -
      (movesNorth ? deltaY : 0);
    layout.setRect(nextLeft, nextTop, nextWidth, nextHeight);
  }
}

function clearRightDockPreview() {
  rightDockPreviewVisible.value = false;
}

function clearSplitUndockGesture() {
  splitUndockArmed.value = false;
  splitUndockReady.value = false;
}

function resetDragGestureState() {
  clearRightDockPreview();
  clearSplitUndockGesture();
  cornerCloseArmed.value = false;
  cornerCloseScale.value = 1;
  splitUndockOffset.value = { x: 0, y: 0 };
}

function updateFloatingDragGesture(
  rawLeft: number,
  rawTop: number,
  deltaX: number,
  deltaY: number,
) {
  const rawRight = rawLeft + layout.width;
  const rawBottom = rawTop + layout.height;
  const atRightEdge = rawRight >= window.innerWidth - FLOAT_EDGE_TRIGGER_PX;
  const atBottomEdge = rawBottom >= window.innerHeight - FLOAT_EDGE_TRIGGER_PX;
  const movingRight = deltaX >= FLOAT_DRAG_DIRECTION_PX;
  const movingDown = deltaY >= FLOAT_DRAG_DIRECTION_PX;
  const closeCandidate =
    atRightEdge && atBottomEdge && movingRight && movingDown;
  const bottomPressure =
    Math.max(0, deltaY - FLOAT_DRAG_DIRECTION_PX) +
    Math.max(0, rawBottom - window.innerHeight);
  const closeProgress = clampNumber(bottomPressure / 360, 0, 1);
  const closeEase = Math.pow(closeProgress, 0.58);

  cornerCloseArmed.value = closeCandidate;
  cornerCloseScale.value = closeCandidate ? 1 - closeEase * 0.11 : 1;
  if (closeCandidate) {
    clearRightDockPreview();
    return;
  }

  rightDockPreviewVisible.value = atRightEdge && movingRight && !closeCandidate;
}

function updateSplitUndockGesture(deltaX: number, deltaY: number) {
  const offsetY = Math.max(0, deltaY);
  splitUndockOffset.value = {
    x: deltaX,
    y: offsetY,
  };

  splitUndockReady.value = offsetY >= SPLIT_UNDOCK_TRIGGER_PX;
  splitUndockArmed.value = splitUndockReady.value;
}

function stopPointerTracking(event?: PointerEvent) {
  if (pendingPointerFrame !== null) {
    window.cancelAnimationFrame(pendingPointerFrame);
    pendingPointerFrame = null;
    flushPointerFrame();
  }
  const canceled = event?.type === "pointercancel";
  const shouldDockRight =
    !canceled &&
    dragStart?.mode === "floating" &&
    rightDockPreviewVisible.value;
  const shouldCloseFromCorner =
    !canceled && dragStart?.mode === "floating" && cornerCloseArmed.value;
  const shouldUndockSplit =
    !canceled && dragStart?.mode === "split" && splitUndockReady.value;
  const releaseLeft = dragStart
    ? dragStart.left + splitUndockOffset.value.x
    : layout.left;
  const releaseTop = dragStart
    ? dragStart.top + splitUndockOffset.value.y
    : layout.top;
  pendingPointerEvent = null;
  dragStart = null;
  resizeStart = null;
  splitResizeStart = null;
  windowDragActive.value = false;
  windowResizeActive.value = false;
  splitResizeActive.value = false;
  resetDragGestureState();
  unlockPageSelection();
  window.removeEventListener("pointermove", handlePointerMove);
  window.removeEventListener("pointerup", stopPointerTracking);
  window.removeEventListener("pointercancel", stopPointerTracking);

  if (shouldCloseFromCorner) {
    requestClose();
  } else if (shouldDockRight) {
    layout.setSplitAgentRatio(0.5);
    layout.setSplitMode(true);
  } else if (shouldUndockSplit) {
    settleSplitUndock(releaseLeft, releaseTop);
  }
}

function settleSplitUndock(releaseLeft: number, releaseTop: number) {
  if (splitUndockSettleTimer !== null) {
    window.clearTimeout(splitUndockSettleTimer);
  }
  const sourceWidth = splitAgentWidth.value;
  const sourceHeight = window.innerHeight;
  const targetWidth = layout.width;
  const targetHeight = layout.height;
  layout.setRect(releaseLeft, releaseTop, targetWidth, targetHeight);
  const settledLeft = layout.left;
  const settledTop = layout.top;
  const settledWidth = layout.width;
  const settledHeight = layout.height;
  splitUndockSettleOffset.value = {
    x: releaseLeft - settledLeft,
    y: releaseTop - settledTop,
  };
  splitUndockSettleScale.value = {
    x: sourceWidth / settledWidth,
    y: sourceHeight / settledHeight,
  };
  splitUndockSettling.value = true;
  layout.setSplitMode(false);
  window.requestAnimationFrame(() => {
    splitUndockSettleOffset.value = { x: 0, y: 0 };
    splitUndockSettleScale.value = { x: 1, y: 1 };
  });
  splitUndockSettleTimer = window.setTimeout(() => {
    splitUndockSettling.value = false;
    splitUndockSettleOffset.value = { x: 0, y: 0 };
    splitUndockSettleScale.value = { x: 1, y: 1 };
    splitUndockSettleTimer = null;
  }, SPLIT_UNDOCK_SETTLE_MS);
}

function syncSplitBodyClass() {
  if (layout.splitMode && layout.isOpen) {
    document.body.classList.add("agent-workspace-split");
    document.body.classList.toggle(
      "agent-workspace-split-resizing",
      splitResizeActive.value,
    );
    document.body.style.setProperty(
      "--agent-split-left-width",
      `${splitLeftWidth.value}px`,
    );
    document.body.style.setProperty(
      "--agent-split-agent-width",
      `${splitAgentWidth.value}px`,
    );
  } else {
    clearSplitBodyClass();
  }
}

function handleWindowResize() {
  viewportWidth.value = window.innerWidth;
  layout.applyResponsivePanels();
}

function clearViewportScaleTimer() {
  if (viewportScaleTimer === null) return;
  window.clearTimeout(viewportScaleTimer);
  viewportScaleTimer = null;
}

function markViewportScaleActive() {
  viewportScaleActive.value = true;
  clearViewportScaleTimer();
  viewportScaleTimer = window.setTimeout(() => {
    viewportScaleActive.value = false;
    viewportScaleTimer = null;
  }, VIEWPORT_SCALE_RESTORE_DELAY_MS);
}

function handleViewportScaleWheel(event: WheelEvent) {
  if (!event.ctrlKey) return;
  markViewportScaleActive();
}

function bindViewportScaleListeners() {
  window.visualViewport?.addEventListener("resize", markViewportScaleActive, {
    passive: true,
  });
  window.visualViewport?.addEventListener("scroll", markViewportScaleActive, {
    passive: true,
  });
  window.addEventListener("wheel", handleViewportScaleWheel, { passive: true });
  window.addEventListener("gesturestart", markViewportScaleActive, {
    passive: true,
  } as AddEventListenerOptions);
  window.addEventListener("gesturechange", markViewportScaleActive, {
    passive: true,
  } as AddEventListenerOptions);
  window.addEventListener("gestureend", markViewportScaleActive, {
    passive: true,
  } as AddEventListenerOptions);
}

function unbindViewportScaleListeners() {
  window.visualViewport?.removeEventListener("resize", markViewportScaleActive);
  window.visualViewport?.removeEventListener("scroll", markViewportScaleActive);
  window.removeEventListener("wheel", handleViewportScaleWheel);
  window.removeEventListener("gesturestart", markViewportScaleActive);
  window.removeEventListener("gesturechange", markViewportScaleActive);
  window.removeEventListener("gestureend", markViewportScaleActive);
  clearViewportScaleTimer();
  viewportScaleActive.value = false;
}

function clearSplitBodyClass() {
  document.body.classList.remove("agent-workspace-split");
  document.body.classList.remove("agent-workspace-split-resizing");
  document.body.style.removeProperty("--agent-split-left-width");
  document.body.style.removeProperty("--agent-split-agent-width");
}

function traceMessage(messageId: number | null) {
  if (messageId === null) return;
  chatPanelRef.value?.scrollToMessage(messageId);
}

function buildSnippet(content: string, start: number, length: number) {
  const before = Math.max(0, start - 26);
  const after = Math.min(content.length, start + length + 34);
  const prefix = before > 0 ? "..." : "";
  const suffix = after < content.length ? "..." : "";
  return `${prefix}${content.slice(before, after)}${suffix}`;
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function highlightSnippet(snippet: string) {
  const needle = searchQuery.value.trim();
  if (!needle) return escapeHtml(snippet);
  const lowerSnippet = snippet.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  const index = lowerSnippet.indexOf(lowerNeedle);
  if (index < 0) return escapeHtml(snippet);
  return [
    escapeHtml(snippet.slice(0, index)),
    `<mark>${escapeHtml(snippet.slice(index, index + needle.length))}</mark>`,
    escapeHtml(snippet.slice(index + needle.length)),
  ].join("");
}

function handleSearchOutsidePointerDown(event: PointerEvent) {
  if (searchRootRef.value?.contains(event.target as Node | null)) return;
  closeSearch();
}

function openSearch() {
  if (searchPinnedOpen.value) return;
  searchPinnedOpen.value = true;
  window.addEventListener("pointerdown", handleSearchOutsidePointerDown);
}

function closeSearch() {
  searchPinnedOpen.value = false;
  window.removeEventListener("pointerdown", handleSearchOutsidePointerDown);
}

async function jumpToSearchResult(index: number) {
  const target = searchResults.value[index]?.message;
  if (!target) return;
  await nextTick();
  await chatPanelRef.value?.scrollToMessage(target.id);
}

function newSession() {
  startNewSession(accoding.token);
}

function toggleSessionRail() {
  const opening = layout.sessionRailCollapsed;
  if (sidePanelsExclusive.value && opening) {
    layout.setContextPanelCollapsed(true, false);
  }
  layout.setSessionRailCollapsed(!layout.sessionRailCollapsed, false);
}

function toggleContextPanel() {
  const opening = layout.contextPanelCollapsed;
  if (sidePanelsExclusive.value && opening) {
    layout.setSessionRailCollapsed(true, false);
  }
  layout.setContextPanelCollapsed(!layout.contextPanelCollapsed, false);
}

function toggleSplitMode() {
  layout.toggleSplitMode();
}

function clearWorkspaceBootstrapTimer() {
  if (workspaceBootstrapTimer === null) return;
  window.clearTimeout(workspaceBootstrapTimer);
  workspaceBootstrapTimer = null;
}

function scheduleWorkspaceBootstrap() {
  if (workspaceBootstrapStarted || (props.motionPhase !== "opening" && props.motionPhase !== "open")) return;
  clearWorkspaceBootstrapTimer();
  workspaceBootstrapTimer = window.setTimeout(() => {
    workspaceBootstrapTimer = null;
    if (workspaceBootstrapStarted || (props.motionPhase !== "opening" && props.motionPhase !== "open")) return;
    workspaceBootstrapStarted = true;
    void bootstrapAgentWorkspace(accoding.token).catch(() => {
      workspaceBootstrapStarted = false;
    });
  }, WORKSPACE_BOOTSTRAP_AFTER_ORB_MS);
}

onMounted(() => {
  if (isEnglishDemo) {
    void fetch("/en-demo/api/en-demo/status")
      .then(response => response.ok ? response.json() : Promise.reject(new Error("Demo status unavailable")))
      .then((status: { agent_ready?: boolean }) => { demoAgentReady.value = status.agent_ready === true; })
      .catch(() => { demoAgentReady.value = false; });
  }
  window.addEventListener("resize", handleWindowResize);
  bindViewportScaleListeners();
  handleWindowResize();
  layout.applyResponsivePanels();
  syncSplitBodyClass();
});

watch(
  () => props.motionPhase,
  () => {
    if (props.motionPhase === "idle") {
      closeSearch();
      closeSettings();
      stopPointerTracking();
    }
    scheduleWorkspaceBootstrap();
  },
  { immediate: true },
);

watch(
  () => (layout.splitMode ? splitAgentWidth.value : layout.width),
  () => {
    layout.applyResponsivePanels();
  },
);

watch(
  [
    () => layout.splitMode,
    () => layout.isOpen,
    splitLeftWidth,
    splitAgentWidth,
    splitResizeActive,
  ],
  () => {
    syncSplitBodyClass();
  },
  { immediate: true },
);

watch(
  () => layout.splitMode,
  () => {
    closeSearch();
  },
);

watch(
  controlsNarrow,
  (isNarrow) => {
    if (!isNarrow) return;
    closeSearch();
    closeSettings();
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  stopPointerTracking();
  closeSearch();
  window.removeEventListener("resize", handleWindowResize);
  unbindViewportScaleListeners();
  clearSplitBodyClass();
  if (splitUndockSettleTimer !== null) {
    window.clearTimeout(splitUndockSettleTimer);
  }
  clearWorkspaceBootstrapTimer();
});
</script>
