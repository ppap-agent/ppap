<script setup lang="ts">
import {onMounted, ref} from 'vue'
import {dateLabel, request} from './api'
interface Usage {id:string;created_at:string;consumed_points:number;is_free_trial:boolean;round_id?:string}
const rows=ref<Usage[]>([]), error=ref(''), busy=ref(false), next=ref<number|null>(null), since=ref(''), until=ref('')
async function load(reset=true) {
  if(busy.value)return
  error.value=''
  if(since.value && until.value && since.value>until.value){error.value='起始日期不能晚于截止日期。';return}
  busy.value=true
  try {
    const query=new URLSearchParams({offset:String(reset?0:next.value||0)})
    if(since.value)query.set('since',since.value)
    if(until.value)query.set('until',until.value)
    const data=await request<{events:Usage[];next_offset:number|null}>('/usage?'+query)
    rows.value=reset?data.events:rows.value.concat(data.events);next.value=data.next_offset
  } catch(e) {error.value=e instanceof Error?e.message:'无法读取用量'}
  finally {busy.value=false}
}
onMounted(()=>load())
</script>
<template>
  <section class="page-content">
    <form class="toolbar" @submit.prevent="load()"><label>起始日期<input v-model="since" type="date" :disabled="busy" /></label><label>截止日期<input v-model="until" type="date" :disabled="busy" /></label><button :disabled="busy">查询</button></form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy && !rows.length" class="loading-state" role="status">正在加载…</p>
    <div v-else-if="!rows.length && !error" class="empty-state"><h2>当前范围没有用量记录</h2></div>
    <div v-if="rows.length" class="table-scroll"><table class="responsive-table"><thead><tr><th>时间</th><th>用量（点）</th><th>类型</th><th><span class="sr-only">关联审核</span></th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td data-label="时间">{{ dateLabel(row.created_at) }}</td><td data-label="用量（点）">{{ row.consumed_points.toLocaleString('zh-CN') }}</td><td data-label="类型">{{ row.is_free_trial?'试用':'额度' }}</td><td class="row-action"><a v-if="row.round_id" :href="'/?round='+row.round_id">查看审核 →</a></td></tr></tbody></table></div>
    <button v-if="next!==null" class="load-more" :disabled="busy" @click="load(false)">加载更多</button>
  </section>
</template>
