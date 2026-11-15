<template>
  <section :class="['float-section', 'diagram-panel-section', { 'is-inline': inline, 'is-awaiting': inline && !scenePreview }]">
    <div v-if="!inline" class="float-kicker">图示</div>
    <div v-if="!inline && workspace.diagrams.length === 0" class="empty-note">暂无图示</div>
    <template v-else>
      <div v-if="!inline" class="diagram-card-list">
        <button
          v-for="diagram in workspace.diagrams"
          :key="sceneId(diagram)"
          :class="['diagram-mini-card', { 'is-selected': sceneId(diagram) === selectedSceneId }]"
          type="button"
          @click="loadScene(diagram)"
        >
          <strong>{{ diagram.title || diagram.summary || '生成图示' }}</strong>
          <span>{{ diagram.diagram_kind || 'diagram' }} · {{ diagram.status || 'ready' }}</span>
        </button>
      </div>

      <div v-if="scenePreview" class="diagram-teaching-board">
        <div v-if="!inline" class="diagram-player-head">
          <div>
            <span class="diagram-phase-chip">{{ scenePreview.phaseLabel }}</span>
            <strong>{{ scenePreview.title }}</strong>
          </div>
          <span>{{ selectedStepIndex + 1 }} / {{ stepFrames.length }}</span>
        </div>

        <div
          class="diagram-scene-preview"
          :class="{ 'is-semantic': isSemantic, 'is-playing': isPlaying }"
          :style="isSemantic ? { '--semantic-frame-ratio': String(semanticFrameRatio) } : undefined"
          :aria-label="scenePreview.title || '图示预览'"
        >
          <svg
            v-if="!isSemantic"
            :viewBox="scenePreview.viewBox"
            role="img"
            aria-hidden="true"
            preserveAspectRatio="xMidYMid meet"
          >
            <defs>
              <marker
                id="diagram-arrow"
                markerHeight="6"
                markerWidth="6"
                orient="auto"
                refX="5"
                refY="3"
                viewBox="0 0 6 6"
              >
                <path d="M0 0 L6 3 L0 6 Z" />
              </marker>
            </defs>
            <line
              v-for="edge in scenePreview.edges"
              :key="edge.id"
              :class="{ 'is-highlighted': edge.highlighted }"
              class="diagram-preview-edge"
              :x1="edge.x1"
              :x2="edge.x2"
              :y1="edge.y1"
              :y2="edge.y2"
            />
            <g
              v-for="item in scenePreview.items"
              :key="item.id"
              :class="[
                'diagram-preview-item',
                `is-${item.kind}`,
                { 'is-highlighted': item.highlighted, 'is-muted': item.muted },
              ]"
              :transform="`translate(${item.x}, ${item.y})`"
            >
              <rect
                v-if="item.kind !== 'circle'"
                :height="item.height"
                :rx="item.kind === 'frame' ? 12 : 9"
                :width="item.width"
                x="0"
                y="0"
              />
              <circle
                v-else
                :cx="item.width / 2"
                :cy="item.height / 2"
                :r="Math.min(item.width, item.height) / 2"
              />
              <text :x="item.width / 2" :y="textStartY(item)">
                <tspan
                  v-for="(line, index) in item.labelLines"
                  :key="`${item.id}-line-${index}`"
                  :x="item.width / 2"
                  :dy="index === 0 ? 0 : 15"
                >
                  {{ line }}
                </tspan>
              </text>
            </g>
          </svg>
          <div v-else ref="semanticFrameStage" class="semantic-frame-stage" role="img" :aria-label="scenePreview.title" />
          <div v-if="isSemantic && !semanticFrameReady" class="diagram-preview-empty" role="status">{{ semanticFrameError || '正在加载图示帧' }}</div>
          <div v-if="inline" class="inline-diagram-controls" aria-label="图示控制">
            <button type="button" aria-label="上一步图示" @click="previousFrame">
              <span class="inline-diagram-icon is-previous" aria-hidden="true" />
            </button>
            <button type="button" :aria-label="isPlaying ? '暂停图示' : '播放图示'" @click="togglePlaying">
              <span :class="['inline-diagram-icon', isPlaying ? 'is-pause' : 'is-play']" aria-hidden="true" />
            </button>
            <button type="button" aria-label="下一步图示" @click="nextFrame">
              <span class="inline-diagram-icon is-next" aria-hidden="true" />
            </button>
          </div>
          <input
            v-if="inline"
            class="inline-diagram-progress"
            type="range"
            min="0"
            :max="Math.max(0, totalFrameCount - 1)"
            :value="currentFrameNumber"
            :style="{ '--diagram-progress': `${progressPercent}%` }"
            aria-label="图示播放进度"
            @input="seekFrame"
          />
        </div>

        <div v-if="!inline" class="diagram-frame-timeline" aria-label="图示播放进度">
          <button
            v-for="frame in currentStepFrames"
            :key="frame.phase"
            :class="{ 'is-active': frame.phase === framePhase }"
            type="button"
            @click="setFramePhase(frame.phase)"
          >
            <i />
            <span>{{ frame.phaseLabel }}</span>
          </button>
        </div>

        <div v-if="!inline" class="diagram-player-controls">
          <button type="button" aria-label="上一步图示" @click="previousFrame">上一帧</button>
          <button type="button" aria-label="播放或暂停图示" @click="togglePlaying">
            {{ isPlaying ? "暂停" : "播放" }}
          </button>
          <button type="button" aria-label="下一步图示" @click="nextFrame">下一帧</button>
        </div>

        <div v-if="!inline && scenePreview.actions.length" class="diagram-action-row">
          <span
            v-for="action in scenePreview.actions"
            :key="action"
            class="diagram-action-chip"
          >
            {{ action }}
          </span>
        </div>

        <p v-if="!inline && scenePreview.note" class="diagram-step-note">{{ scenePreview.note }}</p>
        <p v-if="inline" class="inline-diagram-caption" :title="scenePreview.note">{{ scenePreview.note || scenePreview.title }}</p>
      </div>
      <div v-else-if="inline" class="inline-diagram-awaiting" role="status">正在准备图示<i /><i /><i /></div>
      <div v-else class="diagram-scene-preview" aria-label="图示预览">
        <div class="diagram-preview-empty">
          <span>正在准备图示预览</span>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { loadAgentDiagramFrame, loadAgentDiagramScene } from "../api/agentApi";
