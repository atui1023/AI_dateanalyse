<script setup lang="ts">
import { ref, watch, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { Download, Bookmark, AlertTriangle } from 'lucide-vue-next'

interface ChartData { series?: any; [k: string]: any }
interface TableData { columns: string[]; rows: any[][]; truncated?: boolean }
interface ResultData {
  stdout?: string
  table?: TableData
  chart?: ChartData
  error?: string
}
interface SourceItem { filename: string; snippet: string }

const props = defineProps<{
  role: 'user' | 'assistant'
  text: string
  result?: ResultData | null
  sources?: SourceItem[] | null
  streaming?: boolean
}>()

const chartRefs = ref<HTMLElement[]>([])
const chartInstances: echarts.ECharts[] = []

function renderCharts() {
  if (!props.result?.chart) return
  nextTick(() => {
    const el = chartRefs.value[0]
    if (el && !chartInstances[0]) {
      const inst = echarts.init(el)
      inst.setOption(props.result!.chart)
      chartInstances[0] = inst
    }
  })
}

function exportChart(idx: number) {
  const inst = chartInstances[idx]
  if (!inst) return
  const url = inst.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
  const a = document.createElement('a')
  a.href = url
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  a.download = `图表_${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}-${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}.png`
  a.click()
}

onMounted(renderCharts)
watch(() => props.result, renderCharts, { deep: true })
</script>

<template>
  <div class="msg" :class="role">
    <div class="bubble">
      <div v-if="text" class="text">{{ text }}</div>
      <span v-if="streaming && !text" class="cursor">▋</span>

      <!-- 分析结果区 -->
      <div v-if="result" class="result">
        <div v-if="result.stdout" class="result-section">
          <div class="label">分析结论</div>
          <pre class="stdout">{{ result.stdout }}</pre>
        </div>
        <div v-if="result.chart" class="result-section">
          <div class="label chart-label">
            可视化图表
            <el-button size="small" text @click="exportChart(0)">
              <el-icon><Download /></el-icon> 导出 PNG
            </el-button>
          </div>
          <div ref="chartRefs" class="chart"></div>
        </div>
        <div v-if="result.table" class="result-section">
          <div class="label">结果表格</div>
          <table class="data-table">
            <thead>
              <tr><th v-for="(c, i) in result.table.columns" :key="i">{{ c }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in result.table.rows" :key="i">
                <td v-for="(v, j) in row" :key="j">{{ v }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="result.table.truncated" class="meta">仅显示前 200 行</div>
        </div>
        <div v-if="result.error" class="result-section err">
          <el-icon><AlertTriangle /></el-icon> {{ result.error }}
        </div>
      </div>

      <!-- 知识库来源 -->
      <div v-if="sources && sources.length" class="sources">
        <div class="label"><el-icon><Bookmark /></el-icon> 依据来源</div>
        <details v-for="(s, i) in sources" :key="i">
          <summary>《{{ s.filename }}》</summary>
          <p>{{ s.snippet }}…</p>
        </details>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  margin-bottom: 16px;
}
.msg.user {
  justify-content: flex-end;
}
.msg.assistant {
  justify-content: flex-start;
}
.bubble {
  max-width: 80%;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.msg.user .bubble {
  background: var(--primary);
  color: #fff;
}
.msg.assistant .bubble {
  background: #fff;
  border: 1px solid var(--border);
}
.text {
  white-space: pre-wrap;
}
.cursor {
  display: inline-block;
  animation: blink 1s steps(2) infinite;
}
@keyframes blink {
  to { opacity: 0; }
}
.result {
  margin-top: 10px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
.result-section {
  margin-bottom: 12px;
}
.result-section:last-child {
  margin-bottom: 0;
}
.label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.chart-label {
  justify-content: space-between;
}
.chart-label .el-button {
  margin-left: auto;
}
.stdout {
  background: var(--bg);
  padding: 10px;
  border-radius: 4px;
  font-size: 13px;
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}
.chart {
  width: 100%;
  height: 320px;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th,
.data-table td {
  border: 1px solid var(--border);
  padding: 4px 8px;
  text-align: left;
}
.data-table thead {
  background: var(--bg);
}
.data-table th {
  font-weight: 500;
}
.meta {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
}
.err {
  color: #f53f3f;
}
.sources {
  margin-top: 10px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
.sources details {
  font-size: 13px;
  margin-bottom: 4px;
}
.sources summary {
  cursor: pointer;
  color: var(--primary);
}
.sources p {
  color: var(--text-secondary);
  margin-top: 4px;
  font-size: 12px;
}
</style>
