<template>
  <aside class="teaching-float">
    <AgentOverlayScroll class-name="context-body" @viewport="setContextViewport">
        <div v-if="workspace.workspaceLoading && !hasRenderableContext" class="context-skeleton" data-float-layout-key="loading" aria-hidden="true">
          <section class="float-section context-skeleton-card">
            <i class="agent-skeleton-line context-skeleton-kicker" />
            <i class="agent-skeleton-line context-skeleton-title" />
            <i class="agent-skeleton-line context-skeleton-text" />
            <i class="agent-skeleton-line context-skeleton-text short" />
          </section>
          <section class="float-section context-skeleton-card">
            <i class="agent-skeleton-line context-skeleton-kicker" />
            <i class="agent-skeleton-line context-skeleton-step" />
            <i class="agent-skeleton-line context-skeleton-step" />
            <i class="agent-skeleton-line context-skeleton-step short" />
          </section>
        </div>
        <template v-else>
          <AgentProblemWorkspace />
          <AgentWhiteboard data-float-layout-key="whiteboard" @trace="emitTrace" />
        </template>
    </AgentOverlayScroll>
  </aside>
</template>

<script setup lang="ts">
import { computed, nextTick, provide, ref } from "vue";
import AgentOverlayScroll from "./AgentOverlayScroll.vue";
import AgentProblemWorkspace from "./AgentProblemWorkspace.vue";
import AgentWhiteboard from "./AgentWhiteboard.vue";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import {
  FLOAT_LAYOUT_MOTION_KEY,
  getFloatLayoutShifts,
  readFloatLayoutSnapshot,
} from "../utils/floatLayoutMotion";

const emit = defineEmits<{
  trace: [messageId: number | null];
}>();

const workspace = useAgentWorkspaceStore();
const contextBodyRef = ref<HTMLElement | null>(null);
const hasRenderableContext = computed(() =>
  Boolean(
    workspace.selectedWorkspace ||
      workspace.workspaces.length ||
      workspace.blackboard.length,
  ),
);

function setContextViewport(element: HTMLElement | null) {
  contextBodyRef.value = element;
}

function captureLayoutMotion() {
  const container = contextBodyRef.value;
  const before = container ? readFloatLayoutSnapshot(container) : [];

  return async () => {
    await nextTick();
    window.requestAnimationFrame(() => {
      const currentContainer = contextBodyRef.value;
      if (!currentContainer) return;
      const after = readFloatLayoutSnapshot(currentContainer);
      const afterByKey = new Map(after.map((item) => [item.key, item]));
      const shifts = getFloatLayoutShifts(before, after);

      shifts.forEach((shift) => {
        const item = afterByKey.get(shift.key);
        if (!item) return;
        item.element.animate(
          [
            { transform: `translate(${shift.dx}px, ${shift.dy}px)` },
            { transform: "translate(0, 0)" },
          ],
          {
            duration: 460,
            easing: "cubic-bezier(0.16, 1, 0.3, 1)",
          },
        );
      });

    });
  };
}

provide(FLOAT_LAYOUT_MOTION_KEY, captureLayoutMotion);

function emitTrace(messageId: number | null) {
  emit("trace", messageId);
}
</script>
