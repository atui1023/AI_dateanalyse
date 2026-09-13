<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import ChatMessage from '@/components/ChatMessage.vue'
import * as api from '@/api/workbench'
const route = useRoute()
const data = ref<any>(null)
const comment = ref('')
const password = ref('')
const needsPassword = ref(false)
async function load() {
  try {
    data.value = (await api.getShared(String(route.params.token), password.value || undefined)).data
    needsPassword.value = false
  } catch (error: any) {
    if (error?.response?.status === 401) needsPassword.value = true
    else ElMessage.error(error?.response?.data?.detail || '分享链接加载失败')
  }
}
async function submit() {
  if (!comment.value.trim()) return
  await api.addComment(String(route.params.token), { author_name: '访客', content: comment.value }, password.value || undefined)
  comment.value = ''
  await load()
  ElMessage.success('评论已提交')
}
onMounted(load)
</script>
<template>
  <main class="shared-view"><div v-if="needsPassword" class="password-gate"><h1>此分享需要密码</h1><el-input v-model="password" type="password" show-password placeholder="输入分享密码" @keyup.enter="load"><template #append><el-button type="primary" @click="load">验证</el-button></template></el-input></div><el-skeleton v-else-if="!data" :rows="6" animated /><template v-else><h1>{{ data.dashboard?.name || '共享分析结果' }}</h1><p class="desc">{{ data.dashboard?.description || '此页面由数据分析平台生成' }}</p><ChatMessage v-if="data.result" role="assistant" :text="data.result.question || ''" :result="data.result" :allow-download="data.allow_download" /><section v-for="item in data.items || []" :key="item.id" class="shared-item"><h2>{{ item.title }}</h2><ChatMessage v-if="item.result" role="assistant" text="" :result="item.result" :chart-config="item.chart_config" :allow-download="data.allow_download" /></section><section class="comments"><h2>评论</h2><div v-for="item in data.comments" :key="item.id" class="comment"><strong>{{ item.author_name }}</strong>：{{ item.content }}</div><el-input v-model="comment" placeholder="留下评论" @keyup.enter="submit"><template #append><el-button @click="submit">发送</el-button></template></el-input></section></template></main>
</template>
<style scoped>
.shared-view { max-width: 1000px; margin: 0 auto; min-height: 100%; padding: 36px 20px; background: var(--bg); }
.password-gate { max-width: 420px; margin: 18vh auto 0; }
h1 { font-size: 26px; margin-bottom: 7px; }
.desc { color: var(--text-secondary); margin-bottom: 22px; }
.shared-item, .comments { background: #fff; border: 1px solid var(--border-light); border-radius: 8px; padding: 20px; margin-top: 18px; box-shadow: 0 3px 12px rgba(35, 54, 50, .025); }
h2 { font-size: 16px; margin-bottom: 14px; }
.comment { border-bottom: 1px solid var(--border-light); padding: 9px 0; font-size: 13px; }
</style>
