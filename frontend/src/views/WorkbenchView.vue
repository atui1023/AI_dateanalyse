<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbStore } from '@/stores/kb'
import * as api from '@/api/workbench'
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
const dashboardDetail = ref<any>(null)
const shareToken = ref('')
const shareComments = ref<any[]>([])
const comment = ref('')
const scheduleName = ref('每日分析报告')
const scheduleText = ref('daily 09:00')
const scheduleQuestion = ref('请生成今日数据分析报告')
const scheduleRecipients = ref('demo@example.com')
const scheduleDatasetIds = ref<string[]>([])

const tableDocs = computed(() => kb.documents.filter((d) => ['.csv', '.xlsx', '.xls'].includes(d.ext)))
const selectedFirst = computed(() => tableDocs.value.find((d) => d.doc_id === selectedDatasets.value[0]))
const selectedSecond = computed(() => tableDocs.value.find((d) => d.doc_id === selectedDatasets.value[1]))

async function refresh() {
  const [r, a, d, s] = await Promise.all([api.listRelations(), api.listResults(), api.listDashboards(), api.listSchedules()])
  relations.value = r.data
  results.value = a.data
  dashboards.value = d.data
  schedules.value = s.data
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
async function loadDashboard() { if (selectedDashboard.value) dashboardDetail.value = (await api.getDashboard(selectedDashboard.value)).data }
async function createShare() {
  const payload = selectedResult.value ? { result_id: selectedResult.value } : selectedDashboard.value ? { dashboard_id: selectedDashboard.value } : null
  if (!payload) return ElMessage.warning('请选择分析结果或仪表盘')
  const { data } = await api.createShare(payload)
  shareToken.value = data.token
  const shared = (await api.getShared(data.token)).data
  shareComments.value = shared.comments || []
  ElMessage.success('分享链接已创建，当前为本地模拟地址')
}
async function submitComment() { if (!shareToken.value || !comment.value.trim()) return; await api.addComment(shareToken.value, { author_name: '当前用户', content: comment.value }); comment.value = ''; shareComments.value = (await api.getShared(shareToken.value)).data.comments || [] }
async function createSchedule() {
  await api.createSchedule({ name: scheduleName.value, schedule_text: scheduleText.value, question: scheduleQuestion.value, job_type: scheduleType.value, source_path: scheduleSourcePath.value, dataset_ids: scheduleDatasetIds.value, recipients: scheduleRecipients.value.split(/[,，、]/).map((x) => x.trim()).filter(Boolean) })
  ElMessage.success('调度任务已创建，邮件仅模拟记录')
  await refresh()
}
async function runSchedule(id: string) { const { data } = await api.runSchedule(id); ElMessage.success(data.status === 'success' ? '模拟任务执行成功' : '模拟任务执行失败') ; await refresh() }
async function toggleSchedule(job: api.Schedule) { await api.toggleSchedule(job.id, !job.enabled); await refresh() }
async function removeSchedule(id: string) { await api.deleteSchedule(id); await refresh() }

onMounted(async () => { await kb.fetchDocuments(); await refresh() })
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
      <el-tab-pane label="仪表盘与分享" name="dashboards">
        <div class="tool-grid"><section class="tool-section"><h2>仪表盘</h2><el-button type="primary" size="small" @click="createDashboard">新建仪表盘</el-button><el-select v-model="selectedDashboard" class="full-control" placeholder="选择仪表盘" @change="loadDashboard"><el-option v-for="d in dashboards" :key="d.id" :label="d.name" :value="d.id" /></el-select><el-select v-model="dashboardDatasets" multiple class="full-control" placeholder="仪表盘读取的文件"><el-option v-for="d in tableDocs" :key="d.doc_id" :label="d.filename" :value="d.doc_id" /></el-select><el-input v-model="dashboardInstruction" type="textarea" :rows="4" placeholder="自定义指令，例如：按月份统计销售额并生成折线图" /><el-button type="primary" :loading="dashboardBusy" @click="runDashboardInstruction">{{ dashboardBusy ? '正在执行中...' : '执行自定义指令' }}</el-button><el-divider content-position="left">已有分析结果</el-divider><el-button @click="addToDashboard">加入仪表盘</el-button><el-select v-model="chartType" class="full-control" placeholder="图表配置"><el-option label="使用原始图表" value="original" /><el-option label="折线图" value="line" /><el-option label="柱状图" value="bar" /><el-option label="饼图" value="pie" /></el-select><div v-if="dashboardDetail" class="dashboard-items"><div v-for="item in dashboardDetail.items" :key="item.id" class="dashboard-card"><h3>{{ item.title }}</h3><ChatMessage v-if="item.result" role="assistant" text="" :result="item.result" /></div><el-empty v-if="!dashboardDetail.items.length" description="暂无项目" :image-size="40" /></div></section><section class="tool-section"><h2>分享与评论</h2><el-button type="primary" @click="createShare">创建本地分享链接</el-button><div v-if="shareToken" class="share-box"><code>/shared/{{ shareToken }}</code><p>当前为本地分享，外部用户需在同一服务中访问。</p><div v-for="item in shareComments" :key="item.id" class="comment"><strong>{{ item.author_name }}</strong>：{{ item.content }}</div><el-input v-model="comment" placeholder="添加评论" @keyup.enter="submitComment"><template #append><el-button @click="submitComment">发送</el-button></template></el-input></div></section></div>
      </el-tab-pane>
      <el-tab-pane label="定时任务" name="schedules">
        <div class="tool-grid"><section class="tool-section"><h2>新建自动任务</h2><el-input v-model="scheduleName" placeholder="任务名称" /><el-select v-model="scheduleType" class="full-control"><el-option label="定时分析" value="analysis" /><el-option label="定时上传" value="upload" /></el-select><el-input v-model="scheduleText" placeholder="every 15m / every 2h / daily 09:00" /><el-input v-if="scheduleType === 'analysis'" v-model="scheduleQuestion" type="textarea" :rows="3" placeholder="分析问题" /><el-select v-if="scheduleType === 'analysis'" v-model="scheduleDatasetIds" multiple class="full-control" placeholder="选择定时分析数据集"><el-option v-for="d in tableDocs" :key="d.doc_id" :label="d.filename" :value="d.doc_id" /></el-select><el-input v-if="scheduleType === 'upload'" v-model="scheduleSourcePath" placeholder="本机源文件完整路径（模拟定时上传）" /><el-input v-model="scheduleRecipients" placeholder="模拟收件人，用逗号分隔" /><el-button type="primary" @click="createSchedule">创建任务</el-button></section><section class="tool-section"><h2>任务列表</h2><el-empty v-if="!schedules.length" description="暂无定时任务" :image-size="50" /><div v-for="job in schedules" :key="job.id" class="list-row"><div><strong>{{ job.name }}</strong><small>{{ job.schedule_text }} · {{ job.next_run_at || '未排期' }}</small></div><div><el-switch :model-value="job.enabled" @change="toggleSchedule(job)" /><el-button size="small" @click="runSchedule(job.id)">模拟执行</el-button><el-button size="small" text type="danger" @click="removeSchedule(job.id)">删除</el-button></div></div></section></div>
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
.tool-section, .preview-section {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 18px;
  box-shadow: 0 3px 12px rgba(35, 54, 50, .025);
}
h2 { font-size: 16px; margin-bottom: 16px; }
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
.list-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 13px 0;
  border-bottom: 1px solid var(--border-light);
}
.list-row:last-child { border-bottom: 0; }
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
@media (max-width: 850px) {
  .tool-grid { grid-template-columns: 1fr; }
  .workbench-view { padding: 20px 14px; }
  .field-row { grid-template-columns: 1fr; }
  .workbench-head { flex-direction: column; }
}
</style>
