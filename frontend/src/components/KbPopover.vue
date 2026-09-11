<script setup lang="ts">
import { ref, computed } from 'vue'
import { useKbStore } from '@/stores/kb'
import type { DatasetSummary, DocumentVersion } from '@/api/kb'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  FolderPlus, Upload, RefreshCw, Folder, File as FileIcon,
  Check, AlertTriangle, Loader, PlusCircle, Move, Pencil, X, Star, Tag,
} from 'lucide-vue-next'

const emit = defineEmits<{ close: [] }>()
const kb = useKbStore()

const uploadTarget = ref<string>('')  // 上传目标文件夹
const selectedTag = ref<string>('')
const expandedFolders = ref<Set<string>>(new Set())
const uploading = ref(false)
const moveTargetDoc = ref<string | null>(null)
const moveTargetFolder = ref<string>('')
const moveDialogVisible = ref(false)
const previewVisible = ref(false)
const previewFilename = ref('')
const previewSummary = ref<DatasetSummary | null>(null)
const versionDialogVisible = ref(false)
const versionDocName = ref('')
const versionRows = ref<DocumentVersion[]>([])

const TAB_EXT = ['.csv', '.xlsx', '.xls']

function extOf(filename: string) {
  return filename.slice(filename.lastIndexOf('.')).toLowerCase()
}

function isTableFile(filename: string) {
  return TAB_EXT.includes(extOf(filename))
}

const visibleFolders = computed(() => kb.folders)
const allTags = computed(() => Array.from(new Set(kb.documents.flatMap((d) => d.tags || []))).sort())

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
    const result = await kb.uploadFile(file, folderId)
    if (result.summary) {
      previewFilename.value = result.filename
      previewSummary.value = result.summary
      previewVisible.value = true
    }
    ElMessage.success('已上传，后台解析中')
    await kb.fetchDocuments()
  } catch {
    // axios 拦截器已提示
  } finally {
    uploading.value = false
  }
}

