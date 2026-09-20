<script setup lang="ts">
import {onMounted, ref} from 'vue'
import {apiUrl, connect, profile, request} from './api'
import Workbench from './Workbench.vue'
import HistoryView from './HistoryView.vue'
import RulesView from './RulesView.vue'
import UsageView from './UsageView.vue'
import brandIcon from './assets/brand.svg'
import filesIcon from './assets/files.svg'
import rulesIcon from './assets/rules.svg'
import usageIcon from './assets/usage.svg'
import collapseIcon from './assets/collapse.svg'

const page = location.pathname.replace(/\/$/,'')
const title = ({'/audit-files':'审核文件','/rules':'规则库','/usage':'用量记录'} as Record<string,string>)[page] || '资料审核'

const ready = ref(false), error = ref(''), user = ref(''), development = ref(false), menuOpen = ref(false), collapsed = ref(false)
const navigation=[{path:'/',label:'新建审核',icon:brandIcon},{path:'/audit-files',label:'审核文件',icon:filesIcon},{path:'/rules',label:'规则库',icon:rulesIcon},{path:'/usage',label:'用量记录',icon:usageIcon}]
const loginUrl = ref('#')
onMounted(async () => {
  try {
    await connect(); development.value = profile === 'isolated-dev'
    loginUrl.value = `${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`
    try { const me=await request<{display_name:string;identity:{subject:string}}>('/me'); user.value=me.display_name || '已登录用户' } catch { /* Login remains available. */ }
    ready.value = true
  } catch { error.value = '暂时无法连接服务，请确认本地 API 已启动后刷新。' }
})
async function logout() { try {await request('/logout', 'POST'); location.reload()} catch {error.value='退出失败，请稍后重试。'} }
async function devLogin() { await request('/isolated-dev/sessions', 'POST', {subject: 'alice'}); location.reload() }
</script>

<template>
  <aside class="pc-shell" :class="{'is-collapsed':collapsed,'is-open':menuOpen}">
    <div class="pc-shell__brand"><img :src="brandIcon" alt="" width="24" height="24" /><span>PPAP Audit</span><button class="mobile-menu" :aria-expanded="menuOpen" aria-controls="main-navigation" @click="menuOpen=!menuOpen">{{ menuOpen?'收起':'菜单' }}</button></div>
    <nav id="main-navigation" class="pc-shell__nav" aria-label="主导航"><a v-for="link in navigation" :key="link.path" :href="link.path" :title="link.label" :aria-current="(page || '/')===link.path?'page':undefined" :class="{'is-active':(page || '/')===link.path}"><img :src="link.icon" alt="" width="22" height="22" /><span>{{ link.label }}</span></a></nav>
    <div class="pc-shell__session"><span>{{ user || '未登录' }}</span><button class="sidebar-toggle" :aria-label="collapsed?'展开导航':'收起导航'" :aria-expanded="!collapsed" @click="collapsed=!collapsed"><img :src="collapseIcon" alt="" width="18" height="18" /></button></div>
  </aside>
  <main class="pc-main">
    <header class="audit-heading"><h1>{{ title }}</h1><div class="session-actions"><span v-if="user" class="session-name">{{ user }}</span><a v-else :href="loginUrl">登录飞书</a><button v-if="user" type="button" @click="logout">退出</button></div></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="!ready && !error" class="loading-state" role="status">正在连接审核服务…</p><template v-if="ready"><HistoryView v-if="page==='/audit-files'" /><RulesView v-else-if="page==='/rules'" /><UsageView v-else-if="page==='/usage'" /><Workbench v-else /></template>
    <details v-if="development" class="pc-runtime"><summary>开发工具</summary><button type="button" @click="devLogin">使用隔离测试身份</button></details>
  </main>
</template>
