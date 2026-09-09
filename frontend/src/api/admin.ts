// 管理接口：用户管理 + 审计日志
import request from './request'

export interface AdminUser {
  id: number
  username: string
  display_name: string | null
  role: 'admin' | 'user'
  created_at: string
}

export interface AuditLog {
  id: number
  user_id: number | null
  username: string | null
  action: string
  target_type: string | null
  target_id: string | null
  detail: string | null
  ip: string | null
  created_at: string
}

export function listUsers() {
  return request.get<AdminUser[]>('/auth/admin/users')
}

export function createUser(username: string, password: string, display_name?: string, role?: string) {
  return request.post<AdminUser>('/auth/admin/users', {
    username,
    password,
    display_name,
    role,
  })
}

export function resetPassword(userId: number, password: string) {
  return request.patch(`/auth/admin/users/${userId}/password`, { new_password: password })
}

export function deleteUser(userId: number) {
  return request.delete(`/auth/admin/users/${userId}`)
}

export function listAuditLogs(limit: number = 200) {
  return request.get<AuditLog[]>('/auth/admin/audit-logs', { params: { limit } })
}
