export interface ProblemListWorkspace {
  workspace_id: number;
}

export interface ProblemRouteActionInput {
  problemId?: number | null;
  currentPageProblemId?: number | null;
}

export type ProblemRouteAction =
  | {
      kind: "current";
      label: "当前题目";
      href: null;
    }
  | {
      kind: "return";
      label: "返回题目";
      href: string;
    };

export function shouldShowProblemList(workspaces: readonly ProblemListWorkspace[]) {
  return workspaces.length > 1;
}

export function getProblemRouteAction(input: ProblemRouteActionInput): ProblemRouteAction | null {
  if (!input.problemId) return null;
  if (input.currentPageProblemId === input.problemId) {
    return {
      kind: "current",
      label: "当前题目",
      href: null,
    };
  }
  return {
    kind: "return",
    label: "返回题目",
    href: `/problem/${input.problemId}`,
  };
}
