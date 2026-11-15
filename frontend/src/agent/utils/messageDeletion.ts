import type { AgentUiMessage } from "../stores/agentWorkspace";

export function filterDeletedMessage(messages: AgentUiMessage[], messageId: number) {
  return messages.filter((message) => message.messageId !== messageId);
}

export function filterDeletedMessages(messages: AgentUiMessage[], messageIds: number[]) {
  const removed = new Set(messageIds);
  return messages.filter((message) => message.messageId === undefined || !removed.has(message.messageId));
}
