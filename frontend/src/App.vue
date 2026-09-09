<script setup lang="ts">
// 根组件：承载 router-view，并统一处理“登录用户身份变化”时的用户态数据隔离
import { watch } from 'vue'
import { useAuthStore, resetUserScopedState } from '@/stores/auth'
import { useKbStore } from '@/stores/kb'
import { useSessionsStore } from '@/stores/sessions'

const auth = useAuthStore()
const kb = useKbStore()
const sessions = useSessionsStore()

// 监听当前登录用户 id：
// - 登录/刷新后身份确认：把挂载缓存按 uid 校验，不属于当前用户（旧号残留）一律清空，
//   同一用户刷新则保留挂载；若从 A 号直接切到 B 号，重置会话列表
// - 登出/会话失效（id 变 null）：清空所有与用户绑定的内存态和本地缓存
watch(
  () => auth.user?.id ?? null,
  (newId, oldId) => {
    if (newId !== null) {
      kb.reconcileOwner(newId)
      if (oldId !== null && oldId !== newId) sessions.reset()
    } else if (oldId !== null) {
      resetUserScopedState()
    }
  },
  { immediate: true },
)
</script>

<template>
  <router-view />
</template>
