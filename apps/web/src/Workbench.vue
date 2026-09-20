<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, reactive, ref, watch} from 'vue'
import {request, upload, type Material, type Rule, type Round, type Analysis, type Job, type Finding} from './api'
import OriginalPreview from './OriginalPreview.vue'
import DrivePicker from './DrivePicker.vue'

const items = ref<Material[]>([]), rules = ref<Rule[]>([]), round = ref<Round | null>(null)
const busy = ref(false), error = ref(''), message = ref(''), selectedFileId = ref(''), selectedKey = ref(''), filter = ref(''), search = ref('')
const draftId = ref(new URLSearchParams(location.search).get('draft') || '')
const review = reactive({judgment:'', rationale:'', next_step:''}), reviewDirty = ref(false)
const workspacePanel = ref('checks')
const selectedFile = computed(() => (round.value?.inputs || items.value).find(item => item.file_version_id === selectedFileId.value))
const findings = computed(() => (round.value?.findings || []).filter(item => (!selectedFileId.value || item.locator?.file_version_id === selectedFileId.value) && (!filter.value || item.status === filter.value)))
const selectedFinding = computed(() => round.value?.findings.find(item => item.item_key === selectedKey.value))
const visibleFiles = computed(() => (round.value?.inputs || []).filter(item => item.original_filename.toLowerCase().includes(search.value.toLowerCase())))
const counts = computed(() => ['PASS','NG','ERR'].map(status => ({status, count:(round.value?.findings || []).filter(row => row.status === status).length})))
let disposed = false
const timers = new Set<ReturnType<typeof setTimeout>>()
function later(work:()=>void) { const timer = setTimeout(() => {timers.delete(timer); if (!disposed) work()}, 2000); timers.add(timer) }
function query(values: Record<string,string | null>) { const url = new URL(location.href); for (const [key,value] of Object.entries(values)) value ? url.searchParams.set(key,value) : url.searchParams.delete(key); history.replaceState(null,'',url) }
async function action(work:()=>Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await work() } catch(e) { error.value = e instanceof Error ? e.message : '操作失败，请重试。' } finally { busy.value = false } }
function choose(item?: Finding) {
  if (item?.item_key !== selectedKey.value && reviewDirty.value && !confirm('复核尚未保存，是否放弃修改？')) return false
  selectedKey.value = item?.item_key || ''; Object.assign(review, {judgment:item?.review?.judgment || '', rationale:item?.review?.rationale || '', next_step:item?.review?.next_step || ''}); reviewDirty.value = false; return true
}
function chooseFile(file:Material) { const first = round.value?.findings.find(row=>row.locator?.file_version_id === file.file_version_id && (!filter.value || row.status === filter.value)); if (choose(first)) { selectedFileId.value = file.file_version_id; workspacePanel.value = 'checks' } }
watch(filter, () => { choose(findings.value[0]) })
async function saveDraft() {
  if (!draftId.value) draftId.value = (await request<{id:string}>('/drafts','POST')).id
  await request(`/drafts/${draftId.value}`,'PUT',{items:items.value.map(item=>({file_version_id:item.file_version_id, role:item.role, rule_version_id:item.role === 'target' ? item.rule_version_id : null}))})
  query({draft:draftId.value, round:null})
}
async function readAnalysis(item:Material, id:string) {
  try {
    const job = await request<Job<Analysis>>(`/file-analyses/${id}`)
    if (disposed) return
    item.analysisStatus = job.status
    if (job.status === 'succeeded' && job.result) {
      item.analysis = job.result
      if (item.role === 'target' && !item.rule_version_id) item.rule_version_id = job.result.matched_rule_id
    } else if (['queued','leased'].includes(job.status)) later(()=>{void readAnalysis(item,id)})
  } catch { item.analysisStatus = 'failed' }
}
async function analyze(item:Material) {
  item.analysisStatus = 'queued'
  try { const job = await request<Job<Analysis>>('/file-analyses','POST',{request_id:crypto.randomUUID(),source_file_id:item.file_id,source_file_version_id:item.file_version_id}); item.analysisId=job.id; void readAnalysis(item,job.id) }
  catch { item.analysisStatus = 'failed' }
}
async function addFiles(event:Event, role:Material['role']) {
  const input = event.target as HTMLInputElement; const files = Array.from(input.files || [])
  await action(async()=>{
    for (const file of files) {
      message.value=`正在上传 ${file.name}`
      const material = await upload(file,role); items.value.push(material)
      await saveDraft(); void analyze(items.value[items.value.length-1])
    }
    input.value=''; message.value='资料已上传，请核对匹配结果。'
  })
}
async function imported(material:Material) { items.value.push(material); await saveDraft(); void analyze(items.value[items.value.length-1]) }
async function loadRound(id:string) {
  try {
    const result = await request<Round>(`/rounds/${id}`)
    if (disposed) return
    round.value = result
    if (!selectedFileId.value) {selectedFileId.value=result.inputs.find(item=>item.role==='target')?.file_version_id || result.inputs[0]?.file_version_id || ''; choose(findings.value[0])}
    if (['queued','running'].includes(result.execution)) later(()=>{void loadRound(id)})
  } catch(e) { error.value=e instanceof Error ? e.message : '无法加载审核' }
}
async function submit() { await action(async()=>{
  await saveDraft()
  const key=`ppap-idempotency-${draftId.value}`; const id=sessionStorage.getItem(key)||crypto.randomUUID(); sessionStorage.setItem(key,id)
  const result=await request<Round>(`/drafts/${draftId.value}/accept`,'POST',{idempotency_key:id})
  query({round:result.id,draft:null}); await loadRound(result.id)
}) }
async function saveReview() { await action(async()=>{
  if (!round.value || !selectedFinding.value) return
  const result=await request<Round>(`/rounds/${round.value.id}/reviews`,'PUT',{item_key:selectedFinding.value.item_key,...review,version:selectedFinding.value.review?.version || 0})
  round.value=result; reviewDirty.value=false; message.value='复核已保存。'
}) }
async function prepareAgain() { await action(async()=>{
  if (!round.value || (reviewDirty.value && !confirm('复核尚未保存，是否放弃修改？'))) return
  items.value=round.value.inputs.map(item=>({...item})); round.value=null; draftId.value=''; selectedFileId.value=''; choose(); await saveDraft()
}) }
onMounted(async()=>{
  await action(async()=>{
    rules.value=(await request<{rules:Rule[]}>('/rules')).rules
    const id=new URLSearchParams(location.search).get('round')
    if (id) await loadRound(id)
    else if (draftId.value) {
      items.value=(await request<{items:Material[]}>(`/drafts/${draftId.value}`)).items
      for (const item of items.value) {
        const saved=await request<{analysis:Job<Analysis>|null}>(`/files/${item.file_id}/versions/${item.file_version_id}/analysis`)
        if(saved.analysis){item.analysisId=saved.analysis.id;void readAnalysis(item,saved.analysis.id)}
      }
    }
  })
})
onBeforeUnmount(()=>{disposed=true; timers.forEach(clearTimeout)})
</script>

