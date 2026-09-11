<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import { useKbStore } from '@/stores/kb'
import { streamChat, AuthError } from '@/api/chat'
import { notifyUnauthorized } from '@/api/request'
import ChatMessage from './ChatMessage.vue'
import { ElMessage } from 'element-plus'
import { Send, ClipboardList } from 'lucide-vue-next'

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

const analysisTemplates = [
  { key: 'trend', label: '趋势分析', prompt: '请按时间字段分析核心指标的趋势，给出总体变化、关键拐点，并绘制折线图。' },
  { key: 'ranking', label: '排名分析', prompt: '请按核心指标对对象进行排名，展示前 10 名，并说明排名靠前对象的主要原因。' },
  { key: 'yoy', label: '同比环比', prompt: '请按时间字段计算核心指标的同比和环比变化，指出增长最快和下降最明显的期间。' },
  { key: 'customer', label: '客户贡献', prompt: '请分析各客户的销售额和贡献度，计算累计贡献占比，并识别重点客户。' },
  { key: 'inventory', label: '库存周转', prompt: '请分析库存周转情况，计算各商品或类别的周转率，识别周转过慢和库存风险。' },
  { key: 'outlier', label: '异常检测', prompt: '请检测数据中的异常值和异常期间，说明异常记录、异常程度以及可能原因。' },
]

function applyAnalysisTemplate(prompt: string) {
  mode.value = 'analysis'
  input.value = prompt
}

const mountList = computed(() => kb.mounts)

onMounted(() => {
  if (sessions.currentId && sessions.messages.length) {
    // 历史消息转为渲染格式
    streamMessages.value = sessions.messages.map((m) => ({
      role: m.role as 'user' | 'assistant',
      text: m.content,
      result: m.result ?? null,
    }))
    scrollToBottom()
  }
})

// 监听会话切换，重置消息
function syncFromSession() {
  const msgs = sessions.messages
  streamMessages.value = Array.isArray(msgs)
    ? msgs.map((m) => ({
        role: m.role as 'user' | 'assistant',
        text: m.content,
        result: m.result ?? null,
      }))
    : []
  scrollToBottom()
}
// 用 watch 更简单，但 onMounted 已处理；切换会话时 sessions.messages 变化
watch(() => sessions.currentId, () => {
  syncFromSession()
})
// 流结束后 handleSend 会把消息落库到 sessions.messages，这是组件自身的写入，
// 不能触发重建（syncFromSession 只映射 role/text，会把流式收到的 result/sources 冲掉）
let selfPush = false
watch(() => sessions.messages, () => {
  if (selfPush) {
    selfPush = false
    return
  }
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

  // 助手占位消息；后续更新必须通过 proxy（数组索引）进行，
  // 直接改闭包里的原始对象不会触发 Vue 响应式更新（result/图表会因此不渲染）
  streamMessages.value.push({
    role: 'assistant',
    text: '',
    result: null,
    sources: null,
    streaming: true,
  })
  const amIdx = streamMessages.value.length - 1
  const am = () => streamMessages.value[amIdx]
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
      let evt: any
      try {
        evt = JSON.parse(chunk)
      } catch {
        console.warn('[chat] 事件解析失败:', String(chunk).slice(0, 100))
        return
      }
      if (evt.type === 'content') {
        am().text += evt.content
        scrollToBottom()
      } else if (evt.type === 'session') {
        if (evt.session_id && evt.session_id !== sessions.currentId) {
          sessions.currentId = evt.session_id
        }
      } else if (evt.type === 'result') {
        am().result = evt
        scrollToBottom()
      } else if (evt.type === 'sources') {
        am().sources = evt.sources || []
      } else if (evt.type === 'error') {
        am().text = '出错了：' + evt.error
      }
    })

    // 流结束：本地保存消息（标记为自身写入，避免 watch 重建冲掉刚收到的 result）
    am().streaming = false
    selfPush = true
    sessions.messages.push(
      { role: 'user', content: text },
      { role: 'assistant', content: am().text, result: am().result ?? null },
    )
    // 刷新会话列表（标题/时间会更新）
    sessions.fetchList()
  } catch (e: any) {
    am().streaming = false
    if (e instanceof AuthError) {
      // session 失效：走全局处理器（清 auth.user + 提示 + SPA 跳登录页）
      notifyUnauthorized()
    } else {
      am().text = '网络错误：' + (e.message || '未知错误')
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
        <el-dropdown v-if="mode === 'analysis'" trigger="click" @command="applyAnalysisTemplate">
          <el-button size="small" plain>
            <el-icon><ClipboardList /></el-icon> 分析模板
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="item in analysisTemplates" :key="item.key" :command="item.prompt">
                {{ item.label }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>        <div v-if="mode === 'analysis' && mountList.length" class="mount-list">
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
  min-width: 0;
  background: #f7f9f8;
}
.msg-stream {
  flex: 1;
  overflow-y: auto;
  padding: 26px clamp(18px, 4vw, 54px);
  scroll-behavior: smooth;
}
.tip {
  width: min(420px, 100%);
  margin: 80px auto 0;
  padding: 24px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 14px;
  border: 1px dashed #cedbd8;
  border-radius: 8px;
  background: rgba(255,255,255,.6);
}
.input-area {
  flex-shrink: 0;
  padding: 12px clamp(16px, 3vw, 36px) 16px;
  background: rgba(255,255,255,.96);
  border-top: 1px solid var(--border);
}
.input-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  flex-wrap: wrap;
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
  padding: 6px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.input-row:focus-within {
  border-color: #9bb9b4;
  box-shadow: 0 0 0 3px rgba(63,118,111,.08);
}
.input-row :deep(.el-textarea__inner) {
  min-height: 38px !important;
  border: 0;
  box-shadow: none;
  resize: none;
}
.input-row > .el-button { width: 40px; height: 38px; padding: 0; }
.input-meta :deep(.el-radio-button__inner) { min-width: 72px; }
@media (max-width: 640px) {
  .msg-stream { padding: 18px 12px; }
  .input-area { padding: 10px; }
}
</style>
