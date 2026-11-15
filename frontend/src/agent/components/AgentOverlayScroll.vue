<template>
  <OverlayScrollbarsComponent
    :element="element"
    :class="['agent-overlay-scroll', className]"
    :options="mergedOptions"
    defer
    @os-initialized="handleInitialized"
    @os-updated="handleUpdated"
    @os-scroll="handleScroll"
    @os-destroyed="handleDestroyed"
  >
    <slot />
  </OverlayScrollbarsComponent>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount } from "vue";
import { OverlayScrollbarsComponent } from "overlayscrollbars-vue";
import type { EventListeners, OverlayScrollbars, PartialOptions } from "overlayscrollbars";

const props = withDefaults(
  defineProps<{
    element?: string;
    className?: string;
    options?: PartialOptions;
    events?: EventListeners;
  }>(),
  {
    element: "div",
    className: "",
    options: undefined,
    events: undefined,
  },
);

const emit = defineEmits<{
  viewport: [element: HTMLElement | null];
  scroll: [event: Event];
}>();

const mergedOptions = computed<PartialOptions>(() => ({
  overflow: {
    x: "hidden",
    y: "scroll",
    ...props.options?.overflow,
  },
  scrollbars: {
    theme: "os-theme-agent",
    visibility: "auto",
    autoHide: "leave",
    autoHideDelay: 520,
    autoHideSuspend: false,
    dragScroll: true,
    clickScroll: true,
    pointers: ["mouse", "touch", "pen"],
    ...props.options?.scrollbars,
  },
  update: props.options?.update,
}));

function resolveScrollElement(instance: OverlayScrollbars | null) {
  if (!instance) return null;
  const { host, viewport } = instance.elements();
  if (viewport && viewport.scrollHeight >= viewport.clientHeight) return viewport;
  if (host && host.scrollHeight >= host.clientHeight) return host;
  return viewport ?? host ?? null;
}

function emitViewport(instance: OverlayScrollbars | null) {
  emit("viewport", resolveScrollElement(instance));
}

function callListener<T extends unknown[]>(listener: ((...args: T) => void) | ((...args: T) => void)[] | null | undefined, ...args: T) {
  if (Array.isArray(listener)) {
    listener.forEach((item) => item(...args));
    return;
  }
  listener?.(...args);
}

function handleInitialized(instance: OverlayScrollbars) {
  emitViewport(instance);
  callListener(props.events?.initialized, instance);
}

function handleUpdated(instance: OverlayScrollbars, updateHints: Parameters<NonNullable<Exclude<EventListeners["updated"], unknown[]>>>[1]) {
  emitViewport(instance);
  callListener(props.events?.updated, instance, updateHints);
}

function handleScroll(instance: OverlayScrollbars, event: Event) {
  emit("scroll", event);
  callListener(props.events?.scroll, instance, event);
}

function handleDestroyed(instance: OverlayScrollbars, canceled: boolean) {
  emit("viewport", null);
  callListener(props.events?.destroyed, instance, canceled);
}

onBeforeUnmount(() => {
  emit("viewport", null);
});
</script>
