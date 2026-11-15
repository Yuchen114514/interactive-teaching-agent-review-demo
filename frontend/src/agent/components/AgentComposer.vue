<template>
  <footer ref="composerShellRef" class="composer-shell">
    <div class="composer-attachment-track" :class="{ 'is-empty': !workspace.draftImages.length }">
      <TransitionGroup name="attachment-chip-motion" tag="div" class="composer-image-strip">
        <div
          v-for="image in workspace.draftImages"
          :key="image.image_id"
          class="composer-image-chip"
        >
          <img
            v-if="image.previewUrl?.startsWith('blob:')"
            :src="image.previewUrl"
            alt="uploaded"
          />
          <AgentAuthenticatedImage
            v-else
            :token="store.token"
            :src="image.thumbnail_url || image.url"
            :filename="image.filename"
            :openable="false"
            alt="uploaded"
          />
          <span>{{ image.filename || "图片" }}</span>
          <button class="composer-image-remove" type="button" aria-label="移除图片" @click="removeImage(image.image_id)">
            <AgentIcon name="x" :size="10" />
          </button>
        </div>
      </TransitionGroup>
    </div>

    <div ref="composerCardRef" class="composer-card" @paste="handlePaste">
      <textarea
        v-model="draft"
        rows="1"
        :maxlength="AGENT_DRAFT_MAX_LENGTH"
        placeholder="输入你的问题..."
        :disabled="disabled"
        @keydown.enter.exact.prevent="submit"
      />
      <div class="composer-controls">
        <div class="composer-action-cluster">
          <div ref="depthPickerRef" class="depth-picker" :class="{ 'is-open': depthMenuOpen }">
            <button class="depth-trigger" type="button" @click.stop="toggleDepthMenu">
              <AgentIcon name="brain" :size="12" />
              <span>{{ activeDepth?.label || "粒度" }}</span>
              <AgentIcon name="chevron-down" :size="10" />
            </button>
            <div class="depth-switcher floating-option-menu" :class="{ 'is-open': depthMenuOpen }">
              <button
                v-for="option in depthOptions"
                :key="option.value"
                type="button"
                class="floating-option-item"
                :class="{ 'is-active': workspace.guidanceDepth === option.value }"
                @click="selectDepth(option.value)"
              >
                <span v-if="workspace.guidanceDepth === option.value" class="depth-active-bg" />
                <span class="depth-icon">
                  <AgentIcon :name="option.icon" :size="12" />
                </span>
                <span class="depth-copy">
                  <strong>{{ option.label }}</strong>
                  <small>{{ option.description }}</small>
                </span>
                <span class="depth-check">
                  <AgentIcon v-if="workspace.guidanceDepth === option.value" name="check" :size="11" />
                </span>
              </button>
            </div>
          </div>
          <label class="composer-tool-button" :class="{ 'is-loading': uploading }" aria-label="上传图片">
            <AgentIcon :name="uploading ? 'spinner' : 'paperclip'" :size="12" :class="{ spin: uploading }" />
            <input
              type="file"
              accept="image/*"
              multiple
              :disabled="disabled || running || uploading"
              @change="uploadImages"
            />
          </label>
        </div>

        <button
          v-if="running"
          class="send-button is-stop"
          type="button"
          aria-label="停止生成"
          @click="$emit('stop')"
        >
          <AgentIcon name="pause" :size="12" />
        </button>
        <button
          v-else
          class="send-button"
          type="button"
          aria-label="发送"
          :disabled="disabled || uploading || !draft.trim()"
          @click="submit"
        >
          <AgentIcon name="send" :size="12" />
        </button>
      </div>
    </div>
    <slot name="below-composer" />
  </footer>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import AgentIcon from "./AgentIcon.vue";
import AgentAuthenticatedImage from "./AgentAuthenticatedImage.vue";
import { uploadAgentImage } from "../api/agentApi";
import { captureAgentAccount } from "../utils/agentAccountScope";
import { agentDraftCache, AGENT_DRAFT_MAX_LENGTH, type AgentDraftSnapshot } from "../utils/agentDraftCache";
import { uploadAgentImageBatch, releaseAgentImagePreviews } from "../utils/agentImageUploads";
import { useAgentWorkspaceStore, type GuidanceDepth } from "../stores/agentWorkspace";
import { useAccodingStore } from "@/stores";

const props = defineProps<{
  disabled?: boolean;
  running?: boolean;
  initialDraft?: string;
}>();

const emit = defineEmits<{
  send: [message: string, onAccepted: () => void];
  stop: [];
}>();

