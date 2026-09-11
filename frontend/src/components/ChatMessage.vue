<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick, toRaw } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { Download, Bookmark, AlertTriangle, FileSpreadsheet, FileText, Printer } from 'lucide-vue-next'

interface ChartData { series?: any; [k: string]: any }
interface TableData { columns: string[]; rows: any[][]; truncated?: boolean }
interface ResultData {
  stdout?: string
  table?: TableData
  chart?: ChartData
  datasets?: { dataset_id?: string; filename?: string; rows?: number; cols?: number; columns?: string[] }[] | null
  execution_ms?: number | null
  created_at?: string | null
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

const chartRef = ref<HTMLElement | null>(null)
// echarts 实例是重对象，用普通变量保存，避免被 Vue 代理
let chartInstance: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let renderFrame = 0

function disposeChart() {
  chartInstance?.dispose()
  chartInstance = null
}

function renderCharts() {
  const chart = props.result?.chart
  if (!chart) {
    disposeChart()
    return
  }
  nextTick(() => {
    const el = chartRef.value
    if (!el) return
    if (resizeObserver) {
      resizeObserver.disconnect()
      resizeObserver.observe(el)
    }
    cancelAnimationFrame(renderFrame)
    renderFrame = requestAnimationFrame(() => {
      if (!el.clientWidth || !el.clientHeight) return
    // ECharts 不应直接接收 Vue Proxy；先转成普通对象，避免复杂 option 被代理后渲染失败。
    const option = JSON.parse(JSON.stringify(toRaw(chart)))
    // 防御：模型偶尔生成 bar/line series 但漏掉 xAxis/yAxis，ECharts 会报错且不渲染
    const series = Array.isArray(option.series) ? option.series : [option.series]
    const needAxis = series.some((s: any) => s && (s.type === 'bar' || s.type === 'line'))
    if (needAxis) {
      if (!option.xAxis) option.xAxis = { type: 'category', data: series[0]?.data?.map((_: any, i: number) => i + 1) || [] }
      if (!option.yAxis) option.yAxis = { type: 'value' }
    }
    try {
      if (!chartInstance) chartInstance = echarts.init(el)
      chartInstance.setOption(option, { notMerge: true })
      chartInstance.resize()
    } catch (e) {
      console.warn('[chart] 渲染失败:', e)
    }
    })
  })
}

function resultFilename(ext: string) {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `分析结果_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.${ext}`
}

function downloadBlob(content: BlobPart, type: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function cellValue(value: unknown) {
  if (value == null) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function exportCsv() {
  const table = props.result?.table
  if (!table) return
  const rows = [table.columns, ...table.rows]
  const csv = rows.map((row) => row.map((value) => {
    const text = cellValue(value)
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }).join(',')).join('\r\n')
  downloadBlob('\uFEFF' + csv, 'text/csv;charset=utf-8', resultFilename('csv'))
}

function escapeHtml(value: unknown) {
  return cellValue(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char] || char))
}

function tableHtml(table: TableData) {
  const head = table.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join('')
  const body = table.rows.map((row) => `<tr>${row.map((v) => `<td>${escapeHtml(v)}</td>`).join('')}</tr>`).join('')
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
}

function exportExcel() {
  const table = props.result?.table
  if (!table) return
  const html = `<!doctype html><html><head><meta charset="utf-8"></head><body>${tableHtml(table)}</body></html>`
  downloadBlob('\uFEFF' + html, 'application/vnd.ms-excel;charset=utf-8', resultFilename('xls'))
}

function exportPdf() {
  const result = props.result
  if (!result) return
  const chartImage = chartInstance?.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
  const popup = window.open('', '_blank', 'width=1000,height=800')
  if (!popup) {
    ElMessage.warning('浏览器阻止了打印窗口，请允许弹出窗口后重试')
    return
  }
  popup.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>分析报告</title><style>
    body{font-family:Arial,"Microsoft YaHei",sans-serif;color:#222;padding:28px;font-size:13px}
    h1{font-size:20px;margin:0 0 18px}h2{font-size:15px;border-bottom:1px solid #ddd;padding-bottom:6px;margin-top:22px}
    pre{white-space:pre-wrap;background:#f6f7f9;padding:12px;border-radius:4px}
    img{display:block;max-width:100%;height:auto;margin-top:10px}
    table{border-collapse:collapse;width:100%;font-size:12px}th,td{border:1px solid #ccc;padding:5px;text-align:left}th{background:#f3f4f6}
    @media print{body{padding:0}}
  </style></head><body>
    <h1>数据分析报告</h1>
    ${result.execution_ms != null ? `<div>执行时间：${escapeHtml(result.execution_ms)} ms</div>` : ''}
    ${result.datasets?.length ? `<div>数据集：${result.datasets.map((d) => escapeHtml(d.filename)).join('、')}</div>` : ''}
    ${result.stdout ? `<h2>分析结论</h2><pre>${escapeHtml(result.stdout)}</pre>` : ''}
    ${chartImage ? `<h2>可视化图表</h2><img src="${chartImage}">` : ''}
    ${result.table ? `<h2>结果表格</h2>${tableHtml(result.table)}` : ''}
  </body></html>`)
  popup.document.close()
  popup.focus()
  setTimeout(() => { popup.print() }, 250)
}
function exportChart() {
  if (!chartInstance) return
  const url = chartInstance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
  const a = document.createElement('a')
  a.href = url
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  a.download = `图表_${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}-${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}.png`
  a.click()
}

onMounted(() => {
  renderCharts()
  resizeObserver = new ResizeObserver(() => chartInstance?.resize())
  if (chartRef.value) resizeObserver.observe(chartRef.value)
  document.addEventListener('visibilitychange', renderCharts)
})
watch(() => props.result, renderCharts, { deep: true })
onBeforeUnmount(() => {
  cancelAnimationFrame(renderFrame)
  resizeObserver?.disconnect()
  document.removeEventListener('visibilitychange', renderCharts)
  disposeChart()
})
</script>

<template>
  <div class="msg" :class="role">
    <div class="bubble">
      <div v-if="text" class="text">{{ text }}</div>
      <span v-if="streaming && !text" class="cursor">▋</span>

      <!-- 分析结果区 -->
      <div v-if="result" class="result">
        <div v-if="result.execution_ms != null || result.datasets?.length" class="result-meta">
          <span v-if="result.execution_ms != null">执行 {{ result.execution_ms }} ms</span>
          <span v-if="result.datasets?.length">数据集：{{ result.datasets.map((d) => d.filename).join('、') }}</span>
        </div>
        <div v-if="result.stdout" class="result-section">
          <div class="label">分析结论</div>
          <pre class="stdout">{{ result.stdout }}</pre>
        </div>
        <div v-if="result.chart" class="result-section">
          <div class="label chart-label">
            可视化图表
            <el-button size="small" text @click="exportChart">
              <el-icon><Download /></el-icon> 导出 PNG
            </el-button>
          </div>
          <div ref="chartRef" class="chart"></div>
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
        <div v-if="result.table || result.chart" class="export-actions">
          <span class="label">导出结果</span>
          <el-button v-if="result.table" size="small" text @click="exportCsv"><el-icon><Download /></el-icon> CSV</el-button>
          <el-button v-if="result.table" size="small" text @click="exportExcel"><el-icon><FileSpreadsheet /></el-icon> Excel</el-button>
          <el-button size="small" text @click="exportPdf"><el-icon><FileText /></el-icon> PDF</el-button>
          <el-button size="small" text @click="exportPdf"><el-icon><Printer /></el-icon> 打印</el-button>
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
  margin-bottom: 18px;
}
.msg.user {
  justify-content: flex-end;
}
.msg.assistant {
  justify-content: flex-start;
}
.bubble {
  max-width: min(86%, 920px);
  padding: 11px 15px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.msg.user .bubble {
  background: var(--primary);
  color: #fff;
  border-bottom-right-radius: 3px;
}
.msg.assistant .bubble {
  background: #fff;
  border: 1px solid var(--border);
  border-bottom-left-radius: 3px;
  box-shadow: 0 2px 8px rgba(35, 54, 50, .035);
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
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
.result-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin-bottom: 8px;
  color: var(--text-tertiary);
  font-size: 12px;
}
.result-section {
  margin-bottom: 14px;
}
.result-section:last-child {
  margin-bottom: 0;
}
.label {
  font-size: 12px;
  color: var(--text-secondary);
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
.export-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}
.export-actions .label {
  margin: 0 4px 0 0;
}
.stdout {
  background: #f6f8f7;
  padding: 12px;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  font-size: 13px;
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}
.chart {
  width: 100%;
  height: 320px;
  min-width: 0;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  background: #fff;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th,
.data-table td {
  border-bottom: 1px solid var(--border-light);
  padding: 7px 9px;
  text-align: left;
}
.data-table thead {
  background: #f5f8f7;
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
  color: var(--danger);
  padding: 10px 12px;
  border: 1px solid #efd4d4;
  border-radius: 6px;
  background: #fff7f7;
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
@media (max-width: 640px) {
  .bubble { max-width: 94%; }
  .chart { height: 260px; }
}
</style>
  font-weight: 600;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  padding: 6px 8px;
  border-radius: 5px;
  background: var(--primary-soft);
