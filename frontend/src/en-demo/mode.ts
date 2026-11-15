export const isEnglishDemo = import.meta.env?.VITE_EN_DEMO === 'true'

export const DEMO_TOKEN_KEY = 'accoding-en-demo-token'
export const DEMO_USER_KEY = 'accoding-en-demo-user'
export const DEMO_RECOVERY_MARKER_KEY = 'accoding-en-demo-returning-visitor'
export const DEMO_PAUSED_KEY = 'accoding-en-demo-paused'
export const DEMO_AUTH_EXPIRED_EVENT = 'accoding-en-demo-auth-expired'

export function isDemoTokenUsable(token: string, now = Date.now()): boolean {
  try {
    const encoded = token.split('.')[1]
    if (!encoded) return false
    const base64 = encoded.replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '='))) as { exp?: unknown }
    return typeof payload.exp === 'number' && Number.isFinite(payload.exp) && payload.exp * 1000 > now + 30_000
  } catch {
    return false
  }
}

export function notifyDemoAuthExpired(token: string): void {
  if (!isEnglishDemo || typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(DEMO_AUTH_EXPIRED_EVENT, { detail: { token } }))
}

export function demoProblemIdFromRoute(value: unknown): number | null {
  const number = Number(value)
  return Number.isInteger(number) && number > 0 ? number : null
}
