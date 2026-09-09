// 认证 store：用户信息 + 登录/登出
import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as authApi from '@/api/auth'
import type { UserInfo } from '@/api/auth'
import { useKbStore } from './kb'
import { useSessionsStore } from './sessions'

// 账号态切换时，重置所有与用户绑定的 store 内存态和本地缓存
// （kb 的挂载列表持久化在全局 localStorage key，不清理会串到新用户）
export function resetUserScopedState() {
  useKbStore().reset()
  useSessionsStore().reset()
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const loading = ref(false)

  async function fetchMe() {
    // 静默检查：401 时不弹 toast、不跳转（路由守卫会处理）
    try {
      const { data } = await authApi.me()
      user.value = data
      localStorage.setItem('user', JSON.stringify(data))
      return data
    } catch {
      user.value = null
      localStorage.removeItem('user')
      return null
    }
  }

  async function login(username: string, password: string) {
    loading.value = true
    try {
      const { data } = await authApi.login(username, password)
      user.value = data
      localStorage.setItem('user', JSON.stringify(data))
      // 清掉上一个用户残留的会话/知识库状态，新用户数据由页面重新拉取
      resetUserScopedState()
      return data
    } finally {
      loading.value = false
    }
  }

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      // session 已失效时后端返回 401，忽略错误，前端照样清状态
    } finally {
      user.value = null
      localStorage.removeItem('user')
      resetUserScopedState()
    }
  }

  // 初始化时尝试从 localStorage 恢复，再去服务端校验
  function init() {
    const cached = localStorage.getItem('user')
    if (cached) {
      try {
        user.value = JSON.parse(cached)
      } catch {
        localStorage.removeItem('user')
      }
    }
  }

  return { user, loading, fetchMe, login, logout, init }
})
