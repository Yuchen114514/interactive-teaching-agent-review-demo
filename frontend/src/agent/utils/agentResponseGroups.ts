import type { AgentUiMessage } from '../stores/agentWorkspace';

export interface AgentResponseGroup extends AgentUiMessage {
  continuations?: AgentUiMessage[];
  responseMessageIds: number[];
}

/** Keep every visible part of one assistant turn under one timeline action. */
export function groupAgentResponseMessages(messages: AgentUiMessage[]): AgentResponseGroup[] {
  const groups: AgentResponseGroup[] = [];
  for (const message of messages) {
    const previous = groups[groups.length - 1];
    const sameResponse = message.role === 'assistant' && previous?.role === 'assistant' && (
      message.sourceAssistantId === previous.id ||
      (message.turnIndex !== undefined && previous.turnIndex !== undefined &&
        message.turnIndex === previous.turnIndex)
    );
    if (sameResponse) {
      previous.continuations = [...(previous.continuations || []), message];
      if (message.messageId !== undefined) previous.responseMessageIds.push(message.messageId);
      continue;
    }
    groups.push({
      ...message,
      responseMessageIds: message.messageId === undefined ? [] : [message.messageId],
    });
  }
  return groups;
}
