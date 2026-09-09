<script setup lang="ts">
// 聊天主区占位：阶段三逐步迁移会话侧边栏、消息流、知识库、结果区
import { ref } from 'vue'

const placeholder = ref('聊天主区（阶段三迁移）')
</script>

<template>
  <div class="chat-placeholder">
    <el-empty description="聊天主区（阶段三逐步迁移：会话侧边栏、消息流、知识库、结果区）" />
  </div>
</template>

<style scoped>
.chat-placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
