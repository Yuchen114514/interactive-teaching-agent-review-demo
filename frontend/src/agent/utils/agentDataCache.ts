import localforage from "localforage";
import { captureAgentAccount } from "./agentAccountScope";
import type {
  AgentBlackboardEntry,
  AgentDiagramSummary,
  AgentHistoryMessage,
  AgentSessionSummary,
  AgentWorkspaceSummary,
} from "../api/types";

export const AGENT_DATA_CACHE_VERSION = 1;

export type AgentCacheKind =
  | "sessions"
  | "history"
  | "session-workspaces"
  | "workspace-blackboard"
  | "workspace-diagrams";

export interface AgentHistoryWatermark {
  count: number;
  maxMessageId: number | null;
  latestCreatedAt: string | null;
}

export interface AgentCacheEntry<T, W = unknown> {
  fetchedAt: number;
  data: T;
  watermarks?: W;
  lastAccessedAt?: number;
  bytes?: number;
}

const cacheStore =
  typeof window === "undefined"
    ? null
    : localforage.createInstance({
        name: "oj5-agent",
        storeName: "agent_data_cache",
        description: "Agent workspace stale-while-revalidate cache",
      });

function hashCacheOwner(owner: string): string {
  let hash = 2166136261;
  for (let index = 0; index < owner.length; index += 1) {
    hash ^= owner.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

export function agentCacheKey(
  kind: AgentCacheKind,
  owner: string,
  id?: string | number | null,
): string {
  const key = `agent:v${AGENT_DATA_CACHE_VERSION}:${kind}:${hashCacheOwner(owner)}`;
  return id === undefined || id === null
    ? key
    : `${key}:${encodeURIComponent(String(id))}`;
}

function toTimeString(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  const date = value instanceof Date ? value : new Date(String(value));
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

export function deriveHistoryWatermark(
  messages: AgentHistoryMessage[],
): AgentHistoryWatermark {
  let maxMessageId: number | null = null;
  let latestCreatedAt: string | null = null;
  for (const message of messages) {
    if (typeof message.message_id === "number") {
      maxMessageId =
        maxMessageId === null
          ? message.message_id
          : Math.max(maxMessageId, message.message_id);
    }
    const createdAt = toTimeString(message.created_at);
    if (createdAt && (!latestCreatedAt || createdAt > latestCreatedAt)) {
      latestCreatedAt = createdAt;
    }
  }
  return {
    count: messages.length,
    maxMessageId,
    latestCreatedAt,
  };
}

export function shouldRefreshCachedHistory(
  cached:
    | AgentCacheEntry<AgentHistoryMessage[], AgentHistoryWatermark>
    | null
    | undefined,
  session:
    | Pick<AgentSessionSummary, "last_message_at" | "updated_at">
    | null
    | undefined,
): boolean {
  if (!cached) return true;
  if (Date.now() - cached.fetchedAt >= 5 * 60 * 1000) return true;
  const sessionActivity = toTimeString(
    session?.updated_at ?? session?.last_message_at,
  );
  if (!sessionActivity) return false;
  const cachedActivity = toTimeString(cached.watermarks?.latestCreatedAt);
  if (!cachedActivity) return true;
  return sessionActivity !== cachedActivity;
}

export function mergeHistoryMessages(
  cached: AgentHistoryMessage[],
  incoming: AgentHistoryMessage[],
): AgentHistoryMessage[] {
  const byId = new Map<number, AgentHistoryMessage>();
  const withoutId: AgentHistoryMessage[] = [];
  for (const message of [...cached, ...incoming]) {
    if (typeof message.message_id === "number") {
      byId.set(message.message_id, message);
    } else {
      withoutId.push(message);
    }
  }
  return [
    ...Array.from(byId.entries())
      .sort(([leftId], [rightId]) => leftId - rightId)
      .map(([, message]) => message),
    ...withoutId,
  ];
}

export interface AgentCacheStorage {
  keys(): Promise<string[]>;
  getItem<T>(key: string): Promise<T | null>;
  setItem<T>(key: string, value: T): Promise<T>;
  removeItem(key: string): Promise<void>;
}

export function createAgentCacheRepository(
  storage: AgentCacheStorage,
  options: {
    now?: () => number;
    ttlMs?: number;
    maxEntries?: number;
    maxBytes?: number;
    maxEntryBytes?: number;
  } = {},
) {
  const now = options.now ?? Date.now;
  const ttlMs = options.ttlMs ?? 24 * 60 * 60 * 1000;
  const maxEntries = options.maxEntries ?? 200;
  const maxBytes = options.maxBytes ?? 24 * 1024 * 1024;
  const maxEntryBytes = options.maxEntryBytes ?? 4 * 1024 * 1024;
  let pending: Promise<unknown> = Promise.resolve();
  const deletedKeys = new Set<string>();
  const markDeleted = (key: string) => {
    deletedKeys.add(key);
    if (deletedKeys.size > 4096)
      deletedKeys.delete(deletedKeys.values().next().value!);
  };
  function serial<T>(action: () => Promise<T>): Promise<T> {
    const result = pending.then(action);
    pending = result.catch(() => undefined);
    return result;
  }
  const isKey = (key: string) => /^agent:v\d+:/.test(key);
  const ownerKey = (key: string, owner: string) =>
    isKey(key) && key.split(":")[3] === hashCacheOwner(owner);
  const expired = (entry: AgentCacheEntry<unknown>) =>
    !Number.isFinite(entry.fetchedAt) || now() - entry.fetchedAt >= ttlMs;
  const bytes = (entry: AgentCacheEntry<unknown>) =>
    entry.bytes ?? new TextEncoder().encode(JSON.stringify(entry)).byteLength;

  async function read<T, W = unknown>(
    owner: string,
    kind: AgentCacheKind,
    id?: string | number | null,
  ): Promise<AgentCacheEntry<T, W> | null> {
    return serial(async () => {
      const key = agentCacheKey(kind, owner, id);
      if (deletedKeys.has(key)) return null;
      const entry = await storage.getItem<AgentCacheEntry<T, W>>(key);
      if (!entry) return null;
      if (expired(entry)) {
        await storage.removeItem(key);
        return null;
      }
      entry.lastAccessedAt = now();
      await storage.setItem(key, entry);
      return entry;
    }).catch(() => null);
  }

  async function write<T, W = unknown>(
    owner: string,
    kind: AgentCacheKind,
    data: T,
    settings: {
      id?: string | number | null;
      watermarks?: W;
      isCurrent?: () => boolean;
    } = {},
  ) {
    return serial(async () => {
      if (settings.isCurrent && !settings.isCurrent()) return;
      const key = agentCacheKey(kind, owner, settings.id);
      if (deletedKeys.has(key)) return;
      const entry: AgentCacheEntry<T, W> = {
        fetchedAt: now(),
        lastAccessedAt: now(),
        data,
        watermarks: settings.watermarks,
      };
      entry.bytes = bytes(entry);
      if (entry.bytes > Math.min(maxEntryBytes, maxBytes)) {
        await storage.removeItem(key);
        return;
      }
      const candidates: { key: string; entry: AgentCacheEntry<unknown> }[] = [];
      for (const candidateKey of await storage.keys()) {
        if (!isKey(candidateKey) || candidateKey === key) continue;
        const cached =
          await storage.getItem<AgentCacheEntry<unknown>>(candidateKey);
        if (!cached || expired(cached)) {
          await storage.removeItem(candidateKey);
          continue;
        }
        candidates.push({ key: candidateKey, entry: cached });
      }
      candidates.sort(
        (a, b) =>
          (a.entry.lastAccessedAt ?? a.entry.fetchedAt) -
          (b.entry.lastAccessedAt ?? b.entry.fetchedAt),
      );
      let totalBytes = candidates.reduce(
        (sum, candidate) => sum + bytes(candidate.entry),
        entry.bytes,
      );
      while (
        candidates.length &&
        (candidates.length + 1 > maxEntries || totalBytes > maxBytes)
      ) {
        const removed = candidates.shift()!;
        await storage.removeItem(removed.key);
        totalBytes -= bytes(removed.entry);
      }
      if (settings.isCurrent && !settings.isCurrent()) return;
      await storage.setItem(key, entry);
    }).catch(() => undefined);
  }

  async function clearAccount(owner: string) {
    return serial(async () => {
      for (const key of await storage.keys())
        if (ownerKey(key, owner)) await storage.removeItem(key);
      for (const key of deletedKeys)
        if (ownerKey(key, owner)) deletedKeys.delete(key);
    }).catch(() => undefined);
  }

  async function removeSession(owner: string, sessionId: string) {
    return serial(async () => {
      const workspaces = await storage.getItem<
        AgentCacheEntry<AgentWorkspaceSummary[]>
      >(agentCacheKey("session-workspaces", owner, sessionId));
      const keys = [
        agentCacheKey("history", owner, sessionId),
        agentCacheKey("session-workspaces", owner, sessionId),
      ];
      for (const workspace of workspaces?.data ?? []) {
        keys.push(
          agentCacheKey("workspace-blackboard", owner, workspace.workspace_id),
          agentCacheKey("workspace-diagrams", owner, workspace.workspace_id),
        );
      }
      for (const key of keys) {
        markDeleted(key);
        await storage.removeItem(key);
      }
    }).catch(() => undefined);
  }
  return { read, write, clearAccount, removeSession };
}

const deletedSessions = new Set<string>();
export function isAgentSessionCacheAvailable(token: string, sessionId: string) {
  return !deletedSessions.has(agentCacheKey("history", token, sessionId));
}

const repository = cacheStore ? createAgentCacheRepository(cacheStore) : null;

async function readAgentCache<T, W = unknown>(
  token: string,
  kind: AgentCacheKind,
  id?: string | number | null,
): Promise<AgentCacheEntry<T, W> | null> {
  const account = captureAgentAccount(token);
  if (!repository || !account.isCurrent()) return null;
  const entry = await repository.read<T, W>(token, kind, id);
  return account.isCurrent() ? entry : null;
}

async function writeAgentCache<T, W = unknown>(
  token: string,
  kind: AgentCacheKind,
  data: T,
  options: { id?: string | number | null; watermarks?: W } = {},
) {
  const account = captureAgentAccount(token);
  if (!repository || !account.isCurrent()) return;
  await repository.write(token, kind, data, {
    ...options,
    isCurrent: account.isCurrent,
  });
}

export async function clearAgentAccountCache(token: string) {
  if (token) await repository?.clearAccount(token);
}
export async function removeCachedSession(token: string, sessionId: string) {
  if (!token) return;
  deletedSessions.add(agentCacheKey("history", token, sessionId));
  if (deletedSessions.size > 2048)
    deletedSessions.delete(deletedSessions.values().next().value!);
  await repository?.removeSession(token, sessionId);
}

export function readCachedSessions(token: string) {
  return readAgentCache<AgentSessionSummary[]>(token, "sessions");
}

export function writeCachedSessions(
  token: string,
  sessions: AgentSessionSummary[],
) {
  return writeAgentCache(token, "sessions", sessions);
}

export function readCachedHistory(token: string, sessionId: string) {
  return readAgentCache<AgentHistoryMessage[], AgentHistoryWatermark>(
    token,
    "history",
    sessionId,
  );
}

export function writeCachedHistory(
  token: string,
  sessionId: string,
  history: AgentHistoryMessage[],
) {
  return writeAgentCache(token, "history", history, {
    id: sessionId,
    watermarks: deriveHistoryWatermark(history),
  });
}

export function readCachedSessionWorkspaces(token: string, sessionId: string) {
  return readAgentCache<AgentWorkspaceSummary[]>(
    token,
    "session-workspaces",
    sessionId,
  );
}

export function writeCachedSessionWorkspaces(
  token: string,
  sessionId: string,
  workspaces: AgentWorkspaceSummary[],
) {
  return writeAgentCache(token, "session-workspaces", workspaces, {
    id: sessionId,
  });
}

export function readCachedWorkspaceBlackboard(
  token: string,
  workspaceId: number,
) {
  return readAgentCache<AgentBlackboardEntry[]>(
    token,
    "workspace-blackboard",
    workspaceId,
  );
}

export function writeCachedWorkspaceBlackboard(
  token: string,
  workspaceId: number,
  blackboard: AgentBlackboardEntry[],
) {
  return writeAgentCache(token, "workspace-blackboard", blackboard, {
    id: workspaceId,
  });
}

export function readCachedWorkspaceDiagrams(
  token: string,
  workspaceId: number,
) {
  return readAgentCache<AgentDiagramSummary[]>(
    token,
    "workspace-diagrams",
    workspaceId,
  );
}

export function writeCachedWorkspaceDiagrams(
  token: string,
  workspaceId: number,
  diagrams: AgentDiagramSummary[],
) {
  return writeAgentCache(token, "workspace-diagrams", diagrams, {
    id: workspaceId,
  });
}
