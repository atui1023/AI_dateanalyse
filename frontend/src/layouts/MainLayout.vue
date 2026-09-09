<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="topbar-left">
        <span class="logo">数据分析助手</span>
      </div>
      <div class="topbar-right">
        <el-button
          v-if="auth.user?.role === 'admin'"
          size="small"
          @click="router.push('/admin')"
        >
          管理
        </el-button>
        <el-button size="small" @click="handleLogout">登出</el-button>
      </div>
    </header>
    <!-- 主内容区：子路由（聊天 / 管理） -->
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  height: 48px;
  flex-shrink: 0;
  background: #fff;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
}
.logo {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.main {
  flex: 1;
  overflow: hidden;
}
</style>
