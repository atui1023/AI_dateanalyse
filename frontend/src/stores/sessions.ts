// 会话 store：列表/新建/重命名/删除/切换/历史加载
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as sessionsApi from '@/api/sessions'
import type { Session, ChatMessage } from '@/api/sessions'

export const useSessionsStore = defineStore('sessions', () => {
  const list = ref<Session[]>([])
  const currentId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const loading = ref(false)

  const current = computed(() => list.value.find((s) => s.session_id === currentId.value) || null)

  async function fetchList() {
    loading.value = true
    try {
      const { data } = await sessionsApi.listSessions()
      list.value = data
      // 自动选中第一个会话（若未选中且有会话）
      if (!currentId.value && data.length > 0) {
        await select(data[0].session_id)
      }
    } finally {
      loading.value = false
    }
  }

  async function create(mode: string = 'chat') {
    const { data } = await sessionsApi.createSession(mode)
    list.value.unshift(data)
    await select(data.session_id)
    return data
  }

  async function select(id: string) {
    currentId.value = id
    try {
      const { data } = await sessionsApi.getMessages(id)
      messages.value = data
    } catch {
      messages.value = []
    }
  }

  async function rename(id: string, title: string) {
    await sessionsApi.renameSession(id, title)
    const s = list.value.find((x) => x.session_id === id)
    if (s) s.title = title
  }

  async function remove(id: string) {
    await sessionsApi.deleteSession(id)
    list.value = list.value.filter((x) => x.session_id !== id)
    if (currentId.value === id) {
      currentId.value = null
      messages.value = []
      // 自动切到第一个剩余会话
      if (list.value.length > 0) await select(list.value[0].session_id)
    }
  }

  function clearCurrent() {
    currentId.value = null
    messages.value = []
  }

  // 在当前会话追加一条消息（本地乐观更新）
  function pushMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  return {
    list, currentId, messages, loading, current,
    fetchList, create, select, rename, remove, clearCurrent, pushMessage,
  }
})
