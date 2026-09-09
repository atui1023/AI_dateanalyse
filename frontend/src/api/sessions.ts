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
