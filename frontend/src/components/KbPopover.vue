<script setup lang="ts">
import { ref, computed } from 'vue'
import { useKbStore } from '@/stores/kb'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  FolderPlus, Upload, RefreshCw, Folder, File as FileIcon,
  Check, AlertTriangle, Loader, PlusCircle, Move, Pencil,
} from 'lucide-vue-next'

const emit = defineEmits<{ close: [] }>()
const kb = useKbStore()

const uploadTarget = ref<string>('')  // 上传目标文件夹
const expandedFolders = ref<Set<string>>(new Set())
const uploading = ref(false)
const moveTargetDoc = ref<string | null>(null)
const moveTargetFolder = ref<string>('')

const TAB_EXT = ['.csv', '.xlsx', '.xls']

function extOf(filename: string) {
  return filename.slice(filename.lastIndexOf('.')).toLowerCase()
}

function isTableFile(filename: string) {
  return TAB_EXT.includes(extOf(filename))
}

const visibleFolders = computed(() => kb.folders)

function toggleExpand(id: string) {
  if (expandedFolders.value.has(id)) {
    expandedFolders.value.delete(id)
  } else {
    expandedFolders.value.add(id)
  }
}

async function handleCreateFolder() {
  const { value } = await ElMessageBox.prompt('文件夹名称', '新建知识库', {
    inputPlaceholder: '输入名称',
    confirmButtonText: '创建',
    cancelButtonText: '取消',
  })
  const name = value.trim()
  if (name) {
    await kb.createFolder(name)
    ElMessage.success('已创建')
  }
}

async function handleRenameFolder(id: string, name: string) {
  const { value } = await ElMessageBox.prompt('文件夹名称', '重命名', {
    inputValue: name,
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  })
  const newName = value.trim()
  if (newName && newName !== name) {
    await kb.renameFolder(id, newName)
    ElMessage.success('已重命名')
  }
}

