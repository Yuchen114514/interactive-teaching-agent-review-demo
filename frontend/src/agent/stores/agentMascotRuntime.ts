import { computed, ref } from "vue";
import { defineStore } from "pinia";
import {
  getProblemSubmissionListService,
  getSubmissionByIdService,
} from "../../apis/problem";
import { getExamSubmissionListService } from "../../apis/exam";
import { AGENT_MASCOT_ERROR_DISPLAY_MS } from "../utils/agentMascotState";
import type { AgentMascotState } from "../utils/agentMascot";

const ACCEPTED_DISPLAY_MS = 2600;
const TRACKING_TIMEOUT_MS = 120_000;
const RESULT_POLL_MS = 2000;
const DISCOVERY_POLL_MS = 1000;
const SUBMISSION_CLOCK_TOLERANCE_MS = 10_000;
const MAX_TRANSIENT_FAILURES = 3;

export interface MascotSubmissionContext {
  problemId: number;
  examId?: number;
  nickname?: string;
  startedAt?: number;
}

export interface MascotObservedSubmission {
  id?: unknown;
  sub_id?: unknown;
  submissionId?: unknown;
  submission_id?: unknown;
  problemId?: unknown;
  problem_id?: unknown;
  examId?: unknown;
  exam_id?: unknown;
  nickname?: unknown;
  createTime?: unknown;
  create_time?: unknown;
  submitTime?: unknown;
  submit_time?: unknown;
  result?: unknown;
}

interface ActiveMascotSubmission {
  generation: number;
  problemId: number;
  examId?: number;
  nickname: string;
  startedAt: number;
  submissionId: number | null;
}

function toPositiveId(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function extractSubmissionId(payload: unknown): number | null {
  const direct = toPositiveId(payload);
  if (direct !== null) return direct;
  if (!payload || typeof payload !== "object") return null;

  const value = payload as Record<string, unknown>;
  for (const key of ["id", "sub_id", "submissionId", "submission_id"]) {
    const id = toPositiveId(value[key]);
    if (id !== null) return id;
  }
  for (const key of ["submission", "data"]) {
    const id = extractSubmissionId(value[key]);
    if (id !== null) return id;
  }
  return null;
}

export function mapSubmissionResultToMascotState(
  result: unknown,
): AgentMascotState {
  switch (
    String(result ?? "")
      .trim()
      .toUpperCase()
  ) {
    case "WT":
    case "JG":
      return "judging";
    case "AC":
      return "accepted";
    case "WA":
    case "PE":
      return "wrong-answer";
    case "CE":
      return "compile-error";
    case "TLE":
      return "timeout";
    case "RE":
      return "runtime-error";
    case "OE":
    default:
      return "other-error";
  }
}

function readSubmissionTime(
  submission: MascotObservedSubmission,
): number | null {
  const raw =
    submission.createTime ??
    submission.create_time ??
    submission.submitTime ??
    submission.submit_time;
  if (typeof raw !== "string" && typeof raw !== "number") return null;
  const normalized = typeof raw === "string" ? raw.replace(" ", "T") : raw;
  const parsed = new Date(normalized).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

function wait(delay: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, delay));
}

