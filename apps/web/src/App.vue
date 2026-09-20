<script setup lang="ts">
import {onMounted, ref} from 'vue'
import {apiUrl, connect, profile, request} from './api'
import Workbench from './Workbench.vue'
import HistoryView from './HistoryView.vue'
import RulesView from './RulesView.vue'
import UsageView from './UsageView.vue'

const page = location.pathname.replace(/\/$/,'')
const title = ({'/audit-files':'审核文件','/rules':'规则库','/usage':'用量记录'} as Record<string,string>)[page] || '资料审核'

const ready = ref(false), error = ref(''), user = ref('未登录'), development = ref(false)
const loginUrl = ref('#')
onMounted(async () => {
  try {
    await connect(); development.value = profile === 'isolated-dev'
    loginUrl.value = `${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`
    try { user.value = (await request<{identity:{subject:string}}>('/me')).identity.subject } catch { /* Login remains available. */ }
    ready.value = true
  } catch { error.value = '暂时无法连接服务，请确认本地 API 已启动后刷新。' }
})
async function logout() { await request('/logout', 'POST'); location.reload() }
async function devLogin() { await request('/isolated-dev/sessions', 'POST', {subject: 'alice'}); location.reload() }
</script>

<template>
  <aside class="pc-shell">
    <div class="pc-shell__brand">PPAP Audit</div>
    <nav class="pc-shell__nav" aria-label="主导航"><a href="/" :class="{'is-active':!page}">新建审核</a><a href="/audit-files" :class="{'is-active':page==='/audit-files'}">审核文件</a><a href="/rules" :class="{'is-active':page==='/rules'}">规则库</a><a href="/usage" :class="{'is-active':page==='/usage'}">用量记录</a></nav>
    <p class="pc-shell__session">{{ user }}</p>
  </aside>
  <main class="pc-main">
    <header class="audit-heading"><h1>{{ title }}</h1><div><a :href="loginUrl">登录飞书</a><button type="button" @click="logout">退出</button></div></header>
    <p v-if="error" role="alert">{{ error }}</p><template v-if="ready"><HistoryView v-if="page==='/audit-files'" /><RulesView v-else-if="page==='/rules'" /><UsageView v-else-if="page==='/usage'" /><Workbench v-else /></template>
    <details v-if="development" class="pc-runtime"><summary>开发工具</summary><button type="button" @click="devLogin">使用隔离测试身份</button></details>
  </main>
</template>