import { captureAgentAccount } from "../utils/agentAccountScope";
import type { AgentDiagramSummary } from "../api/types";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { useAgentLayoutStore } from "../stores/agentLayout";
import { useAccodingStore } from "@/stores";
import { useAppearanceStore } from "@/theme/store";
import { diagramSvgAspectRatio, themeDiagramSvg } from "../utils/agentDiagramTheme";
import { estimateSemanticFrameRatio } from "../utils/agentDiagramLayout";
import { animateTransition, mountFrameSvg } from "../utils/agentDiagramMotion.js";
import type { DiagramMotionHandle } from "../utils/agentDiagramMotion.js";

const layout = useAgentLayoutStore();

type SceneRecord = Record<string, unknown>;
type DiagramFramePhase = "entry" | "change" | "exit";
const MAX_PREVIEW_ROW_COLUMNS = 4;

interface PreviewItem {
  id: string;
  kind: "cell" | "circle" | "frame" | "label";
  x: number;
  y: number;
  width: number;
  height: number;
  label: string;
  labelLines: string[];
  highlighted: boolean;
  muted: boolean;
}

interface PreviewEdge {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  highlighted: boolean;
}

interface DiagramFrame {
  phase: DiagramFramePhase;
  phaseLabel: string;
  title: string;
  note: string;
  state: SceneRecord;
  actions: string[];
  focusIds: Set<string>;
}

interface ScenePreview {
  phaseLabel: string;
  title: string;
  note: string;
  viewBox: string;
  items: PreviewItem[];
  edges: PreviewEdge[];
  actions: string[];
}

const store = useAccodingStore();
const appearance = useAppearanceStore();
const workspace = useAgentWorkspaceStore();
const props = withDefaults(defineProps<{
  inline?: boolean;
  diagram?: AgentDiagramSummary | null;
  sceneOverride?: SceneRecord | null;
}>(), { inline: false, diagram: null, sceneOverride: null });
const inline = computed(() => props.inline);
const selectedSceneId = ref<string | null>(null);
const selectedStepIndex = ref(0);
const framePhase = ref<DiagramFramePhase>("entry");
const isPlaying = ref(true);
if (props.inline) isPlaying.value = false;
let playbackTimer: number | null = null;
let playbackGeneration = 0;
let disposed = false;
let sceneRequestSequence = 0;
let sceneRequest: { key: string; controller: AbortController; isCurrent: () => boolean; promise: Promise<void> } | null = null;

const selectedDiagram = computed(() => {
  if (props.inline) return props.diagram || null;
  const preferred = workspace.diagrams.find((diagram) => sceneId(diagram) === selectedSceneId.value);
  return preferred || workspace.diagrams[0] || null;
});

const selectedScene = computed(() => {
  if (props.sceneOverride) return props.sceneOverride;
  const id = selectedDiagram.value ? sceneId(selectedDiagram.value) : "";
  return id ? workspace.diagramScenes[id] : null;
});

