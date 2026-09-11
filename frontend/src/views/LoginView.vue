<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import * as authApi from '@/api/auth'
import { ElMessage } from 'element-plus'
import { Database } from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const form = ref({ username: '', password: '' })
const loading = ref(false)
const showRegister = ref(false)
const regForm = ref({ username: '', password: '', display_name: '' })

async function handleLogin() {
  if (!form.value.username || !form.value.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(form.value.username, form.value.password)
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch {
    // 错误已由 axios 拦截器统一提示
  } finally {
    loading.value = false
  }
}

async function handleRegister() {
  const f = regForm.value
  if (!f.username || !f.password) {
    ElMessage.warning('用户名和密码必填')
    return
  }
  loading.value = true
  try {
    await authApi.register(f.username, f.password, f.display_name || undefined)
    ElMessage.success('注册成功，请登录')
    form.value.username = f.username
    showRegister.value = false
    regForm.value = { username: '', password: '', display_name: '' }
  } catch {
    // axios 拦截器已提示
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-mark"><Database /></div>
      <h2 class="title">数据分析助手</h2>
      <p class="subtitle">登录后开始整理、分析和理解数据</p>
      <el-form @submit.prevent="handleLogin" label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="用户名" @keyup.enter="handleLogin" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="handleLogin">
          登录
        </el-button>
        <div class="register-link">
          <a href="javascript:void(0)" @click="showRegister = !showRegister">
            没有账号？注册
          </a>
        </div>
      </el-form>

      <!-- 注册表单 -->
      <el-form v-if="showRegister" @submit.prevent="handleRegister" label-position="top" style="margin-top: 16px; border-top: 1px solid var(--border); padding-top: 16px">
        <el-form-item label="用户名">
          <el-input v-model="regForm.username" placeholder="用户名" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="regForm.password" type="password" placeholder="密码" show-password />
        </el-form-item>
        <el-form-item label="显示名（选填）">
          <el-input v-model="regForm.display_name" placeholder="显示名" />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="handleRegister">
          注册
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: #f2f6f4;
}
.login-card {
  width: min(380px, 100%);
  padding: 34px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid var(--border-light);
  box-shadow: 0 18px 48px rgba(44, 67, 62, 0.09);
}
.login-mark {
  display: grid;
  width: 40px;
  height: 40px;
  margin-bottom: 18px;
  place-items: center;
  color: #fff;
  background: var(--primary);
  border-radius: 8px;
}
.login-mark svg { width: 20px; height: 20px; }
.login-card :deep(.el-form-item__label) {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}
.title {
  font-size: 22px;
  font-weight: 600;
  margin-bottom: 4px;
}
.subtitle {
  color: var(--text-tertiary);
  font-size: 13px;
  margin-bottom: 28px;
}
.register-link {
  text-align: center;
  margin-top: 12px;
  font-size: 13px;
}
.register-link a:hover { text-decoration: underline; }
</style>
