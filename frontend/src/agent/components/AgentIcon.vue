<template>
  <span
    class="sf-symbol"
    :style="{ '--sf-symbol-size': `${size}px` }"
    aria-hidden="true"
    v-html="svg"
  />
</template>

<script setup lang="ts">
import { computed } from "vue";
import arrowLeft from "../assets/sf-symbols/arrow-left.svg?raw";
import arrowRight from "../assets/sf-symbols/arrow-right.svg?raw";
import brain from "../assets/sf-symbols/brain.svg?raw";
import camera from "../assets/sf-symbols/camera.svg?raw";
import check from "../assets/sf-symbols/check.svg?raw";
import checkCircle from "../assets/sf-symbols/check-circle.svg?raw";
import chevronDown from "../assets/sf-symbols/chevron-down.svg?raw";
import chevronRight from "../assets/sf-symbols/chevron-right.svg?raw";
import copy from "../assets/sf-symbols/copy.svg?raw";
import doc from "../assets/sf-symbols/doc.svg?raw";
import list from "../assets/sf-symbols/list.svg?raw";
import paperclip from "../assets/sf-symbols/paperclip.svg?raw";
import pause from "../assets/sf-symbols/pause.svg?raw";
import pencil from "../assets/sf-symbols/pencil.svg?raw";
import play from "../assets/sf-symbols/play.svg?raw";
import plus from "../assets/sf-symbols/plus.svg?raw";
import repeat from "../assets/sf-symbols/repeat.svg?raw";
import rightPlaceholder from "../assets/sf-symbols/right-placeholder.svg?raw";
import rightSplit from "../assets/sf-symbols/right-split.svg?raw";
import rightUtility from "../assets/sf-symbols/right-utility.svg?raw";
import search from "../assets/sf-symbols/search.svg?raw";
import send from "../assets/sf-symbols/send.svg?raw";
import sidebar from "../assets/sf-symbols/sidebar.svg?raw";
import sparkle from "../assets/sf-symbols/sparkle.svg?raw";
import spinner from "../assets/sf-symbols/spinner.svg?raw";
import stack from "../assets/sf-symbols/stack.svg?raw";
import trash from "../assets/sf-symbols/trash.svg?raw";
import x from "../assets/sf-symbols/x.svg?raw";

const props = withDefaults(
  defineProps<{
    name:
      | "arrow-left"
      | "arrow-right"
      | "brain"
      | "camera"
      | "check"
      | "check-circle"
      | "chevron-down"
      | "chevron-right"
      | "copy"
      | "doc"
      | "list"
      | "paperclip"
      | "pause"
      | "pencil"
      | "play"
      | "plus"
      | "repeat"
      | "right-placeholder"
      | "right-split"
      | "right-utility"
      | "search"
      | "send"
      | "sidebar"
      | "sparkle"
      | "spinner"
      | "stack"
      | "trash"
      | "x";
    size?: number;
  }>(),
  {
    size: 18,
  },
);

const icons = {
  "arrow-left": arrowLeft,
  "arrow-right": arrowRight,
  brain,
  camera,
  check,
  "check-circle": checkCircle,
  "chevron-down": chevronDown,
  "chevron-right": chevronRight,
  copy,
  doc,
  list,
  paperclip,
  pause,
  pencil,
  play,
  plus,
  repeat,
  "right-placeholder": rightPlaceholder,
  "right-split": rightSplit,
  "right-utility": rightUtility,
  search,
  send,
  sidebar,
  sparkle,
  spinner,
  stack,
  trash,
  x,
} satisfies Record<typeof props.name, string>;

const svg = computed(() => icons[props.name]);
</script>
