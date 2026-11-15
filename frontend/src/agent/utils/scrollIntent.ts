export interface ScrollIntentState {
  scrollToBottomRequest: number;
}

export function requestScrollIntent(state: ScrollIntentState) {
  state.scrollToBottomRequest += 1;
}

export function consumeScrollIntent(state: ScrollIntentState, handledRequest: number) {
  if (state.scrollToBottomRequest <= 0 || state.scrollToBottomRequest === handledRequest) return null;
  return state.scrollToBottomRequest;
}
