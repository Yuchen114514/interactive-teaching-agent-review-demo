import type { AgentUploadedImage } from '../api/types';
import type { AgentDraftImage } from '../stores/agentWorkspace';

export const MAX_AGENT_IMAGES = 4;
export const MAX_AGENT_IMAGE_BYTES = 10 * 1024 * 1024;
export const MAX_AGENT_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif']);

type UploadOptions = {
  signal: AbortSignal;
  isCurrent: () => boolean;
  upload: (file: File, signal: AbortSignal) => Promise<AgentUploadedImage>;
  createUrl?: (file: File) => string;
  revokeUrl?: (url: string) => void;
};

export async function uploadAgentImageBatch(files: File[], existing: AgentDraftImage[], options: UploadOptions): Promise<AgentDraftImage[]> {
  if (files.length + existing.length > MAX_AGENT_IMAGES) throw new Error('每轮最多上传 4 张图片');
  for (const file of files) {
    if (!IMAGE_TYPES.has(file.type)) throw new Error(`${file.name} 不支持此图片格式`);
    if (file.size <= 0) throw new Error(`${file.name} 是空文件`);
    if (file.size > MAX_AGENT_IMAGE_BYTES) throw new Error(`${file.name} 超过 10MB`);
  }
  const existingBytes = existing.reduce((sum, item) => sum + (Number.isFinite(item.size) && item.size! > 0 ? item.size! : MAX_AGENT_IMAGE_BYTES), 0);
  if (existingBytes + files.reduce((sum, file) => sum + file.size, 0) > MAX_AGENT_TOTAL_IMAGE_BYTES) throw new Error('每轮图片总大小不能超过 20MB');
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal.addEventListener('abort', abort, { once: true });
  if (options.signal.aborted) abort();
  const createUrl = options.createUrl ?? ((file: File) => URL.createObjectURL(file));
  const revokeUrl = options.revokeUrl ?? ((url: string) => URL.revokeObjectURL(url));
  const previews = new Set<string>();
  const result: AgentDraftImage[] = new Array(files.length);
  let next = 0;
  let failure: unknown;
  const assertCurrent = () => {
    if (controller.signal.aborted || !options.isCurrent()) throw new DOMException('上传已取消', 'AbortError');
  };
  async function worker() {
    try {
      while (next < files.length && !failure) {
        assertCurrent();
        const index = next++;
        const file = files[index];
        const previewUrl = createUrl(file);
        previews.add(previewUrl);
        const uploaded = await options.upload(file, controller.signal);
        assertCurrent();
        result[index] = { image_id: uploaded.image_id, url: uploaded.url, thumbnail_url: uploaded.thumbnail_url, filename: uploaded.filename || file.name, previewUrl, size: file.size };
      }
    } catch (error) {
      failure ??= error;
      controller.abort();
    }
  }
  try {
    await Promise.all(Array.from({ length: Math.min(files.length, 2) }, worker));
    if (failure) throw failure;
    assertCurrent();
    return result;
  } catch (error) {
    previews.forEach(url => revokeUrl(url));
    throw error;
  } finally {
    options.signal.removeEventListener('abort', abort);
  }
}

export function releaseAgentImagePreviews(images: AgentDraftImage[]) {
  images.forEach(image => { if (image.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(image.previewUrl); });
}
