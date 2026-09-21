<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref} from 'vue'
import {request, upload, originalUrl, latestRules, dateLabel, type Rule, type Material, type Job} from './api'
import rulesIcon from './assets/rules.svg'
interface RuleItem {id:string; category:string; priority:string|null; rule_to_check:string; type_of_rule:string}
interface RuleBody {kind:string; type:string; rule_profile:string; customers:string[]; models:string[]; parts:string[]; rules:RuleItem[]}
interface Detail extends Rule {body:RuleBody; source_file_id:string; source_file_version_id:string}
interface Candidate {title:string; body:RuleBody; source_file_version_id:string}
const rules=ref<Rule[]>([]), selected=ref<Detail|null>(null), candidate=ref<Candidate|null>(null), source=ref<Material>(), types=ref<string[]>([]), type=ref(''), query=ref(''), error=ref(''), status=ref(''), busy=ref(false), predecessor=ref<string|null>(null)
const customer=ref(''), view=ref<'list'|'detail'|'upload'>('list'), showVersions=ref(false)
const parseId=ref(new URLSearchParams(location.search).get('parse') || '')
function guardLeave(event:BeforeUnloadEvent){if(candidate.value){event.preventDefault();event.returnValue=''}}
const currentRules=computed(()=>latestRules(rules.value))
const customers=computed(()=>Array.from(new Set(currentRules.value.flatMap(rule=>rule.customers?.length?rule.customers:['未指定客户']))).sort())
function belongs(rule:Rule,name:string){return name==='未指定客户'?!rule.customers?.length:rule.customers?.includes(name)}
const visible=computed(()=>(showVersions.value?rules.value:currentRules.value).filter(rule=>(!customer.value || belongs(rule,customer.value)) && (rule.title+' '+rule.type).toLowerCase().includes(query.value.toLowerCase())))
function leaveCandidate(){return !candidate.value || confirm('解析内容尚未保存，是否离开？')}
function showList(name=customer.value){if(busy.value || !leaveCandidate())return;customer.value=name;candidate.value=null;selected.value=null;view.value='list';predecessor.value=null;parseId.value='';status.value='';error.value='';setUrl()}
function newRule(){if(busy.value || !leaveCandidate())return;candidate.value=null;selected.value=null;predecessor.value=null;parseId.value='';source.value=undefined;type.value='';status.value='';error.value='';view.value='upload';setUrl()}
const body=computed(()=>candidate.value?.body || selected.value?.body)
const sourceFile=computed(()=>candidate.value?source.value:selected.value?{file_id:selected.value.source_file_id,file_version_id:selected.value.source_file_version_id,original_filename:selected.value.title,role:'reference' as const,rule_version_id:null}:undefined)

const params = new URLSearchParams(location.search)
const backTo = params.get('return_to') || ''
const returnTo = /^\/\?draft=[a-f0-9-]+(?:&from=[a-f0-9-]+)?$/.test(backTo) ? backTo : ''
function setUrl(ruleId?:string) {
  const query = new URLSearchParams()
  if(ruleId)query.set('rule',ruleId)
  if(returnTo)query.set('return_to',returnTo)
  history.replaceState(null,'','/rules'+(query.size?'?'+query:''))
}
function setScope(field:'customers'|'models'|'parts', event:Event) {
  if(candidate.value)candidate.value.body[field]=[...new Set((event.target as HTMLInputElement).value.split(/[,，、]/).map(value=>value.trim()).filter(Boolean))]
}

