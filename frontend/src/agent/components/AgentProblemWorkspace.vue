<template>
  <section
    class="float-section current-problem"
    data-float-layout-key="problem"
  >
    <div class="float-kicker">当前题目</div>
    <div v-if="!workspace.selectedWorkspace" class="empty-note">暂无题目工作区</div>
    <template v-else>
      <div class="problem-title-copy">
        <strong>{{ currentTitle }}</strong>
        <small v-if="workspace.selectedWorkspace.problem_id">
          题目 ID {{ workspace.selectedWorkspace.problem_id }}
        </small>
      </div>

      <div class="problem-preview-block">
        <div class="problem-preview-head">
          <a
            v-if="routeAction?.kind === 'return'"
            class="problem-action-pill problem-return-link"
            :href="routeAction.href"
          >
            {{ routeAction.label }}
          </a>
          <span v-else-if="routeAction?.kind === 'current'" class="problem-page-chip">{{ routeAction.label }}</span>
          <button class="problem-action-pill problem-preview-toggle" type="button" @click="togglePreview">
            {{ previewExpanded ? "收起题面" : "展开题面" }}
          </button>
        </div>
        <div
          ref="previewBodyRef"
          class="problem-preview-motion"
          :class="{ 'is-open': previewExpanded }"
          @transitionend="finishPreviewHeightAnimation"
        >
          <div v-if="!previewExpanded" key="collapsed" class="problem-preview-state problem-preview-collapsed">
            <p class="problem-preview-copy">{{ summaryPreview }}</p>
          </div>
          <div v-else key="expanded" class="problem-preview-state problem-preview-expanded">
            <div class="problem-expanded">
              <AgentMessageRenderer
                v-if="workspace.selectedWorkspace.problem_summary"
                :content="workspace.selectedWorkspace.problem_summary"
              />
              <div v-else class="empty-note">当前题目还没有正文摘要。</div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </section>

  <section class="float-section problem-plan-section" data-float-layout-key="problem-plan">
    <div class="float-kicker">题目规划</div>
    <div v-if="!workspace.selectedWorkspace" class="empty-note">暂无题目规划</div>
    <div v-else-if="steps.length === 0" class="empty-note">教学计划生成后会出现在这里。</div>
    <div v-else class="step-chain">
      <div
        v-for="(step, index) in steps"
        :key="step.step_key || step.title || index"
        class="step-dot-row"
        :class="{ 'is-current': isCurrentStep(step), 'is-reached': isReachedStep(step) }"
      >
        <i />
        <span>{{ step.title || step.label || step.step_key || `步骤 ${index + 1}` }}</span>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useRoute } from "vue-router";
import AgentMessageRenderer from "./AgentMessageRenderer.vue";
import type { AgentWorkspaceStep } from "../api/types";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import { readPreviewMotionTargetHeight } from "../utils/floatLayoutMotion";
import { getProblemRouteAction } from "../utils/problemWorkspaceUi";

const route = useRoute();
const workspace = useAgentWorkspaceStore();
const previewExpanded = ref(false);
const previewBodyRef = ref<HTMLElement | null>(null);

const steps = computed(() => {
  const selected = workspace.selectedWorkspace;
  if (!selected) return [];
  if (Array.isArray(selected.steps) && selected.steps.length) return selected.steps;
  return selected.current_step ? [selected.current_step] : [];
});
const currentTitle = computed(() => compactTitle(workspace.selectedWorkspace));
const currentPageProblemId = computed(() => {
  const raw = route.params.id ?? route.params.problemId;
  const value = Array.isArray(raw) ? raw[0] : raw;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
});
const routeAction = computed(() =>
  getProblemRouteAction({
    problemId: workspace.selectedWorkspace?.problem_id,
    currentPageProblemId: currentPageProblemId.value,
  }),
);
const summaryPreview = computed(() => workspacePreview(workspace.selectedWorkspace, 96, "点击展开查看题目内容"));

function compactTitle(item: typeof workspace.selectedWorkspace) {
  const raw = item?.problem_title || item?.title || (item?.problem_id ? `题目 #${item.problem_id}` : "当前题目");
  const normalized = String(raw).replace(/\s+/g, " ").trim();
  return normalized.length > 20 ? `${normalized.slice(0, 20)}...` : normalized;
}

function workspacePreview(item: typeof workspace.selectedWorkspace, limit = 42, fallback = "暂无摘要") {
  const raw = item?.problem_summary || item?.title || item?.problem_title || "";
  const normalized = String(raw).replace(/[#*_`>$\\]/g, "").replace(/\s+/g, " ").trim();
  if (!normalized) return item?.problem_id ? `题目 #${item.problem_id}` : fallback;
  return normalized.length > limit ? `${normalized.slice(0, limit)}...` : normalized;
}

function settlePreviewHeightAnimation(element: HTMLElement) {
  return new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      element.removeEventListener("transitionend", handleTransitionEnd);
      window.clearTimeout(timer);
      resolve();
    };
    const handleTransitionEnd = (event: TransitionEvent) => {
      if (event.propertyName === "height" && event.target === element) finish();
    };
    const timer = window.setTimeout(finish, 470);
    element.addEventListener("transitionend", handleTransitionEnd);
  });
}

async function togglePreview() {
  const previewBody = previewBodyRef.value;
  if (!previewBody) {
    previewExpanded.value = !previewExpanded.value;
    return;
  }
  const startHeight = previewBody.getBoundingClientRect().height;
  previewBody.style.height = `${startHeight}px`;
  previewBody.style.overflow = "hidden";
  previewBody.getBoundingClientRect();
  previewExpanded.value = !previewExpanded.value;
  await nextTick();
  const endHeight = readPreviewMotionTargetHeight(previewBody);
  window.requestAnimationFrame(() => {
    previewBody.getBoundingClientRect();
    previewBody.style.height = `${endHeight}px`;
    void settlePreviewHeightAnimation(previewBody).then(() => {
      finishPreviewElement(previewBody);
    });
  });
}

function finishPreviewHeightAnimation(event: TransitionEvent) {
  if (event.propertyName !== "height" || event.target !== previewBodyRef.value) return;
  const previewBody = previewBodyRef.value;
  if (!previewBody) return;
  finishPreviewElement(previewBody);
}

function finishPreviewElement(previewBody: HTMLElement) {
  previewBody.style.height = `${readPreviewMotionTargetHeight(previewBody)}px`;
  previewBody.style.overflow = "";
}

function isCurrentStep(step: AgentWorkspaceStep) {
  return step.is_current === true || step.isCurrent === true;
}

function isReachedStep(step: AgentWorkspaceStep) {
  return ["done", "completed", "reached", "current"].includes(String(step.status || "").toLowerCase()) || isCurrentStep(step);
}
</script>
