<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import { useKbStore } from '@/stores/kb'
import { streamChat, AuthError } from '@/api/chat'
import { notifyUnauthorized } from '@/api/request'
import ChatMessage from './ChatMessage.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Send, ClipboardList, Download, Sparkles, RefreshCw } from 'lucide-vue-next'

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
  retryText?: string
}
const streamMessages = ref<StreamMessage[]>([])

const analysisTemplates = [
  { key: 'trend', group: '通用分析', label: '趋势分析', prompt: '请按时间字段分析核心指标的趋势，给出总体变化、关键拐点，并绘制折线图。' },
  { key: 'ranking', group: '通用分析', label: '排名分析', prompt: '请按核心指标对对象进行排名，展示前 10 名，并说明排名靠前对象的主要原因。' },
  { key: 'yoy', group: '通用分析', label: '同比环比', prompt: '请按时间字段计算核心指标的同比和环比变化，指出增长最快和下降最明显的期间。' },
  { key: 'customer', group: '通用分析', label: '客户贡献', prompt: '请分析各客户的销售额和贡献度，计算累计贡献占比，并识别重点客户。' },
  { key: 'inventory', group: '通用分析', label: '库存周转', prompt: '请分析库存周转情况，计算各商品或类别的周转率，识别周转过慢和库存风险。' },
  { key: 'outlier', group: '通用分析', label: '异常检测', prompt: '请检测数据中的异常值和异常期间，说明异常记录、异常程度以及可能原因。' },
  { key: 'ecommerce', group: '行业模板', label: '电商经营诊断', prompt: '请按日期、渠道、商品和订单字段分析电商经营情况，拆解销售额、订单数、客单价和转化趋势，识别增长来源、滞销商品和需要优先改进的环节，并生成图表。' },
  { key: 'sales', group: '行业模板', label: '销售漏斗分析', prompt: '请分析销售线索、商机、成交和回款数据，计算各阶段转化率、销售周期和人员贡献，找出流失最严重的环节并给出改进建议。' },
  { key: 'finance', group: '行业模板', label: '财务经营分析', prompt: '请从收入、成本、费用、利润和现金流字段分析经营状况，计算毛利率、净利率和期间变化，识别异常波动并给出经营建议。' },
  { key: 'operations', group: '行业模板', label: '运营效率分析', prompt: '请分析各部门或业务环节的处理量、完成率、耗时和异常数，比较不同团队的效率，定位瓶颈并生成可执行的优化建议。' },
]
const industryTemplates = analysisTemplates.filter((item) => item.group === '行业模板')
const customTemplates = ref<{ key: string; label: string; prompt: string }[]>([])

function applyAnalysisTemplate(prompt: string) {
  mode.value = 'analysis'
  input.value = prompt
}

async function saveCurrentTemplate() {
  const prompt = input.value.trim()
  if (mode.value !== 'analysis' || !prompt) return ElMessage.warning('请先输入要保存的分析问题')
  try {
    const { value } = await ElMessageBox.prompt('给这个分析问题起一个名称', '保存分析模板', { confirmButtonText: '保存', cancelButtonText: '取消', inputPlaceholder: '例如：每周销售复盘', inputValidator: (value) => Boolean(value?.trim()) || '请输入模板名称' })
    const label = value.trim()
    customTemplates.value = [{ key: `custom-${Date.now()}`, label, prompt }, ...customTemplates.value.filter((item) => item.label !== label)].slice(0, 12)
    localStorage.setItem('analysis-custom-templates', JSON.stringify(customTemplates.value))
    ElMessage.success('分析模板已保存')
  } catch {
    // 用户取消保存时不提示错误
  }
}

function removeCustomTemplate(key: string) {
  customTemplates.value = customTemplates.value.filter((item) => item.key !== key)
  localStorage.setItem('analysis-custom-templates', JSON.stringify(customTemplates.value))
}

const mountList = computed(() => kb.mounts)
const showOnboarding = computed(() => streamMessages.value.length === 0)

function downloadSampleData() {
  const csv = '\uFEFF日期,渠道,销售额,订单数\n2026-01-01,线上,12800,96\n2026-01-02,门店,9400,71\n2026-01-03,线上,15600,112\n2026-01-04,门店,10100,79\n2026-01-05,线上,18400,135\n2026-01-06,门店,11900,88\n2026-01-07,线上,20100,148\n'
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = '销售分析示例数据.csv'
  link.click()
  URL.revokeObjectURL(url)
  ElMessage.success('示例 CSV 已下载')
}

function useQuickPrompt() {
  mode.value = 'analysis'
  input.value = analysisTemplates[0].prompt
}

