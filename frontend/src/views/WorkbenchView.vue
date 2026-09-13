<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbStore } from '@/stores/kb'
import * as api from '@/api/workbench'
import * as teamApi from '@/api/team'
import ChatMessage from '@/components/ChatMessage.vue'

const kb = useKbStore()
const router = useRouter()
const relationBusy = ref(false)
const dashboardBusy = ref(false)
const activeTab = ref('relations')
const relations = ref<api.Relation[]>([])
const results = ref<api.AnalysisResultItem[]>([])
const dashboards = ref<api.Dashboard[]>([])
const schedules = ref<api.Schedule[]>([])
const selectedDatasets = ref<string[]>([])
const relationName = ref('销售关联分析')
const relationInstruction = ref('')
const relationResult = ref<any>(null)
const dashboardInstruction = ref('')
const dashboardDatasets = ref<string[]>([])
const leftKey = ref('')
const rightKey = ref('')
const preview = ref<any>(null)
const selectedResult = ref<number | null>(null)
const selectedDashboard = ref('')
const chartType = ref('original')
const scheduleType = ref('analysis')
const scheduleSourcePath = ref('')
const scheduleSourceUrl = ref('')
const dashboardDetail = ref<any>(null)
const shareToken = ref('')
const shareExpiresDays = ref(7)
const sharePassword = ref('')
const shareAllowDownload = ref(true)
const shareAllowComments = ref(true)
const scheduleRuns = ref<Record<string, any[]>>({})
const shareComments = ref<any[]>([])
const comment = ref('')
const scheduleName = ref('每日分析报告')
const scheduleText = ref('daily 09:00')
const scheduleQuestion = ref('请生成今日数据分析报告')
const scheduleRecipients = ref('demo@example.com')
const reportTemplate = ref('经营分析报告')
const notificationChannel = ref('email')
const webhookUrl = ref('')
const dataSourceTypes = ref<api.DataSourceType[]>([])
const dataSourceType = ref('mysql')
const dataSourceForm = ref({ host: '127.0.0.1', port: 3306, database: '', username: '', password: '' })
const dataSourceResult = ref<any>(null)
const dataSourceBusy = ref(false)
const selectedDataSourceTable = ref('')
const dataSourcePreview = ref<any>(null)
const dataSourcePreviewBusy = ref(false)
const dataSourceAlias = ref('')
const savedDataSources = ref<any[]>([])
const qualityDatasetId = ref('')
const qualityResult = ref<any>(null)
const qualityBusy = ref(false)
const forecastDatasetId = ref('')
const forecastDateColumn = ref('')
const forecastValueColumn = ref('')
const forecastHorizon = ref(7)
const forecastProfile = ref<any>(null)
const forecastResult = ref<any>(null)
const forecastBusy = ref(false)
const remoteSourceType = ref('api')
const remoteSourceUrl = ref('')
const remoteSourceName = ref('远程数据快照')
const remoteSourceBusy = ref(false)
const scheduleDatasetIds = ref<string[]>([])
const dashboardDateRange = ref<[string, string] | null>(null)
const dashboardDimension = ref('')
const dashboardDimensionValue = ref('')
const dashboardAutoRefresh = ref(false)
const workspaces = ref<teamApi.Workspace[]>([])
const selectedWorkspace = ref('')
let dashboardRefreshTimer: number | undefined

const tableDocs = computed(() => kb.documents.filter((d) => ['.csv', '.xlsx', '.xls'].includes(d.ext)))
const selectedFirst = computed(() => tableDocs.value.find((d) => d.doc_id === selectedDatasets.value[0]))
const selectedSecond = computed(() => tableDocs.value.find((d) => d.doc_id === selectedDatasets.value[1]))
const forecastDateOptions = computed(() => {
  const columns = forecastProfile.value?.columns_detail || []
  return [...columns].sort((a: any, b: any) => {
    const aDate = a.type === 'date' || /日期|时间|date|time/i.test(a.name) ? 0 : 1
    const bDate = b.type === 'date' || /日期|时间|date|time/i.test(b.name) ? 0 : 1
    return aDate - bDate
  }).map((item: any) => item.name)
})
const forecastValueOptions = computed(() => forecastProfile.value?.columns_detail?.filter((item: any) => item.mean != null).map((item: any) => item.name) || [])
const selectedResultItem = computed(() => results.value.find((item) => item.id === selectedResult.value) || null)
const dashboardDimensionOptions = computed(() => {
  const values = new Set<string>()
  for (const item of dashboardDetail.value?.items || []) {
    const table = item.result?.table
    if (!table?.columns) continue
    const index = dashboardDimension.value ? table.columns.indexOf(dashboardDimension.value) : -1
    if (index >= 0) for (const row of table.rows || []) if (row[index] != null && row[index] !== '') values.add(String(row[index]))
  }
  return [...values].sort()
})
const dashboardDimensionColumns = computed(() => {
  const first = dashboardDetail.value?.items?.find((item: any) => item.result?.table?.columns?.length)
  return first?.result?.table?.columns || []
})
const filteredDashboardItems = computed(() => (dashboardDetail.value?.items || []).map((item: any) => {
  const table = item.result?.table
  if (!table?.columns || !Array.isArray(table.rows)) return item
  const dateIndex = table.columns.findIndex((name: string) => /日期|时间|date|time/i.test(name))
  const dimensionIndex = dashboardDimension.value ? table.columns.indexOf(dashboardDimension.value) : -1
  const rows = table.rows.filter((row: any[]) => {
    const date = dateIndex >= 0 ? String(row[dateIndex] ?? '').slice(0, 10) : ''
    const dateOk = !dashboardDateRange.value || !date || (date >= dashboardDateRange.value[0] && date <= dashboardDateRange.value[1])
    const dimensionOk = dimensionIndex < 0 || !dashboardDimensionValue.value || String(row[dimensionIndex] ?? '') === dashboardDimensionValue.value
    return dateOk && dimensionOk
  })
  return { ...item, result: { ...item.result, table: { ...table, rows } } }
}))
const dashboardKpis = computed(() => {
  const values: number[] = []
  let recentChange: number | null = null
  let overallChange: number | null = null
  for (const item of filteredDashboardItems.value) {
    const table = item.result?.table
    if (!table?.columns?.length) continue
    const numericIndex = table.columns.findIndex((_: string, index: number) => table.rows.some((row: any[]) => typeof row[index] === 'number' || (row[index] != null && row[index] !== '' && !Number.isNaN(Number(row[index])))))
    if (numericIndex < 0) continue
    const itemValues = (table.rows || []).map((row: any[]) => Number(row[numericIndex])).filter((value: number) => Number.isFinite(value))
    values.push(...itemValues)
    if (itemValues.length >= 2) {
      const previous = itemValues[itemValues.length - 2]
      const latest = itemValues[itemValues.length - 1]
      if (previous !== 0) recentChange = ((latest - previous) / Math.abs(previous)) * 100
      const first = itemValues[0]
      if (first !== 0) overallChange = ((latest - first) / Math.abs(first)) * 100
    }
  }
  if (!values.length) return []
  const total = values.reduce((sum, value) => sum + value, 0)
  const average = total / values.length
  const sorted = [...values].sort((a, b) => a - b)
  const q1 = sorted[Math.floor((sorted.length - 1) * 0.25)]
  const q3 = sorted[Math.floor((sorted.length - 1) * 0.75)]
  const iqr = q3 - q1
  const anomalies = iqr > 0 ? values.filter((value) => value < q1 - iqr * 1.5 || value > q3 + iqr * 1.5).length : 0
  const kpis = [{ label: '记录数', value: values.length, suffix: '' }, { label: '指标合计', value: total, suffix: '' }, { label: '指标均值', value: average, suffix: '' }]
  if (recentChange != null) kpis.push({ label: '最近环比', value: recentChange, suffix: '%' })
  if (overallChange != null) kpis.push({ label: '整体变化', value: overallChange, suffix: '%' })
  kpis.push({ label: '异常值', value: anomalies, suffix: anomalies ? '需关注' : '正常' })
  return kpis
})

