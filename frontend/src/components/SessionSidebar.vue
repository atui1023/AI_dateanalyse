<script setup lang="ts">
import { onMounted } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, MessageSquare, Delete, Pencil,
} from 'lucide-vue-next'

const store = useSessionsStore()

onMounted(() => {
  store.fetchList()
})

async function handleNew() {
  await store.create('chat')
}

async function handleSelect(id: string) {
  if (id === store.currentId) return
  await store.select(id)
}

async function handleRename(id: string, title: string | null) {
  const { value } = await ElMessageBox.prompt('会话标题', '重命名会话', {
    inputValue: title || '',
    inputPlaceholder: '输入新标题',
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  })
  const newTitle = value.trim()
  if (newTitle && newTitle !== title) {
    await store.rename(id, newTitle)
    ElMessage.success('已重命名')
  }
}

async function handleDelete(id: string, title: string | null) {
  await ElMessageBox.confirm(
    `确定删除会话"${title || '无标题'}"？此操作不可恢复。`,
    '删除会话',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  await store.remove(id)
  ElMessage.success('已删除')
}

function formatTime(t: string) {
  if (!t) return ''
  const d = new Date(t)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <span class="title">会话列表</span>
      <el-button type="primary" size="small" @click="handleNew">
        <el-icon><Plus /></el-icon> 新建
      </el-button>
    </div>
    <div class="sidebar-list">
      <div
        v-for="s in store.list"
        :key="s.session_id"
        class="session-item"
        :class="{ active: s.session_id === store.currentId }"
        @click="handleSelect(s.session_id)"
      >
        <el-icon class="session-icon"><MessageSquare /></el-icon>
        <div class="session-info">
          <div class="session-title">{{ s.title || '新会话' }}</div>
          <div class="session-time">{{ formatTime(s.updated_at || s.created_at) }}</div>
        </div>
        <div class="session-actions" @click.stop>
          <el-button
            size="small"
            circle
            title="重命名"
            @click="handleRename(s.session_id, s.title)"
          >
            <el-icon><Pencil /></el-icon>
          </el-button>
          <el-button
            size="small"
            circle
            type="danger"
            title="删除"
            @click="handleDelete(s.session_id, s.title)"
          >
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
      </div>
      <el-empty
        v-if="store.list.length === 0"
        description="暂无会话"
        :image-size="60"
      />
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 240px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}
.sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  border-bottom: 1px solid var(--border);
}
.sidebar-head .title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}
.sidebar-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  position: relative;
}
.session-item:hover {
  background: var(--bg-hover);
}
.session-item.active {
  background: var(--primary-light);
}
.session-item.active .session-title {
  color: var(--primary);
  font-weight: 500;
}
.session-icon {
  flex-shrink: 0;
  color: var(--text-tertiary);
}
.session-info {
  flex: 1;
  min-width: 0;
}
.session-title {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text);
}
.session-time {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}
.session-actions {
  display: none;
  gap: 4px;
}
.session-item:hover .session-actions {
  display: flex;
}
</style>