async function handleMount(docId: string) {
  const result = await kb.mountDocument(docId)
  previewFilename.value = result.filename
  previewSummary.value = result.summary
  previewVisible.value = true
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

async function handleUploadVersion(docId: string, file: File) {
  try {
    const result = await kb.uploadDocumentVersion(docId, file)
    ElMessage.success(`已上传第 ${result.version} 版，后台解析中`)
    await kb.fetchDocuments()
  } catch {
    // axios 拦截器已提示
  }
}

async function handleShowVersions(docId: string, filename: string) {
  versionRows.value = await kb.listDocumentVersions(docId)
  versionDocName.value = filename
  versionDialogVisible.value = true
}

async function handleToggleFavorite(docId: string, favorite: boolean) {
  await kb.updateDocumentMetadata(docId, { favorite: !favorite })
}

async function handleEditTags(docId: string, tags: string[] = []) {
  const { value } = await ElMessageBox.prompt('多个标签用逗号分隔', '编辑标签', {
    inputValue: tags.join('、'),
    inputPlaceholder: '例如：销售、2026、月报',
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  })
  const nextTags = value.split(/[、,，]/).map((tag: string) => tag.trim()).filter(Boolean)
  await kb.updateDocumentMetadata(docId, { tags: nextTags })
  ElMessage.success('标签已保存')
}

function openMoveModal(docId: string) {
  moveTargetDoc.value = docId
  moveDialogVisible.value = true
  const doc = kb.documents.find((d) => d.doc_id === docId)
  moveTargetFolder.value = doc?.folder_id || ''
}

async function handleMove() {
  if (!moveTargetDoc.value || !moveTargetFolder.value) return
  await kb.moveDocument(moveTargetDoc.value, moveTargetFolder.value)
  moveTargetDoc.value = null
  moveDialogVisible.value = false
  ElMessage.success('已移动')
}

function folderDocs(folderId: string) {
  return kb.documents.filter((d) => {
    if (d.folder_id !== folderId) return false
    return !selectedTag.value || (d.tags || []).includes(selectedTag.value)
  })
}

function mountIndex(docId: string) {
  return kb.mounts.findIndex((m) => m.doc_id === docId)
}

async function handleRefresh() {
  await Promise.all([kb.fetchFolders(), kb.fetchDocuments()])
}
</script>

<template>
  <div class="kb-popover" @click.stop>
    <div class="kb-header">
      <span>知识库文件</span>
      <el-button size="small" text title="关闭" @click="emit('close')"><el-icon><X /></el-icon></el-button>
    </div>
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
          :disabled="f.favorite_system"
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
      <el-select v-model="selectedTag" placeholder="按标签筛选" clearable size="small" style="width: 150px">
        <el-option v-for="tag in allTags" :key="tag" :label="tag" :value="tag" />
      </el-select>
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
          <el-tag v-if="f.favorite_system" size="small" type="warning">收藏</el-tag>
          <el-tag v-else-if="f.system" size="small" type="info">系统</el-tag>
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
            <el-button
              size="small"
              text
              class="icon-action"
              :class="{ favorite: d.favorite }"
              :title="d.favorite ? '取消收藏' : '收藏'"
              @click="handleToggleFavorite(d.doc_id, !!d.favorite)"
            ><el-icon><Star /></el-icon></el-button>
            <span v-if="d.tags?.length" class="doc-tags" :title="d.tags.join('、')">
              <el-icon><Tag /></el-icon>{{ d.tags.join('、') }}
            </span>

            <!-- 状态 -->
            <span v-if="d.status === 'ready'" class="st st-ready">
              <el-icon><Check /></el-icon> 就绪
            </span>
            <span
              v-else-if="d.status === 'failed'"
              class="st st-failed"
              :title="d.error_msg || '知识库处理失败'"
            >
              <el-icon><AlertTriangle /></el-icon> 知识库处理失败
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
              v-else-if="isTableFile(d.filename)"
              size="small"
              text
              @click="handleMount(d.doc_id)"
              title="挂载为分析数据集（不依赖知识库向量化）"
            ><el-icon><PlusCircle /></el-icon></el-button>

            <!-- 操作 -->
            <el-button size="small" text @click="handleEditTags(d.doc_id, d.tags || [])" title="编辑标签">
              <el-icon><Tag /></el-icon>
            </el-button>
            <template v-if="!d.virtual_favorite">
              <el-upload
                :show-file-list="false"
                :before-upload="(f: File) => { handleUploadVersion(d.doc_id, f); return false }"
                accept=".txt,.md,.pdf,.csv,.xlsx,.xls"
              >
                <el-button size="small" text title="上传新版本"><el-icon><Upload /></el-icon></el-button>
              </el-upload>
              <el-button size="small" text @click="handleShowVersions(d.doc_id, d.filename)" title="查看历史版本">
                v{{ d.version || 1 }}
              </el-button>
              <el-button size="small" text @click="openMoveModal(d.doc_id)" title="移动">
                <el-icon><Move /></el-icon>
              </el-button>
              <el-button size="small" text type="danger" @click="handleDeleteDoc(d.doc_id, d.filename)">
                <el-icon><AlertTriangle /></el-icon>
              </el-button>
            </template>
          </div>
          <el-empty v-if="folderDocs(f.id).length === 0" description="无文档" :image-size="40" />
        </div>
      </div>
    </div>

    <!-- 移动文档模态 -->
    <el-dialog v-model="moveDialogVisible" title="移动文档到" width="360" append-to-body>
      <el-select v-model="moveTargetFolder" placeholder="选择目标知识库" style="width: 100%">
        <el-option
          v-for="f in kb.folders.filter(x => x.id !== (kb.documents.find(d => d.doc_id === moveTargetDoc)?.folder_id))"
          :key="f.id"
          :label="f.name"
          :value="f.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="moveDialogVisible = false; moveTargetDoc = null">取消</el-button>
        <el-button type="primary" @click="handleMove">移动</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="versionDialogVisible" :title="`历史版本 · ${versionDocName}`" width="min(620px, 92vw)" append-to-body>
      <el-empty v-if="versionRows.length === 0" description="暂无历史版本" :image-size="48" />
      <el-table v-else :data="versionRows" border size="small">
        <el-table-column prop="version" label="版本" width="80">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="created_at" label="上传时间" width="170" />
      </el-table>
    </el-dialog>

    <el-dialog v-model="previewVisible" :title="`数据预览 · ${previewFilename}`" width="min(900px, 92vw)" append-to-body>
      <template v-if="previewSummary">
        <div class="summary-stats">
          <span>{{ previewSummary.rows.toLocaleString() }} 行</span>
          <span>{{ previewSummary.cols }} 列</span>
          <span :class="{ danger: previewSummary.quality.missing_cells > 0 }">
            缺失值 {{ previewSummary.quality.missing_cells.toLocaleString() }}
          </span>
          <span :class="{ danger: previewSummary.quality.duplicate_rows > 0 }">
            重复行 {{ previewSummary.quality.duplicate_rows.toLocaleString() }}
          </span>
        </div>
        <el-alert
          v-for="issue in previewSummary.quality.issues"
          :key="issue.code"
          :title="issue.message"
          :type="issue.level === 'error' ? 'error' : 'warning'"
          show-icon
          :closable="false"
          class="quality-alert"
        />
        <el-empty v-if="previewSummary.quality.issues.length === 0" description="未发现明显质量问题" :image-size="48" />
        <el-table :data="previewSummary.preview_rows" border size="small" max-height="360" class="preview-table">
          <el-table-column
            v-for="(column, index) in previewSummary.preview_columns"
            :key="`${column}-${index}`"
            :label="column"
            :prop="String(index)"
            min-width="140"
            show-overflow-tooltip
          >
            <template #default="{ row }">{{ row[index] }}</template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-popover {
  width: 100%;
  min-width: 0;
  height: 100%;
  box-sizing: border-box;
  padding: 14px;
  overflow-x: hidden;
  overflow-y: auto;
}
.kb-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 36px;
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 600;
}
.kb-toolbar {
  display: flex;
  min-width: 0;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
  align-items: center;
}
.upload-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
  padding: 7px 9px;
  background: var(--primary-soft);
  border: 1px solid var(--border-light);
  border-radius: 6px;
}
.kb-list {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
}
.folder-group {
  border-radius: 6px;
  border: 1px solid transparent;
}
.folder-group.active {
  border-color: #d7e5e2;
  background: #f7faf9;
}
.folder-row {
  display: flex;
  flex-wrap: wrap;
  min-width: 0;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 7px 9px;
  cursor: pointer;
  border-radius: 6px;
}
.folder-row:hover {
  background: var(--primary-soft);
}
.folder-icon {
  color: var(--text-tertiary);
}
.folder-name {
  flex: 1;
  min-width: 80px;
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
  min-width: 0;
  padding-left: 12px;
  border-left: 1px solid var(--border);
  margin-left: 14px;
}
.doc-row {
  display: flex;
  flex-wrap: wrap;
  min-width: 0;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 6px 8px;
  border-radius: 6px;
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
  min-width: 100px;
  max-width: none;
}
.st {
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.st-ready { color: var(--success); }
.icon-action { color: var(--text-tertiary); }
.icon-action.favorite { color: var(--warning); }
.doc-tags { color: var(--text-tertiary); font-size: 11px; max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 2px; }
.st-failed { color: var(--danger); }
.st-parsing { color: var(--text-tertiary); }
.spin {
  animation: spin 1s linear infinite;
}
.summary-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-bottom: 12px;
  color: var(--text-secondary);
  font-size: 13px;
}
.summary-stats .danger {
  color: var(--danger);
}
.quality-alert {
  margin-bottom: 8px;
}
.preview-table {
  margin-top: 12px;
}
:deep(.el-empty) {
  min-height: 84px;
  padding: 12px 0;
}
:deep(.el-empty__image) {
  width: 40px;
}
:deep(.kb-toolbar .el-select) {
  max-width: 100%;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
