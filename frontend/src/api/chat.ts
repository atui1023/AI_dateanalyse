// 聊天接口：SSE 流式 + 普通分析
import request from './request'

export interface ChatPayload {
  messages: { role: 'user' | 'assistant'; content: string }[]
  session_id?: string | null
  mode?: string
  dataset_ids?: string[]
}

/**
 * 发起聊天请求（SSE 流式）。
 * 后端返回 text/plain 流，按事件格式分块；这里返回一个可迭代的 reader。
 */
export async function streamChat(
  payload: ChatPayload,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({
      messages: payload.messages,
      session_id: payload.session_id ?? null,
      mode: payload.mode ?? 'chat',
      dataset_ids: payload.dataset_ids ?? [],
    }),
    signal,
  })

  if (!resp.ok) {
    const detail = await resp.text().catch(() => '请求失败')
    throw new Error(detail || `HTTP ${resp.status}`)
  }

  if (!resp.body) throw new Error('无响应流')

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // 简单策略：按行或事件边界回调，后端 SSE 事件以 event: / data: 形式
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data:')) {
        onChunk(line.slice(5).trim())
      } else if (line.startsWith('event:')) {
        // 事件类型暂不处理，直接透传 data
      } else if (line.trim()) {
        onChunk(line)
      }
    }
  }
}
