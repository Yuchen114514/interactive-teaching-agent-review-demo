import { onBeforeUnmount, onMounted, onUpdated, ref, watch, type Ref } from 'vue';

export function measureSelectionShadow(rail: HTMLElement, viewport: HTMLElement, row: HTMLElement) {
  const railBox = rail.getBoundingClientRect();
  const viewportBox = viewport.getBoundingClientRect();
  const rowBox = row.getBoundingClientRect();
  // Measurements must stay in rail coordinates while the workspace is scaled.
  const scaleX = railBox.width / rail.offsetWidth || 1;
  const scaleY = railBox.height / rail.offsetHeight || 1;
  const localX = (x: number) => (x - railBox.left) / scaleX - rail.clientLeft;
  const localY = (y: number) => (y - railBox.top) / scaleY - rail.clientTop;
  const round = (value: number) => Math.round(value * 1000) / 1000;
  return {
    left: round(localX(rowBox.left)),
    top: round(localY(rowBox.top)),
    width: round(rowBox.width / scaleX),
    height: round(rowBox.height / scaleY),
    clipTop: round(Math.max(0, localY(viewportBox.top))),
    clipBottom: round(Math.max(0, rail.clientHeight - localY(viewportBox.bottom))),
  };
}

/** Paint only the selection shadow outside the scroller's clipping context. */
export function useSessionSelectionShadow(
  rail: Ref<HTMLElement | null>,
  viewport: Ref<HTMLElement | null>,
  content: Ref<HTMLElement | null>,
  selectedShell: () => HTMLElement | undefined,
) {
  const shadowLayer = ref<HTMLElement | null>(null);
  const shadowSurface = ref<HTMLElement | null>(null);
  let frame: number | null = null;
  let observer: ResizeObserver | null = null;

  function syncShadow() {
    frame = null;
    const layer = shadowLayer.value;
    const surface = shadowSurface.value;
    const shell = selectedShell();
    const row = shell?.querySelector<HTMLElement>('.session-row');
    if (!layer || !surface) return;
    if (!rail.value || !viewport.value || !row || shell?.classList.contains('is-exiting')) {
      layer.hidden = true;
      return;
    }
    const geometry = measureSelectionShadow(rail.value, viewport.value, row);
    layer.style.clipPath = `inset(${geometry.clipTop}px -40px ${geometry.clipBottom}px -40px)`;
    surface.style.width = `${geometry.width}px`;
    surface.style.height = `${geometry.height}px`;
    surface.style.transform = `translate3d(${geometry.left}px, ${geometry.top}px, 0)`;
    layer.hidden = false;
  }

  function scheduleShadow() {
    if (frame === null) frame = window.requestAnimationFrame(syncShadow);
  }

  // No reactive writes or continuous loop while dragging the workspace.
  onUpdated(scheduleShadow);
  // Slot content can update without updating the rail component itself.
  watch(selectedShell, scheduleShadow, { flush: 'post' });
  onMounted(() => {
    observer = new ResizeObserver(scheduleShadow);
    if (rail.value) observer.observe(rail.value);
    if (content.value) observer.observe(content.value);
    if (viewport.value) observer.observe(viewport.value);
    scheduleShadow();
  });
  watch(viewport, (next, previous) => {
    if (previous) observer?.unobserve(previous);
    if (next) observer?.observe(next);
    scheduleShadow();
  }, { flush: 'post' });
  onBeforeUnmount(() => {
    observer?.disconnect();
    if (frame !== null) window.cancelAnimationFrame(frame);
  });

  return { shadowLayer, shadowSurface, scheduleShadow };
}
