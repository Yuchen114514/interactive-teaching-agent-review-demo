import { computed, onScopeDispose, ref } from 'vue';
import { defineStore } from 'pinia';
import { AppearanceController, nextAppearanceMode, resilientAppearanceStorage, type AppearanceMode, type AppearanceSnapshot, type ColorScheme } from './appearance';

export function applyColorScheme(scheme: ColorScheme, mode: AppearanceMode = scheme) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.colorScheme = scheme;
  root.dataset.appearance = mode;
  root.classList.toggle('dark', scheme === 'dark');
  root.style.colorScheme = scheme;
}

export const useAppearanceStore = defineStore('appearance', () => {
  const state = ref<AppearanceSnapshot>({mode: 'system', scheme: 'light', userId: null});
  const media = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const storage = resilientAppearanceStorage({
    getItem(key) { return localStorage.getItem(key); },
    setItem(key, value) { localStorage.setItem(key, value); },
  });
  const controller = new AppearanceController({
    storage,
    systemDark: media?.matches ?? false,
    changed(snapshot) { state.value = snapshot; applyColorScheme(snapshot.scheme, snapshot.mode); },
  });
  state.value = {...controller.snapshot};
  applyColorScheme(state.value.scheme, state.value.mode);

  const onSystemChange = (event: MediaQueryListEvent) => controller.setSystemDark(event.matches);
  const onStorage = (event: StorageEvent) => {
    storage.acceptExternal(event.key);
    controller.receiveStorage(event.key, event.newValue);
  };
  if (media?.addEventListener) media.addEventListener('change', onSystemChange);
  else media?.addListener(onSystemChange);
  if (typeof window !== 'undefined') window.addEventListener('storage', onStorage);
  onScopeDispose(() => {
    if (media?.removeEventListener) media.removeEventListener('change', onSystemChange);
    else media?.removeListener(onSystemChange);
    if (typeof window !== 'undefined') window.removeEventListener('storage', onStorage);
  });

  return {
    mode: computed(() => state.value.mode),
    scheme: computed(() => state.value.scheme),
    isDark: computed(() => state.value.scheme === 'dark'),
    bindAccount: (userId: string | null) => controller.bindAccount(userId),
    select: (mode: AppearanceMode) => controller.select(mode),
    toggle: () => controller.select(nextAppearanceMode(state.value.mode)),
  };
});
