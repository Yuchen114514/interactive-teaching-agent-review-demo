<template>
  <span
    ref="rootRef"
    class="agent-mascot"
    :class="[
      `is-${activeState}`,
      `tone-${tone}`,
      skinClass,
      { 'is-hovered': isHovered, 'is-compact': compact, 'is-interactive': interactive },
    ]"
    :style="fallbackPaletteStyle"
    :role="interactive ? 'button' : 'img'"
    :tabindex="interactive ? 0 : -1"
    :aria-label="interactive ? (isEnglishDemo ? `Open the mascot, current state: ${label}` : `打开助教，当前状态：${label}`) : label"
    :aria-hidden="interactive ? undefined : 'true'"
    @click="handleClick"
    @keydown="handleKeydown"
    @pointerenter="handlePointerEnter"
    @pointermove="handlePointerMove"
    @pointerleave="handlePointerLeave"
  >
    <span
      ref="canvasHostRef"
      class="mascot-canvas-host"
      :class="{ 'is-unavailable': !webglAvailable }"
      aria-hidden="true"
    />
    <span v-if="!webglAvailable" class="mascot-voxel-fallback" aria-hidden="true">
      <span class="fallback-cube">
        <span class="fallback-skin-detail">
          <span v-if="skin.features.scanlines" class="fallback-scanlines">
            <i v-for="line in 7" :key="line" class="fallback-scanline" />
          </span>
          <span v-if="skin.features.keycapLabel" class="fallback-keycap-label">
            <i /><i /><i />
          </span>
          <span v-if="skin.features.serverPanel" class="fallback-server-panel">
            <i
              v-for="index in 3"
              :key="index"
              class="fallback-server-led"
              :class="{ 'is-primary': index === 1 }"
            />
          </span>
          <span v-if="skin.features.serverVents" class="fallback-server-vents">
            <i v-for="line in 6" :key="line" />
          </span>
        </span>
        <i class="fallback-eye fallback-eye-left" />
        <i class="fallback-eye fallback-eye-right" />
        <i class="fallback-mouth" />
      </span>
      <i class="fallback-arm fallback-arm-left" />
      <i class="fallback-arm fallback-arm-right" />
    </span>
    <span v-if="showLabel" class="mascot-label">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { isEnglishDemo } from "@/en-demo/mode";
import {
  AGENT_MASCOT_LABELS,
  AGENT_MASCOT_TONES,
  isAgentMascotErrorState,
  normalizeAgentMascotSkin,
  normalizeAgentMascotState,
  type AgentMascotErrorReason,
  type AgentMascotSkinId,
  type AgentMascotState,
} from "../utils/agentMascot";
import {
  AGENT_MASCOT_ERROR_DISPLAY_MS,
  resolveAgentMascotErrorRestoreState,
} from "../utils/agentMascotState";
import {
  createAgentMascotScene,
  type AgentMascotSceneController,
} from "../utils/agentMascotScene";

import { useAppearanceStore } from "../../theme/store";
import { resolveMascotAppearance } from "../utils/agentMascotAppearance";
const appearance = useAppearanceStore();

const props = withDefaults(
  defineProps<{
    state?: AgentMascotState;
    skin?: AgentMascotSkinId;
    errorReason?: AgentMascotErrorReason;
    compact?: boolean;
    interactive?: boolean;
    showLabel?: boolean;
    paused?: boolean;
  }>(),
  {
    state: "idle",
    skin: "base",
    errorReason: "output-mismatch",
    compact: false,
    interactive: false,
    showLabel: false,
    paused: false,
  },
);

const emit = defineEmits<{
  activate: [];
}>();

const rootRef = ref<HTMLElement | null>(null);
const canvasHostRef = ref<HTMLElement | null>(null);
const isHovered = ref(false);
const webglAvailable = ref(true);
let sceneController: AgentMascotSceneController | null = null;
let errorRestoreTimer: number | null = null;