async function refresh() {
  const [r, a, d, s] = await Promise.all([api.listRelations(), api.listResults(), api.listDashboards(), api.listSchedules()])
  relations.value = r.data
  results.value = a.data
  dashboards.value = d.data
  schedules.value = s.data
}
async function testDataSource() {
  dataSourceBusy.value = true
  dataSourceResult.value = null
  try {
    const { data } = await api.testDataSource({ type: dataSourceType.value, ...dataSourceForm.value })
    dataSourceResult.value = data
    selectedDataSourceTable.value = data.tables?.[0] || ''
    dataSourcePreview.value = null
    ElMessage.success(`连接成功，发现 ${data.table_count} 张表`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '数据源连接失败')
  } finally {
    dataSourceBusy.value = false
  }
}
function saveDataSourceTemplate() {
  const alias = dataSourceAlias.value.trim()
  if (!alias) return ElMessage.warning('请输入连接名称')
  const next = savedDataSources.value.filter((item) => item.alias !== alias)
  next.unshift({ alias, type: dataSourceType.value, form: { ...dataSourceForm.value, password: '' } })
  savedDataSources.value = next.slice(0, 8)
  localStorage.setItem('data-source-templates', JSON.stringify(savedDataSources.value))
  ElMessage.success('连接模板已保存，密码不会写入浏览器')
}
function useDataSourceTemplate(item: any) {
  dataSourceType.value = item.type
  dataSourceForm.value = { ...dataSourceForm.value, ...item.form, password: '' }
  dataSourceResult.value = null
  selectedDataSourceTable.value = ''
  dataSourcePreview.value = null
  ElMessage.info('已填入连接信息，请补充密码后测试')
}
function removeDataSourceTemplate(alias: string) {
  savedDataSources.value = savedDataSources.value.filter((item) => item.alias !== alias)
  localStorage.setItem('data-source-templates', JSON.stringify(savedDataSources.value))
}
async function previewDataSourceTable() {
  if (!dataSourceResult.value || !selectedDataSourceTable.value) return ElMessage.warning('请先选择数据表')
  dataSourcePreviewBusy.value = true
  try {
    const { data } = await api.previewDataSourceTable({ type: dataSourceType.value, ...dataSourceForm.value, table: selectedDataSourceTable.value, preview_rows: 20 })
    dataSourcePreview.value = data
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '数据表预览失败')
  } finally {
    dataSourcePreviewBusy.value = false
  }
}
async function importDataSourceTable() {
  if (!dataSourceResult.value || !selectedDataSourceTable.value) return ElMessage.warning('请先选择数据表')
  dataSourcePreviewBusy.value = true
  try {
    const { data } = await api.importDataSourceTable({ type: dataSourceType.value, ...dataSourceForm.value, table: selectedDataSourceTable.value, max_rows: 100000 })
    ElMessage.success(`已导入 ${data.filename}，现在可以在数据集和流水线中使用`)
    await kb.fetchDocuments()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '数据表导入失败')
  } finally {
    dataSourcePreviewBusy.value = false
  }
}
async function importRemoteDataSource() {
  if (!remoteSourceUrl.value.trim()) return ElMessage.warning('请输入 API 或 Webhook 地址')
  remoteSourceBusy.value = true
  try {
    const { data } = await api.importRemoteDataSource({ type: remoteSourceType.value, url: remoteSourceUrl.value.trim(), name: remoteSourceName.value })
    ElMessage.success(`已导入 ${data.filename}`)
    await kb.fetchDocuments()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '远程数据导入失败')
  } finally {
    remoteSourceBusy.value = false
  }
}
async function inspectDatasetQuality() {
  if (!qualityDatasetId.value) return ElMessage.warning('请选择要检查的数据集')
  qualityBusy.value = true
  try {
    qualityResult.value = (await api.getDatasetQuality(qualityDatasetId.value)).data
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '数据质量检查失败')
  } finally {
    qualityBusy.value = false
  }
}
async function prepareForecastFields() {
  forecastProfile.value = null
  forecastResult.value = null
  forecastDateColumn.value = ''
  forecastValueColumn.value = ''
  if (!forecastDatasetId.value) return
  try {
    forecastProfile.value = (await api.getDatasetQuality(forecastDatasetId.value)).data
    forecastDateColumn.value = forecastDateOptions.value[0] || ''
    forecastValueColumn.value = forecastValueOptions.value[0] || ''
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '字段识别失败，请检查数据集')
  }
}
async function runForecast() {
  if (!forecastDatasetId.value || !forecastDateColumn.value || !forecastValueColumn.value) return ElMessage.warning('请选择数据集、日期字段和指标字段')
  forecastBusy.value = true
  try {
    forecastResult.value = (await api.forecastDataset(forecastDatasetId.value, { date_column: forecastDateColumn.value, value_column: forecastValueColumn.value, horizon: forecastHorizon.value })).data
    ElMessage.success('预测分析已完成')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '预测分析失败，请确认日期和指标字段可计算')
  } finally {
    forecastBusy.value = false
  }
}
async function createAndExecuteRelation() {
  if (selectedDatasets.value.length < 2 || !leftKey.value || !rightKey.value) return ElMessage.warning('请选择两个数据集并填写关联字段')
  relationBusy.value = true
  try {
    const created = await api.createRelation({ name: relationName.value, dataset_ids: selectedDatasets.value, joins: [{ left_dataset_id: selectedDatasets.value[0], right_dataset_id: selectedDatasets.value[1], left_key: leftKey.value, right_key: rightKey.value, how: 'left' }] })
    const { data } = await api.executeRelation(created.data.id, relationInstruction.value)
    relationResult.value = data
    await kb.fetchDocuments()
    await refresh()
    ElMessage.success('关联配置和结果文件已生成')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '关联创建或文件生成失败')
  } finally {
    relationBusy.value = false
  }
}
async function previewRelation(id: string) {
  const { data } = await api.previewRelation(id)
  preview.value = data
}
async function runDashboardInstruction() {
  if (!selectedDashboard.value || !dashboardInstruction.value.trim() || !dashboardDatasets.value.length) return ElMessage.warning('请选择仪表盘、文件并填写指令')
  dashboardBusy.value = true
  try {
    const { data } = await api.runDashboardInstruction(selectedDashboard.value, { instruction: dashboardInstruction.value, dataset_ids: dashboardDatasets.value })
    dashboardDetail.value = data.dashboard
    results.value.unshift(data.result)
    ElMessage.success('仪表盘指令已执行并保存')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '仪表盘指令执行失败')
  } finally {
    dashboardBusy.value = false
  }
}
async function removeRelation(id: string) { await api.deleteRelation(id); await refresh() }
async function removeResult(id: number) {
  await ElMessageBox.confirm('删除后该分析结果将无法恢复，确定继续吗？', '删除分析结果', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  await api.deleteResult(id)
  if (selectedResult.value === id) selectedResult.value = null
  await refresh()
  ElMessage.success('分析结果已删除')
}
async function retryResult(id: number) {
  try {
    const { data } = await api.retryResult(id)
    results.value.unshift({ id: data.result_id, title: data.question || results.value.find((item) => item.id === id)?.title || '重新执行的分析', question: data.question || results.value.find((item) => item.id === id)?.question || '重新执行的分析', status: data.error ? 'error' : 'ok', stdout: data.stdout, table: data.table, chart: data.chart, datasets: data.datasets, execution_ms: data.execution_ms, error: data.error, created_at: new Date().toISOString() })
    ElMessage.success('已重新执行并生成新的分析结果')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重新执行失败')
  }
}
async function toggleResultFavorite(item: api.AnalysisResultItem) {
  const { data } = await api.updateResult(item.id, { is_favorite: !item.is_favorite })
  const index = results.value.findIndex((result) => result.id === item.id)
  if (index >= 0) results.value[index] = data
}
async function renameResult(item: api.AnalysisResultItem) {
  const { value } = await ElMessageBox.prompt('输入结果标题', '重命名分析结果', { inputValue: item.title || item.question, confirmButtonText: '保存', cancelButtonText: '取消' })
  if (!value?.trim()) return
  const { data } = await api.updateResult(item.id, { title: value.trim() })
  const index = results.value.findIndex((result) => result.id === item.id)
  if (index >= 0) results.value[index] = data
}
async function copyResult(item: api.AnalysisResultItem) {
  const { data } = await api.copyResult(item.id)
  results.value.unshift(data)
  selectedResult.value = data.id
  ElMessage.success('结果副本已创建')
}
function askAgain(item: api.AnalysisResultItem) {
  localStorage.setItem('analysis-draft', JSON.stringify({ question: item.question, dataset_ids: (item.datasets || []).map((dataset: any) => dataset.dataset_id).filter(Boolean) }))
  router.push('/')
}
async function createDashboard() {
  const { value } = await ElMessageBox.prompt('输入仪表盘名称', '新建仪表盘', { inputValue: '经营分析仪表盘', confirmButtonText: '创建', cancelButtonText: '取消' })
  await api.createDashboard({ name: value })
  await refresh()
}
async function addToDashboard() {
  if (!selectedResult.value || !selectedDashboard.value) return ElMessage.warning('请选择分析结果和仪表盘')
  const result = results.value.find((item) => item.id === selectedResult.value)
  await api.addDashboardItem(selectedDashboard.value, { result_id: selectedResult.value, title: result?.question?.slice(0, 60) || '分析图表', chart_config: { type: chartType.value } })
  ElMessage.success('已加入仪表盘')
  await loadDashboard()
}
async function removeDashboardItem(itemId: string) {
  if (!selectedDashboard.value) return
  await api.deleteDashboardItem(selectedDashboard.value, itemId)
  await loadDashboard()
}
async function moveDashboardItem(itemId: string, delta: number) {
  if (!selectedDashboard.value || !dashboardDetail.value) return
  const items = [...dashboardDetail.value.items]
  const index = items.findIndex((item: any) => item.id === itemId)
  const nextIndex = index + delta
  if (index < 0 || nextIndex < 0 || nextIndex >= items.length) return
  const current = items[index]
  const target = items[nextIndex]
  // 顺序更新，避免两个并发 PATCH 读到相同的旧顺序导致交换结果不稳定。
  await api.updateDashboardItem(selectedDashboard.value, current.id, { position: { order: nextIndex } })
  await api.updateDashboardItem(selectedDashboard.value, target.id, { position: { order: index } })
  await loadDashboard()
}
async function loadDashboard() { if (selectedDashboard.value) { dashboardDetail.value = (await api.getDashboard(selectedDashboard.value)).data; selectedWorkspace.value = dashboardDetail.value.workspace_id || '' } }
async function loadWorkspaces() { try { workspaces.value = (await teamApi.listWorkspaces()).data } catch { workspaces.value = [] } }
async function shareCurrentDashboard() {
  if (!selectedDashboard.value || !selectedWorkspace.value) return ElMessage.warning('请选择仪表盘和团队')
  try { await api.shareDashboardToWorkspace(selectedDashboard.value, selectedWorkspace.value); await loadDashboard(); await refresh(); ElMessage.success('仪表盘已共享给团队成员') }
  catch (error: any) { ElMessage.error(error?.response?.data?.detail || '共享仪表盘失败') }
}
async function unshareCurrentDashboard() {
  if (!selectedDashboard.value) return
  try { await api.unshareDashboardFromWorkspace(selectedDashboard.value); await loadDashboard(); await refresh(); ElMessage.success('已取消团队共享') }
  catch (error: any) { ElMessage.error(error?.response?.data?.detail || '取消共享失败') }
}
function toggleDashboardAutoRefresh(value: string | number | boolean) {
  dashboardAutoRefresh.value = Boolean(value)
  if (dashboardRefreshTimer) window.clearInterval(dashboardRefreshTimer)
  if (dashboardAutoRefresh.value) dashboardRefreshTimer = window.setInterval(() => { loadDashboard() }, 60000)
}
async function createShare() {
  const payload = selectedResult.value ? { result_id: selectedResult.value, expires_days: shareExpiresDays.value, password: sharePassword.value, allow_download: shareAllowDownload.value, allow_comments: shareAllowComments.value } : selectedDashboard.value ? { dashboard_id: selectedDashboard.value, expires_days: shareExpiresDays.value, password: sharePassword.value, allow_download: shareAllowDownload.value, allow_comments: shareAllowComments.value } : null
  if (!payload) return ElMessage.warning('请选择分析结果或仪表盘')
  const { data } = await api.createShare(payload)
  shareToken.value = data.token
  const shared = (await api.getShared(data.token, sharePassword.value || undefined)).data
  shareComments.value = shared.comments || []
  ElMessage.success(data.expires_at ? `分享已创建，将于 ${data.expires_at} 过期` : '分享链接已创建，当前为本地模拟地址')
}
async function revokeCurrentShare() { if (!shareToken.value) return; await api.revokeShare(shareToken.value); shareToken.value = ''; shareComments.value = []; ElMessage.success('分享链接已撤销') }
async function loadScheduleRuns(id: string) { scheduleRuns.value[id] = (await api.listScheduleRuns(id)).data }
async function submitComment() { if (!shareToken.value || !comment.value.trim()) return; await api.addComment(shareToken.value, { author_name: '当前用户', content: comment.value }, sharePassword.value || undefined); comment.value = ''; shareComments.value = (await api.getShared(shareToken.value, sharePassword.value || undefined)).data.comments || [] }
async function createSchedule() {
  await api.createSchedule({ name: scheduleName.value, schedule_text: scheduleText.value, question: scheduleQuestion.value, job_type: scheduleType.value, source_path: scheduleSourcePath.value, source_url: scheduleSourceUrl.value, dataset_ids: scheduleDatasetIds.value, recipients: scheduleRecipients.value.split(/[,，、]/).map((x) => x.trim()).filter(Boolean), report_template: reportTemplate.value, notification_channel: notificationChannel.value, webhook_url: webhookUrl.value })
  ElMessage.success('调度任务已创建，邮件仅模拟记录')
  await refresh()
}
async function runSchedule(id: string) {
  try {
    const { data } = await api.runSchedule(id)
    ElMessage[data.status === 'success' ? 'success' : 'error'](data.status === 'success' ? '任务执行成功，邮件仅模拟记录' : (data.error || '任务执行失败'))
    await refresh()
  } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '任务执行失败') }
}
async function retryScheduleRun(jobId: string, runId: number) {
  try { await api.retryScheduleRun(jobId, runId); await loadScheduleRuns(jobId); await refresh(); ElMessage.success('失败任务已重新执行') }
  catch (error: any) { ElMessage.error(error?.response?.data?.detail || '重试失败') }
}
async function toggleSchedule(job: api.Schedule) {
  try { await api.toggleSchedule(job.id, !job.enabled); await refresh() }
  catch (error: any) { ElMessage.error(error?.response?.data?.detail || '任务状态更新失败') }
}
async function removeSchedule(id: string) {
  try { await api.deleteSchedule(id); await refresh() }
  catch (error: any) { ElMessage.error(error?.response?.data?.detail || '任务删除失败') }
}

