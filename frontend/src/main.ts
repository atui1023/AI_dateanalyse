import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus, { ElMessage } from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { useAuthStore, resetUserScopedState } from './stores/auth'
import { setUnauthorizedHandler } from './api/request'
import './styles/main.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 初始化认证状态（从 localStorage 恢复）后再挂路由守卫
useAuthStore().init()

app.use(router)
app.use(ElementPlus)

// 注册全局 401 处理器：清登录态 + SPA 跳登录页（用真实 router/store 实例）
// 必须在 app.use(pinia) 之后，useAuthStore 才能取到实例
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.user = null
  localStorage.removeItem('user')
  // 会话失效：同步清空与用户绑定的知识库/会话缓存，避免重新登录前残留旧数据
  resetUserScopedState()
  ElMessage.error('登录已失效，请重新登录')
  router.push('/login')
})

// 注册所有 Element Plus 图标
for (const [key, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, comp)
}

app.mount('#app')
