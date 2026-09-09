// axios 封装：带 cookie（session 鉴权）+ 401 统一跳登录
import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '',
  withCredentials: true, // 后端用 session cookie 鉴权，必须带
  timeout: 30000,
})

// 401 处理：用动态 import 避免循环依赖，走 SPA router.push（不刷新页面）
let redirecting = false
async function redirectToLogin() {
  if (redirecting || location.pathname === '/login') return
  redirecting = true
  const { default: router } = await import('@/router')
  ElMessage.error('登录已失效，请重新登录')
  router.push('/login')
  setTimeout(() => { redirecting = false }, 500)
}

request.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail || err.response?.data?.message || err.message
    if (status === 401) {
      localStorage.removeItem('user')
      redirectToLogin()
    } else if (status === 403) {
      ElMessage.error('无权访问')
    } else if (status === 422) {
      ElMessage.error(typeof detail === 'string' ? detail : '请求参数错误')
    } else if (status >= 400 && status < 500) {
      ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    } else if (status >= 500) {
      ElMessage.error('服务器错误，请稍后重试')
    }
    return Promise.reject(err)
  },
)

export default request
