import type { AgentSessionSummary } from "../api/types";

function parseTimeValue(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return 0;
  const parsed = typeof value === "number" ? value : Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function sessionActivityTimeValue(session: AgentSessionSummary) {
  return parseTimeValue(session.last_message_at ?? session.updated_at ?? session.created_at);
}

export function sortSessionsByRecentActivity(sessions: AgentSessionSummary[]) {
  return [...sessions].sort((left, right) => {
    const activityDiff = sessionActivityTimeValue(right) - sessionActivityTimeValue(left);
    if (activityDiff !== 0) return activityDiff;
    return String(right.session_id).localeCompare(String(left.session_id));
  });
}
