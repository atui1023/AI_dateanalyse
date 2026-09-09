<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useKbStore } from '@/stores/kb'
import SessionSidebar from '@/components/SessionSidebar.vue'
import ChatMain from '@/components/ChatMain.vue'
import KbPopover from '@/components/KbPopover.vue'

const kb = useKbStore()
const showKb = ref(false)

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
    <SessionSidebar />
    <ChatMain />
    <!-- 知识库入口按钮 + 弹层 -->
    <el-popover
      v-model:visible="showKb"
      placement="top-end"
      :width="560"
      trigger="click"
    >
      <template #reference>
        <el-button class="kb-entry" type="primary" circle>
          <el-icon><Folder /></el-icon>
        </el-button>
      </template>
      <KbPopover @close="showKb = false" />
    </el-popover>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  height: 100%;
  position: relative;
}
.kb-entry {
  position: absolute;
  right: 20px;
  top: 12px;
  z-index: 10;
}
</style>
