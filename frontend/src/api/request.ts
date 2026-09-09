// axios 封装：带 cookie（session 鉴权）+ 401 统一跳登录
import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '',
  withCredentials: true, // 后端用 session cookie 鉴权，必须带
  timeout: 30000,
})

request.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail || err.response?.data?.message || err.message
    if (status === 401) {
      // 未登录或 session 失效：清状态、跳登录（避免在拦截器里直接跳路由循环）
      localStorage.removeItem('user')
      if (location.pathname !== '/login') {
        ElMessage.error('登录已失效，请重新登录')
        location.href = '/login'
      }
    } else if (status === 403) {
      ElMessage.error('无权访问')
    } else if (status >= 400 && status < 500) {
      ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    } else if (status >= 500) {
      ElMessage.error('服务器错误，请稍后重试')
    }
    return Promise.reject(err)
  },
)

export default request
