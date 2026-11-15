export type AgentUiMessageRole = "user" | "assistant" | "system";

export function normalizeAgentMessageRole(role: unknown): AgentUiMessageRole {
  const normalized = String(role || "").toLowerCase();
  if (["user", "human", "owner"].includes(normalized)) return "user";
  if (normalized === "system") return "system";
  return "assistant";
}
