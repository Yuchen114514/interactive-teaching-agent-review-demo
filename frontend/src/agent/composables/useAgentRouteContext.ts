import { computed } from "vue";
import { useRoute } from "vue-router";

export interface AgentRouteContext {
  source: string;
  problem_id?: number;
  submission_id?: number;
  course_id?: number;
  class_id?: number;
  exam_id?: number;
}

function toPositiveNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isInteger(value) && value > 0) return value;
  if (typeof value !== "string") return undefined;
  if (!/^\d+$/.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function pickIdFromPath(path: string, pattern: RegExp): number | undefined {
  const matched = path.match(pattern);
  return matched?.[1] ? toPositiveNumber(matched[1]) : undefined;
}

function inferAgentSource(path: string): string {
  if (/^\/submission\//.test(path)) return "submission_debug";
  if (/^\/exam\//.test(path)) return "exam_assistant";
  if (/^\/class\//.test(path)) return "course_tutor";
  if (/^\/problem\//.test(path) || /^\/editProblem\//.test(path)) return "problem_help";
  return "web";
}

export function useAgentRouteContext() {
  const route = useRoute();

  const context = computed<AgentRouteContext>(() => {
    const path = route.path || "";
    const params = route.params || {};
    const query = route.query || {};
    const problemId =
      toPositiveNumber(params.problemId) ||
      pickIdFromPath(path, /^\/problem\/(\d+)(?:\/|$)/) ||
      pickIdFromPath(path, /^\/editProblem\/(\d+)(?:\/|$)/);
    const submissionId =
      toPositiveNumber(params.submissionId) ||
      pickIdFromPath(path, /^\/submission\/(\d+)(?:\/|$)/);
    const courseId =
      toPositiveNumber(params.courseId) ||
      toPositiveNumber(query.course_id) ||
      toPositiveNumber(query.courseId) ||
      pickIdFromPath(path, /^\/course\/(\d+)(?:\/|$)/);
    const classId =
      toPositiveNumber(params.classId) ||
      pickIdFromPath(path, /^\/class\/(\d+)(?:\/|$)/);
    const examId =
      toPositiveNumber(params.examId) ||
      pickIdFromPath(path, /^\/exam\/(\d+)(?:\/|$)/);

    return {
      source: inferAgentSource(path),
      problem_id: problemId,
      submission_id: submissionId,
      course_id: courseId,
      class_id: classId,
      exam_id: examId,
    };
  });

  function appendRouteContext<T extends Record<string, unknown>>(body: T): T & AgentRouteContext {
    const snapshot = context.value;
    return {
      ...body,
      source: snapshot.source,
      ...(snapshot.problem_id !== undefined ? { problem_id: snapshot.problem_id } : {}),
      ...(snapshot.submission_id !== undefined ? { submission_id: snapshot.submission_id } : {}),
      ...(snapshot.course_id !== undefined ? { course_id: snapshot.course_id } : {}),
      ...(snapshot.class_id !== undefined ? { class_id: snapshot.class_id } : {}),
      ...(snapshot.exam_id !== undefined ? { exam_id: snapshot.exam_id } : {}),
    };
  }

  return {
    context,
    appendRouteContext,
  };
}