const normalizedState = computed(() => normalizeAgentMascotState(props.state));
const displayedState = ref<AgentMascotState>(normalizedState.value);
const activeSkin = computed(() => normalizeAgentMascotSkin(props.skin));
const skin = computed(() => resolveMascotAppearance(activeSkin.value, appearance.scheme, activeState.value));
const skinClass = computed(() => `skin-${activeSkin.value}`);
const activeState = computed<AgentMascotState>(() => (
  isHovered.value && displayedState.value === "idle" ? "attentive" : displayedState.value
));
const englishLabels: Readonly<Record<AgentMascotState, string>> = {
  idle: "Ready",
  attentive: "Listening",
  thinking: "Thinking",
  judging: "Judging",
  accepted: "Accepted",
  "wrong-answer": "Wrong answer",
  "compile-error": "Compilation error",
  timeout: "Time limit exceeded",
  "runtime-error": "Runtime error",
  "other-error": "Error",
  offline: "Offline",
};
const label = computed(() => isEnglishDemo ? englishLabels[activeState.value] : AGENT_MASCOT_LABELS[activeState.value]);
const tone = computed(() => AGENT_MASCOT_TONES[activeState.value]);
const fallbackPaletteStyle = computed(() => {
  const palette = skin.value.palette;
  return {
    "--mascot-front": palette.front,
    "--mascot-top": palette.top,
    "--mascot-side": palette.side,
    "--mascot-back": palette.back,
    "--mascot-arm": palette.arm,
    "--mascot-pixel": palette.pixel,
    "--mascot-accent": skin.value.palette.accent,
    "--mascot-indicator": skin.value.indicator[tone.value],
    "--mascot-scanline-opacity": skin.value.features.scanlines ? "0.18" : "0",
    "--mascot-core-color": skin.value.indicator[tone.value],
  };
});

function syncVisibility() {
  sceneController?.setPaused(props.paused || document.hidden || !webglAvailable.value);
}

watch([() => props.paused, webglAvailable], syncVisibility);

onMounted(() => {
  document.addEventListener("visibilitychange", syncVisibility);
  const host = canvasHostRef.value;
  if (!host) return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  try {
    sceneController = createAgentMascotScene(host, {
      state: activeState.value,
      skin: activeSkin.value,
      theme: appearance.scheme,
      errorReason: props.errorReason,
      reducedMotion,
      paused: props.paused || document.hidden,
      onUnavailable: () => {
        webglAvailable.value = false;
      },
    });
  } catch {
    webglAvailable.value = false;
    sceneController = null;
  }
});

watch([activeState, () => props.errorReason], ([state, errorReason]) => {
  sceneController?.setState(state, errorReason);
});

watch(normalizedState, (state) => {
  clearErrorRestoreTimer();
  const restoreState = resolveAgentMascotErrorRestoreState(displayedState.value);
  displayedState.value = state;
  if (!isAgentMascotErrorState(state)) return;
  errorRestoreTimer = window.setTimeout(() => {
    displayedState.value = restoreState;
    errorRestoreTimer = null;
  }, AGENT_MASCOT_ERROR_DISPLAY_MS);
}, { immediate: true });

watch(() => appearance.scheme, scheme => {
  sceneController?.setTheme(scheme);
});

watch(activeSkin, (skinId) => {
  sceneController?.setSkin(skinId);
});

function handlePointerEnter() {
  isHovered.value = true;
}

function handlePointerMove(event: PointerEvent) {
  const root = rootRef.value;
  if (!root) return;
  const rect = root.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const x = Math.max(-1, Math.min(1, ((event.clientX - rect.left) / rect.width - 0.5) * 2));
  const y = Math.max(-1, Math.min(1, ((event.clientY - rect.top) / rect.height - 0.5) * 2));
  sceneController?.setPointer(x, y);
}

function handlePointerLeave() {
  isHovered.value = false;
  sceneController?.resetPointer();
}

function clearErrorRestoreTimer() {
  if (errorRestoreTimer === null) return;
  window.clearTimeout(errorRestoreTimer);
  errorRestoreTimer = null;
}

function handleClick() {
  if (props.interactive) emit("activate");
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.interactive || (event.key !== "Enter" && event.key !== " ")) return;
  event.preventDefault();
  emit("activate");
}

onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", syncVisibility);
  clearErrorRestoreTimer();
  sceneController?.dispose();
  sceneController = null;
});
</script>

<style scoped>
.agent-mascot {
  --mascot-size: 52px;
  --mascot-front: #9ebbd2;
  --mascot-top: #c1d6e6;
  --mascot-side: #748fa6;
  --mascot-back: #647f96;
  --mascot-arm: #8facc3;
  --mascot-pixel: #252a2d;
  --mascot-accent: #8facc3;
  --mascot-indicator: #8fa7b7;
  --mascot-scanline-opacity: 0;
  position: relative;
  display: inline-flex;
  width: var(--mascot-size);
  height: var(--mascot-size);
  align-items: center;
  justify-content: center;
  flex: 0 0 var(--mascot-size);
  vertical-align: middle;
  outline:none;
  isolation: isolate;
  user-select: none;
  transform: translateZ(0);
  cursor: default;
}

.agent-mascot.is-compact {
  --mascot-size: 88px;
}

.agent-mascot.is-interactive {
  cursor: pointer;
}