<template>
  <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="message" role="status">{{ message }}</p>
  <div v-if="!round" id="audit-preparation">
    <ol class="audit-steps"><li>1 上传资料</li><li>2 确认匹配</li><li>3 查看审核</li></ol>
    <section class="pc-card audit-upload"><h2>上传待审文件</h2><p class="pc-muted">Excel、Word 或 PDF，每个文件不超过 64 MiB。</p>
      <label class="audit-dropzone"><strong>选择待审文件</strong><span>一次可以选择多份资料</span><input type="file" multiple :disabled="busy" @change="addFiles($event,'target')" /></label>
      <details><summary>添加参考资料（可选）</summary><p>用于交叉核对，不单独审核。</p><input type="file" multiple :disabled="busy" @change="addFiles($event,'reference')" /></details>
      <DrivePicker :disabled="busy" :on-import="imported" @busy="busy=$event" />
    </section>
    <section class="pc-card"><h2>确认文件与规则</h2><p v-if="!items.length" class="pc-muted">上传文件后，在这里核对资料用途与匹配规则。</p>
      <div v-for="item in items" :key="item.file_version_id" class="audit-material">
        <div><strong>{{ item.original_filename }}</strong><small v-if="item.analysis">{{ [item.analysis.document_type,item.analysis.metadata.customer,item.analysis.metadata.model,item.analysis.metadata.part].filter(Boolean).join(' · ') || '未识别到业务信息' }}</small><small v-if="['queued','leased'].includes(item.analysisStatus || '')">正在识别信息与匹配规则…</small><small v-if="['failed','unknown'].includes(item.analysisStatus || '')">分析未完成，可手动选择规则。</small><small v-if="item.analysis?.matching_status === 'unmatched'">未找到匹配规则，请手动选择。</small></div>
        <select v-model="item.role" aria-label="文件用途" :disabled="busy" @change="item.rule_version_id=null"><option value="target">待审</option><option value="reference">参考</option></select>
        <select v-model="item.rule_version_id" aria-label="匹配规则" :disabled="busy || item.role==='reference'"><option :value="null">选择审核规则</option><option v-for="rule in rules" :key="rule.id" :value="rule.id">{{ rule.title }} · {{ rule.type }}</option></select>
        <button type="button" :disabled="busy" @click="items=items.filter(row=>row!==item)">移除</button>
      </div>
      <p class="pc-muted">客户规则解析内容供阅读；自动审核使用当前已支持的检查能力。</p>
      <div class="audit-actions"><button :disabled="busy || !items.length" @click="action(saveDraft)">保存草稿</button><button class="pc-primary" :disabled="busy || !items.some(item=>item.role==='target') || items.some(item=>item.role==='target' && !item.rule_version_id)" @click="submit">开始审核</button></div>
    </section>
  </div>
  <div v-else>
    <div class="audit-result-heading"><p><span v-for="count in counts" :key="count.status" :class="`status-${count.status}`">{{ count.status }} {{ count.count }}　</span></p><span>{{ ({queued:'排队中',running:'审核中',completed:'审核完成',failed:'执行失败'} as Record<string,string>)[round.execution] || round.execution }}</span><button :disabled="busy" @click="prepareAgain">准备下一次审核</button></div>
    <nav class="workspace-switch" aria-label="审核工作区视图"><button v-for="panel in [{id:'files',label:'文件'},{id:'checks',label:'检查结果'},{id:'original',label:'原件'}]" :key="panel.id" :aria-pressed="workspacePanel===panel.id" @click="workspacePanel=panel.id">{{ panel.label }}</button></nav>
    <div class="audit-workspace" :data-panel="workspacePanel">
      <aside class="audit-files"><label>本次资料<input v-model="search" type="search" placeholder="查找文件" /></label><button v-for="file in visibleFiles" :key="file.file_version_id" :aria-pressed="selectedFileId===file.file_version_id" @click="chooseFile(file)">{{ file.original_filename }}<small>{{ file.role==='reference'?'参考资料':file.rule?.title }}</small></button></aside>
      <section class="audit-checks"><h2>{{ selectedFile?.original_filename || '检查结果' }}</h2><p class="pc-muted">{{ selectedFile?.rule?.title }}</p><nav class="audit-filters" aria-label="结果筛选"><button v-for="status in ['', 'NG', 'ERR', 'PASS']" :key="status" :aria-pressed="filter===status" @click="filter=status">{{ status || '全部' }}</button></nav>
        <p v-if="!findings.length">{{ ['queued','running'].includes(round.execution)?'审核正在进行，完成后会在此显示结果。':'当前没有检查结果。' }}</p>
        <button v-for="finding in findings" :key="finding.item_key" class="finding-row" :class="{'is-selected':selectedKey===finding.item_key}" @click="choose(finding)"><strong :class="`status-${finding.status}`">{{ finding.status }}</strong> {{ finding.item }}<p>{{ finding.suggestions }}</p><small v-if="finding.locator?.locator">依据：{{ finding.locator.locator }}</small><small v-else>可在右侧原件中核对；尚无精确位置。</small><p v-if="finding.review">人工复核：{{ finding.review.judgment }}</p></button>
        <details v-if="selectedFinding" class="audit-review" open><summary>人工复核 · {{ selectedFinding.item }}</summary><label>判断<input v-model="review.judgment" @input="reviewDirty=true" /></label><label>理由<textarea v-model="review.rationale" @input="reviewDirty=true" /></label><label>下一步<textarea v-model="review.next_step" @input="reviewDirty=true" /></label><button :disabled="busy || !review.judgment.trim()" @click="saveReview">保存复核</button></details>
      </section>
      <OriginalPreview :file="selectedFile" :round-id="round.id" />
    </div>
    <details><summary>审核范围与记录</summary><p>待审：{{ round.coverage?.checked?.join('、') }}</p><p>参考：{{ round.coverage?.unchecked?.join('、') }}</p><a v-if="round.predecessor_id" :href="`/?round=${round.predecessor_id}`">查看上一轮</a><a href="/audit-files">全部审核记录</a></details>
  </div>
</template>
