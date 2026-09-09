import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import './styles/main.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 初始化认证状态（从 localStorage 恢复）后再挂路由守卫
useAuthStore().init()

app.use(router)
app.use(ElementPlus)

// 注册所有 Element Plus 图标
for (const [key, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, comp)
}

app.mount('#app')
