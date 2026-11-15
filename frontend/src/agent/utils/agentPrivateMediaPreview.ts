import { AuthenticatedMediaResource, resolveAuthenticatedMediaUrl } from './authenticatedMedia';

interface PrivatePreview {
  window: Window;
  resource: AuthenticatedMediaResource;
}

const previews = new Map<Window, PrivatePreview>();
const MAX_PREVIEWS = 3;
let ownerToken = '';
let closeTimer: ReturnType<typeof setInterval> | null = null;

function disposePreview(preview: PrivatePreview) {
  previews.delete(preview.window);
  preview.resource.clear();
  if (!preview.window.closed) preview.window.close();
  if (!previews.size && closeTimer !== null) {
    clearInterval(closeTimer);
    closeTimer = null;
  }
}

/** Logout/account changes release both in-flight fetches and private Blob URLs. */
export function closeAgentPrivateMediaPreviews() {
  for (const preview of [...previews.values()]) disposePreview(preview);
  ownerToken = '';
}

/** Call directly in a trusted click handler: Safari needs the synchronous open. */
export async function openAgentPrivateMedia(source: string, token: string, label = '图片'): Promise<void> {
  if (!token) throw new Error('请登录后查看图片');
  const url = resolveAuthenticatedMediaUrl(source).replace(/\/thumbnail$/, '');
  if (ownerToken !== token) closeAgentPrivateMediaPreviews();
  ownerToken = token;
  while (previews.size >= MAX_PREVIEWS) disposePreview(previews.values().next().value!);
  const target = window.open('about:blank', '_blank');
  if (!target) throw new Error('浏览器阻止了图片窗口，请允许弹出窗口后重试');
  target.opener = null;
  const preview = { window: target, resource: new AuthenticatedMediaResource() };
  previews.set(target, preview);
  const document = target.document;
  document.documentElement.lang = 'zh-CN';
  document.title = label;
  document.body.textContent = '正在加载图片…';
  document.body.style.cssText = 'margin:24px;font:16px system-ui;background:#171e26;color:#e3ebf3;';
  if (closeTimer === null) {
    closeTimer = setInterval(() => {
      for (const current of [...previews.values()]) {
        if (current.window.closed) disposePreview(current);
      }
    }, 1000);
  }
  try {
    const blobUrl = await preview.resource.load(url, token);
    if (!previews.has(target) || target.closed) return;
    const image = document.createElement('img');
    image.src = blobUrl;
    image.alt = label;
    image.referrerPolicy = 'no-referrer';
    image.style.cssText = 'display:block;max-width:100%;height:auto;margin-top:20px;';
    const download = document.createElement('a');
    download.href = blobUrl;
    const filename = label.replace(/[\\/\u0000-\u001f\u007f]/g, '_').slice(0, 180);
    download.download = /\.(?:png|jpe?g|gif|webp)$/i.test(filename) ? filename : 'image';
    download.textContent = '下载图片';
    download.style.color = '#b9d8f3';
    document.body.replaceChildren(download, image);
  } catch (cause) {
    if (previews.has(target) && !target.closed) {
      try {
        document.body.textContent = cause instanceof Error ? cause.message : '图片加载失败';
      } catch {
        disposePreview(preview);
      }
    }
    throw cause;
  }
}
