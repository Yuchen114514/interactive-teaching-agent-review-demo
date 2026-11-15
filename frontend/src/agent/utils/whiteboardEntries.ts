import type { AgentBlackboardEntry } from "../api/types";

export function filterDisplayWhiteboardEntries(entries: AgentBlackboardEntry[]): AgentBlackboardEntry[] {
  return entries.filter((entry) => entry.entry_type === "whiteboard_entry");
}

export function whiteboardEntryDisplayTitle(entry: AgentBlackboardEntry): string {
  return normalizedDisplayText(entry.title) || "关键结论";
}

export function whiteboardEntryDisplayBody(entry: AgentBlackboardEntry): string {
  const body = normalizedDisplayText(entry.content) || normalizedDisplayText(entry.summary);
  if (!body) {
    return "";
  }
  const title = whiteboardEntryDisplayTitle(entry);
  return normalizeForCompare(body) === normalizeForCompare(title) ? "" : body;
}

function normalizedDisplayText(value: string | null | undefined): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeForCompare(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}
