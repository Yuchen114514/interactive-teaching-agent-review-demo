type RecordValue = Record<string, unknown>;

const treeKinds = new Set([
  'tree', 'bst', 'avl_tree', 'red_black_tree', 'heap', 'priority_queue',
  'trie', 'segment_tree', 'union_find',
]);

function record(value: unknown): RecordValue | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : null;
}

function records(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record).filter((item): item is RecordValue => !!item) : [];
}

function treeDepth(container: RecordValue): number {
  const items = records(container.items);
  const parent = new Map<string, string>();
  for (const item of items) {
    if (typeof item.id === 'string') parent.set(item.id, typeof item.parent === 'string' ? item.parent : '');
  }
  for (const edge of records(container.edges)) {
    if (typeof edge.to === 'string' && parent.has(edge.to) && !parent.get(edge.to) && typeof edge.from === 'string') {
      parent.set(edge.to, edge.from);
    }
  }
  function depth(id: string, seen: Set<string>): number {
    const predecessor = parent.get(id);
    if (!predecessor || seen.has(predecessor) || !parent.has(predecessor)) return 0;
    return 1 + depth(predecessor, new Set([...seen, id]));
  }
  return Math.max(0, ...[...parent.keys()].map(id => depth(id, new Set())));
}

function scenarioHeight(scene: RecordValue, frames: RecordValue[]): number | null {
  const regions = records(scene.regions);
  if (!regions.length) return null;
  let rows = 0;
  let pending = 0;
  const flush = () => { if (pending) { rows += 1; pending = 0; } };
  for (const region of regions) {
    const largest = Math.max(0, ...frames.map(frame =>
      Object.values(record(frame.placements) || {}).filter(value => value === region.id).length));
    if (region.view === 'row' && largest >= 4) {
      flush();
      rows += 1;
    } else {
      pending += 1;
      if (pending === 3) flush();
    }
  }
  flush();
  return 40 + rows * 230 + (rows - 1) * 28 + 96;
}

/** Mirror the renderer's geometry so the card has its real size before the SVG request completes. */
export function estimateSemanticFrameRatio(scene: unknown, frameIndex: number): number | null {
  const value = record(scene);
  const frames = records(value?.frames);
  const frame = frames[frameIndex];
  if (!value || !frame) return null;
  if (value.structure === 'scenario') {
    const height = scenarioHeight(value, frames);
    return height ? 960 / height : null;
  }
  const containers = records(frame.containers);
  if (!containers.length && !Array.isArray(frame.items)) return null;
  const views = containers.length ? containers : [{ kind: value.structure, items: frame.items, edges: frame.edges }];
  const count = views.length;
  const rods = count >= 4 && views.every(view => view.kind === 'rod');
  const columns = rods ? (count <= 7 ? count : Math.ceil(count / 2)) :
    count <= 3 ? count : count <= 9 ? 3 : 4;
  const baseHeight = rods ? 270 : count <= 3 ? 260 : 220;
  const cellHeight = Math.max(baseHeight, ...views.filter(view => treeKinds.has(String(view.kind)))
    .map(view => 110 + 54 * treeDepth(view)));
  const gap = frames.some(item => Array.isArray(item.transfers) && item.transfers.length) ? 44 : 12;
  const rows = Math.ceil(count / columns);
  return 960 / (40 + rows * cellHeight + (rows - 1) * gap);
}
