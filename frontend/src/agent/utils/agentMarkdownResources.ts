import type MarkdownIt from 'markdown-it';
import { resolveAuthenticatedMediaUrl, type AuthenticatedMediaAddress } from './authenticatedMedia';

/** Untrusted generated Markdown never starts image requests on its own. */
export function installAgentMarkdownResources(md: MarkdownIt, options: AuthenticatedMediaAddress = {}) {
  const previousLinkOpen = md.renderer.rules.link_open;
  md.renderer.rules.link_open = (tokens, index, renderOptions, env, renderer) => {
    const token = tokens[index];
    const origin = options.pageOrigin ?? (typeof window === 'undefined' ? 'http://localhost' : window.location.origin);
    try {
      const href = new URL(token.attrGet('href') || '', origin);
      if (['http:', 'https:'].includes(href.protocol) && href.origin !== new URL(origin).origin) token.attrSet('target', '_blank');
    } catch { /* MarkdownIt already rejects unsafe link destinations. */ }
    token.attrSet('rel', 'noopener noreferrer');
    token.attrSet('referrerpolicy', 'no-referrer');
    return previousLinkOpen
      ? previousLinkOpen(tokens, index, renderOptions, env, renderer)
      : renderer.renderToken(tokens, index, renderOptions);
  };

  md.renderer.rules.image = (tokens, index) => {
    const token = tokens[index];
    const source = token.attrGet('src') || '';
    const label = md.utils.escapeHtml(token.content || '图片');
    const origin = options.pageOrigin ?? (typeof window === 'undefined' ? 'http://localhost' : window.location.origin);
    let href: string;
    let privateUrl = '';
    try {
      const url = new URL(source, origin);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('unsupported image');
      href = url.href;
      try {
        privateUrl = resolveAuthenticatedMediaUrl(source, options);
        href = privateUrl;
      } catch {
        // A public external image remains an explicit, non-referring link.
      }
    } catch {
      return `<span class="agent-markdown-resource-unavailable">${label}（图片地址不受支持）</span>`;
    }
    const escapedHref = md.utils.escapeHtml(href);
    const privateAttribute = privateUrl ? ` data-agent-media-url="${md.utils.escapeHtml(privateUrl)}"` : '';
    return `<a class="agent-markdown-resource" href="${escapedHref}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"${privateAttribute}>${label}（点击查看图片）</a>`;
  };
}
