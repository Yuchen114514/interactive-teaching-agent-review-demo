<template>
  <div class="agent-rendered-message">
  <div
    class="message-content"
    v-html="renderedHtml"
    @click="handleRenderedClick"
  />
  <button v-if="content.length > renderLimit" class="markdown-render-more" type="button" @click="renderLimit += 50000">继续显示内容</button>
  </div>
</template>

<script setup lang="ts">
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import css from "highlight.js/lib/languages/css";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import katex from "katex";
import "katex/dist/katex.min.css";
import MarkdownIt from "markdown-it";
import texmath from "markdown-it-texmath";
import type { RenderRule } from "markdown-it/lib/renderer.mjs";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { installAgentMarkdownResources } from "../utils/agentMarkdownResources";
import { agentMarkdownRenderCache } from "../utils/agentMarkdownRenderCache";
import { openAgentPrivateMedia } from "../utils/agentPrivateMediaPreview";
import { useAccodingStore } from "@/stores";
const account = useAccodingStore();
import checkCircleIcon from "../assets/sf-symbols/check-circle.svg?raw";
import copyIcon from "../assets/sf-symbols/copy.svg?raw";

const props = defineProps<{
  content: string;
  streaming?: boolean;
}>();

type CodeBlockInfo = {
  key: string;
  code: string;
};

type CodeBlockRenderEnv = {
  codeBlocks: CodeBlockInfo[];
  copiedBlockKey: string | null;
};

hljs.registerLanguage("bash", bash);
hljs.registerLanguage("c", c);
hljs.registerLanguage("cpp", cpp);
hljs.registerLanguage("css", css);
hljs.registerLanguage("java", java);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("python", python);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("xml", xml);

const languageAliases: Record<string, string> = {
  cxx: "cpp",
  "c++": "cpp",
  html: "xml",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  shell: "bash",
  sh: "bash",
  ts: "typescript",
  tsx: "typescript",
  vue: "xml",
  zsh: "bash",
};

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});
md.use(texmath, {
  engine: katex,
  delimiters: "dollars",
  katexOptions: { strict: "ignore", maxSize: 20, maxExpand: 1000 },
});

installAgentMarkdownResources(md);
const renderLimit = ref(50_000);
watch(() => props.content, (next, previous) => { if (!next.startsWith(previous)) renderLimit.value = 50_000; });

const copiedBlockKey = ref<string | null>(null);
const copyingBlockKey = ref<string | null>(null);

function hashCodeBlock(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash.toString(36);
}

function buildCodeBlockKey(index: number, code: string) {
  return `code-${index}-${hashCodeBlock(code)}`;
}

function codeBlockCopyIcon(isCopied: boolean) {
  return `<span class="sf-symbol" style="--sf-symbol-size: 12px" aria-hidden="true">${isCopied ? checkCircleIcon : copyIcon}</span>`;
}

function normalizeCodeLanguage(language: string) {
  const normalized = language.trim().toLowerCase();
  return normalized ? languageAliases[normalized] || normalized : "";
}

function highlightCodeBlock(code: string, language: string) {
  const normalizedLanguage = normalizeCodeLanguage(language);
  if (code.length <= 30_000 && normalizedLanguage && hljs.getLanguage(normalizedLanguage)) {
    return hljs.highlight(code, { language: normalizedLanguage }).value;
  }
  return md.utils.escapeHtml(code);
}

