export interface SessionRenderOrderItem {
  session_id: string;
}

export function sessionIds<T extends SessionRenderOrderItem>(sessions: T[]) {
  return new Set(sessions.map((session) => session.session_id));
}

export function mergeRenderedSessions<T extends SessionRenderOrderItem>(
  currentSessions: T[],
  nextSessions: T[],
  exitingIds: Set<string>,
) {
  const nextIds = sessionIds(nextSessions);
  const exitingById = new Map(
    currentSessions
      .filter((session) => exitingIds.has(session.session_id) && !nextIds.has(session.session_id))
      .map((session) => [session.session_id, session]),
  );
  const merged: T[] = [...nextSessions];
  const indexById = new Map(merged.map((session, index) => [session.session_id, index]));

  for (const session of currentSessions) {
    if (!exitingById.has(session.session_id)) continue;
    const previousIndex = currentSessions.indexOf(session);
    const nextAnchor = currentSessions.slice(previousIndex + 1).find((item) => indexById.has(item.session_id));
    const previousAnchor = [...currentSessions.slice(0, previousIndex)].reverse().find((item) => indexById.has(item.session_id));
    const insertIndex = nextAnchor
      ? indexById.get(nextAnchor.session_id) ?? merged.length
      : previousAnchor
        ? (indexById.get(previousAnchor.session_id) ?? merged.length - 1) + 1
        : merged.length;

    merged.splice(insertIndex, 0, session);
    indexById.clear();
    for (let index = 0; index < merged.length; index += 1) {
      indexById.set(merged[index].session_id, index);
    }
  }

  return merged;
}
