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
  title?: string
  code?: string | null
  is_favorite?: boolean
  status: string
  stdout?: string | null
  table?: any
  chart?: Record<string, any> | null
  datasets?: any[]
  execution_ms?: number | null
  error?: string | null
  created_at?: string
}
export interface Dashboard { id: string; name: string; description?: string | null; item_count?: number; updated_at?: string }
export interface Schedule { id: string; name: string; job_type: string; schedule_text: string; dataset_ids: string[]; question?: string; parameters?: Record<string, any>; recipients: string[]; enabled: boolean; next_run_at?: string | null; last_run_at?: string | null }
export interface WorkflowStep { id?: string; step_type: string; step_order?: number; name: string; config: Record<string, any>; enabled?: boolean }
export interface Workflow { id: string; name: string; description?: string | null; enabled: boolean; step_count?: number; steps?: WorkflowStep[]; updated_at?: string }
export interface DataSourceType { type: string; label: string; status: string; driver: string }

export const listRelations = () => request.get<Relation[]>('/analysis/relations')
export const createRelation = (payload: any) => request.post<Relation>('/analysis/relations', payload)
export const deleteRelation = (id: string) => request.delete('/analysis/relations/' + id)
export const previewRelation = (id: string) => request.post('/analysis/relations/' + id + '/preview')
export const executeRelation = (id: string, instruction: string) => request.post('/analysis/relations/' + id + '/execute', { instruction })
export const listResults = () => request.get<AnalysisResultItem[]>('/analysis/results')
export const deleteResult = (id: number) => request.delete('/analysis/results/' + id)
export const retryResult = (id: number) => request.post<any>('/analysis/results/' + id + '/retry')
export const updateResult = (id: number, payload: { title?: string; is_favorite?: boolean }) => request.patch<AnalysisResultItem>('/analysis/results/' + id, payload)
export const copyResult = (id: number) => request.post<AnalysisResultItem>('/analysis/results/' + id + '/copy')
export const listDashboards = () => request.get<Dashboard[]>('/dashboards')
export const createDashboard = (payload: any) => request.post<Dashboard>('/dashboards', payload)
export const getDashboard = (id: string) => request.get<any>('/dashboards/' + id)
export const runDashboardInstruction = (id: string, payload: any) => request.post('/dashboards/' + id + '/analyze', payload)
export const addDashboardItem = (id: string, payload: any) => request.post('/dashboards/' + id + '/items', payload)
export const updateDashboardItem = (dashboardId: string, itemId: string, payload: any) => request.patch('/dashboards/' + dashboardId + '/items/' + itemId, payload)
export const deleteDashboardItem = (dashboardId: string, itemId: string) => request.delete('/dashboards/' + dashboardId + '/items/' + itemId)
export const createShare = (payload: any) => request.post<{ token: string; path: string; expires_at?: string | null }>('/shares', payload)
export const revokeShare = (token: string) => request.delete('/shares/' + token)
export const deleteShareComment = (token: string, commentId: number) => request.delete('/shares/' + token + '/comments/' + commentId)
export const getShared = (token: string, password?: string) => request.get<any>('/shared/' + token, { params: password ? { password } : undefined })
export const addComment = (token: string, payload: any, password?: string) => request.post('/shared/' + token + '/comments', payload, { params: password ? { password } : undefined })
export const listSchedules = () => request.get<Schedule[]>('/schedules')
export const createSchedule = (payload: any) => request.post('/schedules', payload)
export const runSchedule = (id: string) => request.post('/schedules/' + id + '/run')
export const toggleSchedule = (id: string, enabled: boolean) => request.patch('/schedules/' + id, { enabled })
export const deleteSchedule = (id: string) => request.delete('/schedules/' + id)
export const listScheduleRuns = (id: string) => request.get<any[]>('/schedules/' + id + '/runs')
export const retryScheduleRun = (jobId: string, runId: number) => request.post<any>('/schedules/' + jobId + '/runs/' + runId + '/retry')
export const listWorkflows = () => request.get<Workflow[]>('/workflows')
export const createWorkflow = (payload: any) => request.post<Workflow>('/workflows', payload)
export const getWorkflow = (id: string) => request.get<Workflow>('/workflows/' + id)
export const updateWorkflow = (id: string, payload: any) => request.patch<Workflow>('/workflows/' + id, payload)
export const deleteWorkflow = (id: string) => request.delete('/workflows/' + id)
export const runWorkflow = (id: string, payload: any) => request.post<any>('/workflows/' + id + '/run', payload)
export const listWorkflowRuns = (id: string) => request.get<any[]>('/workflows/' + id + '/runs')
export const getWorkflowRun = (id: string) => request.get<any>('/workflow-runs/' + id)
export const retryWorkflowRun = (id: string) => request.post<any>('/workflow-runs/' + id + '/retry')
export const workflowReportUrl = (id: string) => '/workflow-runs/' + id + '/report'
export const listDataSourceTypes = () => request.get<DataSourceType[]>('/data-sources/types')
export const testDataSource = (payload: Record<string, any>) => request.post<any>('/data-sources/test', payload)
export const previewDataSourceTable = (payload: Record<string, any>) => request.post<any>('/data-sources/preview-table', payload)
export const importDataSourceTable = (payload: Record<string, any>) => request.post<any>('/data-sources/import-table', payload)
export const importRemoteDataSource = (payload: Record<string, any>) => request.post<any>('/data-sources/import', payload)
export const evaluateAnomalies = (payload: Record<string, any>) => request.post<any>('/analysis/anomalies', payload)
export const getDatasetQuality = (docId: string) => request.get<any>('/datasets/' + docId + '/quality')
export const shareDashboardToWorkspace = (dashboardId: string, workspaceId: string) => request.put('/dashboards/' + dashboardId + '/workspace-share', { workspace_id: workspaceId })
export const unshareDashboardFromWorkspace = (dashboardId: string) => request.delete('/dashboards/' + dashboardId + '/workspace-share')
