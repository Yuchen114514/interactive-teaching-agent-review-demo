import {
  isAgentMascotTerminalState,
  type AgentMascotState,
} from "./agentMascot";

export const AGENT_MASCOT_ERROR_DISPLAY_MS = 5000;

export function resolveAgentMascotErrorRestoreState(
  previousState: AgentMascotState,
): AgentMascotState {
  return isAgentMascotTerminalState(previousState) ? "idle" : previousState;
}
