let accountToken = '';
let generation = 0;

export function bindAgentAccount(token: string) {
  if (accountToken !== token) {
    accountToken = token;
    generation += 1;
  }
  return generation;
}

export function currentAgentAccountToken() {
  return accountToken;
}

export function captureAgentAccount(token: string) {
  const capturedGeneration = generation;
  return {
    isCurrent: () => Boolean(token) && token === accountToken && generation === capturedGeneration,
  };
}
