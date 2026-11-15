export interface DiagramMotionHandle {
  finished: Promise<unknown>;
  cancel(): void;
}

export function planTransition(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
  structure?: string,
): {
  matches: Array<{ from: { container: string; item: string }; to: { container: string; item: string } }>;
  entering: Array<{ container: string; item: string }>;
  exiting: Array<{ container: string; item: string }>;
  changedEdges: string[];
  selectedEdges: string[];
};

export function mountFrameSvg(
  stage: HTMLElement,
  markup: string,
  animate: boolean,
): { oldSvg: SVGSVGElement | null; newSvg: SVGSVGElement | null };

export function animateTransition(
  stage: HTMLElement,
  oldSvg: SVGSVGElement | null,
  newSvg: SVGSVGElement | null,
  before: Record<string, unknown>,
  after: Record<string, unknown>,
  structure: string,
  duration?: number,
): DiagramMotionHandle;
