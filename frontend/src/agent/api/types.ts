export type AgentStreamEvent =
  | { kind: "session"; sessionId: string; runId?: string }
  | {
      kind: "problem_redirect";
      problemId: number | null;
      problemTitle: string;
      message: string;
      actionLabel: string;
    }
  | {
      kind: "progress";
      stage: string;
      message: string;
      agent?: string;
      heartbeat?: boolean;
      model?: string;
      providerBackend?: string;
    }
  | { kind: "main_delta"; delta: string }
  | { kind: "answer_delta"; delta: string }
  | { kind: "text"; text: string }
  | { kind: "partial_response"; response: string }
  | { kind: "main_visible_complete"; response: string }
  | { kind: "diagram_decision"; needDiagram: boolean }
  | { kind: "followup_start" }
  | { kind: "followup_delta"; delta: string }
  | { kind: "followup_complete"; response: string }
  | {
      kind: "mcp_status";
      status: string;
      resource?: string;
      message?: string;
      agent?: string;
      payload: Record<string, unknown>;
    }
  | {
      kind: "plan_status";
      status: string;
      agent?: string;
      message?: string;
      payload: Record<string, unknown>;
    }
  | {
      kind: "diagram_status";
      status: string;
      payload: Record<string, unknown>;
    }
  | { kind: "diagram_ready"; payload: Record<string, unknown> }
  | {
      kind: "whiteboard_status";
      status: string;
      payload: Record<string, unknown>;
    }
  | {
      kind: "memory_status";
      status: string;
      payload: Record<string, unknown>;
    }
  | {
      kind: "compaction_status";
      status: string;
      payload: Record<string, unknown>;
    }
  | {
      kind: "whiteboard_ready";
      entry?: unknown;
      payload: Record<string, unknown>;
    }
  | {
      kind: "run_status";
      status: string;
      message?: string;
      payload: Record<string, unknown>;
    }
  | { kind: "error"; error: string }
  | { kind: "final"; response: Record<string, unknown> }
  | { kind: "done" };

export type AgentMascotSkinPreference =
  | "base"
  | "terminal"
  | "keycap"
  | "server";
export type AgentFontSizePreference = "small" | "medium" | "large";

export interface AgentUserPreferences {
  mascot_skin: AgentMascotSkinPreference;
  font_size: AgentFontSizePreference;
}

export interface AgentSessionSummary {
  session_id: string;
  is_shared_demo?: boolean;
  user_id?: string;
  created_at?: string | number;
  updated_at?: string | number;
  last_message_at?: string | number | null;
  preview?: string;
  title?: string;
  source?: string;
  problem_id?: number | null;
  problem_title?: string | null;
}

export interface AgentProblemSessionFolder {
  problem_id: number;
  problem_title: string;
  last_active_at?: string | number | null;
  sessions: AgentSessionSummary[];
}

export interface AgentSessionGroupsResponse {
  problem_groups: AgentProblemSessionFolder[];
  standalone_sessions: AgentSessionSummary[];
}

export interface AgentHistoryMessage {
  message_id?: number;
  role: "user" | "assistant" | "system" | string;
  message_type?: string;
  content: string;
  turn_index?: number;
  created_at?: string | number | null;
  images?: AgentMessageImage[];
}

export interface AgentMessageImage {
  image_id: string;
  url: string;
  thumbnail_url?: string;
  filename?: string;
}

export interface AgentUploadedImage {
  image_id: string;
  url: string;
  thumbnail_url?: string;
  filename?: string;
  mime_type?: string;
  size?: number;
  created_at?: string;
}

export interface AgentRuntimeInfo {
  provider?: {
    enabled?: boolean;
    backend?: string;
    model?: string;
    activation_status?: string;
    activation_reason?: string | null;
  };
  streaming?: {
    transport?: string;
    progress_event_type?: string;
    partial_response_event_type?: string;
    final_event_type?: string;
  };
}

export interface AgentWorkspaceSummary {
  workspace_id: number;
  session_id?: string;
  title?: string | null;
  problem_id?: number | null;
  problem_title?: string | null;
  problem_summary?: string | null;
  current_step?: AgentWorkspaceStep | null;
  steps?: AgentWorkspaceStep[];
  guidance_depth?: string | null;
}

export interface AgentWorkspaceStep {
  step_key?: string | null;
  title?: string | null;
  label?: string | null;
  status?: string | null;
  is_current?: boolean | null;
  isCurrent?: boolean | null;
  order?: number | null;
}

export interface AgentBlackboardEntry {
  entry_id?: number | string;
  id?: number | string;
  workspace_id?: number;
  title?: string | null;
  content?: string | null;
  summary?: string | null;
  entry_type?: string | null;
  source_message_id?: number | null;
  created_at?: string | number | null;
}

export interface AgentDiagramSummary {
  scene_id?: string;
  diagram_scene_id?: string;
  workspace_id?: number;
  title?: string | null;
  summary?: string | null;
  diagram_kind?: string | null;
  status?: string | null;
  step_key?: string | null;
  source_turn_index?: number | null;
  created_at?: string | number | null;
}

export interface AgentChatRequestBody {
  message: string;
  session_id?: string;
  guidance_depth?: string;
  depth_override?: boolean;
  images?: string[];
  source?: string;
  problem_id?: number;
  submission_id?: number;
  course_id?: number;
  class_id?: number;
  exam_id?: number;
}

export interface AgentTurnRunSummary {
  run_id: string;
  status: string;
  turn_index: number;
  user_message_id?: number | null;
  assistant_message_id?: number | null;
  response_preview?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  event_count?: number;
}

export interface AgentTurnRunEventSummary {
  type: string;
  event_index: number;
  payload?: Record<string, unknown>;
}

export interface AgentTurnRunEventsResponse extends AgentTurnRunSummary {
  events: AgentTurnRunEventSummary[];
}