const selectedSteps = computed(() => asArray(selectedScene.value?.steps).filter(isRecord));
const selectedStep = computed(() => selectedSteps.value[selectedStepIndex.value] || null);
const stepFrames = computed(() => selectedSteps.value.map((step, index) => buildStepFrames(step, index)));
const currentStepFrames = computed(() => stepFrames.value[selectedStepIndex.value] || []);
const currentFrame = computed(() => currentStepFrames.value.find((frame) => frame.phase === framePhase.value) || currentStepFrames.value[0] || null);
const semanticScene = computed(() => {
  const metadata = selectedScene.value?.metadata_json;
  const scene = isRecord(metadata) ? metadata.semantic_scene : null;
  return isRecord(scene) && Array.isArray(scene.frames) ? scene : null;
});
const semanticFrames = computed(() => asArray(semanticScene.value?.frames).filter(isRecord));
const isSemantic = computed(() => semanticFrames.value.length > 0);
const displayedSemanticIndex = ref(0);
const scenePreview = computed<ScenePreview | null>(() => {
  if (isSemantic.value) {
    const frameIndex = semanticFrameReady.value ? displayedSemanticIndex.value : selectedStepIndex.value;
    const frame = semanticFrames.value[frameIndex] || semanticFrames.value[0];
    return {
      phaseLabel: "图示", title: asText(frame?.title) || asText(semanticScene.value?.title) || "图示",
      note: asText(frame?.note) || asText(frame?.title) || "图示变化",
      viewBox: "0 0 1 1", items: [], edges: [], actions: [],
    };
  }
  return buildScenePreview(currentFrame.value);
});
const totalFrameCount = computed(() => isSemantic.value ? semanticFrames.value.length :
  stepFrames.value.reduce((sum, frames) => sum + frames.length, 0));
const currentFrameNumber = computed(() => isSemantic.value ? selectedStepIndex.value :
  selectedStepIndex.value * 3 + Math.max(0, ["entry", "change", "exit"].indexOf(framePhase.value)));
const progressPercent = computed(() => totalFrameCount.value <= 1 ? 100 :
  (currentFrameNumber.value / (totalFrameCount.value - 1)) * 100);
const semanticFrameStage = ref<HTMLElement | null>(null);
const semanticFrameReady = ref(false);
const semanticFrameError = ref("");
const semanticFrameRatio = ref(16 / 10);
const semanticFrameRawCache = new Map<string, string>();
const semanticFrameRequests = new Map<string, { controller: AbortController; promise: Promise<string> }>();
let semanticFrameGeneration = 0;
let displayedSceneId = "";
let displayedScheme = "";
let activeSemanticMotion: DiagramMotionHandle | null = null;

function resetSemanticFrameView() {
  activeSemanticMotion?.cancel();
  activeSemanticMotion = null;
  semanticFrameStage.value?.replaceChildren();
  semanticFrameReady.value = false;
  semanticFrameError.value = "";
  displayedSemanticIndex.value = 0;
  displayedScheme = "";
}

function clearSemanticFrames() {
  for (const request of semanticFrameRequests.values()) request.controller.abort();
  semanticFrameRequests.clear();
  semanticFrameGeneration += 1;
  resetSemanticFrameView();
  semanticFrameRatio.value = 16 / 10;
  displayedSceneId = "";
  semanticFrameRawCache.clear();
}

async function rawSemanticFrame(token: string, workspaceId: number, id: string, index: number): Promise<string> {
  const key = `${workspaceId}:${id}:${index}`;
  const cached = semanticFrameRawCache.get(key);
  if (cached) return cached;
  const inflight = semanticFrameRequests.get(key);
  if (inflight) return inflight.promise;
  const controller = new AbortController();
  const promise = (async () => {
    const response = await loadAgentDiagramFrame(token, workspaceId, id, index, controller.signal);
    const svg = await response.text();
    if (!controller.signal.aborted) {
      semanticFrameRawCache.set(key, svg);
      while (semanticFrameRawCache.size > 16) {
        const oldest = semanticFrameRawCache.keys().next().value;
        if (!oldest) break;
        semanticFrameRawCache.delete(oldest);
      }
    }
    return svg;
  })();
  semanticFrameRequests.set(key, { controller, promise });
  try { return await promise; }
  finally { if (semanticFrameRequests.get(key)?.promise === promise) semanticFrameRequests.delete(key); }
}

function crossfadeSemanticFrames(oldSvg: SVGSVGElement, newSvg: SVGSVGElement): DiagramMotionHandle {
  oldSvg.style.position = "absolute";
  oldSvg.style.inset = "0";
  oldSvg.style.width = "100%";
  oldSvg.style.height = "100%";
  oldSvg.style.pointerEvents = "none";
  oldSvg.setAttribute("aria-hidden", "true");
  oldSvg.parentElement?.appendChild(oldSvg);
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    oldSvg.remove();
    return { finished: Promise.resolve(), cancel() {} };
  }
  const options: KeyframeAnimationOptions = { duration: 220, easing: "ease-out" };
  const oldAnimation = oldSvg.animate([{ opacity: 1 }, { opacity: 0 }], options);
  const newAnimation = newSvg.animate([{ opacity: 0 }, { opacity: 1 }], options);
  const cleanup = () => oldSvg.remove();
  return {
    finished: Promise.allSettled([oldAnimation.finished, newAnimation.finished]).then(cleanup),
    cancel() { oldAnimation.cancel(); newAnimation.cancel(); cleanup(); },
  };
}

