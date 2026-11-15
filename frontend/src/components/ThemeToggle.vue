<template>
  <div class="appearance-switch">
    <button
      type="button"
      class="appearance-toggle"
      :aria-label="label"
      :title="label"
      @click.stop="appearance.toggle()"
    >
      <span aria-hidden="true" v-html="icon" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { nextAppearanceMode } from '../theme/appearance';
import { useAppearanceStore } from '../theme/store';
import { isEnglishDemo } from '../en-demo/mode';
import lightIcon from '../assets/theme-light.svg?raw';
import darkIcon from '../assets/theme-dark.svg?raw';
import systemIcon from '../assets/theme-system.svg?raw';
const appearance = useAppearanceStore();
const names = isEnglishDemo
  ? {light: 'Light', dark: 'Dark', system: 'System'}
  : {light: '浅色', dark: '深色', system: '随系统'};
const icon = computed(() => ({light: lightIcon, dark: darkIcon, system: systemIcon})[appearance.mode]);
const label = computed(() => isEnglishDemo
  ? `Appearance: ${names[appearance.mode]}. Switch to ${names[nextAppearanceMode(appearance.mode)]}`
  : `当前外观：${names[appearance.mode]}，点击切换为${names[nextAppearanceMode(appearance.mode)]}`);
</script>

<style scoped>
.appearance-switch { position: relative; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; }
.appearance-toggle { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; padding: 0; border: 1px solid transparent; border-radius: 50%; background: transparent; color: var(--app-text-secondary, #536273); cursor: pointer; }
.appearance-toggle:hover { color: var(--app-text-primary, #202a35); background: var(--app-bg-hover, #edf1f5); }
.appearance-toggle:focus-visible { outline: 2px solid var(--app-focus-ring, #397dcc); outline-offset: 3px; }
.appearance-toggle span { display: flex; width: 20px; height: 20px; }
.appearance-toggle :deep(svg) { width: 100%; height: 100%; }
</style>
