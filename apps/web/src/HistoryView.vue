<script setup lang="ts">
import {onMounted, ref} from 'vue'
import {dateLabel, executionLabels, resultLabels, request, type Round} from './api'
const rows=ref<Round[]>([]), query=ref(''), execution=ref(''), error=ref(''), busy=ref(false), next=ref<number|null>(null)
async function load(reset=true) {
  if (busy.value) return
  busy.value=true; error.value=''
  try {
    const result=await request<{rounds:Round[];next_offset:number|null}>('/rounds?'+new URLSearchParams({q:query.value,execution:execution.value,offset:String(reset?0:next.value || 0)}))
    rows.value=reset?result.rounds:rows.value.concat(result.rounds); next.value=result.next_offset
  } catch(e) {error.value=e instanceof Error?e.message:'无法加载审核结果'}
  finally {busy.value=false}
}
function total(row:Round) {return Object.values(row.result_counts || {}).reduce((sum,n)=>sum+n,0)}
onMounted(()=>load())
</script>
<template>
  <section class="page-content">
    <form class="toolbar" @submit.prevent="load()">
      <input v-model="query" type="search" placeholder="搜索文件或规则" aria-label="搜索审核结果" :disabled="busy" />
      <select v-model="execution" aria-label="执行状态" :disabled="busy" @change="load()"><option value="">全部状态</option><option v-for="(label,key) in executionLabels" :key="key" :value="key">{{ label }}</option></select>
      <button :disabled="busy">搜索</button><a class="button pc-primary toolbar-end" href="/">新建审核</a>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy && !rows.length" class="loading-state" role="status">正在加载…</p>
    <div v-else-if="!rows.length && !error" class="empty-state"><h2>{{ query || execution?'没有符合条件的审核':'还没有审核结果' }}</h2><a v-if="!query && !execution" class="button pc-primary" href="/">新建审核</a><button v-else @click="query='';execution='';load()">清除筛选</button></div>
    <div v-if="rows.length" class="table-scroll">
      <table class="responsive-table history-table"><thead><tr><th>审核资料</th><th>提交时间</th><th>检查结果</th><th>人工复核</th><th><span class="sr-only">操作</span></th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.id">
          <td class="record-title"><a :href="'/?round='+row.id">{{ row.inputs.filter(item=>item.role==='target').map(item=>item.original_filename).join('、') }}</a><small v-if="row.predecessor_id">再次审核</small></td>
          <td data-label="提交时间"><time :datetime="row.created_at">{{ dateLabel(row.created_at) }}</time></td>
          <td data-label="检查结果"><span v-if="row.execution!=='completed'" class="badge" :class="{'status-ERR':row.execution==='failed'}">{{ executionLabels[row.execution] || row.execution }}</span><div v-else class="result-counts"><span v-for="status in ['NG','ERR','PASS']" :key="status" :class="'status-'+status">{{ resultLabels[status] }} {{ row.result_counts?.[status] || 0 }}</span></div></td>
          <td data-label="人工复核">{{ total(row)?(row.reviewed_count || 0)+' / '+total(row):'—' }}</td>
          <td class="row-action"><a :href="'/?round='+row.id">查看结果 →</a></td>
        </tr></tbody>
      </table>
    </div>
    <button v-if="next!==null" class="load-more" :disabled="busy" @click="load(false)">加载更多</button>
  </section>
</template>
