// 认证 store：用户信息 + 登录/登出
import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as authApi from '@/api/auth'
import type { UserInfo } from '@/api/auth'

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
