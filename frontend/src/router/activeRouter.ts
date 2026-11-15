import type { Router } from 'vue-router'

let activeRouter: Router | null = null

export function setActiveRouter(router: Router): void {
  activeRouter = router
}

export function getActiveRouter(): Router {
  if (!activeRouter) throw new Error('Router has not been installed')
  return activeRouter
}
