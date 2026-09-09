// 知识库接口
import request from './request'

export interface Folder {
  id: string
  name: string
  system: boolean
  active?: boolean
}

export interface Document {
  doc_id: string
  folder_id: string
  filename: string
  ext: string
  status: 'parsing' | 'ready' | 'failed'
  active?: boolean
  chunks?: number
  error_msg?: string | null
  created_at?: string
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
  return request.post('/kb/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function mountDocument(docId: string) {
  return request.post(`/kb/documents/${docId}/mount`)
}

export function unmountDocument(docId: string) {
  return request.delete(`/datasets/${docId}`)
}

export function toggleDocumentActive(docId: string, active: boolean) {
  return request.patch(`/kb/documents/${docId}/active`, { active })
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
