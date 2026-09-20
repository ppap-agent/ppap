<script setup lang="ts">
import {onMounted, ref} from 'vue'
import {request, type Round} from './api'
const rows=ref<Round[]>([]), query=ref(''), execution=ref(''), error=ref(''), busy=ref(false), next=ref<number|null>(null)
async function load(reset=true) {
  if (busy.value) return
  busy.value=true; error.value=''
  try { const result=await request<{rounds:Round[]; next_offset:number|null}>(`/rounds?${new URLSearchParams({q:query.value,execution:execution.value,offset:String(reset?0:next.value || 0)})}`); rows.value=reset?result.rounds:rows.value.concat(result.rounds); next.value=result.next_offset }
  catch(e) {error.value=e instanceof Error?e.message:'无法加载审核记录'} finally {busy.value=false}
}
const labels:Record<string,string>={queued:'排队中',running:'审核中',completed:'已完成',failed:'执行失败'}
onMounted(()=>load())
</script>
<template><section class="pc-card"><form class="pc-toolbar" @submit.prevent="load()"><input v-model="query" type="search" placeholder="搜索文件或规则" aria-label="搜索审核" :disabled="busy" /><select v-model="execution" aria-label="执行状态" :disabled="busy"><option value="">全部状态</option><option v-for="(label,key) in labels" :key="key" :value="key">{{ label }}</option></select><button :disabled="busy">查询</button><a href="/">新建审核</a></form><p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="!rows.length && !busy">还没有审核记录。</p><div class="table-scroll"><table v-if="rows.length"><thead><tr><th>待审文件</th><th>提交时间</th><th>状态</th><th>审核结果</th><th></th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td>{{ row.inputs.filter(item=>item.role==='target').map(item=>item.original_filename).join('、') }}</td><td>{{ row.created_at?new Date(row.created_at).toLocaleString():'' }}</td><td>{{ labels[row.execution] || row.execution }}</td><td><span v-for="status in ['PASS','NG','ERR']" :key="status" :class="`status-${status}`">{{ status }} {{ row.result_counts?.[status] || 0 }}　</span></td><td><a :href="`/?round=${row.id}`">查看审核</a></td></tr></tbody></table></div><button v-if="next!==null" :disabled="busy" @click="load(false)">加载更多</button></section></template>
