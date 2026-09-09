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

export function getMessages(sessionId: string) {
  return request.get<ChatMessage[]>(`/sessions/${sessionId}/messages`)
}
