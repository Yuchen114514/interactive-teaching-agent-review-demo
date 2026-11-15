export type AgentMascotState =
  | "idle"
  | "attentive"
  | "thinking"
  | "judging"
  | "accepted"
  | "wrong-answer"
  | "compile-error"
  | "timeout"
  | "runtime-error"
  | "other-error"
  | "offline";

export const AGENT_MASCOT_STATES: readonly AgentMascotState[] = [
  "idle",
  "attentive",
  "thinking",
  "judging",
  "accepted",
  "wrong-answer",
  "compile-error",
  "timeout",
  "runtime-error",
  "other-error",
  "offline",
] as const;

export const AGENT_MASCOT_LABELS: Readonly<Record<AgentMascotState, string>> = {
  idle: "准备答题",
  attentive: "在听",
  thinking: "正在分析",
  judging: "提交判题中",
  accepted: "AC · 通过",
  "wrong-answer": "WA · 输出错误",
  "compile-error": "CE · 编译失败",
  timeout: "TLE · 超时",
  "runtime-error": "RE · 运行时异常",
  "other-error": "OE · 其他错误",
  offline: "暂时离线",
};

export type AgentMascotTerminalState =
  | "accepted"
  | "wrong-answer"
  | "compile-error"
  | "timeout"
  | "runtime-error"
  | "other-error";

export type AgentMascotJudgeCode = "AC" | "WA" | "CE" | "TLE" | "RE" | "OE";

export const AGENT_MASCOT_CODES: Readonly<Record<AgentMascotTerminalState, AgentMascotJudgeCode>> = {
  accepted: "AC",
  "wrong-answer": "WA",
  "compile-error": "CE",
  timeout: "TLE",
  "runtime-error": "RE",
  "other-error": "OE",
};

export type AgentMascotTone = "neutral" | "active" | "positive" | "caution" | "danger" | "muted";

export type AgentMascotSkinId = "base" | "terminal" | "keycap" | "server";

export interface AgentMascotSkinPalette {
  front: string;
  top: string;
  side: string;
  back: string;
  arm: string;
  pixel: string;
  accent: string;
}

export interface AgentMascotSkin {
  label: string;
  palette: AgentMascotSkinPalette;
  indicator: Readonly<Record<AgentMascotTone, string>>;
  features: {
    scanlines: boolean;
    keycapLabel: boolean;
    serverPanel: boolean;
    serverVents: boolean;
  };
}

export const AGENT_MASCOT_SKIN_IDS: readonly AgentMascotSkinId[] = [
  "base",
  "terminal",
  "keycap",
  "server",
] as const;

export const AGENT_MASCOT_SKINS: Readonly<Record<AgentMascotSkinId, AgentMascotSkin>> = {
  base: {
    label: "基础蓝灰",
    palette: {
      front: "#9ebbd2",
      top: "#c1d6e6",
      side: "#748fa6",
      back: "#647f96",
      arm: "#8facc3",
      pixel: "#252a2d",
      accent: "#8facc3",
    },
    indicator: {
      neutral: "#8fa7b7",
      active: "#78a2a9",
      positive: "#79a88b",
      caution: "#b49b6b",
      danger: "#ad7774",
      muted: "#879294",
    },
    features: {
      scanlines: false,
      keycapLabel: false,
      serverPanel: false,
      serverVents: false,
    },
  },
  terminal: {
    label: "老式绿终端",
    palette: {
      front: "#244d45",
      top: "#38675b",
      side: "#173a35",
      back: "#122e2b",
      arm: "#2c5d50",
      pixel: "#b8d6a6",
      accent: "#78a888",
    },
    indicator: {
      neutral: "#6f9a82",
      active: "#9fc596",
      positive: "#b7df99",
      caution: "#cfb16e",
      danger: "#d27f72",
      muted: "#45635c",
    },
    features: {
      scanlines: true,
      keycapLabel: false,
      serverPanel: false,
      serverVents: false,
    },
  },
  keycap: {
    label: "机械键帽",
    palette: {
      front: "#d8d2c3",
      top: "#f0e9db",
      side: "#aaa99e",
      back: "#8e918b",
      arm: "#c4beb0",
      pixel: "#3b2418",
      accent: "#7f8e89",
    },
    indicator: {
      neutral: "#87918b",
      active: "#6f9b92",
      positive: "#709a7c",
      caution: "#b18f5d",
      danger: "#a96f68",
      muted: "#929893",
    },
    features: {
      scanlines: false,
      keycapLabel: false,
      serverPanel: false,
      serverVents: false,
    },
  },
  server: {
    label: "服务器机柜",
    palette: {
      front: "#4c5558",
      top: "#626b6d",
      side: "#323b3e",
      back: "#252c2f",
      arm: "#414b4f",
      pixel: "#d5e0dd",
      accent: "#87979a",
    },
    indicator: {
      neutral: "#9da8a8",
      active: "#77a8a0",
      positive: "#83b18a",
      caution: "#b69c65",
      danger: "#b8736c",
      muted: "#697477",
    },
    features: {
      scanlines: false,
      keycapLabel: false,
      serverPanel: true,
      serverVents: true,
    },
  },
};

