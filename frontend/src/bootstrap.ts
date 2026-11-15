import { createApp, watch } from 'vue'
import type { App as VueApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useAppearanceStore } from './theme/store'
import { useAgentPreferencesStore } from './agent/stores/agentPreferences'
import { bindAgentAccount } from './agent/utils/agentAccountScope'
import { useAgentWorkspaceStore } from './agent/stores/agentWorkspace'
import { useAgentRunsStore } from './agent/stores/agentRuns'
import { cancelAgentWorkspaceBootstrap } from './agent/composables/useAgentWorkspaceBootstrap'
import { clearAgentAccountCache } from './agent/utils/agentDataCache'
import { clearAgentDrafts } from './agent/utils/agentDraftCache'
import { agentMarkdownRenderCache } from './agent/utils/agentMarkdownRenderCache'
import { closeAgentPrivateMediaPreviews } from './agent/utils/agentPrivateMediaPreview'
import { useAccodingStore } from './stores'
import { bootstrapDevLoginState } from './dev/bootstrapToken'
import { DEMO_AUTH_EXPIRED_EVENT, isEnglishDemo } from './en-demo/mode'
import { installEnglishDemoDomTranslation } from './en-demo/englishDom'
import axios from 'axios'
import type { Router } from 'vue-router'
import { setActiveRouter } from './router/activeRouter'

import '@/style/index'
import 'overlayscrollbars/overlayscrollbars.css'
import './agent/styles/showcaseAgent.css'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'

import mavonEditor from 'mavon-editor'
import 'mavon-editor/dist/css/index.css'
import './style/appearance.css'

export function bootstrapApp(router: Router, installUi: (app: VueApp) => void, installIcons: (app: VueApp) => void): void {
const app = createApp(App)

app.use(createPinia())
setActiveRouter(router)
app.use(router)
installUi(app)
app.use(mavonEditor)
installIcons(app)

const accoding = useAccodingStore()
if (isEnglishDemo) axios.defaults.baseURL = '/en-demo'
else bootstrapDevLoginState()
if (!(import.meta.env.DEV && window.location.pathname === '/__agent_inline_diagram_demo')) accoding._init()
const appearance = useAppearanceStore()
const agentPreferences = useAgentPreferencesStore()
const agentWorkspace = useAgentWorkspaceStore()
const agentRuns = useAgentRunsStore()
if (isEnglishDemo) window.addEventListener(DEMO_AUTH_EXPIRED_EVENT, (event) => {
  const token = (event as CustomEvent<{ token?: string }>).detail?.token
  if (!token || accoding.token !== token) return
  void accoding._restoreDemoGuest(true).then((restored) => {
    if (!restored) void router.replace('/login')
  })
})
let boundAgentToken = ""
// Invalidate Agent requests synchronously; appearance can update after the login transaction.
watch(() => accoding.isLogin ? accoding.token : '', (nextAgentToken) => {
    if (boundAgentToken !== nextAgentToken) {
      const previousToken = boundAgentToken
      boundAgentToken = nextAgentToken
      bindAgentAccount(nextAgentToken)
      agentRuns.reset()
      cancelAgentWorkspaceBootstrap()
      closeAgentPrivateMediaPreviews()
      agentMarkdownRenderCache.clear()
      agentWorkspace.clearDraftImages()
      agentWorkspace.$reset()
      if (previousToken) {
        void clearAgentAccountCache(previousToken)
        void clearAgentDrafts(previousToken)
      }
    }
}, {immediate: true, flush: 'sync'})

// Storage events synchronize login/logout across same-origin tabs without writing back.
window.addEventListener('storage', (event) => {
  if (isEnglishDemo) return
  if (event.key !== null && !['token', 'isLogin', 'user'].includes(event.key)) return
  try {
    const token = localStorage.getItem('token') || ''
    const loggedIn = localStorage.getItem('isLogin') === 'true'
    const user = JSON.parse(localStorage.getItem('user') || 'null')
    if (loggedIn && token && user && user.id != null && String(user.id)) {
      accoding.$patch({isLogin: true, token, user})
    } else {
      accoding.$patch({isLogin: false, token: '', user: {}, lastLoginTime: null})
    }
  } catch {
    accoding.$patch({isLogin: false, token: '', user: {}, lastLoginTime: null})
  }
})

watch(
  () => [accoding.isLogin, accoding.user?.id, accoding.token] as const,
  ([loggedIn, userId, token]) => {
    const identity = loggedIn && token && userId != null && String(userId) ? String(userId) : null
    appearance.bindAccount(identity)
    agentPreferences.resetForAccount()
    if (identity) void agentPreferences.load(token)
  },
  { immediate: true, flush: 'post' },
)
app.mount('#app')
if (isEnglishDemo) installEnglishDemoDomTranslation()
}
