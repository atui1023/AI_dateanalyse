// axios 封装：带 cookie（session 鉴权）+ 401 统一跳登录
import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '',
  withCredentials: true, // 后端用 session cookie 鉴权，必须带
  timeout: 30000,
})

// 401 跳转标志：避免拦截器和路由守卫同时跳转导致请求被中断
let redirecting = false

request.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail || err.response?.data?.message || err.message
    if (status === 401) {
      // 未登录或 session 失效：清状态，由路由守卫处理跳转
      // 不在拦截器里用 location.href 跳转，避免中断正在进行的请求
      localStorage.removeItem('user')
      if (!redirecting && location.pathname !== '/login') {
        redirecting = true
        ElMessage.error('登录已失效，请重新登录')
        // 用 setTimeout 让当前请求链完成 reject，再跳转
        setTimeout(() => {
          location.href = '/login'
          redirecting = false
        }, 100)
      }
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