export const AGENT_MASCOT_SKIN_LABELS: Readonly<Record<AgentMascotSkinId, string>> = {
  base: AGENT_MASCOT_SKINS.base.label,
  terminal: AGENT_MASCOT_SKINS.terminal.label,
  keycap: AGENT_MASCOT_SKINS.keycap.label,
  server: AGENT_MASCOT_SKINS.server.label,
};

export const AGENT_MASCOT_SKINS_LIST: readonly {
  id: AgentMascotSkinId;
  skin: AgentMascotSkin;
}[] = AGENT_MASCOT_SKIN_IDS.map((id) => ({ id, skin: AGENT_MASCOT_SKINS[id] }));

const MASCOT_SKIN_SET = new Set<string>(AGENT_MASCOT_SKIN_IDS);

export function normalizeAgentMascotSkin(value: unknown): AgentMascotSkinId {
  return typeof value === "string" && MASCOT_SKIN_SET.has(value)
    ? value as AgentMascotSkinId
    : "base";
}

export type AgentMascotErrorReason =
  | "output-mismatch"
  | "format-error"
  | "runtime-error"
  | "time-limit"
  | "compile-error";

export type AgentMascotWrongAnswerReason = "output-mismatch" | "format-error";

export type AgentMascotPixelPattern = readonly string[];

export interface AgentMascotPalette {
  front: string;
  top: string;
  side: string;
  arm: string;
  pixel: string;
}

export interface AgentMascotExpression {
  leftEye: AgentMascotPixelPattern;
  rightEye: AgentMascotPixelPattern;
  leftBrow: AgentMascotPixelPattern;
  rightBrow: AgentMascotPixelPattern;
  mouth: AgentMascotPixelPattern;
  gaze: readonly [number, number];
}

export interface AgentMascotPose {
  palette: AgentMascotPalette;
  expression: AgentMascotExpression;
  bodyRotation: readonly [number, number, number];
  armRotation: readonly [number, number];
}

export const AGENT_MASCOT_TONES: Readonly<Record<AgentMascotState, AgentMascotTone>> = {
  idle: "neutral",
  attentive: "active",
  thinking: "active",
  judging: "active",
  accepted: "positive",
  "wrong-answer": "caution",
  "compile-error": "danger",
  timeout: "caution",
  "runtime-error": "danger",
  "other-error": "danger",
  offline: "muted",
};

export const AGENT_MASCOT_ERROR_REASONS: readonly AgentMascotErrorReason[] = [
  "output-mismatch",
  "format-error",
  "runtime-error",
  "time-limit",
  "compile-error",
] as const;

export const AGENT_MASCOT_WRONG_ANSWER_REASONS: readonly AgentMascotWrongAnswerReason[] = [
  "output-mismatch",
  "format-error",
] as const;

export const AGENT_MASCOT_ERROR_REASON_LABELS: Readonly<Record<AgentMascotErrorReason, string>> = {
  "output-mismatch": "输出结果错误",
  "format-error": "格式错误",
  "runtime-error": "运行时异常",
  "time-limit": "超出时间限制",
  "compile-error": "编译失败",
};

export const AGENT_MASCOT_ERROR_REASON_CODES: Readonly<Record<AgentMascotErrorReason, string>> = {
  "output-mismatch": "WA",
  "format-error": "WA",
  "runtime-error": "RE",
  "time-limit": "TLE",
  "compile-error": "CE",
};

const DEFAULT_PALETTE: AgentMascotPalette = {
  front: "#9ebbd2",
  top: "#c1d6e6",
  side: "#748fa6",
  arm: "#8facC3".toLowerCase(),
  pixel: "#252a2d",
};

const ACTIVE_PALETTE: AgentMascotPalette = {
  front: "#91b5bb",
  top: "#bed4d6",
  side: "#698d93",
  arm: "#84a8ae",
  pixel: "#213033",
};

