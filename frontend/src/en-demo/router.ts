import { createRouter, createWebHistory } from 'vue-router'
import { useAccodingStore } from '@/stores'
import { findDemoProblem } from './catalog'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/home', component: () => import('@/views/ACCodingHomePage.vue') },
    { path: '/login', component: () => import('@/views/User/UserLogin.vue') },
    { path: '/allProblems', component: () => import('@/views/Problem/ProblemList.vue') },
    {
      path: '/problem/:problemId',
      component: () => import('@/views/Problem/ProblemDetail.vue'),
      beforeEnter: (to) => findDemoProblem(Number(to.params.problemId)) ? true : '/allProblems',
    },
    { path: '/unavailable', component: () => import('./DemoUnavailable.vue') },
    {
      path: '/:catchAll(.*)',
      redirect: (to) => ({ path: '/unavailable', query: { section: to.fullPath } }),
    },
  ],
})

router.beforeEach(async (to) => {
  const store = useAccodingStore()
  await store._restoreDemoGuest()
  if (store.token && to.path === '/login') return '/allProblems'
  if (!store.token && !['/home', '/login', '/unavailable'].includes(to.path)) return '/login'
  return true
})

export default router