let timer:ReturnType<typeof setTimeout>|undefined, disposed=false
async function action(work:()=>Promise<void>) {if(busy.value)return; busy.value=true; error.value=''; try{await work()}catch(e){error.value=e instanceof Error?e.message:'操作失败'}finally{busy.value=false}}
async function refresh(){rules.value=(await request<{rules:Rule[]}>('/rules')).rules}
async function select(id:string){if(!leaveCandidate())return;await action(async()=>{selected.value=await request<Detail>(`/rules/${id}`);candidate.value=null;predecessor.value=null;parseId.value='';status.value='';view.value='detail';setUrl(selected.value?.id)})}
async function poll(id:string){
  try{const job=await request<Job<Candidate>>(`/rules/parse/${id}`);if(disposed)return;status.value=job.status
    if(job.status==='succeeded' && job.result){candidate.value=job.result;view.value='detail';busy.value=false}
    else if(['queued','leased'].includes(job.status))timer=setTimeout(()=>{void poll(id)},2000)
    else{busy.value=false;error.value=job.status==='unknown'?'解析结果暂不确定，请稍后刷新查看，不要重复提交。':job.result?.error || '解析失败，请检查原件与文档类型。'}
  }catch{busy.value=false;error.value='无法获取解析状态，请刷新重访当前任务。'}
}
async function parse(event:Event){const file=(event.target as HTMLInputElement).files?.[0];if(!file || busy.value)return;busy.value=true;error.value='';candidate.value=null
  try{source.value=await upload(file,'reference');const job=await request<Job<Candidate>>('/rules/parse','POST',{request_id:crypto.randomUUID(),source_file_id:source.value.file_id,source_file_version_id:source.value.file_version_id,document_type:type.value});parseId.value=job.id;const url=new URL(location.href);url.searchParams.set('parse',job.id);if(predecessor.value)url.searchParams.set('predecessor',predecessor.value);else url.searchParams.delete('predecessor');history.replaceState(null,'',url);await poll(job.id)}
  catch(e){busy.value=false;error.value=e instanceof Error?e.message:'上传失败'}
}
async function publish(){await action(async()=>{if(!candidate.value)return;const result=await request<Detail>('/rules','POST',{...candidate.value,predecessor_id:predecessor.value});candidate.value=null;selected.value=result;parseId.value='';await refresh();setUrl(result.id);status.value='已保存规则'})}
async function resumeParse(){if(busy.value || !parseId.value)return;busy.value=true;error.value='';view.value='upload';try{const job=await request<Job<Candidate>&{source_file_id:string;source_file_version_id:string;document_type:string}>(`/rules/parse/${parseId.value}`);if(disposed)return;source.value={file_id:job.source_file_id,file_version_id:job.source_file_version_id,original_filename:'规则原件',role:'reference',rule_version_id:null};type.value=job.document_type;predecessor.value=new URLSearchParams(location.search).get('predecessor');await poll(parseId.value)}catch{busy.value=false;error.value='无法读取解析任务，请稍后重新读取。'}}
onMounted(async()=>{window.addEventListener('beforeunload',guardLeave);await action(async()=>{await refresh();types.value=(await request<{types:string[]}>('/rule-types')).types});if(parseId.value && !disposed)await resumeParse();else if(params.get('rule'))await select(params.get('rule')!);else if(params.has('new'))newRule()})
onBeforeUnmount(()=>{disposed=true;clearTimeout(timer);window.removeEventListener('beforeunload',guardLeave)})
</script>
<template>
  <section class="page-content rules-page">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <a v-if="returnTo && !candidate" class="back-link" :href="returnTo">← 返回审核准备</a>
    <template v-if="view==='list'">
      <div class="toolbar">
        <input v-model="query" type="search" placeholder="搜索规则名称或文件类型" aria-label="搜索规则" />
        <select v-model="customer" aria-label="客户筛选"><option value="">全部客户</option><option v-for="name in customers" :key="name">{{ name }}</option></select>
        <label class="inline-check"><input v-model="showVersions" type="checkbox" />历史版本</label>
        <button class="pc-primary toolbar-end" :disabled="busy" @click="newRule">上传规则</button>
      </div>
      <p v-if="busy" class="loading-state" role="status">正在读取规则…</p>
      <div v-else-if="!visible.length" class="empty-state"><img :src="rulesIcon" alt="" width="36" height="36" /><h2>{{ query || customer?'没有匹配的规则':'还没有客户规则' }}</h2><button v-if="query || customer" @click="query='';customer=''">清除筛选</button><button v-else class="pc-primary" @click="newRule">上传规则</button></div>
      <div v-else class="table-scroll"><table class="responsive-table rules-table"><thead><tr><th>规则名称</th><th>适用客户</th><th>文件类型</th><th>更新时间</th></tr></thead><tbody><tr v-for="rule in visible" :key="rule.id"><td class="record-title"><button class="text-button" :disabled="busy" @click="select(rule.id)"><img :src="rulesIcon" alt="" width="18" height="18" />{{ rule.title }}</button><span v-if="!currentRules.some(row=>row.id===rule.id)" class="badge">历史版本</span></td><td data-label="适用客户">{{ rule.customers?.join('、') || '未指定' }}</td><td data-label="文件类型">{{ rule.type || '未指定' }}</td><td data-label="更新时间">{{ dateLabel(rule.created_at) }}</td></tr></tbody></table></div>
    </template>
    <template v-else>
      <button class="back-link" :disabled="busy" @click="showList()">← 规则库</button>
      <div class="section-heading"><h2>{{ candidate?'核对规则':view==='upload'?(predecessor?'上传新版本':'上传客户规则'):selected?.title }}</h2><span v-if="view==='detail'" class="badge">人工复核参考</span></div>
      <fieldset v-if="view==='upload'" class="rule-upload" :disabled="busy">
        <label>文档类型<select v-model="type"><option value="">自动识别</option><option v-for="name in types" :key="name">{{ name }}</option></select></label>
        <label class="file-button button pc-primary">选择规则文件<input type="file" aria-label="规则原件" accept=".xlsx,.xls,.xlsm,.docx,.pdf,.md,.json" @change="parse" /></label>
        <p class="pc-muted">Excel、Word、PDF、Markdown、JSON</p>
      </fieldset>
      <p v-if="status" role="status" class="notice">{{ ({queued:'正在排队解析…',leased:'正在解析规则…',succeeded:'解析完成，请核对并保存。'} as Record<string,string>)[status] || status }}</p>
      <button v-if="parseId && !candidate && (error || status==='unknown')" :disabled="busy" @click="resumeParse">刷新解析状态</button>
      <template v-if="body && view==='detail'">
        <label v-if="candidate" class="rule-name">规则名称<input v-model="candidate.title" :disabled="busy" /></label>
        <dl class="rule-metadata">
          <div><dt>文件类型</dt><dd><select v-if="candidate" v-model="candidate.body.type" aria-label="核对规则类型" :disabled="busy"><option v-for="name in types" :key="name">{{ name }}</option></select><template v-else>{{ body.type }}</template></dd></div>
          <div><dt>适用客户</dt><dd><input v-if="candidate" :value="body.customers.join('、')" aria-label="适用客户" :disabled="busy" @change="setScope('customers',$event)" /><template v-else>{{ body.customers?.join('、') || '未指定' }}</template></dd></div>
          <div><dt>型号</dt><dd><input v-if="candidate" :value="body.models.join('、')" aria-label="型号" :disabled="busy" @change="setScope('models',$event)" /><template v-else>{{ body.models?.join('、') || '未指定' }}</template></dd></div>
          <div><dt>零件</dt><dd><input v-if="candidate" :value="body.parts.join('、')" aria-label="零件" :disabled="busy" @change="setScope('parts',$event)" /><template v-else>{{ body.parts?.join('、') || '未指定' }}</template></dd></div>
        </dl>
        <div class="toolbar rule-detail-actions"><a v-if="sourceFile?.file_id" :href="originalUrl(sourceFile)" target="_blank" rel="noopener">原始文件 ↗</a><span v-if="selected && !candidate" class="pc-muted">版本 · {{ dateLabel(selected.created_at) }}</span><button v-if="selected?.predecessor_id && !candidate" :disabled="busy" @click="select(selected.predecessor_id)">上一版本</button><button v-if="selected && !candidate" :disabled="busy" @click="predecessor=selected.id;type=selected.body.type;view='upload';status=''">上传新版本</button></div>
        <p class="scope-note">客户要求用于人工复核，不会自动增加检查项。</p>
        <div class="section-heading"><h3>客户要求</h3><span class="pc-muted">{{ body.rules?.length || 0 }} 项</span></div>
        <p v-if="!body.rules?.length" class="empty-state">未解析出规则条目，请核对原始文件。</p>
        <article v-for="(item,index) in body.rules" :key="item.id" class="rule-item"><h3><span class="rule-number">{{ String(index+1).padStart(2,'0') }}</span>{{ item.category }} <small v-if="item.priority" class="badge">{{ item.priority }}</small></h3><textarea v-if="candidate" v-model="item.rule_to_check" :disabled="busy" rows="3" :aria-label="'检查要求 '+(index+1)" /><p v-else>{{ item.rule_to_check }}</p></article>
        <div v-if="candidate" class="action-bar sticky-actions"><span class="pc-muted">{{ body.rules.length }} 项客户要求</span><button class="pc-primary" :disabled="busy || !candidate.title.trim()" @click="publish">保存规则</button></div>
        <a v-else-if="returnTo" class="button pc-primary" :href="returnTo">返回审核准备</a>
      </template>
    </template>
  </section>
</template>