export const AGENT_MASCOT_POSES: Readonly<Record<AgentMascotState, AgentMascotPose>> = {
  idle: {
    palette: DEFAULT_PALETTE,
    expression: {
      leftEye: ["##", "##"],
      rightEye: ["##", "##"],
      leftBrow: ["###"],
      rightBrow: ["###"],
      mouth: ["###"],
      gaze: [0, 0],
    },
    bodyRotation: [0, 0, 0],
    armRotation: [0, 0],
  },
  attentive: {
    palette: ACTIVE_PALETTE,
    expression: {
      leftEye: ["###", "###"],
      rightEye: ["###", "###"],
      leftBrow: [".##", "#.."],
      rightBrow: ["##.", "..#"],
      mouth: ["#.#", ".#."],
      gaze: [0, 0.03],
    },
    bodyRotation: [-3, 0, 0],
    armRotation: [-5, 5],
  },
  thinking: {
    palette: {
      front: "#91aeb6",
      top: "#c0d2d6",
      side: "#6d8990",
      arm: "#86a2a9",
      pixel: "#253033",
    },
    expression: {
      leftEye: ["###", "###"],
      rightEye: ["###"],
      leftBrow: ["#..", ".##"],
      rightBrow: ["###"],
      mouth: ["#.#.#"],
      gaze: [0.03, 0.04],
    },
    bodyRotation: [0, -5, -2],
    armRotation: [-12, 8],
  },
  judging: {
    palette: {
      front: "#8ba8b3",
      top: "#bccdd3",
      side: "#687f89",
      arm: "#819aa4",
      pixel: "#202a2e",
    },
    expression: {
      leftEye: ["####"],
      rightEye: ["####"],
      leftBrow: ["####"],
      rightBrow: ["####"],
      mouth: ["##.##.##"],
      gaze: [0, 0],
    },
    bodyRotation: [0, 0, 0],
    armRotation: [5, -5],
  },
  accepted: {
    palette: {
      front: "#91b5a0",
      top: "#c3d9ca",
      side: "#698b75",
      arm: "#83a991",
      pixel: "#24312a",
    },
    expression: {
      leftEye: ["#.#", ".#."],
      rightEye: ["#.#", ".#."],
      leftBrow: ["###"],
      rightBrow: ["###"],
      mouth: ["#...#", ".#.#.", "..#.."],
      gaze: [0, 0],
    },
    bodyRotation: [-2, 3, 1],
    armRotation: [-58, 8],
  },
  "wrong-answer": {
    palette: {
      front: "#c1aa7f",
      top: "#ddcfaf",
      side: "#917a56",
      arm: "#b49b70",
      pixel: "#342d22",
    },
    expression: {
      leftEye: ["###", ".##"],
      rightEye: ["##.", "###"],
      leftBrow: ["#..", ".##"],
      rightBrow: ["..#", "##."],
      mouth: ["..#..", ".#.#.", "#...#"],
      gaze: [-0.02, -0.02],
    },
    bodyRotation: [2, -3, -3],
    armRotation: [15, -18],
  },
  "compile-error": {
    palette: {
      front: "#ba918a",
      top: "#d8b9b3",
      side: "#8a625d",
      arm: "#ad827b",
      pixel: "#352525",
    },
    expression: {
      leftEye: ["#.#", ".#.", "#.#"],
      rightEye: ["#.#", ".#.", "#.#"],
      leftBrow: ["#..", ".##"],
      rightBrow: ["..#", "##."],
      mouth: ["##.##", ".#.#."],
      gaze: [0, 0],
    },
    bodyRotation: [1, 0, 2],
    armRotation: [24, -24],
  },
  timeout: {
    palette: {
      front: "#aaa18c",
      top: "#cbc4b4",
      side: "#7d7565",
      arm: "#9d947f",
      pixel: "#34312b",
    },
    expression: {
      leftEye: ["####"],
      rightEye: ["####"],
      leftBrow: ["####"],
      rightBrow: ["####"],
      mouth: ["#####"],
      gaze: [0, -0.06],
    },
    bodyRotation: [6, 0, 0],
    armRotation: [28, -28],
  },
  "runtime-error": {
    palette: {
      front: "#b48f91",
      top: "#d5b7b8",
      side: "#805f61",
      arm: "#a77e80",
      pixel: "#342526",
    },
    expression: {
      leftEye: ["#.#", ".#.", "#.#"],
      rightEye: ["#.#", ".#.", "#.#"],
      leftBrow: ["###"],
      rightBrow: ["###"],
      mouth: ["####", "#..."],
      gaze: [0, -0.03],
    },
    bodyRotation: [-2, 4, 2],
    armRotation: [36, -12],
  },
  "other-error": {
    palette: {
      front: "#989da6",
      top: "#c5c8cd",
      side: "#6f747c",
      arm: "#888d96",
      pixel: "#2f3338",
    },
    expression: {
      leftEye: ["##"],
      rightEye: ["##"],
      leftBrow: [".##"],
      rightBrow: ["##."],
      mouth: [".##.", "#..#", ".##."],
      gaze: [0, 0],
    },
    bodyRotation: [4, -4, 0],
    armRotation: [12, -12],
  },
  offline: {
    palette: {
      front: "#a2aaad",
      top: "#c4c9ca",
      side: "#747c7f",
      arm: "#959da0",
      pixel: "#505759",
    },
    expression: {
      leftEye: ["####"],
      rightEye: ["####"],
      leftBrow: ["...."],
      rightBrow: ["...."],
      mouth: ["....."],
      gaze: [0, -0.05],
    },
    bodyRotation: [5, 0, 0],
    armRotation: [32, -32],
  },
};

const MASCOT_STATE_SET = new Set<string>(AGENT_MASCOT_STATES);

export function normalizeAgentMascotState(value: unknown): AgentMascotState {
  return typeof value === "string" && MASCOT_STATE_SET.has(value) ? value as AgentMascotState : "idle";
}

export function isAgentMascotTerminalState(state: AgentMascotState): boolean {
  return state === "accepted"
    || state === "wrong-answer"
    || state === "compile-error"
    || state === "timeout"
    || state === "runtime-error"
    || state === "other-error";
}

export function isAgentMascotErrorState(
  state: AgentMascotState,
): state is Exclude<AgentMascotTerminalState, "accepted"> {
  return state !== "accepted" && isAgentMascotTerminalState(state);
}
