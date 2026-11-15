import { defineStore } from "pinia";
import {
  getAgentUserPreferences,
  patchAgentUserPreferences,
} from "../api/agentApi";
import {
  normalizeAgentMascotSkin,
  type AgentMascotSkinId,
} from "../utils/agentMascot";
import type { AgentFontSizePreference } from "../api/types";

function normalizeFontSize(value: unknown): AgentFontSizePreference {
  return value === "medium" || value === "large" ? value : "small";
}

function applyFontSize(value: AgentFontSizePreference) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.agentFontSize = value;
}

let pendingLoad: Promise<void> | null = null;
let pendingToken = "";
let preferenceRevision = 0;
let loadSequence = 0;

function preferenceErrorMessage(error: unknown): string {
  const detail = error instanceof Error ? error.message.trim() : "";
  return detail ? `外观保存失败：${detail}` : "外观保存失败，请稍后重试。";
}

export const useAgentPreferencesStore = defineStore("agentPreferences", {
  state: () => ({
    skin: "base" as AgentMascotSkinId,
    fontSize: "small" as AgentFontSizePreference,
    loaded: false,
    loadedToken: "",
    loading: false,
    saving: false,
    error: null as string | null,
  }),
  actions: {
    resetForAccount() {
      preferenceRevision += 1;
      loadSequence += 1;
      pendingLoad = null;
      pendingToken = "";
      this.$reset();
      applyFontSize("small");
    },
    async load(token: string): Promise<void> {
      if (!token) return;
      if (this.loaded && this.loadedToken === token) return;
      if (pendingLoad && pendingToken === token) return pendingLoad;

      this.loading = true;
      this.error = null;
      pendingToken = token;
      const loadRevision = preferenceRevision;
      const requestId = ++loadSequence;
      pendingLoad = (async () => {
        try {
          const preferences = await getAgentUserPreferences(token);
          if (loadRevision !== preferenceRevision || requestId !== loadSequence) return;
          this.skin = normalizeAgentMascotSkin(preferences.mascot_skin);
          this.fontSize = normalizeFontSize(preferences.font_size);
          applyFontSize(this.fontSize);
          this.loaded = true;
          this.loadedToken = token;
        } catch (error) {
          if (loadRevision !== preferenceRevision || requestId !== loadSequence) return;
          this.error = error instanceof Error
            ? `外观加载失败：${error.message}`
            : "外观加载失败，请稍后重试。";
        } finally {
          if (requestId === loadSequence) {
            this.loading = false;
            pendingLoad = null;
            pendingToken = "";
          }
        }
      })();
      return pendingLoad;
    },
    async selectSkin(token: string, nextSkin: AgentMascotSkinId): Promise<void> {
      const revision = ++preferenceRevision;
      const previousSkin = this.skin;
      const selectedSkin = normalizeAgentMascotSkin(nextSkin);
      this.skin = normalizeAgentMascotSkin(nextSkin);
      this.saving = true;
      this.error = null;
      try {
        const preferences = await patchAgentUserPreferences(token, {mascot_skin: selectedSkin});
        if (revision !== preferenceRevision) return;
        this.skin = preferences.mascot_skin
          ? normalizeAgentMascotSkin(preferences.mascot_skin)
          : selectedSkin;
        this.loaded = true;
        this.loadedToken = token;
      } catch (error) {
        if (revision !== preferenceRevision) return;
        this.skin = previousSkin;
        this.error = preferenceErrorMessage(error);
      } finally {
        if (revision === preferenceRevision) this.saving = false;
      }
    },
    async selectFontSize(token: string, nextFontSize: AgentFontSizePreference): Promise<void> {
      const revision = ++preferenceRevision;
      const previousFontSize = this.fontSize;
      const selectedFontSize = normalizeFontSize(nextFontSize);
      this.fontSize = selectedFontSize;
      applyFontSize(this.fontSize);
      this.saving = true;
      this.error = null;
      try {
        const preferences = await patchAgentUserPreferences(token, {font_size: selectedFontSize});
        if (revision !== preferenceRevision) return;
        this.fontSize = preferences.font_size
          ? normalizeFontSize(preferences.font_size)
          : selectedFontSize;
        applyFontSize(this.fontSize);
        this.loaded = true;
        this.loadedToken = token;
      } catch (error) {
        if (revision !== preferenceRevision) return;
        this.fontSize = previousFontSize;
        applyFontSize(this.fontSize);
        this.error = preferenceErrorMessage(error);
      } finally {
        if (revision === preferenceRevision) this.saving = false;
      }
    },
  },
});
