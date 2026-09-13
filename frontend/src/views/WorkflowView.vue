<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbStore } from '@/stores/kb'
import * as api from '@/api/workbench'
import ChatMessage from '@/components/ChatMessage.vue'

const kb = useKbStore()
const workflows = ref<api.Workflow[]>([])
const selectedId = ref('')
const detail = ref<api.Workflow | null>(null)
const runs = ref<any[]>([])
const runDetail = ref<any>(null)
const selectedDatasets = ref<string[]>([])
const busy = ref(false)
const dashboards = ref<api.Dashboard[]>([])
const parametersText = ref('{}')
const scheduleText = ref('daily 09:00')
const draggedIndex = ref<number | null>(null)
const tableDocs = computed(() => kb.documents.filter((d) => ['.csv', '.xlsx', '.xls'].includes(d.ext)))

const defaultSteps = [
  { step_type: 'clean', name: '数据清洗', config: { drop_duplicates: true, drop_empty_rows: true } },
  { step_type: 'relation', name: '多数据关联', config: { joins: [] } },
  { step_type: 'analysis', name: '智能分析', config: { instruction: '请总结数据中的主要趋势、异常和关键指标' } },
  { step_type: 'chart', name: '图表配置', config: { type: 'original' } },
  { step_type: 'report', name: '生成报告', config: { format: 'html' } },
]

async function refresh() {
  const [workflowResponse, dashboardResponse] = await Promise.all([api.listWorkflows(), api.listDashboards()])
  workflows.value = workflowResponse.data
  dashboards.value = dashboardResponse.data
  if (selectedId.value) await loadWorkflow(selectedId.value)
}
async function loadWorkflow(id: string) {
  selectedId.value = id
  detail.value = (await api.getWorkflow(id)).data
  runs.value = (await api.listWorkflowRuns(id)).data
}
async function createWorkflow() {
  const { value } = await ElMessageBox.prompt('输入流水线名称', '新建流水线', { inputValue: '每日经营分析流水线', confirmButtonText: '创建', cancelButtonText: '取消' })
  const { data } = await api.createWorkflow({ name: value, description: '上传、清洗、关联、分析、图表和报告', steps: defaultSteps })
  await refresh(); await loadWorkflow(data.id); ElMessage.success('流水线已创建')
}
async function saveWorkflow() {
  if (!detail.value) return
  await api.updateWorkflow(detail.value.id, { name: detail.value.name, description: detail.value.description, steps: (detail.value.steps || []).map((step) => ({ step_type: step.step_type, name: step.name, config: step.config, enabled: step.enabled !== false })) })
  await refresh(); ElMessage.success('流水线已保存')
}
async function runWorkflow() {
  if (!detail.value) return ElMessage.warning('请先选择流水线')
  if (!selectedDatasets.value.length) return ElMessage.warning('请选择输入文件')
  let parameters: Record<string, any>
  try { parameters = JSON.parse(parametersText.value || '{}') }
  catch { return ElMessage.warning('运行参数必须是合法 JSON') }
  busy.value = true
  try { const { data } = await api.runWorkflow(detail.value.id, { dataset_ids: selectedDatasets.value, parameters }); ElMessage[data.status === 'success' ? 'success' : 'error'](data.status === 'success' ? '流水线执行成功' : (data.error || '流水线执行失败')); await loadWorkflow(detail.value.id) }
  catch (error: any) { if (error?.response?.status == null || error.response.status < 500) ElMessage.error(error?.response?.data?.detail || '流水线执行失败') }
  finally { busy.value = false }
}
async function inspectRun(run: any) { runDetail.value = (await api.getWorkflowRun(run.id)).data }
async function retryRun(run: any) { busy.value = true; try { const { data } = await api.retryWorkflowRun(run.id); ElMessage[data.status === 'success' ? 'success' : 'error'](data.status === 'success' ? '重试成功' : (data.error || '重试失败')); await loadWorkflow(detail.value!.id) } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '重试失败') } finally { busy.value = false } }
async function removeWorkflow() { if (!detail.value) return; await api.deleteWorkflow(detail.value.id); selectedId.value = ''; detail.value = null; await refresh(); ElMessage.success('流水线已删除') }
function moveStep(index: number, delta: number) { if (!detail.value?.steps) return; const target = index + delta; if (target < 0 || target >= detail.value.steps.length) return; const [step] = detail.value.steps.splice(index, 1); detail.value.steps.splice(target, 0, step) }
function removeStep(index: number) { detail.value?.steps?.splice(index, 1) }
function addStep(type: string) { if (!detail.value) return; const presets: Record<string, any> = { clean: { name: '数据清洗', config: { drop_duplicates: true, drop_empty_rows: true } }, relation: { name: '多数据关联', config: { how: 'left', left_key: '', right_key: '' } }, analysis: { name: '智能分析', config: { instruction: '请总结数据中的主要趋势、异常和关键指标' } }, chart: { name: '图表配置', config: { type: 'original' } }, dashboard: { name: '保存到仪表盘', config: { dashboard_id: '', title: '流水线分析结果' } }, report: { name: '生成报告', config: { format: 'html' } } }; const preset = presets[type]; detail.value.steps = [...(detail.value.steps || []), { step_type: type, name: preset.name, config: preset.config, enabled: true }] }
function dropStep(index: number) { if (draggedIndex.value === null || !detail.value?.steps) return; moveStep(draggedIndex.value, index - draggedIndex.value); draggedIndex.value = null }
function downloadReport(run: any) { window.open(api.workflowReportUrl(run.id), '_blank') }
function openWorkflowReport(run: any) { window.open(api.workflowReportUrl(run.id), '_blank', 'noopener,noreferrer') }
async function scheduleWorkflow() { if (!detail.value || !selectedDatasets.value.length) return ElMessage.warning('请选择流水线输入文件'); let parameters = {}; try { parameters = JSON.parse(parametersText.value || '{}') } catch { return ElMessage.warning('运行参数必须是合法 JSON') } await api.createSchedule({ name: detail.value.name + ' 定时任务', job_type: 'workflow', workflow_id: detail.value.id, dataset_ids: selectedDatasets.value, parameters, schedule_text: scheduleText.value, recipients: [] }); ElMessage.success('定时流水线已创建') }
onMounted(async () => { await kb.fetchDocuments(); await refresh() })
</script>

