import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { isDemoTokenUsable } from '../mode'

const modeSource = readFileSync(new URL('../mode.ts', import.meta.url), 'utf8')
const storeSource = readFileSync(new URL('../../stores/index.ts', import.meta.url), 'utf8')
const routerSource = readFileSync(new URL('../router.ts', import.meta.url), 'utf8')
const apiSource = readFileSync(new URL('../../agent/api/agentApi.ts', import.meta.url), 'utf8')
const bootstrapSource = readFileSync(new URL('../../bootstrap.ts', import.meta.url), 'utf8')
const loginSource = readFileSync(new URL('../../views/User/UserLogin.vue', import.meta.url), 'utf8')
const cssSource = readFileSync(new URL('../../agent/styles/showcaseAgent.css', import.meta.url), 'utf8')

const tokenWithExpiry = (exp: number) => `eyJhbGciOiJub25lIn0.${Buffer.from(JSON.stringify({ exp })).toString('base64url')}.signature`

test('demo token expiration is evaluated from its JWT claim', () => {
  const now = Date.UTC(2026, 10, 13, 12)
  assert.equal(isDemoTokenUsable(tokenWithExpiry(now / 1000 + 3600), now), true)
  assert.equal(isDemoTokenUsable(tokenWithExpiry(now / 1000 - 1), now), false)
  assert.equal(isDemoTokenUsable(tokenWithExpiry(now / 1000 + 20), now), false)
  assert.equal(isDemoTokenUsable('malformed', now), false)
})

test('restored demo identities reject expired or malformed JWTs before Agent requests', () => {
  assert.match(modeSource, /export function isDemoTokenUsable\(/)
  assert.match(storeSource, /demoToken && demoUser && isDemoTokenUsable\(demoToken\)/)
  assert.match(routerSource, /await store\._restoreDemoGuest\(\)/)
})

test('a 401 from the English Agent renews only the matching demo identity', () => {
  assert.match(apiSource, /response\.status === 401[\s\S]*notifyDemoAuthExpired\(token\)/)
  assert.match(bootstrapSource, /DEMO_AUTH_EXPIRED_EVENT[\s\S]*accoding\.token !== token[\s\S]*accoding\._restoreDemoGuest\(true\)/)
  assert.match(loginSource, /store\.demoSessionExpired/)
})

test('English session switch has room for both labels while Chinese geometry stays compact', () => {
  assert.match(cssSource, /\.session-view-switch\s*\{[^}]*width:\s*76px;/)
  assert.match(cssSource, /html\[lang="en"\] \.session-view-switch\s*\{[^}]*width:\s*144px;/)
  assert.match(cssSource, /html\[lang="en"\] \.session-view-switch-option\s*\{[^}]*white-space:\s*nowrap;/)
})

test('a demo visit restores the same server account after a new tab or expired access token', () => {
  assert.match(modeSource, /DEMO_RECOVERY_MARKER_KEY/)
  assert.match(storeSource, /async _restoreDemoGuest\(/)
  assert.match(storeSource, /\/en-demo\/api\/en-demo\/guest\/restore/)
  assert.match(storeSource, /previous_token:\s*previousToken/)
  assert.match(storeSource, /localStorage\.setItem\(DEMO_RECOVERY_MARKER_KEY/)
  assert.match(routerSource, /await store\._restoreDemoGuest\(\)/)
  assert.match(bootstrapSource, /accoding\._restoreDemoGuest\(true\)/)
})
