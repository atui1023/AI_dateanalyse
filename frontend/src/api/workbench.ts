import request from './request'

export interface Relation {
  id: string
  name: string
  dataset_ids: string[]
  joins: any[]
  updated_at?: string
}
export interface AnalysisResultItem {
  id: number
  question: string
  status: string
  stdout?: string | null
  table?: any
  chart?: Record<string, any> | null
  datasets?: any[]
  execution_ms?: number | null
  created_at?: string
}
export interface Dashboard { id: string; name: string; description?: string | null; item_count?: number; updated_at?: string }
export interface Schedule { id: string; name: string; job_type: string; schedule_text: string; dataset_ids: string[]; question?: string; recipients: string[]; enabled: boolean; next_run_at?: string | null; last_run_at?: string | null }

export const listRelations = () => request.get<Relation[]>('/analysis/relations')
export const createRelation = (payload: any) => request.post<Relation>('/analysis/relations', payload)
export const deleteRelation = (id: string) => request.delete('/analysis/relations/' + id)
export const previewRelation = (id: string) => request.post('/analysis/relations/' + id + '/preview')
export const executeRelation = (id: string, instruction: string) => request.post('/analysis/relations/' + id + '/execute', { instruction })
export const listResults = () => request.get<AnalysisResultItem[]>('/analysis/results')
export const listDashboards = () => request.get<Dashboard[]>('/dashboards')
export const createDashboard = (payload: any) => request.post<Dashboard>('/dashboards', payload)
export const getDashboard = (id: string) => request.get<any>('/dashboards/' + id)
export const runDashboardInstruction = (id: string, payload: any) => request.post('/dashboards/' + id + '/analyze', payload)
export const addDashboardItem = (id: string, payload: any) => request.post('/dashboards/' + id + '/items', payload)
export const deleteDashboardItem = (dashboardId: string, itemId: string) => request.delete('/dashboards/' + dashboardId + '/items/' + itemId)
export const createShare = (payload: any) => request.post<{ token: string; path: string }>('/shares', payload)
export const getShared = (token: string) => request.get<any>('/shared/' + token)
export const addComment = (token: string, payload: any) => request.post('/shared/' + token + '/comments', payload)
export const listSchedules = () => request.get<Schedule[]>('/schedules')
export const createSchedule = (payload: any) => request.post('/schedules', payload)
export const runSchedule = (id: string) => request.post('/schedules/' + id + '/run')
export const toggleSchedule = (id: string, enabled: boolean) => request.patch('/schedules/' + id, { enabled })
export const deleteSchedule = (id: string) => request.delete('/schedules/' + id)
