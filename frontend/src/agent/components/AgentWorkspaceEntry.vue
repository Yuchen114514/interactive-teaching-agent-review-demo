<template>
  <Teleport to="body">
    <div class="agent-workspace-root">
      <button
        ref="entryButtonRef"
        class="agent-workspace-entry"
        :class="[`is-${motionPhase}`, { 'is-shell-visible': shellVisible }]"
        type="button"
        :aria-label="
          shellVisible
            ? (isEnglishDemo ? 'Close Accoding Agent' : '关闭新版 Accoding Agent')
            : (isEnglishDemo ? 'Open Accoding Agent' : '打开新版 Accoding Agent')
        "
        @click="toggleWorkspace"
      >
        <AgentMascot
          class="agent-workspace-entry-mark"
          :state="entryMascotState"
          :skin="preferences.skin"
          :paused="motionPhase === 'open'"
          compact
        />
      </button>

      <AgentWorkspaceShell
        v-if="shellMounted"
        :motion-phase="motionPhase"
        :orb-anchor-style="orbAnchorStyle"
        @request-close="closeWorkspace"
        @motion-complete="finishWorkspaceMotion"
      />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useAccodingStore } from "@/stores";
import AgentWorkspaceShell from "./AgentWorkspaceShell.vue";
import AgentMascot from "./AgentMascot.vue";
import { useAgentLayoutStore } from "../stores/agentLayout";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { useAgentPreferencesStore } from "../stores/agentPreferences";
import { useAgentMascotRuntimeStore } from "../stores/agentMascotRuntime";
import { isEnglishDemo } from "@/en-demo/mode";

type AgentWorkspaceMotionPhase = "idle" | "opening" | "open" | "closing";

const layout = useAgentLayoutStore();
const workspace = useAgentWorkspaceStore();
const accoding = useAccodingStore();
const preferences = useAgentPreferencesStore();
const mascotRuntime = useAgentMascotRuntimeStore();
const entryMascotState = computed(
  () =>
    mascotRuntime.mascotState ??
    (workspace.currentSessionRunning ? "thinking" : "idle"),
);
const shellVisible = ref(false);
const shellMounted = ref(false);
const motionPhase = ref<AgentWorkspaceMotionPhase>("idle");
const entryButtonRef = ref<HTMLElement | null>(null);
const orbAnchorStyle = ref<Record<string, string>>({
  "--agent-orb-center-x": "calc(100vw - 70px)",
  "--agent-orb-center-y": "calc(100vh - 70px)",
});

let motionTimer: number | null = null;

function clearMotionTimer() {
  if (motionTimer === null) return;
  window.clearTimeout(motionTimer);
  motionTimer = null;
}

function captureOrbAnchor() {
  const rect = entryButtonRef.value?.getBoundingClientRect();
  if (!rect) return;
  orbAnchorStyle.value = {
    "--agent-orb-center-x": `${Math.round(rect.left + rect.width / 2)}px`,
    "--agent-orb-center-y": `${Math.round(rect.top + rect.height / 2)}px`,
  };
}

function openWorkspace() {
  clearMotionTimer();
  captureOrbAnchor();
  shellMounted.value = true;
  shellVisible.value = true;
  layout.open();
  motionPhase.value = "opening";
  scheduleReducedMotionCompletion("opening");
}

function closeWorkspace() {
  if (!shellVisible.value && !layout.isOpen) return;
  clearMotionTimer();
  captureOrbAnchor();
  motionPhase.value = "closing";
  scheduleReducedMotionCompletion("closing");
}

// CSS animationend owns normal completion, so mounting/paint delays cannot
// truncate the settling tail. Reduced motion disables CSS animations entirely.
function scheduleReducedMotionCompletion(phase: AgentWorkspaceMotionPhase) {
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  motionTimer = window.setTimeout(() => finishWorkspaceMotion(phase), 80);
}

function finishWorkspaceMotion(phase: AgentWorkspaceMotionPhase) {
  if (phase !== motionPhase.value) return;
  clearMotionTimer();
  if (phase === "opening") {
    motionPhase.value = "open";
  } else if (phase === "closing") {
    layout.close();
    shellVisible.value = false;
    motionPhase.value = "idle";
  }
}

function toggleWorkspace() {
  if (motionPhase.value === "closing") {
    openWorkspace();
    return;
  }
  if (shellVisible.value || layout.isOpen) {
    closeWorkspace();
    return;
  }
  openWorkspace();
}

function loadPreferences() {
  if (accoding.token) void preferences.load(accoding.token);
}

onMounted(loadPreferences);

watch(() => accoding.token, (token, previousToken) => {
  if (token !== previousToken) {
    shellMounted.value = false;
    clearMotionTimer();
    shellVisible.value = false;
    motionPhase.value = "idle";
    layout.close();
  }
  loadPreferences();
}, { flush: "sync" });

onBeforeUnmount(() => {
  clearMotionTimer();
});
</script>

<style scoped>
.agent-workspace-root {
  --agent-layer-entry: 4400;
  position: fixed;
  inset: 0;
  z-index: var(--agent-layer-entry);
  pointer-events: none;
}

.agent-workspace-entry {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: var(--agent-layer-entry);
  display: inline-flex;
  width: 92px;
  height: 92px;
  align-items: center;
  justify-content: center;
  border:0;
  border-radius: 0;
  color:var(--theme-ink-primary, #1f2937);
  background:transparent;
  box-shadow:none;
  cursor: pointer;
  isolation: isolate;
  overflow: visible;
  pointer-events: auto;
  transition:
    transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1),
    opacity 180ms cubic-bezier(0.2, 0.8, 0.2, 1),
    box-shadow 180ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.agent-workspace-entry:hover {
  transform: translateY(-2px) scale(1.03);
}

.agent-workspace-entry.is-opening,
.agent-workspace-entry.is-closing {
  transition: none;
  transform: none;
}

.agent-workspace-entry.is-open {
  z-index: 4200;
  opacity: 0.72;
  pointer-events: none;
  transform: translateY(1px) scale(0.88);
}

.agent-workspace-entry.is-shell-visible {
  background:transparent !important;
  box-shadow:none !important;
}

.agent-workspace-entry.is-shell-visible.is-open .agent-workspace-entry-mark {
  opacity: 0;
  visibility: hidden;
}

.agent-workspace-entry:active {
  transform: translateY(0) scale(0.98);
}

.agent-workspace-entry-mark {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0;
}

@media (prefers-reduced-motion: reduce) {
  .agent-workspace-entry {
    animation: none !important;
    transition-duration: 80ms;
  }
}
</style>
