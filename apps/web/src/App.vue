<script setup lang="ts">
import {computed, onMounted, ref} from 'vue'
import {apiUrl, connect, profile, request} from './api'
import Workbench from './Workbench.vue'
import HistoryView from './HistoryView.vue'
import RulesView from './RulesView.vue'
import UsageView from './UsageView.vue'
import brandIcon from './assets/brand.svg'
import filesIcon from './assets/files.svg'
import rulesIcon from './assets/rules.svg'
import usageIcon from './assets/usage.svg'

const page = location.pathname.replace(/\/$/, '') || '/'
const navigation = [
  {path:'/', label:'新建审核', icon:brandIcon},
  {path:'/audit-files', label:'审核结果', icon:filesIcon},
  {path:'/rules', label:'规则库', icon:rulesIcon},
  {path:'/usage', label:'用量记录', icon:usageIcon},
]
const reviewing = ref(page==='/' && new URLSearchParams(location.search).has('round'))
const activePath = computed(()=>reviewing.value?'/audit-files':page)
const title = computed(()=>navigation.find(link => link.path === activePath.value)?.label || 'PPAP')
const ready = ref(false), error = ref(''), user = ref(''), menuOpen = ref(false), accountOpen = ref(false)
const loginUrl = ref('#'), development = ref(false)
// Explicit local-only entry; never part of the product navigation.
const devPage = page === '/dev'
onMounted(async () => {
  try {
    await connect()
    development.value = profile === 'isolated-dev'
    loginUrl.value = apiUrl + '/login/feishu?return_to=' + encodeURIComponent(location.href)
    try {
      const me = await request<{display_name:string}>('/me')
      user.value = me.display_name || '已登录用户'
    } catch { /* Sign-in stays available. */ }
    ready.value = true
  } catch { error.value = '暂时无法连接服务，请稍后刷新。' }
})
async function logout() {
  try { await request('/logout', 'POST'); location.reload() }
  catch { error.value = '退出失败，请稍后重试。' }
}
async function devLogin() {
  try { await request('/isolated-dev/sessions', 'POST', {subject:'alice'}); location.href = '/' }
  catch { error.value = '无法创建本地测试会话。' }
}
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <aside class="pc-shell" :class="{'is-open':menuOpen}">
    <div class="mobile-bar"><span>{{ title }}</span><button :aria-expanded="menuOpen" aria-controls="main-navigation" @click="menuOpen=!menuOpen">{{ menuOpen?'收起菜单':'菜单' }}</button></div>
    <nav id="main-navigation" class="pc-shell__nav" aria-label="主导航">
      <a v-for="link in navigation" :key="link.path" :href="link.path" :aria-current="activePath===link.path?'page':undefined" :class="{'is-active':activePath===link.path, 'nav-secondary':link.path==='/usage'}"><img :src="link.icon" alt="" width="20" height="20" /><span>{{ link.label }}</span></a>
    </nav>
    <div class="pc-shell__session" v-if="user">
      <button class="account-button" :aria-expanded="accountOpen" aria-controls="account-actions" @click="accountOpen=!accountOpen"><span class="avatar" aria-hidden="true">{{ user[0] }}</span><span>{{ user }}</span><span aria-hidden="true">⌄</span></button>
      <div id="account-actions" v-if="accountOpen" class="account-actions"><button @click="logout">退出登录</button></div>
    </div>
  </aside>
  <main id="main-content" class="pc-main" tabindex="-1">
    <h1 class="sr-only">{{ title }}</h1>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!ready && !error" class="loading-state" role="status">正在连接…</p>
    <section v-if="ready && devPage" class="empty-state">
      <template v-if="development"><h2>本地测试身份</h2><button class="pc-primary" @click="devLogin">使用隔离测试身份</button></template>
      <p v-else>此入口不可用。</p>
    </section>
    <section v-else-if="ready && !user" class="sign-in"><h2>登录后开始审核</h2><p class="pc-muted">使用飞书账号访问你的资料与审核结果。</p><a class="button pc-primary" :href="loginUrl">登录飞书</a></section>
    <template v-else-if="ready">
      <HistoryView v-if="page==='/audit-files'" />
      <RulesView v-else-if="page==='/rules'" />
      <UsageView v-else-if="page==='/usage'" />
      <Workbench v-else @reviewing="reviewing=$event" />
    </template>
  </main>
</template>
