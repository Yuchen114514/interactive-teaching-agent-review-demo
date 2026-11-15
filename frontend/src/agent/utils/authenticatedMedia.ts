import { resolveAgentApiBaseUrl } from '../config';

export interface AuthenticatedMediaAddress {
  apiBaseUrl?: string;
  pageOrigin?: string;
}

export interface AuthenticatedMediaOptions extends AuthenticatedMediaAddress {
  fetcher?: (url: string, init: RequestInit) => Promise<Response>;
  signal?: AbortSignal;
}

const RASTER_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

/** Old stored media origins are identifiers, never destinations for credentials. */
export function resolveAuthenticatedMediaUrl(source: string, options: AuthenticatedMediaAddress = {}): string {
  const origin = options.pageOrigin ?? (typeof window === 'undefined' ? 'http://localhost' : window.location.origin);
  const api = new URL(options.apiBaseUrl ?? resolveAgentApiBaseUrl(), origin);
  const input = new URL(source, origin);
  for (const url of [api, input]) {
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('图片地址无效');
    }
  }
  const apiPath = api.pathname.replace(/\/+$/, '');
  const prefixes = [`${apiPath}/media/images/`, '/api/agents/media/images/'];
  const prefix = prefixes.find(value => input.pathname.startsWith(value));
  const suffix = prefix ? input.pathname.slice(prefix.length) : '';
  if (!/^[A-Za-z0-9_-]{1,128}(?:\/thumbnail)?$/.test(suffix)) throw new Error('图片地址不属于 Agent 附件');
  return `${api.origin}${apiPath}/media/images/${suffix}`;
}

export async function fetchAuthenticatedMediaBlob(
  source: string,
  token: string,
  options: AuthenticatedMediaOptions = {},
): Promise<Blob> {
  if (!token) throw new Error('请登录后查看图片');
  const url = resolveAuthenticatedMediaUrl(source, options);
  const response = await (options.fetcher ?? fetch)(url, {
    headers: { 'X-Accoding-Session': token },
    signal: options.signal,
    credentials: 'omit',
    redirect: 'error',
    cache: 'no-store',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw new Error(response.status === 401 ? '登录已失效，请重新登录后查看图片' : '图片不存在或没有访问权限');
  const contentType = response.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() ?? '';
  if (!RASTER_IMAGE_TYPES.has(contentType)) throw new Error('图片格式不受支持');
  const size = Number(response.headers.get('content-length') || 0);
  if (size > MAX_IMAGE_BYTES) throw new Error('图片超过大小限制');
  const blob = await response.blob();
  if (blob.size > MAX_IMAGE_BYTES) throw new Error('图片超过大小限制');
  return blob;
}

interface MediaResourceOptions extends AuthenticatedMediaAddress {
  fetcher?: AuthenticatedMediaOptions['fetcher'];
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
}

/** One component owns one Blob URL; replacement/disposal also cancels its load. */
export class AuthenticatedMediaResource {
  private generation = 0;
  private controller: AbortController | null = null;
  private objectUrl: string | null = null;

  constructor(private readonly options: MediaResourceOptions = {}) {}

  clear() {
    this.generation += 1;
    this.controller?.abort();
    this.controller = null;
    if (this.objectUrl) {
      (this.options.revokeObjectURL ?? URL.revokeObjectURL)(this.objectUrl);
      this.objectUrl = null;
    }
  }

  async load(source: string, token: string): Promise<string> {
    this.clear();
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    const timer = setTimeout(() => controller.abort(), 15_000);
    try {
      const blob = await fetchAuthenticatedMediaBlob(source, token, { ...this.options, signal: controller.signal });
      if (generation !== this.generation || controller.signal.aborted) throw new DOMException('图片加载已取消', 'AbortError');
      this.objectUrl = (this.options.createObjectURL ?? URL.createObjectURL)(blob);
      return this.objectUrl;
    } finally {
      clearTimeout(timer);
      if (this.controller === controller) this.controller = null;
    }
  }
}
