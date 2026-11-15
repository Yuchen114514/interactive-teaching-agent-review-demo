<template>
  <section class="float-section">
    <div class="float-kicker">板书沉淀</div>
    <div v-if="displayEntries.length === 0" class="empty-note">暂无白板沉淀</div>
    <button
      v-for="(entry, index) in displayEntries"
      v-else
      :key="String(entry.entry_id || entry.id || entry.created_at || entry.title)"
      class="whiteboard-card"
      type="button"
      @click="handleEntryClick($event, entry.source_message_id || null)"
    >
      <div class="whiteboard-card-head">
        <span class="whiteboard-card-index" aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</span>
        <div
          class="whiteboard-card-title"
          v-html="renderWhiteboardInlineMarkdown(whiteboardEntryDisplayTitle(entry))"
        />
      </div>
      <div
        v-if="whiteboardEntryDisplayBody(entry)"
        class="whiteboard-card-body"
        v-html="renderWhiteboardMarkdown(whiteboardEntryDisplayBody(entry))"
      />
    </button>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useAccodingStore } from "@/stores";
import { openAgentPrivateMedia } from "../utils/agentPrivateMediaPreview";
import "katex/dist/katex.min.css";
import { useAgentWorkspaceStore } from "../stores/agentWorkspace";
import {
  filterDisplayWhiteboardEntries,
  whiteboardEntryDisplayBody,
  whiteboardEntryDisplayTitle,
} from "../utils/whiteboardEntries";
import { renderWhiteboardInlineMarkdown, renderWhiteboardMarkdown } from "../utils/whiteboardMarkdown";

const emit = defineEmits<{
  trace: [messageId: number | null];
}>();

const workspace = useAgentWorkspaceStore();
const account = useAccodingStore();
function handleEntryClick(event: MouseEvent, messageId: number | null) {
  const target = event.target instanceof Element ? event.target : null;
  const anchor = target?.closest<HTMLAnchorElement>('a');
  if (anchor) {
    event.stopPropagation();
    if (anchor.dataset.agentMediaUrl) {
      event.preventDefault();
      void openAgentPrivateMedia(anchor.dataset.agentMediaUrl, account.token, anchor.textContent || '图片').catch(() => undefined);
    }
    return;
  }
  emit('trace', messageId);
}
const displayEntries = computed(() => filterDisplayWhiteboardEntries(workspace.blackboard));
</script>
