<template>
  <component
    :is="openable ? 'button' : 'span'"
    ref="rootElement"
    class="agent-authenticated-image"
    :type="openable ? 'button' : undefined"
    :aria-label="openable ? `打开${filename || alt}` : undefined"
    :aria-description="error || undefined"
    :aria-busy="loading || opening"
    :disabled="openable && (!token || opening)"
    @click="openOriginal"
  >
    <img v-if="imageSrc" :src="imageSrc" :alt="alt" decoding="async" referrerpolicy="no-referrer" />
    <span v-else class="agent-image-status" role="status">{{ error ? '图片不可用' : '加载图片…' }}</span>
    <slot :image-src="imageSrc" :error="error" :loading="loading" />
  </component>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { AuthenticatedMediaResource } from '../utils/authenticatedMedia';

const props = withDefaults(defineProps<{
  token: string;
  src: string;
  originalSrc?: string;
  alt?: string;
  filename?: string;
  openable?: boolean;
}>(), {
  originalSrc: '',
  alt: '图片',
  filename: '',
  openable: true,
});

const rootElement = ref<HTMLElement | null>(null);
const imageSrc = ref('');
const error = ref('');
const loading = ref(false);
const opening = ref(false);
const visible = ref(typeof IntersectionObserver === 'undefined');
const thumbnail = new AuthenticatedMediaResource();
const original = new AuthenticatedMediaResource();
let generation = 0;
let thumbnailVersion = 0;
let observer: IntersectionObserver | null = null;
let previewWindow: Window | null = null;
let previewPoll: ReturnType<typeof setInterval> | null = null;

function closeOriginal() {
  original.clear();
  if (previewPoll !== null) clearInterval(previewPoll);
  previewPoll = null;
  if (previewWindow && !previewWindow.closed) previewWindow.close();
  previewWindow = null;
  opening.value = false;
}

async function loadThumbnail() {
  const version = ++thumbnailVersion;
  thumbnail.clear();
  imageSrc.value = '';
  error.value = '';
  loading.value = false;
  if (!visible.value || !props.token || !props.src) return;
  loading.value = true;
  try {
    const url = await thumbnail.load(props.src, props.token);
    if (thumbnailVersion === version) imageSrc.value = url;
  } catch (cause) {
    if (thumbnailVersion === version) error.value = cause instanceof Error ? cause.message : '图片加载失败';
  } finally {
    if (thumbnailVersion === version) loading.value = false;
  }
}

function downloadName() {
  const name = props.filename.replace(/[\\/\u0000-\u001f\u007f]/g, '_').slice(0, 180);
  return /\.(?:png|jpe?g|gif|webp)$/i.test(name) ? name : 'image';
}

async function openOriginal() {
  if (!props.openable || !props.token || opening.value) return;
  closeOriginal();
  // Create the window synchronously in the click gesture, including in Safari.
  const target = window.open('about:blank', '_blank');
  if (!target) {
    error.value = '浏览器阻止了图片窗口，请允许弹出窗口后重试';
    return;
  }
  target.opener = null;
  previewWindow = target;
  opening.value = true;
  const version = generation;
  const document = target.document;
  document.documentElement.lang = 'zh-CN';
  document.title = props.filename || props.alt;
  document.body.textContent = '正在加载图片…';
  document.body.style.cssText = 'margin:24px;font:16px system-ui;background:#171e26;color:#e3ebf3;';
  previewPoll = setInterval(() => {
    if (previewWindow === target && target.closed) closeOriginal();
  }, 1000);
  try {
    const url = await original.load(props.originalSrc || props.src.replace(/\/thumbnail$/, ''), props.token);
    if (version !== generation || target.closed || previewWindow !== target) return;
    const image = document.createElement('img');
    image.src = url;
    image.alt = props.alt;
    image.style.cssText = 'display:block;max-width:100%;height:auto;margin-top:20px;';
    const download = document.createElement('a');
    download.href = url;
    download.download = downloadName();
    download.textContent = '下载图片';
    download.style.color = '#b9d8f3';
    // DOM nodes keep user filenames and alt text out of an HTML parsing sink.
    document.body.replaceChildren(download, image);
  } catch (cause) {
    if (version === generation && !target.closed && previewWindow === target) {
      const message = cause instanceof Error ? cause.message : '图片加载失败';
      error.value = message;
      document.body.textContent = message;
    }
  } finally {
    if (version === generation && previewWindow === target) opening.value = false;
  }
}

watch(() => [props.token, props.src, props.originalSrc], () => {
  generation += 1;
  closeOriginal();
  void loadThumbnail();
}, { immediate: true, flush: 'sync' });
watch(visible, () => { void loadThumbnail(); });

onMounted(() => {
  if (typeof IntersectionObserver === 'undefined' || !rootElement.value) {
    visible.value = true;
    return;
  }
  observer = new IntersectionObserver(entries => {
    if (!entries.some(entry => entry.isIntersecting)) return;
    visible.value = true;
    observer?.disconnect();
    observer = null;
  }, { rootMargin: '160px' });
  observer.observe(rootElement.value);
});

onBeforeUnmount(() => {
  generation += 1;
  thumbnailVersion += 1;
  observer?.disconnect();
  thumbnail.clear();
  closeOriginal();
});
</script>

<style scoped>
.agent-image-status {
  display: grid;
  place-items: center;
  width: 54px;
  min-height: 42px;
  font-size: 10px;
  white-space: normal;
  opacity: 0.7;
}
</style>
