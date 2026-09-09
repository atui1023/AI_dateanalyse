// axios 封装：带 cookie（session 鉴权）+ 401 统一跳登录
import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '',
  withCredentials: true, // 后端用 session cookie 鉴权，必须带
  timeout: 30000,
})

// 401 处理器：由 main.ts 启动时注入（那里能拿到真实的 router 实例和 pinia store）
// 避免在拦截器里动态 import 导致实例取不到 / 时序问题
let unauthorizedHandler: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  unauthorizedHandler = fn
}

let redirecting = false
// 单一入口：axios 拦截器和 fetch（chat）都调它，避免跳转逻辑分散
export function notifyUnauthorized() {
  if (redirecting) return
  redirecting = true
  unauthorizedHandler?.()
  setTimeout(() => { redirecting = false }, 800)
}

request.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail || err.response?.data?.message || err.message
    if (status === 401) {
      notifyUnauthorized()
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