export const useAgentMascotRuntimeStore = defineStore(
  "agentMascotRuntime",
  () => {
    const activeSubmission = ref<ActiveMascotSubmission | null>(null);
    const judgeState = ref<AgentMascotState | null>(null);
    const generation = ref(0);
    let restoreTimer: ReturnType<typeof setTimeout> | null = null;

    const mascotState = computed(() => judgeState.value);

    function clearRestoreTimer() {
      if (restoreTimer === null) return;
      clearTimeout(restoreTimer);
      restoreTimer = null;
    }

    function scheduleRestore(
      state: AgentMascotState,
      currentGeneration: number,
    ) {
      clearRestoreTimer();
      const delay =
        state === "accepted"
          ? ACCEPTED_DISPLAY_MS
          : AGENT_MASCOT_ERROR_DISPLAY_MS;
      restoreTimer = setTimeout(() => {
        if (activeSubmission.value?.generation !== currentGeneration) return;
        activeSubmission.value = null;
        judgeState.value = null;
        restoreTimer = null;
      }, delay);
    }

    function beginSubmission(context: MascotSubmissionContext): number {
      clearRestoreTimer();
      generation.value += 1;
      activeSubmission.value = {
        generation: generation.value,
        problemId: context.problemId,
        examId: context.examId,
        nickname: context.nickname?.trim() ?? "",
        startedAt: context.startedAt ?? Date.now(),
        submissionId: null,
      };
      judgeState.value = "judging";
      return generation.value;
    }

    function acknowledgeSubmission(
      currentGeneration: number,
      response: unknown,
    ): boolean {
      const active = activeSubmission.value;
      if (!active || active.generation !== currentGeneration) return false;
      const submissionId = extractSubmissionId(response);
      if (submissionId !== null) active.submissionId = submissionId;
      return true;
    }

    function updateJudgeResult(
      result: unknown,
      submissionId?: number,
    ): boolean {
      const active = activeSubmission.value;
      if (!active) return false;
      if (submissionId !== undefined) {
        if (
          active.submissionId === null ||
          active.submissionId !== submissionId
        )
          return false;
      }

      const state = mapSubmissionResultToMascotState(result);
      judgeState.value = state;
      if (state !== "judging") scheduleRestore(state, active.generation);
      return true;
    }

    function observeSubmission(submission: MascotObservedSubmission): boolean {
      const active = activeSubmission.value;
      if (!active) return false;
      const submissionId = extractSubmissionId(submission);
      if (submissionId === null) return false;

      if (active.submissionId !== null) {
        if (active.submissionId !== submissionId) return false;
        return updateJudgeResult(submission.result, submissionId);
      }

      const problemId = toPositiveId(
        submission.problemId ?? submission.problem_id,
      );
      if (problemId !== null && problemId !== active.problemId) return false;
      const examId = toPositiveId(submission.examId ?? submission.exam_id);
      if (
        active.examId !== undefined &&
        examId !== null &&
        examId !== active.examId
      ) {
        return false;
      }
      if (active.nickname) {
        const nickname = String(submission.nickname ?? "").trim();
        if (nickname && nickname !== active.nickname) return false;
      }
      const submittedAt = readSubmissionTime(submission);
      if (
        submittedAt === null ||
        submittedAt < active.startedAt - SUBMISSION_CLOCK_TOLERANCE_MS
      ) {
        return false;
      }

      active.submissionId = submissionId;
      return updateJudgeResult(submission.result, submissionId);
    }

    function failSubmission(currentGeneration?: number): boolean {
      const active = activeSubmission.value;
      if (
        !active ||
        (currentGeneration !== undefined &&
          active.generation !== currentGeneration)
      ) {
        return false;
      }
      judgeState.value = "other-error";
      scheduleRestore("other-error", active.generation);
      return true;
    }

    async function trackSubmission(currentGeneration: number, token: string) {
      const trackingStartedAt = Date.now();
      let transientFailures = 0;

      while (Date.now() - trackingStartedAt < TRACKING_TIMEOUT_MS) {
        const active = activeSubmission.value;
        if (!active || active.generation !== currentGeneration) return;

        try {
          if (active.submissionId === null) {
            const filter = active.nickname
              ? { nickname: active.nickname }
              : undefined;
            const response =
              active.examId === undefined
                ? (
                    await getProblemSubmissionListService(
                      active.problemId,
                      token,
                      1,
                      20,
                      filter,
                    )
                  ).data
                : (
                    await getExamSubmissionListService(
                      active.examId,
                      token,
                      1,
                      20,
                      { ...filter, problemId: active.problemId },
                    )
                  ).data;
            if (response.status === 200 && Array.isArray(response.data)) {
              const candidates = [...response.data].sort(
                (left, right) =>
                  (extractSubmissionId(right) ?? 0) -
                  (extractSubmissionId(left) ?? 0),
              );
              candidates.some((submission) => observeSubmission(submission));
            }
          } else {
            const submissionId = active.submissionId;
            const response = (
              await getSubmissionByIdService(submissionId, token)
            ).data;
            if (response.status === 200) {
              updateJudgeResult(response.data?.result, submissionId);
              if (
                mapSubmissionResultToMascotState(response.data?.result) !==
                "judging"
              )
                return;
            }
          }
          transientFailures = 0;
        } catch {
          transientFailures += 1;
          if (transientFailures >= MAX_TRANSIENT_FAILURES) {
            failSubmission(currentGeneration);
            return;
          }
        }

        await wait(
          activeSubmission.value?.submissionId === null
            ? DISCOVERY_POLL_MS
            : RESULT_POLL_MS,
        );
      }

      failSubmission(currentGeneration);
    }

    function reset() {
      clearRestoreTimer();
      generation.value += 1;
      activeSubmission.value = null;
      judgeState.value = null;
    }

    return {
      activeSubmission,
      mascotState,
      beginSubmission,
      acknowledgeSubmission,
      updateJudgeResult,
      observeSubmission,
      failSubmission,
      trackSubmission,
      reset,
    };
  },
);
