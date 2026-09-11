<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useKbStore } from '@/stores/kb'
import SessionSidebar from '@/components/SessionSidebar.vue'
import ChatMain from '@/components/ChatMain.vue'
import KbPopover from '@/components/KbPopover.vue'

const kb = useKbStore()
const showKb = ref(false)
const sidebarWidth = ref(Number(localStorage.getItem('chat-sidebar-width') || 240))
const chatWidth = ref(Number(localStorage.getItem('chat-main-width') || 0))
const kbWidth = ref(Number(localStorage.getItem('chat-kb-width') || 420))
let resizing: 'sidebar' | 'chat' | 'kb' | null = null
let startX = 0
let startWidth = 0
function beginResize(kind: 'sidebar' | 'chat' | 'kb', event: PointerEvent) {
  resizing = kind; startX = event.clientX
  const target = event.currentTarget as HTMLElement
  const currentWidth = target.previousElementSibling?.getBoundingClientRect().width || 520
  startWidth = kind === 'sidebar' ? sidebarWidth.value : kind === 'kb' ? kbWidth.value : (chatWidth.value || currentWidth)
  window.addEventListener('pointermove', resizePane)
  window.addEventListener('pointerup', endResize, { once: true })
}
function resizePane(event: PointerEvent) {
  if (!resizing) return
  const next = Math.max(180, Math.min(720, startWidth + event.clientX - startX))
  if (resizing === 'sidebar') { sidebarWidth.value = next; localStorage.setItem('chat-sidebar-width', String(next)) }
  if (resizing === 'kb') { kbWidth.value = next; localStorage.setItem('chat-kb-width', String(next)) }
  if (resizing === 'chat') { chatWidth.value = next; localStorage.setItem('chat-main-width', String(next)) }
}
function endResize() { resizing = null; window.removeEventListener('pointermove', resizePane) }

onMounted(async () => {
  try {
    await kb.fetchFolders()
    await kb.fetchDocuments()
  } catch {
    // 首次加载失败静默
  }
})
</script>

<template>
  <div class="chat-view">
    <SessionSidebar :width="sidebarWidth" /><div class="resize-handle" title="调整历史对话宽度" @pointerdown="beginResize('sidebar', $event)" />
    <ChatMain :style="chatWidth ? { flex: `0 0 ${chatWidth}px` } : undefined" /><div class="resize-handle" title="调整对话区域宽度" @pointerdown="beginResize('chat', $event)" />
    <!-- 知识库入口按钮 + 右侧常驻面板 -->
    <el-button v-if="!showKb" class="kb-entry" type="primary" circle @click="showKb = true">
      <el-icon><Folder /></el-icon>
    </el-button>
    <aside v-if="showKb" class="kb-panel" :style="{ width: kbWidth + 'px', flexBasis: kbWidth + 'px' }"><div class="resize-handle kb-resize" title="调整文件夹区域宽度" @pointerdown="beginResize('kb', $event)" />
      <KbPopover @close="showKb = false" />
    </aside>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  height: 100%;
  position: relative;
  min-width: 0;
  background: var(--bg);
}
.resize-handle {
  flex: 0 0 4px;
  width: 4px;
  cursor: col-resize;
  background: transparent;
  z-index: 20;
}
.resize-handle:hover { background: #b9ccc8; }
.kb-resize { position: absolute; left: 0; top: 0; bottom: 0; }
.kb-entry {
  position: absolute;
  right: 18px;
  top: 16px;
  z-index: 10;
  box-shadow: 0 8px 20px rgba(52, 92, 84, .18);
}
.kb-panel {
  flex: 0 0 min(560px, 42vw);
  width: min(560px, 42vw);
  min-width: 280px;
  max-width: 720px;
  position: relative;
  height: 100%;
  overflow: hidden;
  background: var(--bg-card);
  border-left: 1px solid var(--border);
}
@media (max-width: 760px) {
  .kb-panel {
    flex-basis: 100% !important;
    width: 100% !important;
    min-width: 0;
  }
}
</style>
