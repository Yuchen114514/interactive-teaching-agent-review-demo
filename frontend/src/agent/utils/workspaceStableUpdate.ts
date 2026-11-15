export function stableStringify(value: unknown): string {
  return JSON.stringify(value ?? null);
}

export function sameStableValue(left: unknown, right: unknown) {
  return stableStringify(left) === stableStringify(right);
}
