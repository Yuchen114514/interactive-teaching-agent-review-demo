import type { AgentRouteContext } from "../composables/useAgentRouteContext";

export type ChatRequestCleanup = {
  finalizeAssistant: boolean;
  loadBundle: boolean;
  refreshSessions: boolean;
};

export function buildAgentChatRequest(input: {
  message: string;
  sessionId: string | null;
  guidanceDepth: string;
  images: string[];
  routeContext: AgentRouteContext;
}) {
  const { routeContext } = input;
  return {
    message: input.message,
    session_id: input.sessionId || undefined,
    guidance_depth: input.guidanceDepth,
    depth_override: true,
    images: input.images,
    source: routeContext.source,
    ...(routeContext.problem_id !== undefined
      ? { problem_id: routeContext.problem_id }
      : {}),
    ...(routeContext.submission_id !== undefined
      ? { submission_id: routeContext.submission_id }
      : {}),
    ...(routeContext.course_id !== undefined
      ? { course_id: routeContext.course_id }
      : {}),
    ...(routeContext.class_id !== undefined
      ? { class_id: routeContext.class_id }
      : {}),
    ...(routeContext.exam_id !== undefined
      ? { exam_id: routeContext.exam_id }
      : {}),
  };
}

export function getChatRequestCleanup(redirected: boolean): ChatRequestCleanup {
  return {
    finalizeAssistant: !redirected,
    loadBundle: !redirected,
    refreshSessions: true,
  };
}