<template>
  <section class="workflow-view">
    <header class="page-head"><div><h1>流水线工作流</h1><p>把清洗、关联、分析、图表、仪表盘和报告串成一次可重复执行的任务。</p></div><el-button type="primary" @click="createWorkflow">新建流水线</el-button></header>
    <div class="workflow-layout">
      <aside class="workflow-list"><div v-if="!workflows.length" class="empty">还没有流水线</div><button v-for="item in workflows" :key="item.id" :class="['workflow-item', { active: item.id === selectedId }]" @click="loadWorkflow(item.id)"><strong>{{ item.name }}</strong><small>{{ item.step_count }} 个步骤 · {{ item.updated_at }}</small></button></aside>
      <main class="workflow-editor" v-if="detail">
        <div class="editor-head"><div><el-input v-model="detail.name" class="name-input" /><el-input v-model="detail.description" class="description-input" placeholder="流水线说明" /></div><div class="editor-actions"><el-button @click="saveWorkflow">保存</el-button><el-button type="primary" :loading="busy" @click="runWorkflow">立即执行</el-button><el-button text type="danger" @click="removeWorkflow">删除</el-button></div></div>
        <div class="input-panel"><label>本次输入文件</label><el-select v-model="selectedDatasets" multiple class="wide" placeholder="选择 CSV 或 Excel 文件"><el-option v-for="doc in tableDocs" :key="doc.doc_id" :label="doc.filename" :value="doc.doc_id" /></el-select><label>运行参数 JSON（步骤配置可使用参数占位符）</label><el-input v-model="parametersText" type="textarea" :rows="2" placeholder='{"地区":"华东"}' /><div class="schedule-row"><el-input v-model="scheduleText" placeholder="daily 09:00 / every 2h" /><el-button @click="scheduleWorkflow">设为定时流水线</el-button></div></div>
        <div class="step-toolbar"><span>处理步骤</span><el-dropdown @command="addStep"><el-button size="small">添加步骤</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="clean">数据清洗</el-dropdown-item><el-dropdown-item command="relation">数据关联</el-dropdown-item><el-dropdown-item command="analysis">智能分析</el-dropdown-item><el-dropdown-item command="chart">图表配置</el-dropdown-item><el-dropdown-item command="dashboard">保存到仪表盘</el-dropdown-item><el-dropdown-item command="report">生成报告</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div>
        <div class="steps"><div v-for="(step, index) in detail.steps" :key="step.id || index" class="step-row" draggable="true" @dragstart="draggedIndex = index" @dragover.prevent @drop="dropStep(index)"><span class="step-index">{{ index + 1 }}</span><div class="step-body"><div class="step-title"><el-input v-model="step.name" /><el-switch v-model="step.enabled" active-text="启用" /><el-button text :disabled="index === 0" @click="moveStep(index, -1)">上移</el-button><el-button text :disabled="index === (detail.steps?.length || 0) - 1" @click="moveStep(index, 1)">下移</el-button><el-button text type="danger" @click="removeStep(index)">删除</el-button></div><div class="step-config" v-if="step.step_type === 'clean'"><el-checkbox v-model="step.config.drop_empty_rows">删除空行</el-checkbox><el-checkbox v-model="step.config.drop_duplicates">删除重复行</el-checkbox></div><div class="step-config" v-else-if="step.step_type === 'relation'"><el-input v-model="step.config.left_key" placeholder="左表关联字段（留空自动识别）" /><el-input v-model="step.config.right_key" placeholder="右表关联字段（留空自动识别）" /><el-select v-model="step.config.how"><el-option label="左连接" value="left" /><el-option label="内连接" value="inner" /><el-option label="全连接" value="outer" /></el-select></div><div class="step-config" v-else-if="step.step_type === 'analysis'"><el-input v-model="step.config.instruction" type="textarea" :rows="2" placeholder="分析指令，可使用 {{参数名}}" /></div><div class="step-config" v-else-if="step.step_type === 'chart'"><el-select v-model="step.config.type"><el-option label="自动" value="original" /><el-option label="柱状图" value="bar" /><el-option label="折线图" value="line" /><el-option label="饼图" value="pie" /></el-select></div><div class="step-config" v-else-if="step.step_type === 'dashboard'"><el-select v-model="step.config.dashboard_id" placeholder="选择目标仪表盘"><el-option v-for="board in dashboards" :key="board.id" :label="board.name" :value="board.id" /></el-select><el-input v-model="step.config.title" placeholder="仪表盘项目标题" /></div><div class="step-meta">{{ step.step_type }}</div></div><span v-if="index < (detail.steps?.length || 0) - 1" class="connector"></span></div></div>
        <section class="runs"><div class="section-title"><h2>执行记录</h2><el-button size="small" @click="loadWorkflow(detail!.id)">刷新</el-button></div><el-empty v-if="!runs.length" description="暂无执行记录" :image-size="45" /><div v-for="run in runs" :key="run.id" class="run-row"><div><strong :class="'status-' + run.status">{{ run.status === 'success' ? '成功' : run.status === 'failed' ? '失败' : '执行中' }}</strong><small>{{ run.started_at }} <span v-if="run.error">· {{ run.error }}</span></small></div><div><el-button size="small" @click="inspectRun(run)">查看步骤</el-button><el-button v-if="run.output?.outputs && Object.values(run.output.outputs).some((item: any) => item?.format === 'html')" size="small" @click="downloadReport(run)">下载报告</el-button><el-button v-if="run.status === 'failed'" size="small" type="warning" @click="retryRun(run)">从失败步骤重试</el-button></div></div></section>
      </main>
      <el-empty v-else description="请选择或新建一条流水线" :image-size="80" />
    </div>
    <el-drawer v-model="runDetail" title="流水线执行详情" size="min(780px, 92vw)"><div v-if="runDetail" class="run-detail"><div v-if="runDetail.error" class="run-error">{{ runDetail.error }}</div><div v-for="step in runDetail.steps" :key="step.step_id" class="detail-row"><div class="detail-head"><strong>{{ step.step_type }}</strong><span :class="'status-' + step.status">{{ step.status === 'success' ? '成功' : step.status === 'failed' ? '失败' : step.status }}</span></div><small v-if="step.error">{{ step.error }}</small><ChatMessage v-if="step.output?.result" role="assistant" text="" :result="step.output.result" :allow-download="true" /><pre v-else-if="step.output && step.step_type !== 'report'" class="step-output">{{ JSON.stringify(step.output, null, 2) }}</pre></div><el-button v-if="runDetail.output?.outputs && Object.values(runDetail.output.outputs).some((item: any) => item?.format === 'html')" type="primary" plain @click="openWorkflowReport(runDetail)">打开可读报告</el-button></div></el-drawer>
  </section>
