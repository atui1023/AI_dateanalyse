// 会话接口
import request from './request'

export interface Session {
  session_id: string
  mode: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  result?: {
    result_id?: number | null
    stdout?: string | null
    table?: { columns: string[]; rows: any[][]; truncated?: boolean } | null
    chart?: Record<string, any> | null
    datasets?: { dataset_id?: string; filename?: string; rows?: number; cols?: number; columns?: string[] }[] | null
    conclusion?: string | null
    execution_ms?: number | null
    created_at?: string | null
    error?: string | null
  } | null
}

export function listSessions() {
  return request.get<Session[]>('/sessions')
}

export function createSession(mode: string = 'chat') {
  return request.post<Session>('/sessions', { mode })
}

export function renameSession(sessionId: string, title: string) {
  return request.patch(`/sessions/${sessionId}`, { title })
}

export function deleteSession(sessionId: string) {
  return request.delete(`/sessions/${sessionId}`)
}

// 后端返回 {session_id, title, mode, messages: [...]} 对象
export interface SessionMessages {
  session_id: string
  title: string
  mode: string
  messages: ChatMessage[]
}

export function getMessages(sessionId: string) {
  return request.get<SessionMessages>(`/sessions/${sessionId}/messages`)
}
