export type ColorScheme = 'light' | 'dark';
export type AppearanceMode = ColorScheme | 'system';
export const APPEARANCE_PREFIX = 'accoding:appearance:';
export const appearanceKey = (userId: string | null) => `${APPEARANCE_PREFIX}${userId === null ? 'guest' : `user:${userId}`}`;
export const normalizeAppearanceMode = (value: unknown): AppearanceMode =>
  value === 'light' || value === 'dark' ? value : 'system';
export const resolveColorScheme = (mode: AppearanceMode, systemDark: boolean): ColorScheme =>
  mode === 'system' ? (systemDark ? 'dark' : 'light') : mode;
export const nextAppearanceMode = (mode: AppearanceMode): AppearanceMode =>
  mode === 'light' ? 'dark' : mode === 'dark' ? 'system' : 'light';

export function readAppearanceMode(value: string | null): AppearanceMode {
  try {
    const cached = JSON.parse(value || 'null');
    // Preserve the previous local two-state cache; pending server writes are obsolete.
    return normalizeAppearanceMode(cached?.mode ?? cached?.scheme);
  } catch { return 'system'; }
}

// Storage may be unavailable in private browsing or full; keep the selection in memory.
export function resilientAppearanceStorage(storage: Pick<Storage, 'getItem' | 'setItem'>): Pick<Storage, 'getItem' | 'setItem'> & {acceptExternal: (key: string | null) => void} {
  const fallback = new Map<string, string>();
  return {
    acceptExternal(key) { if (key === null) fallback.clear(); else fallback.delete(key); },
    getItem(key) {
      if (fallback.has(key)) return fallback.get(key)!;
      try { return storage.getItem(key); } catch { return null; }
    },
    setItem(key, value) {
      try { storage.setItem(key, value); fallback.delete(key); }
      catch { fallback.set(key, value); }
    },
  };
}

export interface AppearanceSnapshot { mode: AppearanceMode; scheme: ColorScheme; userId: string | null }
interface AppearanceDependencies {
  storage: Pick<Storage, 'getItem' | 'setItem'>;
  systemDark: boolean;
  changed: (state: AppearanceSnapshot) => void;
}

export class AppearanceController {
  snapshot: AppearanceSnapshot;
  private systemDark: boolean;
  constructor(private dependencies: AppearanceDependencies) {
    this.systemDark = dependencies.systemDark;
    const mode = this.cachedMode(null);
    this.snapshot = {userId: null, mode, scheme: resolveColorScheme(mode, this.systemDark)};
  }
  private cachedMode(userId: string | null) {
    try { return readAppearanceMode(this.dependencies.storage.getItem(appearanceKey(userId))); }
    catch { return 'system' as const; }
  }
  private apply(mode: AppearanceMode) {
    this.snapshot.mode = mode;
    this.snapshot.scheme = resolveColorScheme(mode, this.systemDark);
    this.dependencies.changed({...this.snapshot});
  }
  bindAccount(userId: string | null) {
    this.snapshot.userId = userId;
    this.apply(this.cachedMode(userId));
  }
  select(mode: AppearanceMode) {
    const selected = normalizeAppearanceMode(mode);
    try { this.dependencies.storage.setItem(appearanceKey(this.snapshot.userId), JSON.stringify({mode: selected})); }
    catch { /* The current in-memory selection still applies. */ }
    this.apply(selected);
  }
  setSystemDark(dark: boolean) {
    this.systemDark = dark;
    if (this.snapshot.mode === 'system') this.apply('system');
  }
  receiveStorage(key: string | null, value: string | null) {
    if (key !== null && key !== appearanceKey(this.snapshot.userId)) return;
    this.apply(key === null ? 'system' : readAppearanceMode(value));
  }
}
