// 知识库接口
import request from './request'

export interface Folder {
  id: string
  name: string
  system: boolean
  favorite_system?: boolean
  active?: boolean
}

export interface Document {
  doc_id: string
  folder_id: string
  filename: string
  ext: string
  status: 'parsing' | 'ready' | 'failed'
  active?: boolean
  favorite?: boolean
  tags?: string[]
  version?: number
  virtual_favorite?: boolean
  source_folder_id?: string
  chunks?: number
  error_msg?: string | null
  created_at?: string
}

export interface QualityIssue {
  level: 'warning' | 'error'
  code: string
  message: string
}

export interface DatasetQuality {
  missing_cells: number
  missing_rate: number
  duplicate_rows: number
  empty_rows: number
  missing_by_column: Record<string, number>
  issues: QualityIssue[]
}

export interface DatasetSummary {
  rows: number
  cols: number
  columns: { name: string; dtype: string; samples: string[] }[]
  preview_columns: string[]
  preview_rows: string[][]
  quality: DatasetQuality
}

export interface UploadResult {
  doc_id: string
  filename: string
  status: string
  summary?: DatasetSummary | null
}

export function listFolders() {
  return request.get<Folder[]>('/kb/folders')
}

export function createFolder(name: string) {
  return request.post<Folder>('/kb/folders', { name })
}

export function renameFolder(id: string, name: string) {
  return request.patch<Folder>(`/kb/folders/${id}`, { name, active: true })
}

export function deleteFolder(id: string) {
  return request.delete(`/kb/folders/${id}`)
}

export function listDocuments(folderId?: string) {
  const url = folderId ? `/kb/documents?folder_id=${folderId}` : '/kb/documents'
  return request.get<Document[]>(url)
}

export function uploadFile(file: File, folderId: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('folder_id', folderId)
  return request.post<UploadResult>('/kb/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function mountDocument(docId: string) {
  return request.post<{ dataset_id: string; filename: string; summary: DatasetSummary }>(`/kb/documents/${docId}/mount`)
}

export function unmountDocument(docId: string) {
  return request.delete(`/datasets/${docId}`)
}

export function toggleDocumentActive(docId: string, active: boolean) {
  return request.patch(`/kb/documents/${docId}/active`, { active })
}

export function updateDocumentMetadata(docId: string, payload: { tags?: string[]; favorite?: boolean }) {
  return request.patch(`/kb/documents/${docId}/metadata`, payload)
}

export function moveDocument(docId: string, folderId: string) {
  return request.patch(`/kb/documents/${docId}/folder`, { folder_id: folderId })
}

export function deleteDocument(docId: string) {
  return request.delete(`/kb/documents/${docId}`)
}

export function retryDocument(docId: string) {
  return request.post(`/kb/documents/${docId}/retry`)
}

export interface DocumentVersion {
  id: number
  doc_id: string
  version: number
  filename: string
  file_ext?: string | null
  file_size: number
  created_at: string
}

export function uploadDocumentVersion(docId: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return request.post<{ doc_id: string; filename: string; version: number; status: string }>(`/kb/documents/${docId}/version`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function listDocumentVersions(docId: string) {
  return request.get<DocumentVersion[]>(`/kb/documents/${docId}/versions`)
}