async function handleDeleteFolder(id: string, name: string) {
  await ElMessageBox.confirm(`删除知识库"${name}"？文档将移至临时文件夹。`, '删除', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  await kb.deleteFolder(id)
  ElMessage.success('已删除')
}

async function handleUpload(file: File, folderId: string) {
  if (!folderId) {
    ElMessage.warning('请先选择目标文件夹')
    return
  }
  uploading.value = true
  try {
    await kb.uploadFile(file, folderId)
    ElMessage.success('已上传，后台解析中')
    await kb.fetchDocuments()
  } catch {
    // axios 拦截器已提示
  } finally {
    uploading.value = false
  }
}

async function handleMount(docId: string) {
  await kb.mountDocument(docId)
  ElMessage.success('已挂载')
}

async function handleUnmount(docId: string) {
  await kb.unmountDocument(docId)
}

async function handleDeleteDoc(docId: string, filename: string) {
  await ElMessageBox.confirm(`删除文档"${filename}"？此操作不可恢复。`, '删除文档', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  await kb.deleteDocument(docId)
  ElMessage.success('已删除')
}

async function handleRetry(docId: string) {
  await kb.retryDocument(docId)
  ElMessage.success('已重新解析')
}

function openMoveModal(docId: string) {
  moveTargetDoc.value = docId
  const doc = kb.documents.find((d) => d.doc_id === docId)
  moveTargetFolder.value = doc?.folder_id || ''
}

async function handleMove() {
  if (!moveTargetDoc.value || !moveTargetFolder.value) return
  await kb.moveDocument(moveTargetDoc.value, moveTargetFolder.value)
  moveTargetDoc.value = null
  ElMessage.success('已移动')
}

function folderDocs(folderId: string) {
  return kb.documents.filter((d) => d.folder_id === folderId)
}

function mountIndex(docId: string) {
  return kb.mounts.findIndex((m) => m.doc_id === docId)
}

async function handleRefresh() {
  await Promise.all([kb.fetchFolders(), kb.fetchDocuments()])
}
</script>

<template>
  <div class="kb-popover">
    <!-- 工具栏 -->
    <div class="kb-toolbar">
      <el-button size="small" @click="handleCreateFolder">
        <el-icon><FolderPlus /></el-icon> 新建知识库
      </el-button>
      <el-select
        v-model="uploadTarget"
        placeholder="选择上传目标"
        size="small"
        style="width: 160px"
      >
        <el-option
          v-for="f in kb.folders"
          :key="f.id"
          :label="f.name + (f.system ? '（系统）' : '')"
          :value="f.id"
        />
      </el-select>
      <el-upload
        :show-file-list="false"
        :before-upload="(f: File) => { handleUpload(f, uploadTarget); return false }"
        :disabled="!uploadTarget || uploading"
        multiple
      >
        <el-button size="small" type="primary" :disabled="!uploadTarget" :loading="uploading">
          <el-icon><Upload /></el-icon> 上传文档
        </el-button>
      </el-upload>
      <el-button size="small" @click="handleRefresh">
        <el-icon><RefreshCw /></el-icon>
      </el-button>
    </div>
    <div v-if="!uploadTarget" class="upload-hint">
      请先选择上传目标文件夹
    </div>

    <!-- 文件夹 + 文档列表 -->
    <div class="kb-list">
      <div
        v-for="f in visibleFolders"
        :key="f.id"
        class="folder-group"
        :class="{ active: uploadTarget === f.id }"
      >
        <div class="folder-row" @click="toggleExpand(f.id)">
          <el-icon class="folder-icon"><Folder /></el-icon>
          <span class="folder-name">{{ f.name }}</span>
          <el-tag v-if="f.system" size="small" type="info">系统</el-tag>
          <el-checkbox
            v-if="!f.system"
            :model-value="f.active"
            @change="(v: boolean) => kb.toggleFolderActive(f.id, v)"
            @click.stop
            title="勾选后该知识库参与 RAG 检索"
          />
          <div class="folder-actions" @click.stop>
            <el-button
              v-if="!f.system"
              size="small"
              text
              @click="handleRenameFolder(f.id, f.name)"
            ><el-icon><Pencil /></el-icon></el-button>
            <el-button
              v-if="!f.system"
              size="small"
              text
              type="danger"
              @click="handleDeleteFolder(f.id, f.name)"
            ><el-icon><AlertTriangle /></el-icon></el-button>
          </div>
        </div>

        <!-- 展开后的文档列表 -->
        <div v-if="expandedFolders.has(f.id)" class="doc-list">
          <div v-for="d in folderDocs(f.id)" :key="d.doc_id" class="doc-row">
            <el-checkbox
              :model-value="d.active"
              @change="(v: boolean) => kb.toggleDocActive(d.doc_id, v)"
              title="勾选后该文件参与检索"
            />
            <el-icon class="doc-icon"><FileIcon /></el-icon>
            <span class="doc-name" :title="d.filename">{{ d.filename }}</span>

            <!-- 状态 -->
            <span v-if="d.status === 'ready'" class="st st-ready">
              <el-icon><Check /></el-icon> 就绪
            </span>
            <span v-else-if="d.status === 'failed'" class="st st-failed">
              <el-icon><AlertTriangle /></el-icon> 失败
              <el-button size="small" text @click="handleRetry(d.doc_id)">重试</el-button>
            </span>
            <span v-else class="st st-parsing">
              <el-icon class="spin"><Loader /></el-icon> 解析中
            </span>

            <!-- 挂载按钮 -->
            <el-button
              v-if="mountIndex(d.doc_id) >= 0"
              size="small"
              type="success"
              @click="handleUnmount(d.doc_id)"
              title="取消挂载"
            >df{{ mountIndex(d.doc_id) + 1 }}</el-button>
            <el-button
              v-else-if="isTableFile(d.filename) && d.status === 'ready'"
              size="small"
              text
              @click="handleMount(d.doc_id)"
              title="挂载为分析数据集"
            ><el-icon><PlusCircle /></el-icon></el-button>

            <!-- 操作 -->
            <el-button size="small" text @click="openMoveModal(d.doc_id)" title="移动">
              <el-icon><Move /></el-icon>
            </el-button>
            <el-button size="small" text type="danger" @click="handleDeleteDoc(d.doc_id, d.filename)">
              <el-icon><AlertTriangle /></el-icon>
            </el-button>
          </div>
          <el-empty v-if="folderDocs(f.id).length === 0" description="无文档" :image-size="40" />
        </div>
      </div>
    </div>

    <!-- 移动文档模态 -->
    <el-dialog v-model="moveTargetDoc" title="移动文档到" width="360" append-to-body>
      <el-select v-model="moveTargetFolder" placeholder="选择目标知识库" style="width: 100%">
        <el-option
          v-for="f in kb.folders.filter(x => x.id !== (kb.documents.find(d => d.doc_id === moveTargetDoc)?.folder_id))"
          :key="f.id"
          :label="f.name"
          :value="f.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="moveTargetDoc = null">取消</el-button>
        <el-button type="primary" @click="handleMove">移动</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-popover {
  max-height: 70vh;
  overflow-y: auto;
}
.kb-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.upload-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
  padding: 4px 8px;
  background: var(--bg);
  border-radius: 4px;
}
.kb-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.folder-group {
  border-radius: 6px;
}
.folder-group.active {
  background: var(--primary-light);
}
.folder-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  cursor: pointer;
  border-radius: 6px;
}
.folder-row:hover {
  background: var(--bg-hover);
}
.folder-icon {
  color: var(--text-tertiary);
}
.folder-name {
  flex: 1;
  font-size: 13px;
}
.folder-actions {
  display: none;
  gap: 2px;
}
.folder-row:hover .folder-actions {
  display: flex;
}
.doc-list {
  padding-left: 24px;
  border-left: 2px solid var(--border);
  margin-left: 14px;
}
.doc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 4px;
}
.doc-row:hover {
  background: var(--bg-hover);
}
.doc-name {
  flex: 1;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
}
.st {
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.st-ready { color: #00b42a; }
.st-failed { color: #f53f3f; }
.st-parsing { color: var(--text-tertiary); }
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
