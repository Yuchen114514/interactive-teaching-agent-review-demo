<template>
  <Teleport to="body">
    <div
      class="agent-showcase-root agent-aria-tooltip"
      :class="{ 'is-visible': tooltip.visible }"
      :style="{ left: `${tooltip.left}px`, top: `${tooltip.top}px` }"
      role="tooltip"
    >
      {{ tooltip.label }}
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive } from "vue";

const tooltip = reactive({
  visible: false,
  label: "",
  left: 0,
  top: 0,
});

let activeTarget: HTMLElement | null = null;
let showTimer: number | null = null;

const TOOLTIP_TARGET_SELECTOR = [
  "button[aria-label]",
  "a[aria-label]",
  "label[aria-label]",
  "input[aria-label]",
  "textarea[aria-label]",
  "[role='button'][aria-label]",
].join(",");

function clearShowTimer() {
  if (showTimer === null) return;
  window.clearTimeout(showTimer);
  showTimer = null;
}

function hideTooltip() {
  clearShowTimer();
  activeTarget = null;
  tooltip.visible = false;
}

function handlePointerOut(event: PointerEvent | MouseEvent) {
  const target = findTooltipTarget(event.target);
  if (!target || target !== activeTarget) return;
  if (event.relatedTarget instanceof Node && target.contains(event.relatedTarget)) return;
  hideTooltip();
}

function findTooltipTarget(target: EventTarget | null) {
  if (!(target instanceof Element)) return null;
  const candidate = target.closest<HTMLElement>(TOOLTIP_TARGET_SELECTOR);
  if (!candidate) return null;
  if (candidate.closest(".agent-showcase-root") === null && !candidate.classList.contains("agent-showcase-root")) return null;
  const label = candidate.getAttribute("aria-label")?.trim();
  if (!label || candidate.getAttribute("aria-hidden") === "true") return null;
  return candidate;
}

function updatePosition(target: HTMLElement) {
  const rect = target.getBoundingClientRect();
  tooltip.left = Math.round(rect.left + rect.width / 2);
  tooltip.top = Math.round(rect.top - 8);
}

function scheduleTooltip(event: PointerEvent | MouseEvent | FocusEvent) {
  const target = findTooltipTarget(event.target);
  if (!target) {
    hideTooltip();
    return;
  }
  // Descendant pointerover and compatibility mouseover events share one delay.
  if (activeTarget === target) {
    updatePosition(target);
    return;
  }
  clearShowTimer();
  activeTarget = target;
  tooltip.visible = false;
  tooltip.label = target.getAttribute("aria-label") || "";
  updatePosition(target);
  showTimer = window.setTimeout(() => {
    showTimer = null;
    if (activeTarget !== target || !target.isConnected) return;
    updatePosition(target);
    tooltip.visible = true;
  }, 500);
}

function handlePointerMove(event: PointerEvent | MouseEvent) {
  if (!activeTarget || !tooltip.visible) return;
  const target = findTooltipTarget(event.target);
  if (target !== activeTarget) return;
  updatePosition(activeTarget);
}

onMounted(() => {
  document.addEventListener("pointerover", scheduleTooltip, true);
  document.addEventListener("pointermove", handlePointerMove, true);
  document.addEventListener("pointerout", handlePointerOut, true);
  if (!window.PointerEvent) {
    document.addEventListener("mouseover", scheduleTooltip, true);
    document.addEventListener("mousemove", handlePointerMove, true);
    document.addEventListener("mouseout", handlePointerOut, true);
  }
  document.addEventListener("focusin", scheduleTooltip, true);
  document.addEventListener("focusout", hideTooltip, true);
  window.addEventListener("scroll", hideTooltip, true);
  window.addEventListener("resize", hideTooltip);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerover", scheduleTooltip, true);
  document.removeEventListener("pointermove", handlePointerMove, true);
  document.removeEventListener("pointerout", handlePointerOut, true);
  document.removeEventListener("mouseover", scheduleTooltip, true);
  document.removeEventListener("mousemove", handlePointerMove, true);
  document.removeEventListener("mouseout", handlePointerOut, true);
  document.removeEventListener("focusin", scheduleTooltip, true);
  document.removeEventListener("focusout", hideTooltip, true);
  window.removeEventListener("scroll", hideTooltip, true);
  window.removeEventListener("resize", hideTooltip);
  clearShowTimer();
});
</script>