.agent-mascot:focus-visible::after {
  content: "";
  position: absolute;
  inset: 1px;
  border:2px solid var(--theme-border, rgba(65, 103, 112, 0.55));
  border-radius: 7px;
}

.mascot-canvas-host,
.mascot-voxel-fallback,
.mascot-label {
  position: absolute;
}

.mascot-canvas-host {
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.mascot-canvas-host.is-unavailable {
  display: none;
}

.mascot-canvas-host :deep(.agent-mascot-canvas) {
  display: block;
  width: 100%;
  height: 100%;
}

.mascot-voxel-fallback {
  top: 50%;
  left: 50%;
  width: 68%;
  height: 54%;
  transform: translate(-50%, -45%);
}

.fallback-cube,
.fallback-arm {
  position: absolute;
  background: var(--mascot-front);
}

.fallback-cube {
  inset: 0 11% 0;
}

.fallback-skin-detail {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
}

.fallback-scanlines {
  position: absolute;
  inset: 10% 3%;
  display: grid;
  grid-template-rows: repeat(7, 1fr);
  opacity: var(--mascot-scanline-opacity);
}

.fallback-scanline {
  align-self: center;
  height: 1px;
  background: var(--mascot-pixel);
}

.fallback-keycap-label {
  position: absolute;
  right: 15%;
  bottom: 7%;
  left: 15%;
  display: flex;
  justify-content: center;
  gap: 9%;
}

.fallback-keycap-label i {
  display: block;
  width: 10%;
  height: 4px;
  background: var(--mascot-accent);
}

.fallback-keycap-label i:nth-child(2) {
  width: 26%;
}

.fallback-server-panel {
  position: absolute;
  inset: 9% 5%;
  border: 1px solid var(--mascot-accent);
  opacity: 0.78;
  background: var(--mascot-back);
}

.fallback-server-led {
  position: absolute;
  top: 7%;
  width: 5px;
  height: 5px;
  background: var(--mascot-accent);
}

.fallback-server-led:nth-child(1) { left: 8%; }
.fallback-server-led:nth-child(2) { left: 16%; }
.fallback-server-led:nth-child(3) { left: 24%; }
.fallback-server-led.is-primary { background: var(--mascot-indicator); }

.fallback-server-vents {
  position: absolute;
  top: 27%;
  right: 8%;
  display: grid;
  gap: 4px;
}

.fallback-server-vents i {
  display: block;
  width: 25px;
  height: 2px;
  background: var(--mascot-accent);
  opacity: 0.72;
}

.fallback-cube::before,
.fallback-cube::after {
  content: "";
  position: absolute;
}

.fallback-cube::before {
  right: 0;
  bottom: 100%;
  left: 0;
  height: 22%;
  background: var(--mascot-top);
  clip-path: polygon(12% 0, 100% 0, 88% 100%, 0 100%);
}

.fallback-cube::after {
  top: 0;
  bottom: 0;
  left: 100%;
  width: 16%;
  background: var(--mascot-side);
  clip-path: polygon(0 0, 100% 0, 100% 82%, 0 100%);
}

.fallback-eye,
.fallback-mouth {
  position: absolute;
  z-index: 1;
  display: block;
  background: var(--mascot-pixel);
}

.fallback-eye {
  top: 34%;
  width: 13%;
  height: 24%;
}

.fallback-eye-left { left: 24%; }
.fallback-eye-right { right: 24%; }

.fallback-mouth {
  bottom: 20%;
  left: 40%;
  width: 20%;
  height: 7%;
}

.fallback-arm {
  top: 43%;
  width: 17%;
  height: 31%;
  background: var(--mascot-arm);
}

.fallback-arm-left { left: -6%; }
.fallback-arm-right { right: -7%; }

.mascot-label {
  top: calc(100% + 5px);
  left: 50%;
  z-index: 2;
  padding: 3px 7px;
  border:1px solid var(--theme-border, rgba(74, 91, 96, 0.16));
  border-radius: 4px;
  color:var(--theme-ink-secondary, #43565a);
  background:var(--theme-surface-raised, rgba(248, 250, 248, 0.96));
  box-shadow:0 5px 14px var(--theme-shadow, rgba(42, 60, 64, 0.1));
  font-size: 10px;
  line-height: 1.2;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transform: translate(-50%, -2px);
  transition: opacity 150ms ease, transform 170ms ease;
}

.agent-mascot.is-hovered .mascot-label,
.agent-mascot:focus-visible .mascot-label {
  opacity: 1;
  transform: translate(-50%, 0);
}

.agent-mascot.is-offline {
  opacity: 0.72;
}

@media (prefers-reduced-motion: reduce) {
  .mascot-label {
    transition: none;
  }
}
</style>
