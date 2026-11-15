import { defineStore } from "pinia";

export type AgentMobileTab = "chat" | "problem" | "whiteboard" | "diagram";

const MIN_WIDTH = 380;
const MIN_HEIGHT = 520;
const VIEWPORT_MARGIN = 16;
const MIN_SPLIT_AGENT_WIDTH = 420;
const MIN_SPLIT_PAGE_WIDTH = 360;
const FLOAT_SESSION_RAIL_OPEN_WIDTH = 1060;

function getSplitAgentWidth(ratio: number) {
  const viewportWidth = window.innerWidth || 1280;
  const maxAgentWidth = Math.max(MIN_SPLIT_AGENT_WIDTH, viewportWidth - MIN_SPLIT_PAGE_WIDTH);
  return clamp(viewportWidth * ratio, MIN_SPLIT_AGENT_WIDTH, maxAgentWidth);
}

function getDefaultRect() {
  const width = Math.min(1080, Math.max(MIN_WIDTH, window.innerWidth - 96));
  const height = Math.min(720, Math.max(MIN_HEIGHT, window.innerHeight - 120));
  return {
    left: Math.max(VIEWPORT_MARGIN, window.innerWidth - width - 48),
    top: Math.max(VIEWPORT_MARGIN, window.innerHeight - height - 56),
    width,
    height,
  };
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function maxFloatingWidth() {
  return Math.max(MIN_WIDTH, window.innerWidth - VIEWPORT_MARGIN * 2);
}

export const useAgentLayoutStore = defineStore("agentLayout", {
  state: () => ({
    isOpen: false,
    left: 0,
    top: 0,
    width: 1080,
    height: 720,
    initialized: false,
    sessionRailOpen: true,
    sessionRailCollapsed: false,
    sessionRailView: "sessions" as "sessions" | "problems",
    contextPanelOpen: true,
    contextPanelCollapsed: false,
    mobileTab: "chat" as AgentMobileTab,
    autoSessionRailCollapsed: false,
    autoContextPanelCollapsed: false,
    compactOverlayModeActive: false,
    splitMode: false,
    splitAgentRatio: 0.5,
  }),
  actions: {
    ensureInitialized() {
      if (this.initialized) return;
      const rect = getDefaultRect();
      this.left = rect.left;
      this.top = rect.top;
      this.width = rect.width;
      this.height = rect.height;
      this.initialized = true;
    },
    open() {
      this.ensureInitialized();
      this.isOpen = true;
    },
    close() {
      this.isOpen = false;
      this.splitMode = false;
    },
    toggle() {
      if (this.isOpen) this.close();
      else this.open();
    },
    setPosition(left: number, top: number) {
      this.ensureInitialized();
      this.left = clamp(left, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, window.innerWidth - this.width - VIEWPORT_MARGIN));
      this.top = clamp(top, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, window.innerHeight - this.height - VIEWPORT_MARGIN));
    },
    setSize(width: number, height: number) {
      this.ensureInitialized();
      const maxWidth = maxFloatingWidth();
      const maxHeight = Math.max(MIN_HEIGHT, window.innerHeight - VIEWPORT_MARGIN * 2);
      this.width = clamp(width, MIN_WIDTH, maxWidth);
      this.height = clamp(height, MIN_HEIGHT, maxHeight);
      this.setPosition(this.left, this.top);
    },
    setRect(left: number, top: number, width: number, height: number) {
      this.ensureInitialized();
      const maxWidth = maxFloatingWidth();
      const maxHeight = Math.max(MIN_HEIGHT, window.innerHeight - VIEWPORT_MARGIN * 2);
      const nextWidth = clamp(width, MIN_WIDTH, maxWidth);
      const nextHeight = clamp(height, MIN_HEIGHT, maxHeight);
      this.width = nextWidth;
      this.height = nextHeight;
      this.left = clamp(left, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, window.innerWidth - nextWidth - VIEWPORT_MARGIN));
      this.top = clamp(top, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, window.innerHeight - nextHeight - VIEWPORT_MARGIN));
    },
    setSplitMode(enabled: boolean) {
      this.splitMode = enabled;
      this.applyResponsivePanels();
    },
    toggleSplitMode() {
      this.setSplitMode(!this.splitMode);
    },
    setSplitAgentRatio(ratio: number) {
      this.splitAgentRatio = clamp(ratio, 0.32, 0.72);
      this.applyResponsivePanels();
    },
    toggleSessionRail() {
      this.sessionRailOpen = !this.sessionRailOpen;
    },
    toggleSessionRailCollapsed() {
      this.sessionRailCollapsed = !this.sessionRailCollapsed;
      this.sessionRailOpen = true;
    },
    setSessionRailView(view: "sessions" | "problems") {
      this.sessionRailView = view;
    },
    toggleContextPanel() {
      this.contextPanelCollapsed = !this.contextPanelCollapsed;
      this.contextPanelOpen = true;
    },
    setSessionRailCollapsed(collapsed: boolean, automatic = false) {
      if (!collapsed && !automatic && !this.compactOverlayModeActive) {
        this.ensureFloatingWidth(FLOAT_SESSION_RAIL_OPEN_WIDTH);
      }
      this.sessionRailCollapsed = collapsed;
      this.sessionRailOpen = true;
      this.autoSessionRailCollapsed = automatic && collapsed;
    },
    setContextPanelCollapsed(collapsed: boolean, automatic = false) {
      this.contextPanelCollapsed = collapsed;
      this.contextPanelOpen = true;
      this.autoContextPanelCollapsed = automatic && collapsed;
    },
    ensureFloatingWidth(targetWidth: number) {
      if (this.splitMode || window.innerWidth <= 900) return;
      const maxWidth = maxFloatingWidth();
      if (maxWidth < targetWidth || this.width >= targetWidth) return;
      this.setSize(targetWidth, this.height);
    },
    applyResponsivePanels() {
      const availableFloatingWidth = Math.max(0, window.innerWidth - VIEWPORT_MARGIN);
      const effectiveWidth = this.splitMode ? getSplitAgentWidth(this.splitAgentRatio) : Math.min(this.width, availableFloatingWidth);
      const compactOverlayMode = window.innerWidth <= 900 || effectiveWidth <= 900;
      if (compactOverlayMode) {
        if (!this.compactOverlayModeActive) {
          this.autoSessionRailCollapsed = true;
          this.autoContextPanelCollapsed = true;
          this.sessionRailCollapsed = true;
          this.contextPanelCollapsed = true;
          this.compactOverlayModeActive = true;
        }
        return;
      }
      this.compactOverlayModeActive = false;
      const nextLeftCollapsed = effectiveWidth < 1060;
      const nextRightCollapsed = effectiveWidth < 850;

      if (nextLeftCollapsed && !this.sessionRailCollapsed) {
        this.setSessionRailCollapsed(true, true);
      } else if (!nextLeftCollapsed && this.autoSessionRailCollapsed) {
        this.setSessionRailCollapsed(false, false);
      }

      if (nextRightCollapsed && !this.contextPanelCollapsed) {
        this.setContextPanelCollapsed(true, true);
      } else if (!nextRightCollapsed && this.autoContextPanelCollapsed) {
        this.setContextPanelCollapsed(false, false);
      }
    },
    setMobileTab(tab: AgentMobileTab) {
      this.mobileTab = tab;
    },
  },
});