watch(() => store.token, clearSemanticFrames, { flush: "sync" });
watch(
  [() => selectedDiagram.value ? sceneId(selectedDiagram.value) : "", () => selectedScene.value,
    () => currentFrameNumber.value, () => isSemantic.value, () => appearance.scheme],
  async () => {
    const generation = ++semanticFrameGeneration;
    stopPlayback();
    semanticFrameError.value = "";
    if (!isSemantic.value) return;
    const id = selectedDiagram.value ? sceneId(selectedDiagram.value) : "";
    const workspaceId = selectedDiagram.value?.workspace_id;
    const index = currentFrameNumber.value;
    if (!id || !workspaceId || !store.token) return;
    const sceneKey = `${workspaceId}:${id}`;
    if (displayedSceneId !== sceneKey) {
      for (const request of semanticFrameRequests.values()) request.controller.abort();
      semanticFrameRequests.clear();
      semanticFrameRawCache.clear();
      resetSemanticFrameView();
      semanticFrameRatio.value = estimateSemanticFrameRatio(semanticScene.value, index) || 16 / 10;
    }
    displayedSceneId = sceneKey;
    try {
      const svg = await rawSemanticFrame(store.token, workspaceId, id, index);
      if (generation !== semanticFrameGeneration) return;
      await nextTick();
      if (generation !== semanticFrameGeneration) return;
      const stage = semanticFrameStage.value;
      if (!stage) return;
      activeSemanticMotion?.cancel();
      activeSemanticMotion = null;
      const oldSvg = stage.querySelector<SVGSVGElement>(":scope > svg");
      const previousIndex = displayedSemanticIndex.value;
      const previousRatio = semanticFrameRatio.value;
      const ratio = diagramSvgAspectRatio(svg) || estimateSemanticFrameRatio(semanticScene.value, index);
      if (ratio) semanticFrameRatio.value = ratio;
      const adjacent = Math.abs(index - previousIndex) === 1;
      const sameRatio = !ratio || Math.abs(previousRatio - ratio) < 0.001;
      const markup = themeDiagramSvg(svg, appearance.scheme);
      const pair = mountFrameSvg(stage, markup, !!oldSvg);
      if (oldSvg && pair.newSvg) {
        activeSemanticMotion = adjacent && sameRatio && displayedScheme === appearance.scheme
          ? animateTransition(stage, pair.oldSvg, pair.newSvg,
            semanticFrames.value[previousIndex], semanticFrames.value[index], String(semanticScene.value?.structure || ""), 560)
          : crossfadeSemanticFrames(pair.oldSvg!, pair.newSvg);
      } else if (pair.newSvg && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        pair.newSvg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
      }
      displayedSemanticIndex.value = index;
      displayedScheme = appearance.scheme;
      semanticFrameReady.value = true;
      if (activeSemanticMotion) {
        const motion = activeSemanticMotion;
        void motion.finished.then(() => { if (activeSemanticMotion === motion) activeSemanticMotion = null; });
      }
      if (isPlaying.value) scheduleSemanticPlayback();
      if (index + 1 < semanticFrames.value.length) {
        void rawSemanticFrame(store.token, workspaceId, id, index + 1).catch(() => {});
      }
    } catch (error) {
      if (generation === semanticFrameGeneration) {
        semanticFrameError.value = error instanceof Error ? error.message : "图示帧加载失败";
      }
    }
  },
  { immediate: true },
);

watch(
  () => workspace.diagrams.map((diagram) => sceneId(diagram)).join("|"),
  () => {
    if (props.inline) return;
    const firstId = workspace.diagrams[0] ? sceneId(workspace.diagrams[0]) : "";
    if (!selectedSceneId.value || !workspace.diagrams.some((diagram) => sceneId(diagram) === selectedSceneId.value)) {
      selectedSceneId.value = firstId || null;
    }
  },
  { immediate: true },
);

watch(
  () => selectedSceneId.value,
  () => {
    selectedStepIndex.value = 0;
    framePhase.value = "entry";
    restartPlayback();
  },
);

watch(
  () => selectedStep.value,
  () => {
    if (selectedStepIndex.value >= selectedSteps.value.length) {
      selectedStepIndex.value = Math.max(0, selectedSteps.value.length - 1);
    }
    if (!currentStepFrames.value.some((frame) => frame.phase === framePhase.value)) {
      framePhase.value = "entry";
    }
    if (!isSemantic.value) restartPlayback();
  },
);

watch([isPlaying, () => layout.isOpen], restartPlayback, { immediate: true });

watch(
  [() => store.token, () => workspace.currentSessionId, () => workspace.selectedWorkspaceId],
  cancelSceneRequest,
  { flush: "sync" },
);

// The store evicts old scenes at its size limit. Reload a selected cache miss
// instead of leaving the preview empty until the user clicks it again.
watch(
  [() => store.token, () => workspace.currentSessionId, () => workspace.selectedWorkspaceId,
    () => selectedDiagram.value ? sceneId(selectedDiagram.value) : "", () => Boolean(selectedScene.value)],
  () => {
    if (selectedDiagram.value) void ensureSelectedScene(selectedDiagram.value);
    else cancelSceneRequest();
  },
  { immediate: true, flush: "post" },
);

