type RenderedMarkdown = { html: string; codeBlocks: { key: string; code: string }[] };
/** Completed blocks only; both entry count and retained bytes are bounded. */
export function createMarkdownRenderCache(maxEntries = 32, maxBytes = 4 * 1024 * 1024) {
  const values = new Map<string, { value: RenderedMarkdown; bytes: number }>();
  let bytes = 0;
  return {
    get(key: string) {
      const entry = values.get(key);
      if (!entry) return undefined;
      values.delete(key); values.set(key, entry);
      return entry.value;
    },
    set(key: string, value: RenderedMarkdown) {
      const size = 2 * (key.length + value.html.length + value.codeBlocks.reduce((sum, block) => sum + block.key.length + block.code.length, 0));
      const previous = values.get(key);
      if (previous) { bytes -= previous.bytes; values.delete(key); }
      if (size > maxBytes) return;
      values.set(key, {value, bytes: size}); bytes += size;
      while (values.size > maxEntries || bytes > maxBytes) {
        const oldest = values.keys().next().value!;
        bytes -= values.get(oldest)!.bytes; values.delete(oldest);
      }
    },
    clear() { values.clear(); bytes = 0; },
  };
}
export const agentMarkdownRenderCache = createMarkdownRenderCache();
