// 认证接口
import request from './request'

export interface UserInfo {
  id: number
  username: string
  display_name: string | null
  role: 'admin' | 'user'
}

export function login(username: string, password: string) {
  return request.post<UserInfo>('/auth/login', { username, password })
}

export function register(username: string, password: string, display_name?: string) {
  return request.post<UserInfo>('/auth/register', { username, password, display_name })
}

export function logout() {
  return request.post('/auth/logout')
}

export function me() {
  return request.get<UserInfo>('/auth/me')
}