</template>

<style scoped>
.workflow-view { height: 100%; overflow: auto; padding: 30px clamp(16px, 4vw, 46px); background: var(--bg); }
.page-head, .workflow-layout { max-width: 1180px; margin: 0 auto; }
.page-head { display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:22px; }
h1 { margin:0 0 6px; font-size:26px; } p, small { color:var(--text-secondary); } .workflow-layout { display:grid; grid-template-columns:250px minmax(0,1fr); gap:18px; min-height:520px; }
.workflow-list, .workflow-editor { background:var(--bg-card); border:1px solid var(--border-light); border-radius:8px; padding:16px; } .workflow-item { width:100%; display:block; text-align:left; border:0; background:transparent; padding:13px 12px; border-radius:6px; cursor:pointer; color:var(--text); } .workflow-item.active { background:var(--primary-light); color:var(--primary); } .workflow-item small { display:block; margin-top:5px; font-size:12px; } .empty { color:var(--text-tertiary); padding:30px 10px; text-align:center; }
.editor-head { display:flex; justify-content:space-between; gap:18px; align-items:flex-start; border-bottom:1px solid var(--border-light); padding-bottom:16px; } .name-input { max-width:360px; font-weight:600; } .description-input { max-width:520px; margin-top:8px; } .editor-actions { display:flex; gap:7px; } .input-panel { display:grid; gap:9px; margin:18px 0; padding:14px; background:#f7f9f8; border:1px solid var(--border-light); border-radius:6px; } label { display:block; font-size:13px; color:var(--text-secondary); } .wide { width:100%; } .schedule-row { display:grid; grid-template-columns:1fr auto; gap:8px; }
.step-toolbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; font-weight:600; }
.steps { padding:8px 0 18px; } .step-row { position:relative; display:flex; gap:12px; min-height:68px; cursor:grab; } .step-index { display:grid; place-items:center; width:28px; height:28px; flex:0 0 28px; border-radius:50%; background:var(--primary-soft); color:var(--primary); font-weight:600; z-index:1; } .step-body { flex:1; border:1px solid var(--border-light); border-radius:6px; padding:10px 12px; } .step-title { display:flex; gap:8px; align-items:center; flex-wrap:wrap; } .step-title .el-input { max-width:300px; } .step-config { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:10px; } .step-config .el-select { width:100%; } .step-meta { color:var(--text-tertiary); font-size:12px; margin-top:6px; } .connector { position:absolute; left:13px; top:28px; bottom:0; border-left:1px dashed #b9ccc8; }
.runs { border-top:1px solid var(--border-light); padding-top:16px; } .section-title, .run-row { display:flex; justify-content:space-between; gap:12px; align-items:center; } .run-row { padding:12px 0; border-bottom:1px solid var(--border-light); } .run-row small { display:block; margin-top:4px; } .status-success { color:#3c8c62; } .status-failed { color:#d45a5a; } .status-running { color:#bc8124; } .run-detail { display:grid; gap:10px; } .detail-row { display:grid; grid-template-columns:1fr auto; gap:6px; padding:10px; border:1px solid var(--border-light); border-radius:6px; } .detail-row small { grid-column:1 / -1; color:#c24d4d; }
.detail-head { display:flex; justify-content:space-between; gap:10px; } .step-output { grid-column:1 / -1; margin:4px 0 0; white-space:pre-wrap; overflow:auto; background:#f6f9f8; padding:10px; border-radius:6px; font-size:12px; } .run-error { padding:10px 12px; color:#b74444; background:#fff1f1; border:1px solid #ffd2d2; border-radius:6px; }
@media (max-width: 800px) { .workflow-layout { grid-template-columns:1fr; } .page-head, .editor-head { flex-direction:column; } .editor-actions { flex-wrap:wrap; } }
</style>
