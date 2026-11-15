import katex from "katex";
import { installAgentMarkdownResources } from "./agentMarkdownResources";
import MarkdownIt from "markdown-it";
import texmath from "markdown-it-texmath";

const whiteboardMarkdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

whiteboardMarkdown.use(texmath, {
  engine: katex,
  delimiters: "dollars",
  katexOptions: { strict: "ignore", maxSize: 20, maxExpand: 1000 },
});

installAgentMarkdownResources(whiteboardMarkdown);

const renderLimit = 50_000;
const limitNotice = "内容过长，仅展示前 50,000 字符。";

export function renderWhiteboardMarkdown(content: string | null | undefined): string {
  const source = content || "";
  const rendered = whiteboardMarkdown.render(normalizeMathDelimiters(source.slice(0, renderLimit)));
  return source.length > renderLimit ? `${rendered}<p>${limitNotice}</p>` : rendered;
}

export function renderWhiteboardInlineMarkdown(content: string | null | undefined): string {
  const source = content || "";
  const rendered = whiteboardMarkdown.renderInline(normalizeMathDelimiters(source.slice(0, renderLimit)));
  return source.length > renderLimit ? `${rendered} <span>${limitNotice}</span>` : rendered;
}

function normalizeMathDelimiters(content: string): string {
  return content
    .replace(/&amp;(#36|#x0*24|dollar);/gi, (_, entity: string) => `&${entity};`)
    .replace(/&#x0*24;|&#36;|&dollar;/gi, () => "$")
    .replace(/\\+\[|\\+\]/g, () => "$$")
    .replace(/\\+\(|\\+\)/g, () => "$")
    .replace(/\\+\$\$/g, () => "$$")
    .replace(/\\+\$/g, () => "$");
}
