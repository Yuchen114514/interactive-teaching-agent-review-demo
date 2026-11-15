import type { AgentDiagramSummary } from '../api/types';

export interface DiagramAnchorMessage {
  id: string;
  role: string;
  turnIndex?: number;
  diagramSceneIds?: string[];
}

/** Build one stable placement pass for the visible timeline. */
export function buildInlineDiagramMap(
  diagrams: AgentDiagramSummary[],
  messages: DiagramAnchorMessage[],
): Map<string, AgentDiagramSummary[]> {
  const placements = new Map<string, AgentDiagramSummary[]>();
  const firstByTurn = new Map<number, string>();
  const explicitByScene = new Map<string, string>();
  let lastAssistantId = '';
  for (const message of messages) {
    if (message.role !== 'assistant') continue;
    lastAssistantId = message.id;
    if (message.turnIndex !== undefined && !firstByTurn.has(message.turnIndex))
      firstByTurn.set(message.turnIndex, message.id);
    for (const id of message.diagramSceneIds || []) explicitByScene.set(id, message.id);
  }
  for (const diagram of diagrams) {
    const id = diagram.scene_id || diagram.diagram_scene_id;
    if (!id) continue;
    const owner = explicitByScene.get(id) ||
      (diagram.source_turn_index !== undefined && diagram.source_turn_index !== null
        ? firstByTurn.get(diagram.source_turn_index)
        : undefined) || lastAssistantId;
    if (!owner) continue;
    const list = placements.get(owner) || [];
    list.push(diagram);
    placements.set(owner, list);
  }
  for (const list of placements.values())
    list.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
  return placements;
}

export function diagramsForMessage(
  diagrams: AgentDiagramSummary[],
  messages: DiagramAnchorMessage[],
  message: DiagramAnchorMessage,
): AgentDiagramSummary[] {
  return buildInlineDiagramMap(diagrams, messages).get(message.id) || [];
}
