<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Database, LayoutDashboard, LogOut, MessageSquare, Shield } from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

async function handleLogout() {
  await auth.logout()
  // 无论后端 logout 是否成功，都强制跳登录页
  router.push('/login')
}
</script>

<template>
  <div class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="topbar-left" @click="router.push('/')">
        <span class="brand-mark"><Database /></span>
        <div class="brand-copy">
          <span class="logo">数据分析助手</span>
          <span class="brand-subtitle">AI Data Workspace</span>
        </div>
      </div>
      <div class="topbar-right">
        <el-button :class="{ active: route.name === 'chat' }" text @click="router.push('/')">
          <el-icon><MessageSquare /></el-icon><span>数据分析</span>
        </el-button>
        <el-button :class="{ active: route.name === 'workbench' }" text @click="router.push('/workbench')">
          <el-icon><LayoutDashboard /></el-icon><span>工作台</span>
        </el-button>
        <el-button
          v-if="auth.user?.role === 'admin'"
          :class="{ active: route.name === 'admin' }"
          text
          @click="router.push('/admin')"
        >
          <el-icon><Shield /></el-icon><span>管理</span>
        </el-button>
        <span class="topbar-divider"></span>
        <el-button text title="退出登录" @click="handleLogout">
          <el-icon><LogOut /></el-icon><span class="logout-text">登出</span>
        </el-button>
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
  height: 58px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.94);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  backdrop-filter: blur(10px);
  z-index: 30;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.brand-mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #fff;
  background: var(--primary);
  border-radius: 8px;
}
.brand-mark svg { width: 18px; height: 18px; }
.brand-copy {
  display: flex;
  flex-direction: column;
}
.logo {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.brand-subtitle {
  margin-top: 1px;
  color: var(--text-tertiary);
  font-size: 10px;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 2px;
}
.topbar-right .el-button {
  margin-left: 0;
  color: var(--text-secondary);
  border-radius: 6px;
}
.topbar-right .el-button.active {
  color: var(--primary);
  background: var(--primary-light);
}
.topbar-divider {
  width: 1px;
  height: 20px;
  margin: 0 6px;
  background: var(--border);
}
.main {
  flex: 1;
  overflow: hidden;
}
@media (max-width: 660px) {
  .brand-subtitle, .topbar-right .el-button span, .logout-text { display: none; }
  .topbar { padding: 0 10px; }
  .topbar-right .el-button { width: 34px; padding: 0; }
}
</style>
