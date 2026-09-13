// 团队空间与商业化基础接口，页面可按需组合为团队/套餐模块。
import request from './request'

export interface Workspace {
  id: string
  name: string
  owner_id: number
  role: 'owner' | 'editor' | 'viewer' | null
  created_at: string
}

export interface WorkspaceMember {
  id: number
  user_id: number
  username: string
  display_name: string | null
  role: 'owner' | 'editor' | 'viewer'
  created_at: string
}

export interface UsageSummary {
  plan: string
  status: string
  used_units: number
  monthly_limit: number
  storage_mb: number
  events: { count: number; execution_ms: number; tokens: number; cost_usd: number }
}

export const listWorkspaces = () => request.get<Workspace[]>('/workspaces')
export const createWorkspace = (name: string) => request.post<Workspace>('/workspaces', { name })
export const listMembers = (workspaceId: string) => request.get<WorkspaceMember[]>(`/workspaces/${workspaceId}/members`)
export const inviteMember = (workspaceId: string, username: string, role: 'editor' | 'viewer') =>
  request.post(`/workspaces/${workspaceId}/members`, { username, role })
export const removeMember = (workspaceId: string, memberId: number) =>
  request.delete(`/workspaces/${workspaceId}/members/${memberId}`)
export const listPlans = () => request.get('/billing/plans')
export const getUsage = () => request.get<UsageSummary>('/billing/usage')
export const subscribe = (plan: string) => request.post('/billing/subscribe', { plan })
