import type { AgentSessionSummary } from "../api/types";

export function shouldSubmitSessionRename(nextTitle: string, originalTitle: string) {
  const normalizedNext = nextTitle.trim();
  if (!normalizedNext) return false;
  return normalizedNext !== originalTitle.trim();
}

export function mergeRenamedSessionInPlace(
  current: AgentSessionSummary | undefined,
  renamed: AgentSessionSummary,
): AgentSessionSummary {
  return {
    ...renamed,
    created_at: current?.created_at ?? renamed.created_at,
    updated_at: current?.updated_at ?? renamed.updated_at,
  };
}