function sceneId(diagram: AgentDiagramSummary) {
  return diagram.scene_id || diagram.diagram_scene_id || "";
}

function cancelSceneRequest() {
  sceneRequestSequence += 1;
  sceneRequest?.controller.abort();
  sceneRequest = null;
}

function loadScene(diagram: AgentDiagramSummary): Promise<void> {
  const id = sceneId(diagram);
  if (!id) return Promise.resolve();
  selectedSceneId.value = id;
  return ensureSelectedScene(diagram);
}

function ensureSelectedScene(diagram: AgentDiagramSummary): Promise<void> {
  const id = sceneId(diagram);
  const workspaceId = diagram.workspace_id || workspace.selectedWorkspaceId;
  const selectedWorkspaceId = workspace.selectedWorkspaceId;
  const sessionId = workspace.currentSessionId;
  const token = store.token;
  const account = captureAgentAccount(token);
  const key = `${token}:${sessionId}:${selectedWorkspaceId}:${workspaceId}:${id}`;
  if (sceneRequest && (sceneRequest.key !== key || !sceneRequest.isCurrent())) cancelSceneRequest();
  if (disposed || !account.isCurrent() || !id || !workspaceId || props.sceneOverride || workspace.diagramScenes[id]) return Promise.resolve();
  if (sceneRequest?.key === key) return sceneRequest.promise;
  const sequence = ++sceneRequestSequence;
  const controller = new AbortController();
  const ownsSelection = () => !disposed && !controller.signal.aborted && account.isCurrent() &&
    sequence === sceneRequestSequence && store.token === token && workspace.currentSessionId === sessionId &&
    workspace.selectedWorkspaceId === selectedWorkspaceId && Boolean(selectedDiagram.value && sceneId(selectedDiagram.value) === id);
  const request = { key, controller, isCurrent: ownsSelection, promise: Promise.resolve() };
  sceneRequest = request;
  request.promise = (async () => {
    try {
      const scene = await loadAgentDiagramScene(token, workspaceId, id, controller.signal);
      if (ownsSelection()) workspace.setDiagramScene(id, scene);
    } catch (error) {
      if (ownsSelection()) ElMessage.warning(`加载图示失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      if (sceneRequest === request) sceneRequest = null;
    }
  })();
  return request.promise;
}

function buildStepFrames(step: SceneRecord, index: number): DiagramFrame[] {
  const entryState = isRecord(step.entry_state) ? step.entry_state : {};
  const exitState = isRecord(step.exit_state) ? step.exit_state : entryState;
  const actions = transitionActions(step);
  const focusIds = new Set(actions.map((action) => asText(action.target_id)).filter(Boolean));
  const note = asText(step.teaching_note) || "观察这一步中哪些状态发生了变化。";
  const title = asText(step.title) || `第 ${index + 1} 步`;
  return [
    {
      phase: "entry",
      phaseLabel: "起点",
      title,
      note,
      state: entryState,
      actions: ["先看变化前的状态"],
      focusIds,
    },
    {
      phase: "change",
      phaseLabel: "变化",
      title,
      note,
      state: exitState,
      actions: actions.map(actionLabel).filter(Boolean),
      focusIds,
    },
    {
      phase: "exit",
      phaseLabel: "结果",
      title,
      note,
      state: exitState,
      actions: ["得到这一轮后的状态"],
      focusIds,
    },
  ];
}

function buildScenePreview(frame: DiagramFrame | null): ScenePreview | null {
  if (!frame) return null;
  const nodeItems = asArray(frame.state.nodes).filter(isRecord).map((node) => nodeToPreviewItem(node, frame.focusIds));
  const freeItems = asArray(frame.state.items).filter(isRecord).map((item) => itemToPreviewItem(item, frame.focusIds));
  const rawItems = [...freeItems, ...nodeItems].filter((item) => item.label || item.kind === "frame");
  if (!rawItems.length) return null;
  const layoutItems = shouldReflowPreviewItems(rawItems) ? layoutClusteredPreviewItems(rawItems) : rawItems;
  const rawItemById = new Map(layoutItems.map((item) => [item.id, item]));
  const rawEdges = asArray(frame.state.edges)
    .filter(isRecord)
    .map((edge, index) => edgeToPreviewEdge(edge, index, rawItemById, frame.focusIds))
    .filter((edge): edge is PreviewEdge => Boolean(edge));
  const { items, edges, bounds } = normalizePreviewGeometry(layoutItems, rawEdges);
  return {
    phaseLabel: frame.phaseLabel,
    title: frame.title,
    note: frame.note,
    viewBox: `${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`,
    items,
    edges,
    actions: frame.actions.length ? frame.actions : ["观察当前状态"],
  };
}

function itemToPreviewItem(item: SceneRecord, focusIds: Set<string>): PreviewItem {
  const id = asText(item.item_id) || asText(item.id) || `item-${asNumber(item.x)}-${asNumber(item.y)}`;
  const rawKind = asText(item.kind);
  const kind = rawKind === "frame" ? "frame" : rawKind === "text" ? "label" : "cell";
  const label = asText(item.label) || asText(item.value);
  const labelLines = splitLabel(label, kind === "label" ? 14 : 8);
  const width = Math.max(kind === "label" ? 170 : 58, asNumber(item.width, kind === "label" ? 170 : 74), maxLineUnits(labelLines) * 12 + 22);
  const height = Math.max(kind === "label" ? 34 : 38, asNumber(item.height, kind === "label" ? 34 : 42), labelLines.length * 15 + 18);
  return {
    id,
    kind,
    x: asNumber(item.x),
    y: asNumber(item.y),
    width,
    height,
    label,
    labelLines,
    highlighted: asText(item.state) === "highlighted" || focusIds.has(id),
    muted: asText(item.state) === "muted",
  };
}

function nodeToPreviewItem(node: SceneRecord, focusIds: Set<string>): PreviewItem {
  const id = asText(node.node_id) || asText(node.id) || `node-${asNumber(node.x)}-${asNumber(node.y)}`;
  const label = asText(node.label) || asText(node.value);
  const labelLines = splitLabel(label, 6);
  const width = Math.max(48, asNumber(node.width, 54), maxLineUnits(labelLines) * 12 + 22);
  const height = Math.max(48, asNumber(node.height, 54), labelLines.length * 15 + 20);
  return {
    id,
    kind: asText(node.shape) === "circle" && labelLines.length <= 1 ? "circle" : "cell",
    x: asNumber(node.x) - width / 2,
    y: asNumber(node.y) - height / 2,
    width,
    height,
    label,
    labelLines,
    highlighted: asText(node.state) === "highlighted" || focusIds.has(id),
    muted: asText(node.state) === "muted",
  };
}

function edgeToPreviewEdge(edge: SceneRecord, index: number, itemById: Map<string, PreviewItem>, focusIds: Set<string>): PreviewEdge | null {
  const fromId = asText(edge.from);
  const toId = asText(edge.to);
  const from = itemById.get(fromId);
  const to = itemById.get(toId);
  if (!from || !to) return null;
  return {
    id: asText(edge.edge_id) || asText(edge.id) || `edge-${index}`,
    x1: from.x + from.width / 2,
    y1: from.y + from.height / 2,
    x2: to.x + to.width / 2,
    y2: to.y + to.height / 2,
    highlighted: asText(edge.state) === "highlighted" || focusIds.has(fromId) || focusIds.has(toId),
  };
}

function shouldReflowPreviewItems(items: PreviewItem[]) {
  if (items.length <= 1) return false;
  const drawableItems = items.filter((item) => item.kind !== "frame");
  const centers = items.map((item) => ({
    x: item.x + item.width / 2,
    y: item.y + item.height / 2,
  }));
  const buckets = new Set(centers.map((center) => `${Math.round(center.x / 18)}:${Math.round(center.y / 18)}`));
  const xValues = centers.map((center) => center.x);
  const yValues = centers.map((center) => center.y);
  const xSpan = Math.max(...xValues) - Math.min(...xValues);
  const ySpan = Math.max(...yValues) - Math.min(...yValues);
  const spread = Math.hypot(xSpan, ySpan);
  const duplicateCenters = buckets.size <= Math.ceil(items.length * 0.6);
  const tooCompressed = spread < Math.min(150, 34 * Math.sqrt(items.length));
  const collidingBoxes = hasPreviewItemCollisions(drawableItems);
  return duplicateCenters || tooCompressed || collidingBoxes;
}

function hasPreviewItemCollisions(items: PreviewItem[], gap = 12) {
  for (let index = 0; index < items.length; index += 1) {
    const current = items[index];
    for (let nextIndex = index + 1; nextIndex < items.length; nextIndex += 1) {
      const next = items[nextIndex];
      const separated =
        current.x + current.width + gap <= next.x ||
        next.x + next.width + gap <= current.x ||
        current.y + current.height + gap <= next.y ||
        next.y + next.height + gap <= current.y;
      if (!separated) return true;
    }
  }
  return false;
}

function layoutClusteredPreviewItems(items: PreviewItem[]) {
  const drawableItems = items.filter((item) => item.kind !== "frame");
  const frameItems = items.filter((item) => item.kind === "frame");
  const targetItems = drawableItems.length ? drawableItems : items;
  const rows = layoutPreviewRows(targetItems);
  const columns = Math.max(1, ...rows.map((row) => row.length));
  const maxWidth = Math.max(82, ...targetItems.map((item) => item.width));
  const maxHeight = Math.max(44, ...targetItems.map((item) => item.height));
  const columnGap = 34;
  const rowGap = 28;
  const contentWidth = columns * maxWidth + Math.max(0, columns - 1) * columnGap;
  const contentHeight = rows.length * maxHeight + Math.max(0, rows.length - 1) * rowGap;
  const laidOutById = new Map<string, PreviewItem>();
  rows.forEach((row, rowIndex) => {
    const rowWidth = row.length * maxWidth + Math.max(0, row.length - 1) * columnGap;
    const rowOffsetX = (contentWidth - rowWidth) / 2;
    row.forEach((item, columnIndex) => {
      const x = rowOffsetX + columnIndex * (maxWidth + columnGap) + (maxWidth - item.width) / 2;
      const y = rowIndex * (maxHeight + rowGap) + (maxHeight - item.height) / 2;
      laidOutById.set(item.id, { ...item, x, y });
    });
  });
  const laidOutFrames = frameItems.map((item, index) => ({
    ...item,
    x: -24 - index * 8,
    y: -24 - index * 8,
    width: contentWidth + 48 + index * 16,
    height: contentHeight + 48 + index * 16,
  }));
  return items.map((item) => laidOutById.get(item.id) || laidOutFrames.shift() || item);
}

function layoutPreviewRows(items: PreviewItem[]) {
  const rowThreshold = 46;
  const groupedRows: Array<{ centerY: number; items: PreviewItem[] }> = [];
  const sortedItems = [...items].sort((first, second) => {
    const firstY = first.y + first.height / 2;
    const secondY = second.y + second.height / 2;
    return firstY - secondY || first.x - second.x;
  });
  sortedItems.forEach((item) => {
    const centerY = item.y + item.height / 2;
    const row = groupedRows.find((candidate) => Math.abs(candidate.centerY - centerY) <= rowThreshold);
    if (row) {
      row.items.push(item);
      row.centerY = row.items.reduce((sum, rowItem) => sum + rowItem.y + rowItem.height / 2, 0) / row.items.length;
    } else {
      groupedRows.push({ centerY, items: [item] });
    }
  });
  return groupedRows.flatMap((row) => {
    const sortedRow = [...row.items].sort((first, second) => first.x - second.x);
    const chunks: PreviewItem[][] = [];
    for (let index = 0; index < sortedRow.length; index += MAX_PREVIEW_ROW_COLUMNS) {
      chunks.push(sortedRow.slice(index, index + MAX_PREVIEW_ROW_COLUMNS));
    }
    return chunks;
  });
}

function previewBounds(items: PreviewItem[], edges: PreviewEdge[]) {
  const xs = items.flatMap((item) => [item.x, item.x + item.width]);
  const ys = items.flatMap((item) => [item.y, item.y + item.height]);
  edges.forEach((edge) => {
    xs.push(edge.x1, edge.x2);
    ys.push(edge.y1, edge.y2);
  });
  const padding = 34;
  const minX = Math.min(...xs) - padding;
  const minY = Math.min(...ys) - padding;
  const maxX = Math.max(...xs) + padding;
  const maxY = Math.max(...ys) + padding;
  return {
    minX,
    minY,
    width: Math.max(260, maxX - minX),
    height: Math.max(170, maxY - minY),
  };
}

function normalizePreviewGeometry(items: PreviewItem[], edges: PreviewEdge[]) {
  const bounds = previewBounds(items, edges);
  const targetWidth = 520;
  const targetHeight = 320;
  const scale = Math.min(1.75, targetWidth / bounds.width, targetHeight / bounds.height);
  const offsetX = (targetWidth - bounds.width * scale) / 2 - bounds.minX * scale;
  const offsetY = (targetHeight - bounds.height * scale) / 2 - bounds.minY * scale;
  const normalizeX = (value: number) => Math.round((value * scale + offsetX) * 10) / 10;
  const normalizeY = (value: number) => Math.round((value * scale + offsetY) * 10) / 10;
  const normalizeSize = (value: number) => Math.round(value * scale * 10) / 10;
  return {
    bounds: {
      minX: 0,
      minY: 0,
      width: targetWidth,
      height: targetHeight,
    },
    items: items.map((item) => ({
      ...item,
      x: normalizeX(item.x),
      y: normalizeY(item.y),
      width: normalizeSize(item.width),
      height: normalizeSize(item.height),
    })),
    edges: edges.map((edge) => ({
      ...edge,
      x1: normalizeX(edge.x1),
      y1: normalizeY(edge.y1),
      x2: normalizeX(edge.x2),
      y2: normalizeY(edge.y2),
    })),
  };
}

function transitionActions(step: SceneRecord): SceneRecord[] {
  const transition = isRecord(step.transition) ? step.transition : {};
  return asArray(transition.actions).filter(isRecord);
}

function actionLabel(action: SceneRecord) {
  const kind = asText(action.kind) || "focus";
  const target = asText(action.target_id);
  const labels: Record<string, string> = {
    add: "新增",
    compare: "比较",
    focus: "关注",
    highlight: "高亮",
    move: "移动",
    pop: "弹出",
    push: "入栈",
    remove: "移除",
    update: "更新",
  };
  return target ? `${labels[kind] || kind} ${target}` : labels[kind] || kind;
}

function splitLabel(label: string, maxChars: number) {
  const normalized = label.trim();
  if (!normalized) return [""];
  if (normalized.includes(",") && measureLabelLine(normalized) > maxChars) {
    const commaParts = normalized.split(/\s*,\s*/).filter(Boolean);
    if (commaParts.length > 1) return commaParts.slice(0, 3);
  }
  const lines: string[] = [];
  let current = "";
  for (const char of normalized) {
    const charWidth = measureLabelLine(char);
    const currentWidth = measureLabelLine(current);
    if (current && currentWidth + charWidth > maxChars) {
      lines.push(current);
      current = char;
    } else {
      current += char;
    }
  }
  if (current) lines.push(current);
  return lines.slice(0, 3);
}

function measureLabelLine(line: string) {
  return [...line].reduce((sum, char) => {
    if (/[A-Za-z0-9]/.test(char)) return sum + 0.56;
    if (/[_=+\-*/()[\]{}.,]/.test(char)) return sum + 0.38;
    if (/\s/.test(char)) return sum + 0.28;
    return sum + 1;
  }, 0);
}

function maxLineUnits(lines: string[]) {
  return Math.max(1, ...lines.map(measureLabelLine));
}

function textStartY(item: PreviewItem) {
  const blockHeight = item.labelLines.length * 15;
  return item.height / 2 - blockHeight / 2 + 11;
}

function setFramePhase(phase: DiagramFramePhase) {
  framePhase.value = phase;
  isPlaying.value = false;
}

function seekFrame(event: Event) {
  const frame = Number((event.target as HTMLInputElement).value);
  if (!Number.isInteger(frame) || frame < 0 || frame >= totalFrameCount.value) return;
  if (isSemantic.value) {
    selectedStepIndex.value = frame;
    isPlaying.value = false;
    return;
  }
  selectedStepIndex.value = Math.floor(frame / 3);
  framePhase.value = (["entry", "change", "exit"] as DiagramFramePhase[])[frame % 3];
  isPlaying.value = false;
}

function togglePlaying() {
  if (!isPlaying.value && isSemantic.value && selectedStepIndex.value >= semanticFrames.value.length - 1) {
    selectedStepIndex.value = 0;
  }
  isPlaying.value = !isPlaying.value;
}

function previousFrame() {
  advanceFrame(-1);
  isPlaying.value = false;
}

function nextFrame() {
  advanceFrame(1);
  isPlaying.value = false;
}

function advanceFrame(direction: 1 | -1) {
  if (isSemantic.value) {
    const count = semanticFrames.value.length;
    if (count) selectedStepIndex.value = (selectedStepIndex.value + direction + count) % count;
    return;
  }
  const phases: DiagramFramePhase[] = ["entry", "change", "exit"];
  const currentPhaseIndex = phases.indexOf(framePhase.value);
  const nextPhaseIndex = currentPhaseIndex + direction;
  if (nextPhaseIndex >= 0 && nextPhaseIndex < phases.length) {
    framePhase.value = phases[nextPhaseIndex];
    return;
  }

  const nextStep = selectedStepIndex.value + direction;
  if (nextStep >= 0 && nextStep < stepFrames.value.length) {
    selectedStepIndex.value = nextStep;
    framePhase.value = direction > 0 ? "entry" : "exit";
    return;
  }

  selectedStepIndex.value = direction > 0 ? 0 : Math.max(0, stepFrames.value.length - 1);
  framePhase.value = direction > 0 ? "entry" : "exit";
}

function scheduleSemanticPlayback() {
  if (!isPlaying.value || !semanticFrameReady.value || displayedSemanticIndex.value !== selectedStepIndex.value) return;
  const nextIndex = selectedStepIndex.value + 1;
  if (nextIndex >= semanticFrames.value.length) {
    isPlaying.value = false;
    return;
  }
  const generation = playbackGeneration;
  void (activeSemanticMotion?.finished || Promise.resolve()).then(() => {
    if (generation !== playbackGeneration || !isPlaying.value || disposed) return;
    playbackTimer = window.setTimeout(() => {
      playbackTimer = null;
      if (generation === playbackGeneration && isPlaying.value && !disposed) selectedStepIndex.value = nextIndex;
    }, 220);
  });
}

function restartPlayback() {
  stopPlayback();
  if ((!layout.isOpen && !props.sceneOverride) || !isPlaying.value || !totalFrameCount.value) return;
  if (isSemantic.value) {
    scheduleSemanticPlayback();
    return;
  }
  playbackTimer = window.setInterval(() => {
    advanceFrame(1);
  }, 1600);
}

function stopPlayback() {
  playbackGeneration += 1;
  if (playbackTimer === null) return;
  window.clearTimeout(playbackTimer);
  playbackTimer = null;
}

function isRecord(value: unknown): value is SceneRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asText(value: unknown) {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function asNumber(value: unknown, fallback = 0) {
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

onBeforeUnmount(() => {
  disposed = true;
  clearSemanticFrames();
  cancelSceneRequest();
  stopPlayback();
});
</script>
