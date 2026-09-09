// 认证 store：用户信息 + 登录/登出
import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as authApi from '@/api/auth'
import type { UserInfo } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const loading = ref(false)

  async function fetchMe() {
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
