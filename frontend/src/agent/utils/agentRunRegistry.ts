const activeRuns = new Map<string, number>();
const key = (sessionId: string | null | undefined) => sessionId || 'draft';
export function registerLocalAgentRun(sessionId: string | null | undefined) {
  const id = key(sessionId);
  activeRuns.set(id, (activeRuns.get(id) || 0) + 1);
}
export function unregisterLocalAgentRun(sessionId: string | null | undefined) {
  const id = key(sessionId);
  const count = (activeRuns.get(id) || 0) - 1;
  if (count > 0) activeRuns.set(id, count);
  else activeRuns.delete(id);
}
export function hasLocalAgentRun(sessionId: string | null | undefined) {
  return Boolean(activeRuns.get(key(sessionId)));
}
export function clearLocalAgentRuns() { activeRuns.clear(); }
