export type AgentUiVersion = "legacy" | "workspace" | "both";

const AGENT_UI_VERSION_KEY = "agent_ui_version";
const allowedVersions = new Set<AgentUiVersion>(["legacy", "workspace", "both"]);

function isAgentUiVersion(value: unknown): value is AgentUiVersion {
  return typeof value === "string" && allowedVersions.has(value as AgentUiVersion);
}

export function resolveAgentUiVersion(): AgentUiVersion {
  const localOverride = localStorage.getItem(AGENT_UI_VERSION_KEY);
  if (isAgentUiVersion(localOverride)) {
    return localOverride;
  }

  const envVersion = import.meta.env?.VITE_AGENT_UI_VERSION;
  if (isAgentUiVersion(envVersion)) {
    return envVersion;
  }

  return "both";
}

export function setAgentUiVersionOverride(version: AgentUiVersion | null) {
  if (version) {
    localStorage.setItem(AGENT_UI_VERSION_KEY, version);
  } else {
    localStorage.removeItem(AGENT_UI_VERSION_KEY);
  }
}

export function resolveAgentApiBaseUrl(): string {
  const envBaseUrl = import.meta.env?.VITE_AGENT_API_BASE_URL;
  if (typeof envBaseUrl === "string" && envBaseUrl.trim()) {
    return envBaseUrl.trim().replace(/\/+$/, "");
  }
  return "/api/agents";
}