onMounted(async () => {
  try {
    const saved = JSON.parse(localStorage.getItem('analysis-custom-templates') || '[]')
    if (Array.isArray(saved)) customTemplates.value = saved.filter((item) => item?.label && item?.prompt).slice(0, 12)
  } catch { customTemplates.value = [] }
  if (sessions.currentId && sessions.messages.length) {
    // 历史消息转为渲染格式
    streamMessages.value = sessions.messages.map((m) => ({
      role: m.role as 'user' | 'assistant',
      text: m.content,
      result: m.result ?? null,
    }))
    scrollToBottom()
  }
  const draftRaw = localStorage.getItem('analysis-draft')
  if (draftRaw) {
    try {
      const draft = JSON.parse(draftRaw)
      if (draft?.question) {
        mode.value = 'analysis'
        input.value = draft.question
      }
      if (Array.isArray(draft?.dataset_ids) && draft.dataset_ids.length) {
        await Promise.all(draft.dataset_ids.map((id: string) => kb.mountDocument(id)))
      }
    } catch {
      // 忽略损坏的临时草稿
    } finally {
      localStorage.removeItem('analysis-draft')
    }
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

function retryFailed(index: number) {
  const failed = streamMessages.value[index]
  if (!failed?.retryText || sending.value) return
  if (streamMessages.value[index - 1]?.role === 'user') streamMessages.value.splice(index - 1, 2)
  input.value = failed.retryText
  handleSend()
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
        am().retryText = text
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
      am().retryText = text
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
      <template v-for="(m, i) in streamMessages" :key="i">
        <ChatMessage
          :role="m.role"
          :text="m.text"
          :result="m.result"
          :sources="m.sources"
          :streaming="m.streaming"
        />
        <div v-if="m.retryText" class="retry-action">
          <el-button size="small" plain @click="retryFailed(i)">
            <el-icon><RefreshCw /></el-icon>重试
          </el-button>
        </div>
      </template>
      <div v-if="showOnboarding" class="onboarding">
        <div class="onboarding-icon"><Sparkles :size="20" /></div>
        <h1>从一份数据开始</h1>
        <p>上传 CSV 或 Excel，挂载后即可用自然语言完成分析、图表和报告。</p>
        <div class="onboarding-actions">
          <el-button plain @click="downloadSampleData"><el-icon><Download /></el-icon>下载示例 CSV</el-button>
          <el-button type="primary" @click="useQuickPrompt"><el-icon><Sparkles /></el-icon>使用趋势模板</el-button>
        </div>
        <div class="industry-quick-start">
          <div class="quick-start-title">行业快捷分析</div>
          <div class="quick-start-grid">
            <el-button v-for="item in industryTemplates" :key="item.key" plain @click="applyAnalysisTemplate(item.prompt)">
              {{ item.label }}
            </el-button>
          </div>
        </div>
        <div class="onboarding-next">下一步：在左侧知识库上传并挂载数据集，然后回到这里提问。</div>
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
              <template v-for="group in [...new Set(analysisTemplates.map((item) => item.group))]" :key="group">
                <el-dropdown-item disabled>{{ group }}</el-dropdown-item>
                <el-dropdown-item v-for="item in analysisTemplates.filter((entry) => entry.group === group)" :key="item.key" :command="item.prompt">
                  {{ item.label }}
                </el-dropdown-item>
              </template>
              <template v-if="customTemplates.length">
                <el-dropdown-item disabled>我的模板</el-dropdown-item>
                <el-dropdown-item v-for="item in customTemplates" :key="item.key" :command="item.prompt">{{ item.label }}</el-dropdown-item>
              </template>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button v-if="mode === 'analysis'" size="small" plain @click="saveCurrentTemplate">保存模板</el-button>
        <div v-if="mode === 'analysis' && customTemplates.length" class="custom-template-list">
          <span class="custom-template-label">我的模板：</span>
          <el-tag v-for="item in customTemplates" :key="item.key" size="small" closable @click="applyAnalysisTemplate(item.prompt)" @close="removeCustomTemplate(item.key)">{{ item.label }}</el-tag>
        </div>
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
  min-width: 0;
  background: #f7f9f8;
}
.msg-stream {
  flex: 1;
  overflow-y: auto;
  padding: 26px clamp(18px, 4vw, 54px);
  scroll-behavior: smooth;
}
.onboarding {
  width: min(620px, 100%);
  margin: auto;
  padding: 28px 18px 42px;
  text-align: center;
  color: var(--text-primary);
}
.onboarding-icon {
  width: 42px;
  height: 42px;
  margin: 0 auto 14px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: #e6f3eb;
  color: #2f8050;
}
.onboarding h1 {
  margin: 0 0 8px;
  font-size: 22px;
  font-weight: 650;
}
.onboarding p {
  margin: 0 auto 18px;
  max-width: 480px;
  color: var(--text-secondary);
  line-height: 1.6;
}
.onboarding-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
}
.industry-quick-start {
  margin: 22px auto 0;
  padding-top: 16px;
  border-top: 1px solid var(--border-light);
}
.quick-start-title {
  margin-bottom: 10px;
  color: var(--text-secondary);
  font-size: 12px;
}
.quick-start-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.quick-start-grid .el-button {
  width: 100%;
  margin: 0;
}
.onboarding-next {
  margin-top: 18px;
  font-size: 12px;
  color: var(--text-tertiary);
}
.custom-template-list {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.custom-template-label {
  color: var(--text-tertiary);
  font-size: 12px;
}
.custom-template-list .el-tag { cursor: pointer; }
.retry-action {
  margin: -8px 0 12px 46px;
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
@media (max-width: 620px) {
  .quick-start-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
