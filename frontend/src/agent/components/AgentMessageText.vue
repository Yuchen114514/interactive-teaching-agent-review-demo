<template>
  <div class="message-plain-content">
    <div
      ref="textElement"
      class="message-plain-text"
      :class="{ 'is-expanded': isExpanded }"
    >{{ content }}</div>
    <button
      v-if="expandable && isLong"
      class="message-expand-toggle"
      type="button"
      :aria-expanded="isExpanded"
      @click="isExpanded = !isExpanded"
    >{{ isExpanded ? "收起" : "展开" }}</button>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = withDefaults(defineProps<{ content: string; expandable?: boolean }>(), {
  expandable: false,
});
const textElement = ref<HTMLElement | null>(null);
const isExpanded = ref(false);
const isLong = ref(false);
let resizeObserver: ResizeObserver | null = null;
let cornerBubble: HTMLElement | null = null;

function syncUserBubbleRadius() {
  // Percentages in shape() resolve against different axes. Match native
  // border-radius clamping with one length for short user bubbles instead.
  if (
    !props.expandable ||
    !textElement.value ||
    typeof CSS === "undefined" ||
    CSS.supports("corner-shape", "squircle") ||
    !CSS.supports("clip-path", "shape(evenodd from 0 0, line to 100% 0, close)")
  ) return;
  const bubble = textElement.value.closest<HTMLElement>(".message-bubble");
  if (!bubble) return;
  const radius = Number.parseFloat(getComputedStyle(bubble).getPropertyValue("--agent-compat-radius"));
  if (!Number.isFinite(radius)) return;
  // Layout sizes exclude the workspace's animated transform scale.
  const usedRadius = Math.min(radius, bubble.offsetWidth / 2, bubble.offsetHeight / 2);
  if (usedRadius <= 0) return;
  cornerBubble = bubble;
  const value = `${usedRadius}px`;
  if (bubble.style.getPropertyValue("--agent-paint-radius") !== value) {
    bubble.style.setProperty("--agent-paint-radius", value);
  }
}

function measureOverflow() {
  if (!textElement.value) return;
  const lineHeight = Number.parseFloat(window.getComputedStyle(textElement.value).lineHeight);
  if (!Number.isFinite(lineHeight)) return;
  isLong.value = textElement.value.scrollHeight > lineHeight * 3 + 1;
  if (!isLong.value) isExpanded.value = false;
  syncUserBubbleRadius();
}

watch(() => props.content, async () => {
  isExpanded.value = false;
  await nextTick();
  measureOverflow();
});

watch(() => props.expandable, (expandable) => {
  if (!expandable) {
    isExpanded.value = false;
    cornerBubble?.style.removeProperty("--agent-paint-radius");
    cornerBubble = null;
  } else {
    nextTick(syncUserBubbleRadius);
  }
});

onMounted(() => {
  measureOverflow();
  if (typeof ResizeObserver !== "undefined" && textElement.value) {
    resizeObserver = new ResizeObserver(measureOverflow);
    resizeObserver.observe(textElement.value);
  } else {
    window.addEventListener("resize", measureOverflow);
  }
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  cornerBubble?.style.removeProperty("--agent-paint-radius");
  window.removeEventListener("resize", measureOverflow);
});
</script>