const renderCodeBlockToken: RenderRule = (tokens, index, _options, env) => {
  const renderEnv = env as CodeBlockRenderEnv;
  const token = tokens[index];
  const key = buildCodeBlockKey(index, token.content);
  const isCopied = renderEnv.copiedBlockKey === key;
  const escapedKey = md.utils.escapeHtml(key);
  const tokenInfoLanguage = token.info.trim().split(/\s+/)[0];
  const normalizedLanguage = normalizeCodeLanguage(tokenInfoLanguage);
  const languageClass = normalizedLanguage ? `language-${md.utils.escapeHtml(normalizedLanguage)}` : "language-plain";
  const highlightedCode = highlightCodeBlock(token.content, tokenInfoLanguage);
  const copyButtonClass = isCopied ? "code-block-copy is-copied" : "code-block-copy";
  const label = isCopied ? "已复制" : "复制";

  renderEnv.codeBlocks.push({
    key,
    code: token.content,
  });

  return [
    `<figure class="code-block" data-code-block-key="${escapedKey}">`,
    `<button type="button" class="${copyButtonClass}" data-code-block-key="${escapedKey}" aria-label="${label}代码">`,
    codeBlockCopyIcon(isCopied),
    "</button>",
    `<pre><code class="hljs ${languageClass}">${highlightedCode}</code></pre>`,
    "</figure>",
  ].join("");
};

md.renderer.rules.fence = renderCodeBlockToken;
md.renderer.rules.code_block = renderCodeBlockToken;

const renderedMessage = computed(() => {
  const content = (props.content || "").slice(0, renderLimit.value);
  const cacheKey = `${copiedBlockKey.value || ''}\0${content}`;
  const cached = !props.streaming ? agentMarkdownRenderCache.get(cacheKey) : undefined;
  if (cached) return cached;
  const env: CodeBlockRenderEnv = {codeBlocks: [], copiedBlockKey: copiedBlockKey.value};
  const result = {html: md.render(content, env), codeBlocks: env.codeBlocks};
  if (!props.streaming) agentMarkdownRenderCache.set(cacheKey, result);
  return result;
});

const renderedHtml = computed(() => renderedMessage.value.html);
const codeBlockMap = computed(() => new Map(renderedMessage.value.codeBlocks.map((block) => [block.key, block])));

function resetCopiedState() {
  copiedBlockKey.value = null;
}

function codeBlockFromButton(button: HTMLButtonElement) {
  const key = button.dataset.codeBlockKey;
  return key ? codeBlockMap.value.get(key) || null : null;
}

function writeClipboardFallback(text: string) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.inset = "0 auto auto -9999px";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
  } finally {
    textarea.remove();
  }
}

async function writeClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall back for Safari/private contexts where Clipboard API exists but rejects.
    }
  }
  writeClipboardFallback(text);
}

async function copyCode(codeBlock: CodeBlockInfo) {
  if (copiedBlockKey.value === codeBlock.key || copyingBlockKey.value === codeBlock.key) return;
  copyingBlockKey.value = codeBlock.key;
  try {
    await writeClipboard(codeBlock.code);
    copiedBlockKey.value = codeBlock.key;
  } finally {
    if (copyingBlockKey.value === codeBlock.key) {
      copyingBlockKey.value = null;
    }
  }
}

function handleRenderedClick(event: MouseEvent) {
  const target = event.target instanceof Element ? event.target : null;
  const media = target?.closest<HTMLAnchorElement>('[data-agent-media-url]');
  if (media?.dataset.agentMediaUrl) {
    event.preventDefault();
    event.stopPropagation();
    void openAgentPrivateMedia(media.dataset.agentMediaUrl, account.token, media.textContent || '图片').catch(() => undefined);
    return;
  }
  const button = target?.closest<HTMLButtonElement>(".code-block-copy");
  if (!button) {
    resetCopiedState();
    return;
  }
  event.preventDefault();
  const codeBlock = codeBlockFromButton(button);
  if (!codeBlock) return;
  void copyCode(codeBlock);
}

function handleDocumentPointerDown(event: PointerEvent) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target || target.closest(".code-block-copy")) return;
  resetCopiedState();
}

watch(() => props.content, resetCopiedState);

onMounted(() => {
  document.addEventListener("pointerdown", handleDocumentPointerDown, true);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", handleDocumentPointerDown, true);
});
</script>
