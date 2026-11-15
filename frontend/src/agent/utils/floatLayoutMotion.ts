import type { InjectionKey } from "vue";

export interface FloatLayoutSnapshot {
  key: string;
  left: number;
  top: number;
}

export interface FloatLayoutElementSnapshot extends FloatLayoutSnapshot {
  element: HTMLElement;
}

export interface FloatLayoutShift {
  key: string;
  dx: number;
  dy: number;
}

export type FinishFloatLayoutMotion = () => void;
export type CaptureFloatLayoutMotion = () => FinishFloatLayoutMotion;
export const FLOAT_LAYOUT_MOTION_KEY: InjectionKey<CaptureFloatLayoutMotion> = Symbol("float-layout-motion");

const DEFAULT_SELECTOR = ":scope > .float-section";

export function getFloatLayoutShifts(
  before: readonly FloatLayoutSnapshot[],
  after: readonly FloatLayoutSnapshot[],
  movementThreshold = 0.5,
): FloatLayoutShift[] {
  const beforeByKey = new Map(before.map((item) => [item.key, item]));

  return after.reduce<FloatLayoutShift[]>((shifts, nextItem) => {
    const previousItem = beforeByKey.get(nextItem.key);
    if (!previousItem) return shifts;

    const dx = previousItem.left - nextItem.left;
    const dy = previousItem.top - nextItem.top;
    if (Math.abs(dx) <= movementThreshold && Math.abs(dy) <= movementThreshold) return shifts;

    shifts.push({ key: nextItem.key, dx, dy });
    return shifts;
  }, []);
}

export function readFloatLayoutSnapshot(container: HTMLElement, selector = DEFAULT_SELECTOR): FloatLayoutElementSnapshot[] {
  return Array.from(container.querySelectorAll<HTMLElement>(selector)).map((element, index) => ({
    key: element.dataset.floatLayoutKey || `float-section-${index}`,
    left: element.offsetLeft,
    top: element.offsetTop,
    element,
  }));
}

export interface PreviewHeightSource {
  firstElementChild?: {
    getBoundingClientRect: () => { height: number };
  } | null;
  scrollHeight: number;
}

export function readPreviewMotionTargetHeight(element: PreviewHeightSource) {
  const contentHeight = element.firstElementChild?.getBoundingClientRect().height ?? 0;
  return contentHeight > 0 ? contentHeight : element.scrollHeight;
}
