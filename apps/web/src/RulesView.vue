<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref} from 'vue'
import {request, upload, originalUrl, type Rule, type Material, type Job} from './api'
import rulesIcon from './assets/rules.svg'
interface RuleItem {id:string; category:string; priority:string|null; rule_to_check:string; type_of_rule:string}
interface RuleBody {kind:string; type:string; rule_profile:string; customers:string[]; models:string[]; parts:string[]; rules:RuleItem[]}
interface Detail extends Rule {body:RuleBody; source_file_id:string; source_file_version_id:string}
interface Candidate {title:string; body:RuleBody; source_file_version_id:string}
const rules=ref<Rule[]>([]), selected=ref<Detail|null>(null), candidate=ref<Candidate|null>(null), source=ref<Material>(), types=ref<string[]>([]), type=ref(''), query=ref(''), error=ref(''), status=ref(''), busy=ref(false), predecessor=ref<string|null>(null)
const customer=ref(''), customerQuery=ref(''), view=ref<'list'|'detail'|'upload'>('list'), showVersions=ref(false)
const parseId=ref(new URLSearchParams(location.search).get('parse') || '')
function guardLeave(event:BeforeUnloadEvent){if(candidate.value){event.preventDefault();event.returnValue=''}}
const currentRules=computed(()=>{const older=new Set(rules.value.map(rule=>rule.predecessor_id).filter(Boolean));return rules.value.filter(rule=>!older.has(rule.id))})
const customers=computed(()=>Array.from(new Set(currentRules.value.flatMap(rule=>rule.customers?.length?rule.customers:['未指定客户']))).sort().filter(name=>name.includes(customerQuery.value)))
function belongs(rule:Rule,name:string){return name==='未指定客户'?!rule.customers?.length:rule.customers?.includes(name)}
const visible=computed(()=>(showVersions.value?rules.value:currentRules.value).filter(rule=>(!customer.value || belongs(rule,customer.value)) && (rule.title+' '+rule.type).toLowerCase().includes(query.value.toLowerCase())))
function leaveCandidate(){return !candidate.value || confirm('解析内容尚未保存，是否离开？')}
function showList(name=customer.value){if(busy.value || !leaveCandidate())return;customer.value=name;candidate.value=null;selected.value=null;view.value='list';predecessor.value=null;status.value='';history.replaceState(null,'','/rules')}
function newRule(){if(busy.value || !leaveCandidate())return;candidate.value=null;selected.value=null;predecessor.value=null;type.value='';status.value='';view.value='upload';history.replaceState(null,'','/rules')}
const body=computed(()=>candidate.value?.body || selected.value?.body)
const sourceFile=computed(()=>candidate.value?source.value:selected.value?{file_id:selected.value.source_file_id,file_version_id:selected.value.source_file_version_id,original_filename:selected.value.title,role:'reference' as const,rule_version_id:null}:undefined)
let timer:ReturnType<typeof setTimeout>|undefined, disposed=false
async function action(work:()=>Promise<void>) {if(busy.value)return; busy.value=true; error.value=''; try{await work()}catch(e){error.value=e instanceof Error?e.message:'操作失败'}finally{busy.value=false}}
async function refresh(){rules.value=(await request<{rules:Rule[]}>('/rules')).rules}
async function select(id:string){if(!leaveCandidate())return;await action(async()=>{selected.value=await request<Detail>(`/rules/${id}`);candidate.value=null;predecessor.value=null;status.value='';view.value='detail'})}
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
async function publish(){await action(async()=>{if(!candidate.value)return;const result=await request<Detail>('/rules','POST',{...candidate.value,predecessor_id:predecessor.value});candidate.value=null;selected.value=result;await refresh();history.replaceState(null,'','/rules');status.value='已保存规则'})}
async function resumeParse(){if(busy.value || !parseId.value)return;busy.value=true;error.value='';view.value='upload';try{const job=await request<Job<Candidate>&{source_file_id:string;source_file_version_id:string;document_type:string}>(`/rules/parse/${parseId.value}`);if(disposed)return;source.value={file_id:job.source_file_id,file_version_id:job.source_file_version_id,original_filename:'规则原件',role:'reference',rule_version_id:null};type.value=job.document_type;predecessor.value=new URLSearchParams(location.search).get('predecessor');await poll(parseId.value)}catch{busy.value=false;error.value='无法读取解析任务，请稍后重新读取。'}}
onMounted(async()=>{window.addEventListener('beforeunload',guardLeave);await action(async()=>{await refresh();types.value=(await request<{types:string[]}>('/rule-types')).types});if(parseId.value && !disposed)await resumeParse()})
onBeforeUnmount(()=>{disposed=true;clearTimeout(timer);window.removeEventListener('beforeunload',guardLeave)})
</script>
<template>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div class="rules-layout" :data-view="view">
    <aside class="pc-card rules-categories">
      <p class="section-label">按客户浏览</p>
      <input v-model="customerQuery" type="search" placeholder="搜索客户" aria-label="搜索客户" />
      <button class="customer-link" :aria-pressed="!customer" :disabled="busy" @click="showList('')"><span class="customer-avatar"><img :src="rulesIcon" alt="" width="20" height="20" /></span><span>全部规则<small>{{ currentRules.length }} 份规则</small></span></button>
      <button v-for="name in customers" :key="name" class="customer-link" :aria-pressed="customer===name" :disabled="busy" @click="showList(name)"><span class="customer-avatar">{{ name[0] }}</span><span>{{ name }}<small>{{ currentRules.filter(rule=>belongs(rule,name)).length }} 份规则</small></span></button>
      <p v-if="!customers.length && customerQuery" class="pc-muted">没有匹配的客户</p>
    </aside>
    <section class="pc-card rule-content" :aria-busy="busy">
      <template v-if="view==='list'">
        <div class="section-heading"><div><h2>{{ customer || '全部规则' }}</h2><p class="pc-muted">阅读客户要求，核对适用范围与原始文件。</p></div><button class="pc-primary" :disabled="busy" @click="newRule">上传规则</button></div>
        <form class="pc-toolbar rule-toolbar" @submit.prevent><input v-model="query" type="search" placeholder="搜索规则名称或文件类型" aria-label="搜索规则" /><label class="inline-check"><input v-model="showVersions" type="checkbox" />显示历史版本</label></form>
        <p v-if="busy" class="loading-state" role="status">正在读取规则…</p>
        <div v-else-if="!visible.length" class="empty-state"><img :src="rulesIcon" alt="" width="36" height="36" /><h3>{{ query?'没有匹配的规则':'这里还没有规则' }}</h3><p>{{ query?'换一个关键词试试。':'上传客户的规则文件，解析为易读的检查条目。' }}</p><button v-if="!query" :disabled="busy" @click="newRule">上传第一份规则</button></div>
        <div v-else class="table-scroll"><table class="rules-table"><thead><tr><th>规则名称</th><th>文件类型</th><th>更新时间</th></tr></thead><tbody><tr v-for="rule in visible" :key="rule.id"><td><button class="text-button" :disabled="busy" @click="select(rule.id)"><img :src="rulesIcon" alt="" width="18" height="18" />{{ rule.title }}</button><small v-if="!currentRules.some(row=>row.id===rule.id)" class="history-label">历史版本</small></td><td>{{ rule.type || '未指定类型' }}</td><td>{{ new Date(rule.created_at).toLocaleDateString() }}</td></tr></tbody></table></div>
        <p class="rule-footnote">解析后的客户规则供阅读与核对，目前不会自动改变内置审核程序。</p>
      </template>
      <template v-else>
        <button class="back-link" :disabled="busy" @click="showList()">返回规则列表</button>
        <div class="section-heading"><div><h2>{{ candidate?'核对解析内容':selected?.title || '上传客户规则' }}</h2><p class="pc-muted">{{ candidate?'请核对 AI 提取的条目，再保存到规则库。':'保留规则版本和来源，方便审核时核对。' }}</p></div></div>
        <fieldset v-if="view==='upload'" class="rule-upload" :disabled="busy"><legend>{{ predecessor?'上传新版本':'选择规则文件' }}</legend><p class="pc-muted">支持 Excel、Word、PDF、Markdown 和 JSON。</p><label>文档类型<select v-model="type"><option value="">自动识别</option><option v-for="name in types" :key="name">{{ name }}</option></select></label><label class="rule-upload-file">规则原件<input type="file" accept=".xlsx,.xls,.xlsm,.doc,.docx,.pdf,.md,.json" @change="parse" /></label></fieldset>
        <p v-if="status" role="status" class="rule-status">{{ ({queued:'等待解析',leased:'正在解析，请稍候…',succeeded:'解析完成，请核对后保存'} as Record<string,string>)[status] || status }}</p>
        <button v-if="parseId && !candidate && (error || status==='unknown')" :disabled="busy" @click="resumeParse">重新读取解析状态（不重跑）</button>
        <template v-if="body && view==='detail'">
          <label v-if="candidate">规则名称<input v-model="candidate.title" :disabled="busy" /></label>
          <dl class="rule-metadata"><div><dt>文件类型</dt><dd>{{ body.type }}</dd></div><div><dt>适用客户</dt><dd>{{ body.customers?.join('、') || '未指定' }}</dd></div><div><dt>型号 / 零件</dt><dd>{{ [...(body.models || []),...(body.parts || [])].join('、') || '未指定' }}</dd></div><div v-if="selected && !candidate"><dt>版本时间</dt><dd>{{ new Date(selected.created_at).toLocaleString() }}</dd></div></dl>
          <div class="rule-detail-actions"><a v-if="sourceFile?.file_id" :href="originalUrl(sourceFile)" target="_blank" rel="noopener">查看原始文件</a><button v-if="selected?.predecessor_id && !candidate" :disabled="busy" @click="select(selected.predecessor_id)">上一版本</button><button v-if="selected && !candidate" :disabled="busy" @click="predecessor=selected.id;type=selected.body.type;view='upload';status=''">上传新版本</button></div>
          <div class="rule-items-heading"><h3>解析条目</h3><span>{{ body.rules.length }} 项</span></div>
          <article v-for="(item,index) in body.rules" :key="item.id" class="rule-item"><h3><span class="rule-number">{{ String(index+1).padStart(2,'0') }}</span>{{ item.category }} <small class="priority-label">{{ item.priority }}</small></h3><textarea v-if="candidate" v-model="item.rule_to_check" :disabled="busy" rows="3" :aria-label="`检查要求 ${index+1}`" /><p v-else>{{ item.rule_to_check }}</p></article>
          <div v-if="candidate" class="audit-actions"><button class="pc-primary" :disabled="busy || !candidate.title.trim()" @click="publish">保存规则</button></div>
        </template>
      </template>
    </section>
  </div>
</template>