const store = useAccodingStore();
const workspace = useAgentWorkspaceStore();
const draft = ref("");
const uploading = ref(false);
const depthMenuOpen = ref(false);
const depthPickerRef = ref<HTMLElement | null>(null);
const composerShellRef = ref<HTMLElement | null>(null);
const composerCardRef = ref<HTMLElement | null>(null);
let composerStage: HTMLElement | null = null;
let composerResizeObserver: ResizeObserver | null = null;
let composerShellResizeObserver: ResizeObserver | null = null;

const depthOptions: { value: GuidanceDepth; label: string; description: string; icon: "brain" | "sparkle" | "list" | "send" }[] = [
  { value: "socratic", label: "反问为主", description: "先接住发言，再用问题引导", icon: "brain" },
  { value: "coached", label: "教练提示", description: "给关键提示，保留推理空间", icon: "sparkle" },
  { value: "minimal", label: "简洁推进", description: "少讲背景，直接推进下一步", icon: "list" },
  { value: "direct", label: "直接回答", description: "按用户问题完整回答", icon: "send" },
];

const activeDepth = computed(() => depthOptions.find((option) => option.value === workspace.guidanceDepth));

let disposed = false;
let restoringDraft = false;
let uploadController: AbortController | null = null;
let saveDraftTimer: number | null = null;
type DraftBinding = AgentDraftSnapshot & { token: string; sessionId: string | null; revision: number; account: ReturnType<typeof captureAgentAccount> };
let draftBinding: DraftBinding | null = null;

function persistDraft(binding = draftBinding) {
  if (!binding?.account.isCurrent()) return;
  void agentDraftCache.write(binding.token, binding.sessionId, { text: binding.text, images: [...binding.images] });
}

function cancelDraftSave() {
  if (saveDraftTimer !== null) window.clearTimeout(saveDraftTimer);
  saveDraftTimer = null;
}

function scheduleDraftSave() {
  cancelDraftSave();
  saveDraftTimer = window.setTimeout(() => { saveDraftTimer = null; persistDraft(); }, 200);
}

function cancelImageUploads() {
  uploadController?.abort();
  uploadController = null;
  uploading.value = false;
}

watch([() => store.token, () => workspace.currentSessionId], async ([token, sessionId]) => {
  cancelDraftSave();
  persistDraft();
  cancelImageUploads();
  restoringDraft = true;
  releaseAgentImagePreviews(workspace.draftImages);
  workspace.clearDraftImages();
  draft.value = "";
  const binding: DraftBinding = { token, sessionId, text: "", images: [], revision: 0, account: captureAgentAccount(token) };
  draftBinding = binding;
  restoringDraft = false;
  const cached = await agentDraftCache.read(token, sessionId);
  if (disposed || draftBinding !== binding || !binding.account.isCurrent() || binding.revision !== 0) return;
  restoringDraft = true;
  binding.text = props.initialDraft || cached?.text || "";
  binding.images = cached?.images || [];
  draft.value = binding.text;
  workspace.setDraftImages(binding.images);
  if (props.initialDraft) workspace.setComposerDraft("");
  restoringDraft = false;
}, { immediate: true, flush: "sync" });

watch(draft, value => {
  if (disposed || restoringDraft || !draftBinding) return;
  draftBinding.text = value;
  draftBinding.revision++;
  scheduleDraftSave();
}, { flush: "sync" });

watch(() => workspace.draftImages, images => {
  if (disposed || restoringDraft || !draftBinding) return;
  // Bootstrap clears an already empty attachment list after changing sessions.
  if (JSON.stringify(draftBinding.images) === JSON.stringify(images)) return;
  draftBinding.images = [...images];
  draftBinding.revision++;
  scheduleDraftSave();
}, { flush: "sync" });

watch(() => props.initialDraft, value => {
  if (!value || !draftBinding?.account.isCurrent()) return;
  draft.value = value;
  workspace.setComposerDraft("");
});

function closeDepthMenu() {
  depthMenuOpen.value = false;
  window.removeEventListener("pointerdown", handleDepthOutsidePointerDown);
}

function handleDepthOutsidePointerDown(event: PointerEvent) {
  if (depthPickerRef.value?.contains(event.target as Node | null)) return;
  closeDepthMenu();
}

function openDepthMenu() {
  if (depthMenuOpen.value) return;
  depthMenuOpen.value = true;
  window.addEventListener("pointerdown", handleDepthOutsidePointerDown);
}

