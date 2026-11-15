<template>
  <div class="agent-settings-scrim" @click.self="emit('close')">
    <section
      ref="dialogRef"
      class="agent-settings-panel"
      role="dialog"
      aria-modal="true"
      aria-label="设置"
      tabindex="-1"
    >
      <button
        class="agent-settings-close-button"
        type="button"
        aria-label="关闭设置"
        @click="emit('close')"
      >
        <AgentIcon name="x" :size="16" />
      </button>

      <div class="agent-settings-layout" :class="`agent-font-size-${preferences.fontSize}`">
        <nav class="agent-settings-nav" aria-label="设置分类">
          <button
            v-for="section in settingsSections"
            :key="section.id"
            type="button"
            class="session-row agent-settings-nav-item"
            :class="{ 'is-active': activeSection === section.id }"
            :aria-current="activeSection === section.id ? 'page' : undefined"
            @click="activeSection = section.id"
          >
            <span
              class="agent-settings-nav-mark"
              :style="{
                maskImage: `url(${settingsSectionIcon})`,
                WebkitMaskImage: `url(${settingsSectionIcon})`,
              }"
              aria-hidden="true"
            />
            <span class="agent-settings-nav-label">{{ section.label }}</span>
          </button>
        </nav>

        <main v-if="activeSection === 'appearance'" class="agent-settings-detail">
          <div class="agent-settings-detail-head">
            <div>
              <h3>助教外观</h3>
              <p>选择助教皮肤，并预览判题过程中的静态状态。</p>
            </div>
            <span v-if="preferences.loading" class="agent-settings-status">正在加载</span>
            <span v-else-if="preferences.saving" class="agent-settings-status is-saving">正在保存</span>
            <span v-else class="agent-settings-status is-saved">已同步</span>
          </div>

          <div v-if="preferences.loading && !preferences.loaded" class="agent-settings-loading" aria-label="正在加载外观设置">
            <i /><i /><i />
          </div>
          <template v-else>
            <div class="agent-settings-skin-list" role="radiogroup" aria-label="助教皮肤">
              <button
                v-for="skinId in AGENT_MASCOT_SKIN_IDS"
                :key="skinId"
                type="button"
                role="radio"
                :aria-checked="preferences.skin === skinId"
                :class="{ 'is-selected': preferences.skin === skinId }"
                :disabled="preferences.saving"
                @click="selectSkin(skinId)"
              >
                <span
                  class="agent-settings-skin-swatch"
                  aria-hidden="true"
                  :style="{
                    '--skin-swatch-top': skinAppearance(skinId).palette.top,
                    '--skin-swatch-front': skinAppearance(skinId).palette.front,
                    '--skin-swatch-side': skinAppearance(skinId).palette.side,
                  }"
                />
                <span>{{ AGENT_MASCOT_SKINS[skinId].label }}</span>
                <AgentIcon v-if="preferences.skin === skinId" name="check" :size="14" />
              </button>
            </div>

            <section class="agent-settings-font-size" aria-labelledby="agent-font-size-title">
              <div class="agent-settings-font-size-heading">
                <strong id="agent-font-size-title">界面字号</strong>
                <span>全局调整文字大小</span>
              </div>
              <div class="agent-settings-font-size-options" role="radiogroup" aria-label="界面字号">
                <button
                  v-for="option in fontSizeOptions"
                  :key="option.value"
                  type="button"
                  role="radio"
                  :aria-checked="preferences.fontSize === option.value"
                  :class="{ 'is-selected': preferences.fontSize === option.value }"
                  :disabled="preferences.saving"
                  @click="selectFontSize(option.value)"
                >
                  <span :class="`font-size-sample is-${option.value}`">Aa</span>
                  <span>{{ option.label }}</span>
                </button>
              </div>
            </section>

            <div class="agent-settings-preview">
              <div class="agent-settings-preview-stage">
                <AgentMascot
                  :state="previewState"
                  :skin="preferences.skin"
                />
              </div>
              <div class="agent-settings-preview-controls">
                <span>状态预览</span>
                <div role="group" aria-label="助教状态预览">
                  <button
                    v-for="state in AGENT_MASCOT_STATES"
                    :key="state"
                    type="button"
                    :class="{ 'is-selected': previewState === state }"
                    :aria-pressed="previewState === state"
                    @click="previewState = state"
                  >
                    {{ AGENT_MASCOT_LABELS[state] }}
                  </button>
                </div>
              </div>
            </div>
          </template>

          <div v-if="preferences.error" class="agent-settings-error" role="alert">
            <span>{{ preferences.error }}</span>
            <button type="button" @click="retryPreferences">重试</button>
          </div>
        </main>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useAccodingStore } from "@/stores";
import AgentIcon from "./AgentIcon.vue";
import AgentMascot from "./AgentMascot.vue";
import settingsSectionIcon from "../assets/sf-symbols/U+EF15.svg";
import { useAgentPreferencesStore } from "../stores/agentPreferences";
import type { AgentFontSizePreference } from "../api/types";
import {
  AGENT_MASCOT_LABELS,
  AGENT_MASCOT_SKIN_IDS,
  AGENT_MASCOT_SKINS,
  AGENT_MASCOT_STATES,
  type AgentMascotSkinId,
  type AgentMascotState,
} from "../utils/agentMascot";

import { useAppearanceStore } from "../../theme/store";
import { resolveMascotAppearance } from "../utils/agentMascotAppearance";
const appearance = useAppearanceStore();
const skinAppearance = (id: AgentMascotSkinId) => resolveMascotAppearance(id, appearance.scheme, "idle");

type SettingsSectionId = "appearance";

const settingsSections: ReadonlyArray<{ id: SettingsSectionId; label: string }> = [
  { id: "appearance", label: "外观" },
];
const fontSizeOptions: ReadonlyArray<{ value: AgentFontSizePreference; label: string }> = [
  { value: "small", label: "小" },
  { value: "medium", label: "中" },
  { value: "large", label: "大" },
];

const emit = defineEmits<{ close: [] }>();
const accoding = useAccodingStore();
const preferences = useAgentPreferencesStore();
const dialogRef = ref<HTMLElement | null>(null);
const activeSection = ref<SettingsSectionId>("appearance");
const previewState = ref<AgentMascotState>("idle");
const token = computed(() => accoding.token || "");

function selectSkin(skinId: AgentMascotSkinId) {
  if (!token.value || preferences.skin === skinId) return;
  void preferences.selectSkin(token.value, skinId);
}

function selectFontSize(fontSize: AgentFontSizePreference) {
  if (!token.value || preferences.fontSize === fontSize) return;
  void preferences.selectFontSize(token.value, fontSize);
}

function retryPreferences() {
  if (!token.value) return;
  preferences.loaded = false;
  void preferences.load(token.value);
}

function handleWindowKeydown(event: KeyboardEvent) {
  if (event.key !== "Escape") return;
  event.preventDefault();
  emit("close");
}

onMounted(() => {
  window.addEventListener("keydown", handleWindowKeydown);
  void nextTick(() => dialogRef.value?.focus());
  if (token.value) void preferences.load(token.value);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleWindowKeydown);
});
</script>
