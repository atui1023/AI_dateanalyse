// 知识库 store：文件夹/文档列表 + 挂载数据集
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as kbApi from '@/api/kb'
import type { Folder, Document, DatasetSummary } from '@/api/kb'

export interface DatasetMount {
  doc_id: string
  filename: string
  summary: DatasetSummary
}

export const useKbStore = defineStore('kb', () => {
  const folders = ref<Folder[]>([])
  const documents = ref<Document[]>([])
  // 挂载为分析用的数据集；持久化到 localStorage（带所属用户 id），
  // 同一用户刷新页面后恢复，切换用户时按 uid 校验并清空，避免串到新用户
  const MOUNTS_KEY = 'kb_mounts'
  interface MountsPayload { uid: number | null; mounts: DatasetMount[] }

  function loadPayload(): MountsPayload {
    try {
      const raw = localStorage.getItem(MOUNTS_KEY)
      const obj = raw ? JSON.parse(raw) : null
      if (obj && Array.isArray(obj.mounts)) {
        return { uid: typeof obj.uid === 'number' ? obj.uid : null, mounts: obj.mounts }
      }
    } catch {
      // 旧格式/损坏数据：忽略
    }
    return { uid: null, mounts: [] }
  }

  const initial = loadPayload()
  // 当前挂载归属的用户 id；与登录用户不一致时挂载作废
  const mountOwnerUid = ref<number | null>(initial.uid)
  const mounts = ref<DatasetMount[]>(initial.mounts)

  function saveMounts() {
    localStorage.setItem(MOUNTS_KEY, JSON.stringify({ uid: mountOwnerUid.value, mounts: mounts.value }))
  }
  const loading = ref(false)

  // 绑定当前登录用户：本地缓存的挂载若属于另一个用户（或无主），一律清空
  // 在用户身份确认/切换时调用，保证不刷新页面换号、或刷新后换号都不残留旧文件
  function reconcileOwner(uid: number | null) {
    if (mountOwnerUid.value !== uid) {
      mounts.value = []
      mountOwnerUid.value = uid
      localStorage.removeItem(MOUNTS_KEY)
    }
  }

  // 账号切换（登录/登出/会话失效）时调用：清空与当前用户绑定的内存态和本地缓存
  function reset() {
    folders.value = []
    documents.value = []
    mounts.value = []
    mountOwnerUid.value = null
    localStorage.removeItem(MOUNTS_KEY)
  }

  // RAG 检索时勾选的文件夹/文档（active 状态）
  const activeFolderIds = computed(() =>
    folders.value.filter((f) => f.active && !f.system).map((f) => f.id),
  )
  const activeDocIds = computed(() =>
    documents.value.filter((d) => d.active).map((d) => d.doc_id),
  )
  // 分析模式挂载的 dataset_ids
  const mountIds = computed(() => mounts.value.map((m) => m.doc_id))

  async function fetchFolders() {
    const { data } = await kbApi.listFolders()
    folders.value = data
  }

  async function fetchDocuments(folderId?: string) {
    const { data } = await kbApi.listDocuments(folderId)
    documents.value = data
  }

  async function createFolder(name: string) {
    const { data } = await kbApi.createFolder(name)
    folders.value.push(data)
    return data
  }

  async function renameFolder(id: string, name: string) {
    const { data } = await kbApi.renameFolder(id, name)
    const idx = folders.value.findIndex((f) => f.id === id)
    if (idx >= 0) folders.value[idx] = data
  }

  async function deleteFolder(id: string) {
    await kbApi.deleteFolder(id)
    folders.value = folders.value.filter((f) => f.id !== id)
    documents.value = documents.value.filter((d) => d.folder_id !== id)
    mounts.value = mounts.value.filter((m) => !documents.value.some((d) => d.doc_id === m.doc_id))
    saveMounts()
  }

  async function toggleFolderActive(id: string, active: boolean) {
    const f = folders.value.find((x) => x.id === id)
    if (f) f.active = active
  }

  async function toggleDocActive(docId: string, active: boolean) {
    const d = documents.value.find((x) => x.doc_id === docId)
    if (d) d.active = active
  }

  async function updateDocumentMetadata(docId: string, payload: { tags?: string[]; favorite?: boolean }) {
    const { data } = await kbApi.updateDocumentMetadata(docId, payload)
    const d = documents.value.find((x) => x.doc_id === docId)
    if (d) {
      if (data.tags) d.tags = data.tags
      if (typeof data.favorite === 'boolean') d.favorite = data.favorite
    }
    return data
  }

  async function mountDocument(docId: string) {
    const { data } = await kbApi.mountDocument(docId)
    // 去重
    if (!mounts.value.find((m) => m.doc_id === docId)) {
      const doc = documents.value.find((d) => d.doc_id === docId)
      mounts.value.push({
        doc_id: docId,
        filename: doc?.filename || data.filename,
        summary: data.summary,
      })
      saveMounts()
    }
    return data
  }

  async function unmountDocument(docId: string) {
    await kbApi.unmountDocument(docId)
    mounts.value = mounts.value.filter((m) => m.doc_id !== docId)
    saveMounts()
  }

  async function moveDocument(docId: string, folderId: string) {
    await kbApi.moveDocument(docId, folderId)
    const d = documents.value.find((x) => x.doc_id === docId)
    if (d) d.folder_id = folderId
  }

  async function deleteDocument(docId: string) {
    await kbApi.deleteDocument(docId)
    documents.value = documents.value.filter((d) => d.doc_id !== docId)
    mounts.value = mounts.value.filter((m) => m.doc_id !== docId)
    saveMounts()
  }

  async function retryDocument(docId: string) {
    await kbApi.retryDocument(docId)
    const d = documents.value.find((x) => x.doc_id === docId)
    if (d) d.status = 'parsing'
  }

  async function uploadDocumentVersion(docId: string, file: File) {
    const { data } = await kbApi.uploadDocumentVersion(docId, file)
    const d = documents.value.find((x) => x.doc_id === docId && !x.virtual_favorite)
    if (d) {
      d.filename = data.filename
      d.version = data.version
      d.status = 'parsing'
    }
    return data
  }

  async function listDocumentVersions(docId: string) {
    const { data } = await kbApi.listDocumentVersions(docId)
    return data
  }

  async function uploadFile(file: File, folderId: string) {
    const { data } = await kbApi.uploadFile(file, folderId)
    // 后端返回 doc_id/status，列表会轮询刷新
    return data
  }

  return {
    folders, documents, mounts, loading,
    activeFolderIds, activeDocIds, mountIds,
    fetchFolders, fetchDocuments, reset, reconcileOwner,
    createFolder, renameFolder, deleteFolder, toggleFolderActive,
    toggleDocActive, updateDocumentMetadata, mountDocument, unmountDocument,
    moveDocument, deleteDocument, retryDocument, uploadFile,
    uploadDocumentVersion, listDocumentVersions,
  }
})