onMounted(async () => { await kb.fetchDocuments(); await kb.fetchMounted(); await refresh(); await loadWorkspaces(); try { dataSourceTypes.value = (await api.listDataSourceTypes()).data } catch { /* 登录态失效时由全局请求拦截器处理 */ } try { savedDataSources.value = JSON.parse(localStorage.getItem('data-source-templates') || '[]') } catch { savedDataSources.value = [] } })
onBeforeUnmount(() => { if (dashboardRefreshTimer) window.clearInterval(dashboardRefreshTimer) })
</script>

<template>
  <section class="workbench-view">
    <header class="workbench-head"><div><h1>分析工作台</h1><p>关联数据、配置仪表盘、协作分享和自动化任务</p></div><div class="head-actions"><el-button size="small" @click="router.push('/')">返回数据分析</el-button><el-button size="small" @click="refresh">刷新</el-button></div></header>
    <el-tabs v-model="activeTab" class="workbench-tabs">
      <el-tab-pane label="多数据集关联" name="relations">
        <div class="tool-grid">
          <section class="tool-section relation-builder"><h2>创建关联并生成文件</h2><el-input v-model="relationName" placeholder="关联名称" />
            <el-select v-model="selectedDatasets" multiple placeholder="选择至少两个表格" class="full-control"><el-option v-for="d in tableDocs" :key="d.doc_id" :label="d.filename" :value="d.doc_id" /></el-select>
            <div class="field-row"><el-input v-model="leftKey" :placeholder="selectedFirst?.filename + ' 的关联字段'" /><el-input v-model="rightKey" :placeholder="selectedSecond?.filename + ' 的关联字段'" /></div>
            <el-input v-model="relationInstruction" type="textarea" :rows="3" placeholder="可选：对关联结果执行分析，例如按地区汇总销售额" /><el-button type="primary" :loading="relationBusy" @click="createAndExecuteRelation">{{ relationBusy ? '正在创建并生成...' : '创建关联并生成文件' }}</el-button><div v-if="relationResult" class="result-note">已生成：{{ relationResult.filename }}。可到文件夹中挂载，也可在数据分析区域继续使用。</div>
          </section>
          <section class="tool-section"><h2>已保存关联</h2><el-empty v-if="!relations.length" description="暂无关联配置" :image-size="50" /><div v-for="item in relations" :key="item.id" class="list-row"><div><strong>{{ item.name }}</strong><small>{{ item.dataset_ids.length }} 个数据集</small></div><div><el-button size="small" @click="previewRelation(item.id)">预览</el-button><el-button size="small" text type="danger" @click="removeRelation(item.id)">删除</el-button></div></div></section>
        </div><section v-if="preview" class="preview-section"><h2>{{ preview.name }} · 关联预览（{{ preview.rows }} 行）</h2><table class="data-table"><thead><tr><th v-for="c in preview.table.columns" :key="c">{{ c }}</th></tr></thead><tbody><tr v-for="(row, i) in preview.table.rows.slice(0, 20)" :key="i"><td v-for="(cell, j) in row" :key="j">{{ cell }}</td></tr></tbody></table></section>
      </el-tab-pane>
      <el-tab-pane label="结果中心" name="results">
        <div class="results-layout">
          <section class="tool-section result-list-section">
            <div class="section-head"><h2>历史分析结果</h2><el-button size="small" @click="refresh">刷新</el-button></div>
            <el-empty v-if="!results.length" description="暂无分析结果，请先在数据分析页完成一次分析" :image-size="50" />
            <div v-for="item in results" :key="item.id" class="result-row" :class="{ active: selectedResult === item.id }" @click="selectedResult = item.id">
              <div class="result-row-main"><strong>{{ item.title || item.question || '未命名分析' }}</strong><small>{{ item.created_at || '未知时间' }} · {{ item.status === 'error' ? '执行失败' : '已完成' }}</small></div>
              <div class="result-row-actions" @click.stop><el-button size="small" text @click="toggleResultFavorite(item)">{{ item.is_favorite ? '取消收藏' : '收藏' }}</el-button><el-button size="small" text @click="renameResult(item)">重命名</el-button><el-button size="small" text @click="copyResult(item)">复制</el-button><el-button size="small" text @click="askAgain(item)">再次提问</el-button><el-button size="small" @click="retryResult(item.id)">重新执行</el-button><el-button size="small" text type="danger" @click="removeResult(item.id)">删除</el-button></div>
            </div>
          </section>
          <section class="tool-section result-detail-section">
            <template v-if="selectedResultItem">
              <div class="section-head"><h2>结果详情</h2><el-button size="small" @click="selectedResult = null">关闭</el-button></div>
              <div class="detail-toolbar"><strong>{{ selectedResultItem.title || selectedResultItem.question }}</strong><el-button size="small" text @click="toggleResultFavorite(selectedResultItem)">{{ selectedResultItem.is_favorite ? '取消收藏' : '收藏' }}</el-button></div>
              <ChatMessage role="assistant" text="" :result="selectedResultItem" />
            </template>
            <el-empty v-else description="选择一条结果查看详情" :image-size="50" />
          </section>
        </div>
      </el-tab-pane>
      <el-tab-pane label="仪表盘与分享" name="dashboards">
        <div class="tool-grid"><section class="tool-section"><h2>仪表盘</h2><el-button type="primary" size="small" @click="createDashboard">新建仪表盘</el-button><el-select v-model="selectedDashboard" class="full-control" placeholder="选择仪表盘" @change="loadDashboard"><el-option v-for="d in dashboards" :key="d.id" :label="d.name" :value="d.id" /></el-select><div v-if="dashboardDetail" class="dashboard-filters"><el-date-picker v-model="dashboardDateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" /><el-select v-model="dashboardDimension" clearable placeholder="维度筛选"><el-option v-for="column in dashboardDimensionColumns" :key="column" :label="column" :value="column" /></el-select><el-select v-model="dashboardDimensionValue" clearable placeholder="维度值"><el-option v-for="value in dashboardDimensionOptions" :key="value" :label="value" :value="value" /></el-select><el-switch v-model="dashboardAutoRefresh" active-text="每分钟自动刷新" @change="toggleDashboardAutoRefresh" /></div><el-select v-model="dashboardDatasets" multiple class="full-control" placeholder="仪表盘读取的文件"><el-option v-for="d in tableDocs" :key="d.doc_id" :label="d.filename" :value="d.doc_id" /></el-select><el-input v-model="dashboardInstruction" type="textarea" :rows="4" placeholder="自定义指令，例如：按月份统计销售额并生成折线图" /><el-button type="primary" :loading="dashboardBusy" @click="runDashboardInstruction">{{ dashboardBusy ? '正在执行中...' : '执行自定义指令' }}</el-button><el-divider content-position="left">关键指标</el-divider><div v-if="dashboardKpis.length" class="kpi-grid"><div v-for="kpi in dashboardKpis" :key="kpi.label" class="kpi-card"><small>{{ kpi.label }}</small><strong>{{ typeof kpi.value === 'number' ? kpi.value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : kpi.value }}</strong><span>{{ kpi.suffix }}</span></div></div><el-divider content-position="left">已有分析结果</el-divider><el-button @click="addToDashboard">加入仪表盘</el-button><el-select v-model="chartType" class="full-control" placeholder="图表配置"><el-option label="使用原始图表" value="original" /><el-option label="折线图" value="line" /><el-option label="柱状图" value="bar" /><el-option label="饼图" value="pie" /></el-select><div v-if="dashboardDetail" class="dashboard-items"><div v-for="item in filteredDashboardItems" :key="item.id" class="dashboard-card"><div class="dashboard-card-head"><h3>{{ item.title }}</h3><div class="dashboard-card-actions"><el-button size="small" text @click="moveDashboardItem(item.id, -1)">上移</el-button><el-button size="small" text @click="moveDashboardItem(item.id, 1)">下移</el-button><el-button size="small" text type="danger" @click="removeDashboardItem(item.id)">删除</el-button></div></div><ChatMessage v-if="item.result" role="assistant" text="" :result="item.result" :chart-config="item.chart_config" /></div><el-empty v-if="!filteredDashboardItems.length" description="筛选后暂无项目" :image-size="40" /></div></section><section class="tool-section"><h2>分享与评论</h2><el-input-number v-model="shareExpiresDays" :min="1" :max="365" size="small" /><el-input v-model="sharePassword" type="password" show-password placeholder="可选分享密码" /><el-switch v-model="shareAllowDownload" active-text="允许下载" /><el-switch v-model="shareAllowComments" active-text="允许评论" /><el-button type="primary" @click="createShare">创建本地分享链接</el-button><div v-if="shareToken" class="share-box"><code>/shared/{{ shareToken }}</code><p>当前为本地分享，外部用户需在同一服务中访问。</p><el-button size="small" text type="danger" @click="revokeCurrentShare">撤销分享</el-button><div v-for="item in shareComments" :key="item.id" class="comment"><strong>{{ item.author_name }}</strong>：{{ item.content }}</div><el-input v-model="comment" placeholder="添加评论" @keyup.enter="submitComment"><template #append><el-button @click="submitComment">发送</el-button></template></el-input></div></section></div>
      </el-tab-pane>
      <el-tab-pane label="团队共享" name="team-sharing">
        <div class="tool-grid"><section class="tool-section"><h2>共享仪表盘</h2><p class="muted">团队成员可以查看共享仪表盘，只有创建者可以编辑、删除或执行分析。</p><el-select v-model="selectedDashboard" class="full-control" placeholder="选择仪表盘" @change="loadDashboard"><el-option v-for="d in dashboards.filter((item: any) => !item.read_only)" :key="d.id" :label="d.name" :value="d.id" /></el-select><el-select v-model="selectedWorkspace" class="full-control" placeholder="选择团队"><el-option v-for="workspace in workspaces" :key="workspace.id" :label="workspace.name + '（' + workspace.role + '）'" :value="workspace.id" /></el-select><div class="inline-actions"><el-button type="primary" @click="shareCurrentDashboard">共享给团队</el-button><el-button plain @click="unshareCurrentDashboard">取消共享</el-button></div></section><section class="tool-section"><h2>共享状态</h2><el-empty v-if="!dashboardDetail" description="选择仪表盘查看共享状态" :image-size="50" /><template v-else><el-alert v-if="dashboardDetail.workspace_id" title="当前仪表盘已共享" type="success" :closable="false" /><el-alert v-else title="当前仪表盘未共享" type="info" :closable="false" /><p v-if="dashboardDetail.workspace_id" class="muted">团队 ID：{{ dashboardDetail.workspace_id }}</p></template></section></div>
      </el-tab-pane>
      <el-tab-pane label="定时任务" name="schedules">
        <div class="tool-grid"><section class="tool-section"><h2>新建自动任务</h2><el-input v-model="scheduleName" placeholder="任务名称" /><el-select v-model="scheduleType" class="full-control"><el-option label="定时分析" value="analysis" /><el-option label="定时上传" value="upload" /><el-option label="定时同步 API/Webhook" value="sync" /></el-select><el-input v-model="scheduleText" placeholder="every 15m / every 2h / daily 09:00" /><el-input v-if="scheduleType === 'analysis'" v-model="scheduleQuestion" type="textarea" :rows="3" placeholder="分析问题" /><el-select v-if="scheduleType === 'analysis'" v-model="scheduleDatasetIds" multiple class="full-control" placeholder="选择定时分析数据集"><el-option v-for="d in tableDocs" :key="d.doc_id" :label="d.filename" :value="d.doc_id" /></el-select><el-input v-if="scheduleType === 'upload'" v-model="scheduleSourcePath" placeholder="本机源文件完整路径（模拟定时上传）" /><el-input v-if="scheduleType === 'sync'" v-model="scheduleSourceUrl" placeholder="API/Webhook CSV 或 JSON 地址" /><el-input v-model="scheduleRecipients" placeholder="收件人，用逗号分隔" /><el-select v-model="reportTemplate" class="full-control" placeholder="报告模板"><el-option label="日报" value="日报" /><el-option label="周报" value="周报" /><el-option label="月报" value="月报" /><el-option label="经营分析报告" value="经营分析报告" /></el-select><el-select v-model="notificationChannel" class="full-control" placeholder="通知渠道"><el-option label="邮件 SMTP" value="email" /><el-option label="Webhook" value="webhook" /><el-option label="飞书" value="feishu" /><el-option label="钉钉" value="dingtalk" /><el-option label="企业微信" value="wecom" /></el-select><el-input v-if="notificationChannel !== 'email'" v-model="webhookUrl" placeholder="Webhook 地址" /><el-button type="primary" @click="createSchedule">创建任务</el-button></section><section class="tool-section"><h2>任务列表</h2><el-empty v-if="!schedules.length" description="暂无定时任务" :image-size="50" /><div v-for="job in schedules" :key="job.id" class="list-row"><div><strong>{{ job.name }}</strong><small>{{ job.schedule_text }} · {{ job.next_run_at || '未排期' }}</small></div><div><el-switch :model-value="job.enabled" @change="toggleSchedule(job)" /><el-button size="small" @click="runSchedule(job.id)">立即执行</el-button><el-button size="small" text @click="loadScheduleRuns(job.id)">查看记录</el-button><div v-if="scheduleRuns[job.id]" class="schedule-runs"><div v-for="run in scheduleRuns[job.id]" :key="run.id"><span>{{ run.created_at }} · {{ run.status }}</span><small v-if="run.error">{{ run.error }}</small><el-button v-if="run.status === 'failed'" size="small" text @click="retryScheduleRun(job.id, run.id)">重试</el-button></div></div><el-button size="small" text type="danger" @click="removeSchedule(job.id)">删除</el-button></div></div></section></div>
      </el-tab-pane>
      <el-tab-pane label="数据源" name="data-sources">
        <div class="tool-grid"><section class="tool-section"><h2>连接外部数据库</h2><el-select v-model="dataSourceType" class="full-control" placeholder="选择数据库类型"><el-option v-for="item in dataSourceTypes" :key="item.type" :label="item.label" :value="item.type" /></el-select><el-input v-model="dataSourceForm.host" placeholder="主机地址" /><el-input-number v-model="dataSourceForm.port" :min="1" :max="65535" class="full-control" /><el-input v-model="dataSourceForm.database" placeholder="数据库名称" /><el-input v-model="dataSourceForm.username" placeholder="用户名" /><el-input v-model="dataSourceForm.password" type="password" show-password placeholder="密码" /><el-button type="primary" :loading="dataSourceBusy" @click="testDataSource">测试连接</el-button><p class="muted">测试连接只读取表结构，不会修改外部数据库。</p><el-divider content-position="left">数据库表操作</el-divider><el-select v-model="selectedDataSourceTable" class="full-control" placeholder="选择要分析的数据表" :disabled="!dataSourceResult"><el-option v-for="table in dataSourceResult?.tables || []" :key="table" :label="table" :value="table" /></el-select><div class="inline-actions"><el-button :loading="dataSourcePreviewBusy" :disabled="!dataSourceResult" @click="previewDataSourceTable">预览数据</el-button><el-button type="primary" plain :loading="dataSourcePreviewBusy" :disabled="!dataSourceResult" @click="importDataSourceTable">导入为数据集</el-button></div><p class="muted">导入会读取最多 100,000 行并生成平台内 CSV 副本，之后可用于智能分析、关联和流水线。</p><el-divider content-position="left">HTTP 数据源快照</el-divider><el-select v-model="remoteSourceType" class="full-control"><el-option label="HTTP API" value="api" /><el-option label="Webhook 数据源" value="webhook" /></el-select><el-input v-model="remoteSourceName" placeholder="快照名称" /><el-input v-model="remoteSourceUrl" placeholder="CSV/JSON URL" /><el-button type="primary" plain :loading="remoteSourceBusy" @click="importRemoteDataSource">导入为数据集</el-button></section><section class="tool-section"><h2>连接结果</h2><el-empty v-if="!dataSourceResult" description="填写连接信息后测试" :image-size="50" /><template v-else><el-alert title="连接成功" type="success" :closable="false" /><p>数据库：{{ dataSourceResult.database }}</p><p>表数量：{{ dataSourceResult.table_count }}</p><el-tag v-for="table in dataSourceResult.tables" :key="table" class="table-tag">{{ table }}</el-tag><div v-if="dataSourcePreview" class="table-preview"><div class="preview-head"><strong>{{ dataSourcePreview.table }} 数据预览</strong><span>{{ dataSourcePreview.row_count_preview }} 行</span></div><el-table :data="dataSourcePreview.rows" max-height="300" size="small" border><el-table-column v-for="column in dataSourcePreview.columns" :key="column" :prop="column" :label="column" min-width="140" show-overflow-tooltip /></el-table></div></template></section></div>
      </el-tab-pane>
      <el-tab-pane label="连接模板" name="data-source-templates">
        <div class="tool-grid"><section class="tool-section"><h2>保存连接模板</h2><p class="muted">模板只保存连接地址和账号信息，不保存密码。使用时补充密码后再测试连接。</p><el-input v-model="dataSourceAlias" placeholder="模板名称，例如：生产 MySQL" /><el-button type="primary" @click="saveDataSourceTemplate">保存当前连接信息</el-button></section><section class="tool-section"><h2>已保存模板</h2><el-empty v-if="!savedDataSources.length" description="暂无连接模板" :image-size="50" /><div v-for="item in savedDataSources" :key="item.alias" class="list-row"><div><strong>{{ item.alias }}</strong><small>{{ item.type }} · {{ item.form.host }}:{{ item.form.port }}/{{ item.form.database }}</small></div><div><el-button size="small" @click="useDataSourceTemplate(item)">使用</el-button><el-button size="small" text type="danger" @click="removeDataSourceTemplate(item.alias)">删除</el-button></div></div></section></div>
      </el-tab-pane>
      <el-tab-pane label="预测分析" name="forecast">
        <div class="tool-grid">
          <section class="tool-section">
            <h2>趋势预测</h2>
            <p class="muted">选择日期字段和数值指标，基于历史趋势预测未来数据。预测结果只作为辅助判断，不会修改原始数据。</p>
            <el-select v-model="forecastDatasetId" class="full-control" placeholder="选择数据集" @change="prepareForecastFields">
              <el-option v-for="d in tableDocs" :key="d.doc_id" :label="d.filename" :value="d.doc_id" />
            </el-select>
            <el-select v-model="forecastDateColumn" class="full-control" placeholder="选择日期字段" :disabled="!forecastProfile">
              <el-option v-for="column in forecastDateOptions" :key="column" :label="column" :value="column" />
            </el-select>
            <el-select v-model="forecastValueColumn" class="full-control" placeholder="选择指标字段" :disabled="!forecastProfile">
              <el-option v-for="column in forecastValueOptions" :key="column" :label="column" :value="column" />
            </el-select>
            <div class="inline-actions">
              <span class="muted">预测周期</span>
              <el-input-number v-model="forecastHorizon" :min="1" :max="90" />
              <span class="muted">天</span>
            </div>
            <el-button type="primary" :loading="forecastBusy" @click="runForecast">{{ forecastBusy ? '正在预测...' : '开始预测' }}</el-button>
            <el-alert v-if="forecastProfile && !forecastDateOptions.length" title="没有识别到日期字段，请确认数据包含可解析的日期或时间列" type="warning" :closable="false" />
            <el-alert v-if="forecastProfile && !forecastValueOptions.length" title="没有识别到数值字段，请确认指标列为数字类型" type="warning" :closable="false" />
          </section>
          <section class="tool-section">
            <h2>预测结果</h2>
            <el-empty v-if="!forecastResult" description="选择数据集并开始预测" :image-size="50" />
            <template v-else>
              <el-alert :title="forecastResult.method + ' · 未来 ' + forecastResult.horizon + ' 天'" type="success" :closable="false" />
              <div class="quality-summary forecast-summary">
                <div><small>趋势方向</small><strong>{{ forecastResult.trend }}</strong></div>
                <div><small>有效样本</small><strong>{{ forecastResult.sample_count }}</strong><span>条</span></div>
                <div><small>日均变化</small><strong>{{ forecastResult.daily_change }}</strong></div>
              </div>
              <ChatMessage role="assistant" text="预测结果已生成，可结合上下界观察趋势变化。" :result="forecastResult" :allow-download="true" />
            </template>
          </section>
        </div>
      </el-tab-pane>
      <el-tab-pane label="数据质量" name="quality">
        <div class="tool-grid"><section class="tool-section"><h2>数据质量检查</h2><el-select v-model="qualityDatasetId" class="full-control" placeholder="选择数据集"><el-option v-for="d in tableDocs" :key="d.doc_id" :label="d.filename" :value="d.doc_id" /></el-select><el-button type="primary" :loading="qualityBusy" @click="inspectDatasetQuality">开始检查</el-button><p class="muted">检查空值、重复行、异常值、字段类型和基础统计，不会修改原始数据。</p><div v-if="qualityResult" class="quality-summary"><div><small>质量评分</small><strong>{{ qualityResult.quality_score }}</strong><span>/ 100</span></div><div><small>数据行数</small><strong>{{ qualityResult.rows.toLocaleString() }}</strong></div><div><small>字段数量</small><strong>{{ qualityResult.columns }}</strong></div></div></section><section class="tool-section"><h2>检查结果</h2><el-empty v-if="!qualityResult" description="选择数据集后开始检查" :image-size="50" /><template v-else><el-alert v-for="issue in qualityResult.issues" :key="issue.message" :title="issue.message" :type="issue.level === 'success' ? 'success' : issue.level === 'info' ? 'info' : 'warning'" :closable="false" class="quality-alert" /><el-table :data="qualityResult.columns_detail" size="small" border><el-table-column prop="name" label="字段" min-width="120" /><el-table-column prop="type" label="类型" min-width="100" /><el-table-column prop="missing" label="空值" width="70" /><el-table-column prop="missing_rate" label="空值率" width="85"><template #default="scope">{{ scope.row.missing_rate }}%</template></el-table-column><el-table-column prop="unique" label="唯一值" width="85" /><el-table-column label="统计" min-width="190"><template #default="scope"><span v-if="scope.row.mean != null">均值 {{ scope.row.mean }} · 范围 {{ scope.row.min }} ~ {{ scope.row.max }}</span><span v-else class="muted">非数值字段</span></template></el-table-column></el-table></template></section></div>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.workbench-view {
  height: 100%;
  overflow: auto;
  padding: 30px clamp(16px, 4vw, 46px);
  background: var(--bg);
}
.workbench-head {
  max-width: 1180px;
  margin: 0 auto 22px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
}
.head-actions { display: flex; gap: 8px; }
h1 { font-size: 26px; letter-spacing: -.2px; margin-bottom: 6px; }
p { color: var(--text-secondary); }
.workbench-tabs { max-width: 1180px; margin: 0 auto; }
.tool-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.muted { color: var(--text-secondary); font-size: 12px; }
.table-tag { margin: 0 6px 6px 0; }
.results-layout { display: grid; grid-template-columns: minmax(320px, .85fr) minmax(0, 1.4fr); gap: 18px; align-items: start; }
.tool-section, .preview-section {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 18px;
  box-shadow: 0 3px 12px rgba(35, 54, 50, .025);
}
.tool-section > .el-input,
.tool-section > .el-select,
.tool-section > .el-input-number,
.tool-section > .el-button,
.tool-section > .el-switch,
.tool-section > .el-date-editor {
  margin-bottom: 14px;
}
h2 { font-size: 16px; margin-bottom: 16px; }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.section-head h2 { margin-bottom: 0; }
.detail-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 14px 0 4px; }
.detail-toolbar strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-section h2::after {
  content: '';
  display: block;
  width: 24px;
  height: 2px;
  margin-top: 8px;
  border-radius: 2px;
  background: #9bb9b4;
}
.full-control, .tool-section .el-input, .tool-section .el-select { width: 100%; margin-bottom: 12px; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.inline-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.table-preview { margin-top: 16px; border-top: 1px solid var(--border-light); padding-top: 14px; }
.preview-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 10px; color: var(--text-secondary); }
.preview-head strong { color: var(--text); }
.list-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 13px 0;
  border-bottom: 1px solid var(--border-light);
}
.list-row:last-child { border-bottom: 0; }
.result-row { display: flex; flex-direction: column; align-items: stretch; gap: 10px; padding: 14px 10px; border-bottom: 1px solid var(--border-light); cursor: pointer; }
.result-row:hover, .result-row.active { background: var(--primary-light); }
.result-row-main { min-width: 0; flex: 1; }
.result-row-main strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.result-row-main small { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.result-row-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.result-row-actions .el-button { margin-left: 0; }
small { display: block; color: var(--text-tertiary); margin-top: 5px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 12px; overflow: hidden; border-radius: 6px; }
.data-table th, .data-table td { border-bottom: 1px solid var(--border-light); padding: 8px; text-align: left; }
.data-table th { color: var(--text-secondary); background: #f7f9f8; font-weight: 600; }
.result-note { padding: 11px 12px; margin-top: 12px; color: #3d6c61; background: var(--primary-soft); border: 1px solid #d7e5e2; border-radius: 6px; }
.share-box { margin-top: 14px; padding: 14px; background: #f7f9f8; border: 1px solid var(--border-light); border-radius: 6px; }
code { word-break: break-all; color: var(--primary); }
.comment { padding: 9px 0; border-bottom: 1px solid var(--border-light); font-size: 13px; }
.dashboard-card { padding: 14px 0; border-top: 1px solid var(--border-light); }
.dashboard-card h3 { margin-bottom: 10px; font-size: 14px; font-weight: 600; }
.dashboard-card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.dashboard-card-actions { display: flex; gap: 2px; flex-wrap: wrap; }
.dashboard-filters { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; align-items: center; margin: 4px 0 14px; }
.dashboard-filters .el-date-editor { width: 100%; grid-column: span 3; }
.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
.kpi-card { min-width: 0; padding: 12px; border: 1px solid var(--border-light); background: #f7f9f8; border-radius: 6px; }
.kpi-card small, .kpi-card span { display: block; color: var(--text-tertiary); font-size: 11px; }
.kpi-card strong { display: block; margin: 4px 0; font-size: 18px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; }
.quality-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
.quality-summary > div { min-width: 0; padding: 12px; border: 1px solid var(--border-light); background: #f7f9f8; border-radius: 6px; }
.quality-summary small { margin: 0 0 6px; }
.quality-summary strong { margin-right: 4px; font-size: 22px; color: var(--primary); }
.quality-alert { margin-bottom: 8px; }
.forecast-summary { margin: 14px 0 4px; }
.forecast-summary strong { font-size: 18px; }
@media (max-width: 850px) {
  .tool-grid { grid-template-columns: 1fr; }
  .results-layout { grid-template-columns: 1fr; }
  .workbench-view { padding: 20px 14px; }
  .field-row { grid-template-columns: 1fr; }
  .workbench-head { flex-direction: column; }
  .dashboard-filters { grid-template-columns: 1fr; }
  .dashboard-filters .el-date-editor { grid-column: auto; }
  .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .quality-summary { grid-template-columns: 1fr; }
}
</style>
