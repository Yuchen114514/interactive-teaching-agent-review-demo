import type { AgentDraftImage } from '../stores/agentWorkspace';
import { captureAgentAccount } from './agentAccountScope';
import { isAgentSessionCacheAvailable } from './agentDataCache';

export const AGENT_DRAFT_MAX_LENGTH = 20_000;
const PREFIX = 'accoding:agent-draft:v1:';
const TTL = 7 * 24 * 60 * 60 * 1000;
const OWNER_LIMIT = 20;
const TOTAL_LIMIT = 100;
export interface AgentDraftSnapshot { text: string; images: AgentDraftImage[] }
type DraftEntry = AgentDraftSnapshot & { savedAt: number };
type DraftStorage = Pick<Storage, 'length' | 'key' | 'getItem' | 'setItem' | 'removeItem'>;
const owners = new Map<string, Promise<string>>();
let memoryOwnerSequence = 0;

async function ownerPrefix(token: string) {
  let pending = owners.get(token);
  if (!pending) {
    pending = (async () => {
      if (!globalThis.crypto?.subtle) return PREFIX + 'memory-' + (++memoryOwnerSequence) + ':';
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
      return PREFIX + [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('') + ':';
    })().catch(() => PREFIX + 'memory-' + (++memoryOwnerSequence) + ':');
    owners.set(token, pending);
    if (owners.size > 8) owners.delete(owners.keys().next().value!);
  }
  return pending;
}

function cleanSnapshot(value: AgentDraftSnapshot): AgentDraftSnapshot {
  return {
    text: value.text,
    images: value.images.slice(0, 4).map(image => ({
      image_id: image.image_id, url: image.url, thumbnail_url: image.thumbnail_url,
      filename: image.filename, size: Number.isFinite(image.size) && image.size! > 0 && image.size! <= 10 * 1024 * 1024 ? image.size : undefined,
    })),
  };
}

export function createAgentDraftCache(storage: DraftStorage | null) {
  const fallback = new Map<string, DraftEntry>();
  let lastSavedAt = 0;
  const keys = () => {
    const result = new Set(fallback.keys());
    try { for (let i = 0; i < (storage?.length ?? 0); i++) { const key = storage?.key(i); if (key?.startsWith(PREFIX)) result.add(key); } } catch { /* Storage can be disabled. */ }
    return [...result];
  };
  const remove = (key: string) => { fallback.delete(key); try { storage?.removeItem(key); } catch { /* Ignore denied storage. */ } };
  const readEntry = (key: string): DraftEntry | null => {
    let value = fallback.get(key);
    if (!value) {
      try { value = JSON.parse(storage?.getItem(key) || 'null'); } catch { remove(key); return null; }
    }
    if (!value || typeof value.text !== 'string' || value.text.length > AGENT_DRAFT_MAX_LENGTH || !Array.isArray(value.images) || !Number.isFinite(value.savedAt)) { remove(key); return null; }
    if (Date.now() - value.savedAt > TTL) { remove(key); return null; }
    const images = value.images.filter(image => image && typeof image.image_id === 'string' && typeof image.url === 'string');
    return { ...cleanSnapshot({ text: value.text, images }), savedAt: value.savedAt };
  };
  const prune = (owner: string) => {
    const entries = keys().map(key => ({ key, value: readEntry(key) })).filter(item => item.value).sort((a, b) => b.value!.savedAt - a.value!.savedAt);
    let ownCount = 0;
    entries.forEach((entry, i) => { if (entry.key.startsWith(owner)) ownCount++; if (i >= TOTAL_LIMIT || (entry.key.startsWith(owner) && ownCount > OWNER_LIMIT)) remove(entry.key); });
  };
  const keyFor = async (token: string, sessionId: string | null) => {
    const owner = token ? await ownerPrefix(token) : '';
    return { owner, key: owner ? owner + encodeURIComponent(sessionId || 'draft') : '' };
  };
  return {
    async read(token: string, sessionId: string | null): Promise<AgentDraftSnapshot | null> {
      const { key } = await keyFor(token, sessionId);
      const entry = key ? readEntry(key) : null;
      return entry ? cleanSnapshot(entry) : null;
    },
    async write(token: string, sessionId: string | null, snapshot: AgentDraftSnapshot) {
      const account = captureAgentAccount(token);
      const { owner, key } = await keyFor(token, sessionId);
      if (!key || !account.isCurrent() || (sessionId && !isAgentSessionCacheAvailable(token, sessionId)) || snapshot.text.length > AGENT_DRAFT_MAX_LENGTH) return;
      if (!snapshot.text && !snapshot.images.length) { remove(key); return; }
      lastSavedAt = Math.max(Date.now(), lastSavedAt + 1);
      const entry = { ...cleanSnapshot(snapshot), savedAt: lastSavedAt };
      fallback.set(key, entry);
      try { if (owner.startsWith(PREFIX + 'memory-') || !storage) throw new Error('memory only'); storage.setItem(key, JSON.stringify(entry)); fallback.delete(key); } catch { /* Keep a bounded memory fallback. */ }
      prune(owner);
    },
    async removeIfMatches(token: string, sessionId: string | null, submitted: AgentDraftSnapshot) {
      const { key } = await keyFor(token, sessionId);
      const current = key ? readEntry(key) : null;
      if (current && current.text === submitted.text && current.images.length === submitted.images.length && current.images.every(image => submitted.images.some(sent => sent.image_id === image.image_id))) remove(key);
    },
    async clearSession(token: string, sessionId: string) {
      const { key } = await keyFor(token, sessionId);
      if (key) remove(key);
    },
    async clearOwner(token: string) {
      const owner = await ownerPrefix(token);
      if (owner) keys().filter(key => key.startsWith(owner)).forEach(remove);
      owners.delete(token);
    },
  };
}

let browserStorage: DraftStorage | null = null;
try { browserStorage = typeof window === 'undefined' ? null : window.localStorage; } catch { /* Private browsing. */ }
export const agentDraftCache = createAgentDraftCache(browserStorage);
export const clearAgentDrafts = (token: string) => agentDraftCache.clearOwner(token);
