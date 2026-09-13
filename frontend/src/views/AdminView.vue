<script setup lang="ts">
import { ref, onMounted } from 'vue'
import * as adminApi from '@/api/admin'
import type { AdminUser, AuditLog, AdminMetrics } from '@/api/admin'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Key, Delete } from 'lucide-vue-next'

const activeTab = ref<'users' | 'logs' | 'metrics'>('users')
const users = ref<AdminUser[]>([])
const logs = ref<AuditLog[]>([])
const loading = ref(false)
const metrics = ref<AdminMetrics | null>(null)

// 创建用户表单
const createForm = ref({
  username: '',
  password: '',
  display_name: '',
  role: 'user' as 'user' | 'admin',
})

onMounted(() => {
  loadUsers()
})

async function loadUsers() {
  loading.value = true
  try {
    const { data } = await adminApi.listUsers()
    users.value = data
  } finally {
    loading.value = false
  }
}

async function loadLogs() {
  loading.value = true
  try {
    const { data } = await adminApi.listAuditLogs(200)
    logs.value = data
  } finally {
    loading.value = false
  }
}

async function loadMetrics() {
  loading.value = true
  try {
    const { data } = await adminApi.getMetrics()
    metrics.value = data
  } finally {
    loading.value = false
  }
}

function switchTab(tab: 'users' | 'logs' | 'metrics') {
  activeTab.value = tab
  if (tab === 'users' && users.value.length === 0) loadUsers()
  if (tab === 'logs' && logs.value.length === 0) loadLogs()
  if (tab === 'metrics') loadMetrics()
}

async function handleCreate() {
  const f = createForm.value
  if (!f.username || !f.password) {
    ElMessage.warning('用户名和密码必填')
    return
  }
  await adminApi.createUser(f.username, f.password, f.display_name || undefined, f.role)
  ElMessage.success('已创建')
  f.username = ''
  f.password = ''
  f.display_name = ''
  f.role = 'user'
  await loadUsers()
}

async function handleResetPassword(user: AdminUser) {
  const { value } = await ElMessageBox.prompt(`重置 ${user.username} 的密码`, '重置密码', {
    inputType: 'password',
    inputPlaceholder: '输入新密码',
    confirmButtonText: '重置',
    cancelButtonText: '取消',
  })
  if (value) {
    await adminApi.resetPassword(user.id, value)
    ElMessage.success('密码已重置')
  }
}

async function handleDeleteUser(user: AdminUser) {
  await ElMessageBox.confirm(
    `删除用户 ${user.username}？其所有数据（知识库/会话/分析结果）将级联删除，不可恢复。`,
    '删除用户',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  await adminApi.deleteUser(user.id)
  ElMessage.success('已删除')
  await loadUsers()
}

function formatTime(t: string) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}
</script>

<template>
  <div class="admin-view">
    <div class="admin-header">
      <h3>用户与日志管理</h3>
    </div>
    <el-tabs v-model="activeTab" @tab-change="switchTab">
      <!-- 用户管理 -->
      <el-tab-pane label="用户管理" name="users">
        <div class="create-form">
          <el-input v-model="createForm.username" placeholder="用户名" style="width: 140px" />
          <el-input v-model="createForm.password" type="password" placeholder="密码" show-password style="width: 140px" />
          <el-input v-model="createForm.display_name" placeholder="显示名(选填)" style="width: 140px" />
          <el-select v-model="createForm.role" style="width: 100px">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
          <el-button type="primary" @click="handleCreate">
            <el-icon><Plus /></el-icon> 创建
          </el-button>
        </div>
        <el-table :data="users" v-loading="loading" size="small" style="width: 100%">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户名" width="140" />
          <el-table-column prop="display_name" label="显示名" width="140" />
          <el-table-column label="角色" width="100">
            <template #default="{ row }">
              <el-tag :type="row.role === 'admin' ? 'warning' : 'info'" size="small">
                {{ row.role === 'admin' ? '管理员' : '用户' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="140">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <el-button size="small" text @click="handleResetPassword(row)">
                <el-icon><Key /></el-icon> 重置密码
              </el-button>
              <el-button
                size="small"
                text
                type="danger"
                :disabled="row.username === 'admin' || row.id === 1"
                @click="handleDeleteUser(row)"
              >
                <el-icon><Delete /></el-icon> 删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 审计日志 -->
      <el-tab-pane label="审计日志" name="logs">
        <el-button size="small" @click="loadLogs" style="margin-bottom: 8px">刷新</el-button>
        <el-table :data="logs" v-loading="loading" size="small" style="width: 100%" max-height="500">
          <el-table-column label="时间" width="140">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="username" label="用户" width="120" />
          <el-table-column prop="action" label="动作" width="120" />
          <el-table-column prop="target_type" label="对象类型" width="100" />
          <el-table-column prop="target_id" label="对象ID" width="140" show-overflow-tooltip />
          <el-table-column prop="detail" label="详情" show-overflow-tooltip />
          <el-table-column prop="ip" label="IP" width="120" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="平台指标" name="metrics">
        <div class="metrics-actions"><el-button size="small" @click="loadMetrics">刷新</el-button></div>
        <div v-if="metrics" v-loading="loading" class="metrics-grid">
          <div class="metric"><span>用户总数</span><strong>{{ metrics.users.total }}</strong></div>
          <div class="metric"><span>活跃用户</span><strong>{{ metrics.users.active }}</strong></div>
          <div class="metric"><span>分析结果</span><strong>{{ metrics.analysis_results }}</strong></div>
          <div class="metric"><span>任务成功率</span><strong>{{ (metrics.tasks.success_rate * 100).toFixed(1) }}%</strong></div>
          <div class="metric"><span>执行次数</span><strong>{{ metrics.usage.events }}</strong></div>
          <div class="metric"><span>执行耗时</span><strong>{{ (metrics.usage.execution_ms / 1000).toFixed(1) }}s</strong></div>
          <div class="metric"><span>Token 用量</span><strong>{{ metrics.usage.tokens }}</strong></div>
          <div class="metric"><span>估算成本</span><strong>${{ metrics.usage.cost_usd.toFixed(4) }}</strong></div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.admin-view {
  padding: 30px clamp(16px, 4vw, 42px);
  background: var(--bg);
  height: 100%;
  overflow-y: auto;
}
.admin-header {
  max-width: 1180px;
  margin: 0 auto 20px;
}
.admin-header h3 {
  font-size: 22px;
  font-weight: 600;
}
.create-form {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
  align-items: center;
  max-width: 1180px;
  padding: 16px;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 3px 12px rgba(35, 54, 50, .025);
}

.admin-view :deep(.el-tabs) { max-width: 1180px; margin: 0 auto; }
.admin-view :deep(.el-tabs__content) { padding-top: 4px; }
.metrics-actions { margin-bottom: 12px; }
.metrics-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; }
.metric { padding: 18px; border: 1px solid var(--border-light); border-radius: 8px; background: #fff; }
.metric span { display: block; color: var(--text-tertiary); font-size: 12px; }
.metric strong { display: block; margin-top: 10px; color: var(--text); font-size: 24px; font-weight: 600; }
@media (max-width: 760px) { .metrics-grid { grid-template-columns: repeat(2, minmax(130px, 1fr)); } }
</style>
