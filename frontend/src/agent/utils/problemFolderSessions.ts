import type { AgentSessionSummary } from "../api/types";
import { sessionActivityTimeValue } from "./sessionActivity";

export interface ProblemFolderSessionGroup {
  problemId: number;
  problemTitle: string;
  sessions: AgentSessionSummary[];
  latestActivity: number;
  isTemporary: boolean;
}

export interface ProblemFolderSessionGroups {
  problemFolders: ProblemFolderSessionGroup[];
  standaloneSessions: AgentSessionSummary[];
}

export interface ProblemFolderSessionGroupingOptions {
  currentProblemId?: number | null;
}

function compareSessions(left: AgentSessionSummary, right: AgentSessionSummary) {
  const activityDifference = sessionActivityTimeValue(right) - sessionActivityTimeValue(left);
  if (activityDifference !== 0) return activityDifference;
  return String(right.session_id).localeCompare(String(left.session_id));
}

export function groupProblemFolderSessions(
  sessions: readonly AgentSessionSummary[],
  options: ProblemFolderSessionGroupingOptions = {},
): ProblemFolderSessionGroups {
  const folders = new Map<number, AgentSessionSummary[]>();
  const standaloneSessions: AgentSessionSummary[] = [];

  for (const session of sessions) {
    if (session.problem_id === null || session.problem_id === undefined) {
      standaloneSessions.push(session);
      continue;
    }

    const folderSessions = folders.get(session.problem_id) ?? [];
    folderSessions.push(session);
    folders.set(session.problem_id, folderSessions);
  }

  const problemFolders: ProblemFolderSessionGroup[] = [...folders.entries()].map(([problemId, folderSessions]) => {
    const sortedSessions = [...folderSessions].sort(compareSessions);
    return {
      problemId,
      problemTitle: folderSessions.find((session) => session.problem_title?.trim())?.problem_title?.trim() || `题目 ${problemId}`,
      sessions: sortedSessions,
      latestActivity: sortedSessions.length > 0 ? sessionActivityTimeValue(sortedSessions[0]) : 0,
      isTemporary: false,
    };
  });

  const currentProblemId = options.currentProblemId;
  if (currentProblemId !== null && currentProblemId !== undefined && !folders.has(currentProblemId)) {
    problemFolders.push({
      problemId: currentProblemId,
      problemTitle: `题目 ${currentProblemId}`,
      sessions: [],
      latestActivity: 0,
      isTemporary: true,
    });
  }

  problemFolders.sort((left, right) => {
    if (left.problemId === currentProblemId) return -1;
    if (right.problemId === currentProblemId) return 1;
    const activityDifference = right.latestActivity - left.latestActivity;
    if (activityDifference !== 0) return activityDifference;
    return right.problemId - left.problemId;
  });

  return {
    problemFolders,
    standaloneSessions: [...standaloneSessions].sort(compareSessions),
  };
}
