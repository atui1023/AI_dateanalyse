<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import { useKbStore } from '@/stores/kb'
import { streamChat, AuthError } from '@/api/chat'
import { notifyUnauthorized } from '@/api/request'
import ChatMessage from './ChatMessage.vue'
import { ElMessage } from 'element-plus'
import { Send } from 'lucide-vue-next'

const sessions = useSessionsStore()
const kb = useKbStore()

const input = ref('')
const sending = ref(false)
const mode = ref<'chat' | 'analysis' | 'rag'>('chat')
const chatEl = ref<HTMLElement | null>(null)

interface StreamMessage {
  role: 'user' | 'assistant'
  text: string
  result?: any
  sources?: any[] | null
  streaming?: boolean
}
const streamMessages = ref<StreamMessage[]>([])

const mountList = computed(() => kb.mounts)

onMounted(() => {
  if (sessions.currentId && sessions.messages.length) {
    // 历史消息转为渲染格式
    streamMessages.value = sessions.messages.map((m) => ({
      role: m.role as 'user' | 'assistant',
      text: m.content,
    }))
    scrollToBottom()
  }
})

// 监听会话切换，重置消息
function syncFromSession() {
  const msgs = sessions.messages
  streamMessages.value = Array.isArray(msgs)
    ? msgs.map((m) => ({ role: m.role as 'user' | 'assistant', text: m.content }))
    : []
  scrollToBottom()
}
// 用 watch 更简单，但 onMounted 已处理；切换会话时 sessions.messages 变化
watch(() => sessions.currentId, () => {
  syncFromSession()
})
watch(() => sessions.messages, () => {
  syncFromSession()
}, { deep: true })

function scrollToBottom() {
  nextTick(() => {
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  })
}

async function handleSend() {
  const text = input.value.trim()
  if (!text || sending.value) return

  // 新建会话（若未选中）
  if (!sessions.currentId) {
    await sessions.create(mode.value)
  }

  // 推入用户消息
  streamMessages.value.push({ role: 'user', text })
  input.value = ''
  sending.value = true
  scrollToBottom()

  // 助手占位消息
  const assistantMsg: StreamMessage = {
    role: 'assistant',
    text: '',
    result: null,
    sources: null,
    streaming: true,
  }
  streamMessages.value.push(assistantMsg)
  scrollToBottom()

  // 构造 messages payload（含历史）
  const historyMsgs = Array.isArray(sessions.messages) ? sessions.messages : []
  const payload = {
    messages: [
      ...historyMsgs.map((m: any) => ({ role: m.role, content: m.content })),
      { role: 'user' as const, content: text },
    ],
    session_id: sessions.currentId,
    mode: mode.value,
    dataset_ids: mode.value === 'analysis' ? kb.mountIds : [],
  }

  try {
    await streamChat(payload, (chunk: string) => {
      if (!chunk) return
      let payload: any
      try {
        payload = JSON.parse(chunk)
      } catch {
        return
      }
      if (payload.type === 'content') {
        assistantMsg.text += payload.content
        scrollToBottom()
      } else if (payload.type === 'session') {
        if (payload.session_id && payload.session_id !== sessions.currentId) {
          sessions.currentId = payload.session_id
        }
      } else if (payload.type === 'result') {
        assistantMsg.result = payload
        scrollToBottom()
      } else if (payload.type === 'sources') {
        assistantMsg.sources = payload.sources || []
      } else if (payload.type === 'error') {
        assistantMsg.text = '出错了：' + payload.error
      }
    })

    // 流结束：本地保存消息
    assistantMsg.streaming = false
    sessions.messages.push(
      { role: 'user', content: text },
      { role: 'assistant', content: assistantMsg.text },
    )
    // 刷新会话列表（标题/时间会更新）
    sessions.fetchList()
  } catch (e: any) {
    assistantMsg.streaming = false
    if (e instanceof AuthError) {
      // session 失效：走全局处理器（清 auth.user + 提示 + SPA 跳登录页）
      notifyUnauthorized()
    } else {
      assistantMsg.text = '网络错误：' + (e.message || '未知错误')
      ElMessage.error('发送失败')
    }
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <section class="chat-main">
    <!-- 消息流 -->
    <div ref="chatEl" class="msg-stream">
      <ChatMessage
        v-for="(m, i) in streamMessages"
        :key="i"
        :role="m.role"
        :text="m.text"
        :result="m.result"
        :sources="m.sources"
        :streaming="m.streaming"
      />
      <div v-if="streamMessages.length === 0" class="tip">
        开始新的对话，输入问题开始分析
      </div>
    </div>

    <!-- 输入区 -->
    <div class="input-area">
      <!-- 模式切换 + 挂载信息 -->
      <div class="input-meta">
        <el-radio-group v-model="mode" size="small">
          <el-radio-button label="chat">对话</el-radio-button>
          <el-radio-button label="analysis">数据分析</el-radio-button>
          <el-radio-button label="rag">知识库</el-radio-button>
        </el-radio-group>
        <div v-if="mode === 'analysis' && mountList.length" class="mount-list">
          <el-tag v-for="(m, i) in mountList" :key="m.doc_id" size="small" closable @close="kb.unmountDocument(m.doc_id)">
            df{{ i + 1 }}: {{ m.filename }}
          </el-tag>
        </div>
      </div>
      <div class="input-row">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          :autosize="{ minRows: 1, maxRows: 6 }"
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          :disabled="sending"
          @keydown="handleKeydown"
        />
        <el-button type="primary" :loading="sending" @click="handleSend">
          <el-icon><Send /></el-icon>
        </el-button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg);
}
.msg-stream {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.tip {
  text-align: center;
  color: var(--text-tertiary);
  margin-top: 40px;
  font-size: 14px;
}
.input-area {
  flex-shrink: 0;
  padding: 12px 16px;
  background: #fff;
  border-top: 1px solid var(--border);
}
.input-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.mount-list {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
</style>
