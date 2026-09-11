// 路由 + 登录守卫
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/shared/:token',
      name: 'shared',
      component: () => import('@/views/SharedView.vue'),
      meta: { public: true },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      children: [
        {
          path: '',
          name: 'chat',
          component: () => import('@/views/ChatView.vue'),
        },
        {
          path: 'workbench',
          name: 'workbench',
          component: () => import('@/views/WorkbenchView.vue'),
        },
        {
          path: 'admin',
          name: 'admin',
          component: () => import('@/views/AdminView.vue'),
          meta: { requireAdmin: true },
        },
      ],
    },
  ],
})

// 全局守卫：未登录跳 /login；admin 页仅管理员可进
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.user) {
    // 首次进入：尝试从服务端取当前用户（cookie session）
    await auth.fetchMe()
  }
  if (!to.meta.public && !auth.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requireAdmin && auth.user?.role !== 'admin') {
    return { name: 'chat' }
  }
  if (to.name === 'login' && auth.user) {
    return { name: 'chat' }
  }
})

export default router
