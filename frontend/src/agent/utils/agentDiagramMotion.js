// Mirrors tools/diagram_eval/motion.js. The production player uses
// the same item identity, transfer and edge animation rules as the eval demo.
  const treeKinds = new Set(['tree', 'bst', 'avl_tree', 'red_black_tree', 'heap', 'priority_queue', 'trie', 'segment_tree', 'union_find']);

  function containers(frame, structure) {
    if (Array.isArray(frame?.containers)) return frame.containers;
    return Array.isArray(frame?.items) ? [{ id: 'primary', kind: structure, items: frame.items, edges: frame.edges || [] }] : [];
  }

  function canvasAspect(svgs) {
    let chosen = [960, 300];
    for (const svg of svgs || []) {
      const match = svg.match(/viewBox=["']\s*0\s+0\s+([\d.]+)\s+([\d.]+)["']/);
      if (!match) continue;
      const width = Number(match[1]), height = Number(match[2]);
      if (width > 0 && height > 0 && height / width > chosen[1] / chosen[0]) chosen = [width, height];
    }
    return `${chosen[0]} / ${chosen[1]}`;
  }

  function collect(frame, structure) {
    return containers(frame, structure).flatMap(c => (c.items || []).map(item => ({
      container: c.id, item: item.id, label: String(item.label ?? ''),
      value: String(item.value ?? item.label ?? '').replace(/^\s*(?:下标)?\d+\s*[:：]\s*/, ''), kind: c.kind,
    })));
  }

  function edges(frame, structure) {
    const keys = new Map();
    for (const c of containers(frame, structure)) {
      if (treeKinds.has(c.kind)) {
        for (const item of c.items || []) if (item.parent) keys.set(`${c.id}:${item.parent}>${item.id}`, false);
        for (const edge of c.edges || []) keys.set(`${c.id}:${edge.from}>${edge.to}`, false);
      } else for (const edge of c.edges || []) keys.set(`${c.id}:${edge.from}>${edge.to}`, !!edge.selected);
    }
    return keys;
  }

  function planTransition(before, after, structure) {
    const old = collect(before, structure), next = collect(after, structure);
    const usedOld = new Set(), usedNew = new Set(), matches = [];
    const claim = (oi, ni) => {
      if (oi < 0 || ni < 0 || usedOld.has(oi) || usedNew.has(ni)) return;
      usedOld.add(oi); usedNew.add(ni); matches.push({ from: { container: old[oi].container, item: old[oi].item }, to: { container: next[ni].container, item: next[ni].item }, kind: next[ni].kind });
    };
    // A model may use slot IDs for arrays. Unique values carry identity across a swap.
    for (let ni = 0; ni < next.length; ni++) {
      const n = next[ni];
      if (n.kind !== 'array') continue;
      const candidates = old.map((o, i) => ({ o, i })).filter(({ o }) => o.container === n.container && o.kind === 'array' && o.value === n.value);
      const nextCount = next.filter(x => x.container === n.container && x.kind === 'array' && x.value === n.value).length;
      if (candidates.length === 1 && nextCount === 1) claim(candidates[0].i, ni);
    }
    // Stable IDs are the primary identity for trees, graphs, stacks and rods.
    for (let ni = 0; ni < next.length; ni++) {
      const n = next[ni];
      claim(old.findIndex(o => o.container === n.container && o.item === n.item && (n.kind !== 'array' || o.label === n.label)), ni);
    }
    // Rod disks retain the same physical identity across rods. Other structures need an explicit transfer.
    for (let ni = 0; ni < next.length; ni++) {
      if (usedNew.has(ni)) continue;
      const n = next[ni];
      if (n.kind !== 'rod') continue;
      const candidates = old.map((o, i) => ({ o, i })).filter(({ o, i }) => !usedOld.has(i) && o.kind === 'rod' && o.item === n.item && o.label === n.label);
      if (candidates.length === 1) claim(candidates[0].i, ni);
    }
    // A copy has two visible descendants: retain the source and animate a clone to the new item.
    for (const transfer of after?.transfers || []) {
      const ni = next.findIndex(n => n.container === transfer.to?.container && n.item === transfer.to?.item);
      const oi = old.findIndex(o => o.container === transfer.from?.container && o.item === transfer.from?.item);
      if (ni >= 0 && oi >= 0 && !usedNew.has(ni) && old[oi].label === next[ni].label) {
        usedNew.add(ni);
        if (transfer.kind === 'move') usedOld.add(oi);
        matches.push({ from: { container: old[oi].container, item: old[oi].item }, to: { container: next[ni].container, item: next[ni].item }, kind: next[ni].kind, copy: transfer.kind === 'copy' });
      }
    }
    const oldEdges = edges(before, structure), newEdges = edges(after, structure);
    return {
      matches,
      entering: next.filter((_, i) => !usedNew.has(i)).map(x => ({ container: x.container, item: x.item, kind: x.kind })),
      exiting: old.filter((_, i) => !usedOld.has(i)).map(x => ({ container: x.container, item: x.item, kind: x.kind })),
      changedEdges: [...new Set([...oldEdges.keys(), ...newEdges.keys()])].filter(k => oldEdges.has(k) !== newEdges.has(k)).sort(),
      selectedEdges: [...newEdges].filter(([k, selected]) => selected && !oldEdges.get(k)).map(([k]) => k),
      structural: old.length > 0 || next.length > 0,
    };
  }

  function motionMode(before, after, structure, oldHasMarks, newHasMarks) {
    const oldCount = collect(before, structure).length, newCount = collect(after, structure).length;
    return (oldCount || newCount) && (oldCount === 0 || oldHasMarks) && (newCount === 0 || newHasMarks)
      ? 'structural' : 'crossfade';
  }

  function mountFrameSvg(stage, markup, animate) {
    const oldSvg = stage.querySelector('svg');
    if (animate && oldSvg) {
      // Keep the source in the layout tree until animateTransition reads its screen coordinates.
      oldSvg.style.position = 'absolute';
      oldSvg.style.inset = '0';
      oldSvg.style.width = '100%';
      oldSvg.style.height = '100%';
      stage.insertAdjacentHTML('beforeend', markup);
      return { oldSvg, newSvg: stage.lastElementChild };
    }
    stage.innerHTML = markup;
    return { oldSvg: null, newSvg: stage.querySelector('svg') };
  }

  function entryKeyframes(kind) {
    return [{ opacity: 0, transform: `translateY(${kind === 'stack' ? '-22px' : '10px'})` },
            { opacity: 1, transform: 'translateY(0px)' }];
  }

  function exitKeyframes(kind) {
    return [{ opacity: 1, transform: 'translateY(0px)' },
            { opacity: 0, transform: `translateY(${kind === 'stack' ? '-22px' : '-8px'})` }];
  }

  function center(element) {
    const r = element.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function itemElement(svg, ref) {
    const group = [...svg.querySelectorAll('[data-container]')].find(x => x.getAttribute('data-container') === ref.container);
    return [...(group?.querySelectorAll('[data-motion-item]') || [])].find(x => x.getAttribute('data-motion-item') === ref.item);
  }

  function edgeElement(svg, key) {
    const split = key.indexOf(':');
    const cid = key.slice(0, split), local = key.slice(split + 1);
    const group = [...svg.querySelectorAll('[data-container]')].find(x => x.getAttribute('data-container') === cid);
    return [...(group?.querySelectorAll('[data-motion-edge]') || [])].find(x => x.getAttribute('data-motion-edge') === local);
  }

  function animateTransition(stage, oldSvg, newSvg, before, after, structure, duration = 560) {
    const plan = planTransition(before, after, structure);
    if (!oldSvg || !newSvg || typeof newSvg.animate !== 'function' ||
        matchMedia('(prefers-reduced-motion: reduce)').matches || duration <= 0) {
      oldSvg?.remove();
      return { finished: Promise.resolve(), cancel() {} };
    }
    const options = { duration, easing: 'cubic-bezier(.22,.72,.2,1)', fill: 'none' };
    const animations = [];
    const run = (element, keyframes) => {
      if (element?.animate) animations.push(element.animate(keyframes, options));
    };
    if (motionMode(before, after, structure, !!oldSvg.querySelector('[data-motion-item]'), !!newSvg.querySelector('[data-motion-item]')) === 'crossfade') {
      oldSvg.style.position = 'absolute';oldSvg.style.inset = '0';oldSvg.style.width = '100%';
      oldSvg.style.pointerEvents = 'none';oldSvg.setAttribute('aria-hidden', 'true');
      stage.appendChild(oldSvg);
      run(oldSvg, [{ opacity: 1 }, { opacity: 0 }]);
      run(newSvg, [{ opacity: 0 }, { opacity: 1 }]);
      let cleaned = false;
      const cleanup = () => { if (!cleaned) { cleaned = true; oldSvg.remove(); } };
      return { finished: Promise.allSettled(animations.map(x => x.finished)).then(cleanup),
        cancel() { for (const animation of animations) animation.cancel(); cleanup(); } };
    }
    const oldPositions = new Map();
    const oldFills = new Map();
    for (const match of plan.matches) {
      const source = itemElement(oldSvg, match.from);
      if (source) { oldPositions.set(match, center(source)); oldFills.set(match, source.querySelector('[data-item]')?.getAttribute('fill')); }
    }
    // The previous SVG is retained as a transparent overlay containing only exiting items/edges.
    stage.style.position = 'relative';
    oldSvg.style.position = 'absolute';
    oldSvg.style.inset = '0';
    oldSvg.style.width = '100%';
    oldSvg.style.pointerEvents = 'none';
    oldSvg.setAttribute('aria-hidden', 'true');
    stage.appendChild(oldSvg);
    const keepOldItems = new Set(plan.exiting.map(x => `${x.container}:${x.item}`));
    const keepOldEdges = new Set([...plan.changedEdges, ...plan.selectedEdges]);
    for (const child of [...oldSvg.children]) if (!child.hasAttribute('data-container')) child.remove();
    for (const group of oldSvg.querySelectorAll('[data-container]')) {
      const cid = group.getAttribute('data-container');
      for (const child of [...group.children]) {
        const item = child.getAttribute('data-motion-item');
        const edge = child.getAttribute('data-motion-edge');
        if (item && keepOldItems.has(`${cid}:${item}`)) continue;
        if (edge && keepOldEdges.has(`${cid}:${edge}`)) continue;
        child.remove();
      }
    }
    for (const match of plan.matches) {
      const destination = itemElement(newSvg, match.to);
      const start = oldPositions.get(match);
      if (!destination || !start) continue;
      const end = center(destination);
      const dx = start.x - end.x, dy = start.y - end.y;
      if (Math.abs(dx) + Math.abs(dy) > 1) {
        run(destination, [{ transform: `translate(${dx}px, ${dy}px)`, opacity: match.copy ? .75 : 1 },
                          { transform: 'translate(0px, 0px)', opacity: 1 }]);
      }
      const newShape = destination.querySelector('[data-item]');
      if (newShape) {
        const oldFill = oldFills.get(match), newFill = newShape.getAttribute('fill');
        if (oldFill && newFill && oldFill !== newFill) run(newShape, [{ fill: oldFill }, { fill: newFill }]);
      }
    }
    for (const ref of plan.entering) {
      const element = itemElement(newSvg, ref);
      run(element, entryKeyframes(ref.kind));
    }
    for (const ref of plan.exiting) {
      const element = itemElement(oldSvg, ref);
      run(element, exitKeyframes(ref.kind));
    }
    for (const key of new Set([...plan.changedEdges, ...plan.selectedEdges])) {
      run(edgeElement(oldSvg, key), [{ opacity: 1 }, { opacity: 0 }]);
      run(edgeElement(newSvg, key), [{ opacity: 0 }, { opacity: 1 }]);
    }
    // Tree links follow the actual moving node centers throughout a rotation.
    const treeLines = [...newSvg.querySelectorAll('[data-role="tree-edge"][data-motion-edge]')];
    const originalLines = treeLines.map(line => [line, ...['x1', 'y1', 'x2', 'y2'].map(name => line.getAttribute(name))]);
    let edgeTick = null;
    const updateTreeLines = () => {
      for (const line of treeLines) {
        const group = line.closest('[data-container]'), key = line.getAttribute('data-motion-edge');
        const split = key.indexOf('>');
        const source = itemElement(newSvg, { container: group.getAttribute('data-container'), item: key.slice(0, split) })?.querySelector('[data-item]');
        const target = itemElement(newSvg, { container: group.getAttribute('data-container'), item: key.slice(split + 1) })?.querySelector('[data-item]');
        const matrix = group.getScreenCTM();
        if (!source || !target || !matrix) continue;
        const inverse = matrix.inverse(), point = newSvg.createSVGPoint();
        const a = center(source), b = center(target);
        point.x = a.x;point.y = a.y;const p1 = point.matrixTransform(inverse);
        point.x = b.x;point.y = b.y;const p2 = point.matrixTransform(inverse);
        line.setAttribute('x1', p1.x.toFixed(1));line.setAttribute('y1', p1.y.toFixed(1));
        line.setAttribute('x2', p2.x.toFixed(1));line.setAttribute('y2', p2.y.toFixed(1));
      }
      edgeTick = requestAnimationFrame(updateTreeLines);
    };
    if (treeLines.length && animations.length) edgeTick = requestAnimationFrame(updateTreeLines);
    let cleaned = false;
    const cleanup = () => { if (!cleaned) {
      cleaned = true;if (edgeTick !== null) cancelAnimationFrame(edgeTick);
      for (const [line, x1, y1, x2, y2] of originalLines) {
        line.setAttribute('x1', x1);line.setAttribute('y1', y1);line.setAttribute('x2', x2);line.setAttribute('y2', y2);
      }
      oldSvg.remove();
    } };
    const finished = Promise.allSettled(animations.map(x => x.finished)).then(cleanup);
    return { finished, cancel() { for (const animation of animations) animation.cancel(); cleanup(); } };
  }

export { planTransition, motionMode, canvasAspect, mountFrameSvg, entryKeyframes, exitKeyframes, animateTransition };
