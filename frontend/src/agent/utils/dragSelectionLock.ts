export const DRAG_SELECTION_LOCK_CLASS = "agent-drag-selection-lock";

interface DragSelectionLockClassList {
  add(value: string): void;
  remove(value: string): void;
}

export interface DragSelectionLockDocument {
  body?: { classList: DragSelectionLockClassList } | null;
  documentElement?: { classList: DragSelectionLockClassList } | null;
  addEventListener(type: "selectstart", listener: EventListener, options?: AddEventListenerOptions): void;
  removeEventListener(type: "selectstart", listener: EventListener, options?: EventListenerOptions): void;
}

interface DragSelectionLockState {
  count: number;
  preventSelection: EventListener;
}

const locks = new WeakMap<DragSelectionLockDocument, DragSelectionLockState>();

export function acquireDragSelectionLock(targetDocument: DragSelectionLockDocument = document) {
  let released = false;
  let state = locks.get(targetDocument);

  if (!state) {
    state = {
      count: 0,
      preventSelection(event) {
        event.preventDefault();
      },
    };
    targetDocument.body?.classList.add(DRAG_SELECTION_LOCK_CLASS);
    targetDocument.documentElement?.classList.add(DRAG_SELECTION_LOCK_CLASS);
    targetDocument.addEventListener("selectstart", state.preventSelection);
    locks.set(targetDocument, state);
  }

  state.count += 1;

  return () => {
    if (released) return;
    released = true;

    const current = locks.get(targetDocument);
    if (!current) return;

    current.count -= 1;
    if (current.count > 0) return;

    targetDocument.body?.classList.remove(DRAG_SELECTION_LOCK_CLASS);
    targetDocument.documentElement?.classList.remove(DRAG_SELECTION_LOCK_CLASS);
    targetDocument.removeEventListener("selectstart", current.preventSelection);
    locks.delete(targetDocument);
  };
}