function toggleDepthMenu() {
  if (depthMenuOpen.value) closeDepthMenu();
  else openDepthMenu();
}

function selectDepth(depth: GuidanceDepth) {
  workspace.setGuidanceDepth(depth);
  closeDepthMenu();
}

function submit() {
  if (props.disabled || props.running || uploading.value || !draftBinding?.account.isCurrent()) return;
  const value = draft.value.trim();
  if (!value) return;
  const binding = draftBinding;
  const submitted: AgentDraftSnapshot = { text: draft.value, images: [...workspace.draftImages] };
  cancelDraftSave();
  persistDraft(binding);
  emit("send", value, () => {
    if (!binding.account.isCurrent()) return;
    void agentDraftCache.removeIfMatches(binding.token, binding.sessionId, submitted);
    if (disposed || draftBinding !== binding || draft.value !== submitted.text) return;
    draft.value = "";
    workspace.setComposerDraft("");
    cancelDraftSave();
    persistDraft(binding);
  });
}

async function addImageFiles(files: File[]) {
  if (!files.length || props.disabled || props.running || uploading.value) return;
  if (!store.token || !draftBinding?.account.isCurrent()) {
    ElMessage.warning("请登录后上传图片");
    return;
  }
  const binding = draftBinding;
  const controller = new AbortController();
  uploadController = controller;
  uploading.value = true;
  try {
    const uploaded = await uploadAgentImageBatch(files, workspace.draftImages, {
      signal: controller.signal,
      isCurrent: () => !disposed && draftBinding === binding && binding.account.isCurrent(),
      upload: (file, signal) => uploadAgentImage(binding.token, file, signal),
    });
    if (disposed || draftBinding !== binding || !binding.account.isCurrent()) {
      releaseAgentImagePreviews(uploaded);
      return;
    }
    workspace.setDraftImages([...workspace.draftImages, ...uploaded]);
  } catch (error) {
    if (!controller.signal.aborted && binding.account.isCurrent() && !disposed) {
      ElMessage.warning(`上传图片失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  } finally {
    if (uploadController === controller) {
      uploadController = null;
      uploading.value = false;
    }
  }
}

async function uploadImages(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  await addImageFiles(files);
}

async function handlePaste(event: ClipboardEvent) {
  const items = Array.from(event.clipboardData?.items || []);
  const imageFiles = items
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
  if (!imageFiles.length) return;
  event.preventDefault();
  await addImageFiles(imageFiles);
}

function removeImage(imageId: string) {
  const removed = workspace.draftImages.find((image) => image.image_id === imageId);
  if (removed?.previewUrl?.startsWith("blob:")) {
    URL.revokeObjectURL(removed.previewUrl);
  }
  workspace.setDraftImages(workspace.draftImages.filter((image) => image.image_id !== imageId));
}

function syncComposerMetrics() {
  const card = composerCardRef.value;
  const shell = composerShellRef.value;
  if (!card || !shell) return;
  composerStage = card.closest(".conversation-stage") as HTMLElement | null;
  if (!composerStage) return;
  // Layout dimensions stay stable while the whole workspace is visually scaled.
  composerStage.style.setProperty("--composer-card-height", `${card.offsetHeight}px`);
  composerStage.style.setProperty("--composer-shell-height", `${shell.offsetHeight}px`);
}

onMounted(async () => {
  await nextTick();
  if (disposed) return;
  syncComposerMetrics();
  if (composerCardRef.value && typeof ResizeObserver !== "undefined") {
    composerResizeObserver = new ResizeObserver(syncComposerMetrics);
    composerResizeObserver.observe(composerCardRef.value);
  }
  if (composerShellRef.value && typeof ResizeObserver !== "undefined") {
    composerShellResizeObserver = new ResizeObserver(syncComposerMetrics);
    composerShellResizeObserver.observe(composerShellRef.value);
  }
  window.addEventListener("resize", syncComposerMetrics);
});

onBeforeUnmount(() => {
  disposed = true;
  cancelDraftSave();
  persistDraft();
  cancelImageUploads();
  releaseAgentImagePreviews(workspace.draftImages);
  workspace.clearDraftImages();
  window.removeEventListener("pointerdown", handleDepthOutsidePointerDown);
  window.removeEventListener("resize", syncComposerMetrics);
  composerResizeObserver?.disconnect();
  composerShellResizeObserver?.disconnect();
  composerStage?.style.removeProperty("--composer-card-height");
  composerStage?.style.removeProperty("--composer-shell-height");
});
</script>
