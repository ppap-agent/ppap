<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch} from 'vue'
import {request, upload, dateLabel, executionLabels, resultLabels, latestRules, type Draft, type Material, type Rule, type Round, type Analysis, type Job, type Finding} from './api'
import OriginalPreview from './OriginalPreview.vue'
import DrivePicker from './DrivePicker.vue'
import ReportPreview from './ReportPreview.vue'

const items = ref<Material[]>([]), rules = ref<Rule[]>([]), round = ref<Round | null>(null)
const emit = defineEmits<{reviewing:[value:boolean]}>()
watch(()=>!!round.value, value=>emit('reviewing',value))
const busy = ref(false), error = ref(''), message = ref(''), selectedFileId = ref(''), selectedKey = ref(''), filter = ref('attention')
const initialized = ref(false)
const draftId = ref(new URLSearchParams(location.search).get('draft') || '')
const predecessorId = ref(new URLSearchParams(location.search).get('from') || '')
const review = reactive({judgment:'', rationale:'', next_step:''}), reviewDirty = ref(false)
const workspacePanel = ref('checks'), roundView=ref('review')
const workspace = ref<HTMLElement>(), previewWidth = ref(50), resizing = ref(false)
function resizePreview(event:PointerEvent) {
  if (!resizing.value || !workspace.value) return
  const bounds=workspace.value.getBoundingClientRect()
  previewWidth.value=Math.max(28,Math.min(50,(bounds.right-event.clientX)/bounds.width*100))
}
function startResize(event:PointerEvent) { if(event.button!==0)return; resizing.value=true; (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); resizePreview(event) }
function resizeKey(event:KeyboardEvent) { const change=event.key==='ArrowLeft'?2:event.key==='ArrowRight'?-2:0; if(change){event.preventDefault();previewWidth.value=Math.max(28,Math.min(50,previewWidth.value+change))} }
const selectedFile = computed(() => (round.value?.inputs || items.value).find(item => item.file_version_id === selectedFileId.value))
const findings = computed(() => (round.value?.findings || []).filter(item => item.locator?.file_version_id === selectedFileId.value && matchesFilter(item, filter.value)))
const selectedFinding = computed(() => round.value?.findings.find(item => item.item_key === selectedKey.value))
const currentRules = computed(()=>latestRules(rules.value))
const counts = computed(() => ['PASS','NG','ERR'].map(status => ({status, count:(round.value?.findings || []).filter(row => row.status === status).length})))
const analysisNotices:Record<string,string>={empty_document_text:'未提取到文字，将主要按文件名匹配。',document_text_unavailable:'文字提取失败，请核对文件类型与规则。',metadata_uses_excerpt:'业务信息基于文字摘录识别；审核仍读取完整文件。',metadata_extraction_failed:'业务信息识别失败，请人工核对。',rule_matching_failed:'自动匹配失败，请手动选择规则。',no_available_rules:'暂无可匹配的规则，请先上传规则。'}
function guardLeave(event:BeforeUnloadEvent){if(reviewDirty.value || draftDirty.value){event.preventDefault();event.returnValue=''}}
let disposed = false
let activeRoundId = new URLSearchParams(location.search).get('round') || ''
const timers = new Set<ReturnType<typeof setTimeout>>()
function later(work:()=>void) { const timer = setTimeout(() => {timers.delete(timer); if (!disposed) work()}, 2000); timers.add(timer) }
function query(values: Record<string,string | null>) { const url = new URL(location.href); for (const [key,value] of Object.entries(values)) value ? url.searchParams.set(key,value) : url.searchParams.delete(key); history.replaceState(null,'',url) }
async function action(work:()=>Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await work() } catch(e) { error.value = e instanceof Error ? e.message : '操作失败，请重试。' } finally { busy.value = false } }
function choose(item?: Finding) {
  if (item?.item_key === selectedKey.value) return true
  if (item?.item_key !== selectedKey.value && reviewDirty.value && !confirm('复核尚未保存，是否放弃修改？')) return false
  selectedKey.value = item?.item_key || ''; Object.assign(review, {judgment:item?.review?.judgment || '', rationale:item?.review?.rationale || '', next_step:item?.review?.next_step || ''}); reviewDirty.value = false; return true
}
function chooseFile(file:Material) { const first = round.value?.findings.find(row=>row.locator?.file_version_id === file.file_version_id && matchesFilter(row,filter.value)); if (choose(first)) { selectedFileId.value = file.file_version_id; workspacePanel.value = 'checks' } }
function chooseFilter(status:string) {
  const first=round.value?.findings.find(item=>(!selectedFileId.value || item.locator?.file_version_id===selectedFileId.value) && matchesFilter(item,status))
  if(choose(first)) filter.value=status
}
async function saveDraft() {
  if (!draftId.value) draftId.value = (await request<{id:string}>('/drafts','POST',{predecessor_id:predecessorId.value || null})).id
  const snapshot = materialSnapshot()
  await request(`/drafts/${draftId.value}`,'PUT',{items:snapshot})
  savedDraft.value=JSON.stringify(snapshot)
  query({draft:draftId.value, round:null, from:predecessorId.value || null})
}
async function readAnalysis(item:Material, id:string) {
  try {
    const job = await request<Job<Analysis>>(`/file-analyses/${id}`)
    if (disposed || !items.value.includes(item) || round.value) return
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
  await uploadFiles(files, role)
  input.value=''
}
async function imported(material:Material) { items.value.push(material); await saveDraft(); void analyze(items.value[items.value.length-1]) }
async function loadRound(id:string) {
  if(id!==activeRoundId)return
  try {
    const result = await request<Round>(`/rounds/${id}`)
    if (disposed || id!==activeRoundId) return
    round.value = result
    if (!selectedFileId.value) {selectedFileId.value=result.inputs.find(item=>item.role==='target')?.file_version_id || result.inputs[0]?.file_version_id || ''; filter.value=result.findings.some(item=>item.locator?.file_version_id===selectedFileId.value && item.status!=='PASS')?'attention':''; choose(findings.value[0])}
    if (!selectedKey.value && result.findings.length && !reviewDirty.value) {
      filter.value=result.findings.some(item=>item.locator?.file_version_id===selectedFileId.value && item.status!=='PASS')?'attention':''
      choose(findings.value[0])
    }
    if (['queued','running'].includes(result.execution)) later(()=>{void loadRound(id)})
  } catch(e) { error.value=e instanceof Error ? e.message : '无法加载审核' }
}
async function submit() { await action(async()=>{
  await saveDraft()
  const key=`ppap-idempotency-${draftId.value}`; const id=sessionStorage.getItem(key)||crypto.randomUUID(); sessionStorage.setItem(key,id)
  const result=await request<Round>(`/drafts/${draftId.value}/accept`,'POST',{idempotency_key:id,predecessor_id:predecessorId.value || null})
  query({round:result.id,draft:null,from:null}); predecessorId.value='';activeRoundId=result.id; await loadRound(result.id)
}) }
async function saveReview() { await action(async()=>{
  if (!round.value || !selectedFinding.value) return
  const result=await request<Round>(`/rounds/${round.value.id}/reviews`,'PUT',{item_key:selectedFinding.value.item_key,...review,version:selectedFinding.value.review?.version || 0})
  round.value=result; reviewDirty.value=false; message.value='复核已保存。'
}) }
async function prepareAgain() { await action(async()=>{
  if (!round.value) return
  if(reviewDirty.value && !confirm('复核尚未保存，是否放弃修改？'))return
  reviewDirty.value=false;selectedKey.value=''
  previousRound.value=round.value
  predecessorId.value=round.value.id; items.value=round.value.inputs.map(item=>({...item}));activeRoundId=''; round.value=null; draftId.value=''; selectedFileId.value=''; filter.value='attention'; roundView.value='review'; await saveDraft()
}) }
onMounted(async()=>{
  window.addEventListener('beforeunload',guardLeave)
  await action(async()=>{
    rules.value=(await request<{rules:Rule[]}>('/rules')).rules
    const id=new URLSearchParams(location.search).get('round')
    if (id) await loadRound(id)
    else if (draftId.value) {
      const draft=await request<Draft>(`/drafts/${draftId.value}`)
      items.value=draft.items;predecessorId.value=draft.predecessor_id || predecessorId.value;savedDraft.value=JSON.stringify(materialSnapshot())
      if(predecessorId.value)previousRound.value=await request<Round>(`/rounds/${predecessorId.value}`)
      for (const item of items.value) {
        const saved=await request<{analysis:Job<Analysis>|null}>(`/files/${item.file_id}/versions/${item.file_version_id}/analysis`)
        if(saved.analysis){item.analysisId=saved.analysis.id;void readAnalysis(item,saved.analysis.id)}
      }
    } else await loadDrafts()
  })
  initialized.value=true
})
onBeforeUnmount(()=>{disposed=true; timers.forEach(clearTimeout);window.removeEventListener('beforeunload',guardLeave)})

const drafts=ref<Draft[]>([]), draftNext=ref<number|null>(null), draftLoading=ref(false)
const previousRound=ref<Round|null>(null), savedDraft=ref('[]')
const detailHeading=ref<HTMLElement>(), dragActive=ref(false)
function materialSnapshot(){return items.value.map(item=>({file_version_id:item.file_version_id,role:item.role,rule_version_id:item.role==='target'?item.rule_version_id:null}))}
const draftDirty=computed(()=>!round.value && JSON.stringify(materialSnapshot())!==savedDraft.value)
const missingRules=computed(()=>items.value.filter(item=>item.role==='target' && !item.rule_version_id).length)
const targetCount=computed(()=>items.value.filter(item=>item.role==='target').length)
const reviewed=computed(()=>round.value?.findings.filter(item=>item.review).length || 0)
const fileFindings=computed(()=>round.value?.findings.filter(item=>item.locator?.file_version_id===selectedFileId.value) || [])
const filters=[{id:'attention',label:'需关注'},{id:'',label:'全部'},{id:'PASS',label:'通过'}]
function matchesFilter(item:Finding, status:string){return status==='attention'?item.status!=='PASS':!status || item.status===status}
function filterCount(status:string){return fileFindings.value.filter(item=>matchesFilter(item,status)).length}
function availableRules(item:Material){return rules.value.filter(rule=>currentRules.value.some(current=>current.id===rule.id) || rule.id===item.rule_version_id)}
function selectFile(event:Event) {
  const select=event.target as HTMLSelectElement
  const file=round.value?.inputs.find(item=>item.file_version_id===select.value)
  if(file){if(reviewDirty.value && !confirm('复核尚未保存，是否放弃修改？')){select.value=selectedFileId.value;return}
    reviewDirty.value=false;filter.value=(round.value?.findings.some(item=>item.locator?.file_version_id===file.file_version_id && item.status!=='PASS'))?'attention':''
    chooseFile(file)
  }
}
function selectFinding(finding:Finding){if(choose(finding))void nextTick(()=>detailHeading.value?.focus({preventScroll:true}))}
function nextFinding() {
  const index=findings.value.findIndex(item=>item.item_key===selectedKey.value)
  const next=findings.value[index+1]
  if(next)selectFinding(next)
}
async function saveAndNext(){await saveReview();if(!reviewDirty.value)nextFinding()}
async function loadDrafts(more=false) {
  draftLoading.value=true
  try {
    const result=await request<{drafts:Draft[];next_offset:number|null}>('/drafts?limit=8&offset='+(more?draftNext.value || 0:0))
    drafts.value=more?drafts.value.concat(result.drafts):result.drafts;draftNext.value=result.next_offset
  } finally {draftLoading.value=false}
}
async function openRules(item?:Material) {
  await action(async()=>{
    if(items.value.length)await saveDraft()
    const query=new URLSearchParams()
    if(draftId.value)query.set('return_to','/?draft='+draftId.value)
    if(item?.rule_version_id)query.set('rule',item.rule_version_id)
    else query.set('new','1')
    location.href='/rules?'+query
  })
}
async function dropped(event:DragEvent){
  dragActive.value=false
  if(busy.value || !event.dataTransfer?.files.length)return
  await uploadFiles(Array.from(event.dataTransfer.files),'target')
}
async function uploadFiles(files:File[],role:Material['role']){
  await action(async()=>{
    for(const file of files){message.value='正在上传 '+file.name;const material=await upload(file,role);items.value.push(material);await saveDraft();void analyze(items.value[items.value.length-1])}
    message.value=''
  })
}
function unchanged(item:Material){return previousRound.value?.inputs.some(previous=>previous.file_version_id===item.file_version_id)}

</script>


<template>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <p v-if="message" class="notice" role="status">{{ message }}</p>
  <p v-if="!initialized" class="loading-state" role="status">正在加载资料…</p>
  <div v-else-if="!round" id="audit-preparation">
    <div v-if="predecessorId" class="context-bar"><span>再次审核</span><a :href="'/?round='+predecessorId">查看上一轮 →</a></div>
    <section class="preparation-upload" :class="{'has-materials':items.length}">
      <div v-if="!items.length" class="audit-dropzone" :class="{'is-dragging':dragActive}" @dragover.prevent="dragActive=true" @dragleave.prevent="dragActive=false" @drop.prevent="dropped">
        <h2>添加待审资料</h2><p class="pc-muted">拖入文件，或选择本地资料</p>
        <label class="file-button button pc-primary">选择文件<input type="file" multiple :disabled="busy" aria-label="选择待审文件" @change="addFiles($event,'target')" /></label>
        <small class="pc-muted">Excel、Word、PDF · 单份最大 64 MB</small>
      </div>
      <div class="upload-actions">
        <label v-if="items.length" class="file-button button">添加待审文件<input type="file" multiple :disabled="busy" aria-label="添加待审文件" @change="addFiles($event,'target')" /></label>
        <label class="file-button button">添加参考资料<input type="file" multiple :disabled="busy" aria-label="添加参考资料" @change="addFiles($event,'reference')" /></label>
        <DrivePicker :disabled="busy" :on-import="imported" @busy="busy=$event" />
      </div>
    </section>
    <section v-if="items.length || draftDirty" class="materials-section">
      <div class="section-heading"><h2>本次资料 <span class="count">{{ items.length }}</span></h2><button class="text-button" :disabled="busy" @click="openRules()">上传客户规则</button></div>
      <article v-for="item in items" :key="item.file_version_id" class="audit-material">
        <div class="material-name"><strong>{{ item.original_filename }}</strong><span v-if="previousRound" class="badge">{{ unchanged(item)?'沿用原件':'本轮新增' }}</span><small v-if="item.analysis">{{ [item.analysis.metadata.customer,item.analysis.metadata.model,item.analysis.metadata.part].filter(Boolean).join(' · ') }}</small><small v-if="['queued','leased'].includes(item.analysisStatus || '')" role="status">正在识别并匹配…</small><small v-if="['failed','unknown'].includes(item.analysisStatus || '')">自动匹配未完成，请手动选择。</small><small v-for="notice in (item.analysis?.notices || []).filter(notice=>notice!=='metadata_uses_excerpt')" :key="notice">{{ analysisNotices[notice] || notice }}</small></div>
        <label>用途<select v-model="item.role" :disabled="busy" @change="item.rule_version_id=null"><option value="target">待审文件</option><option value="reference">参考资料</option></select></label>
        <div v-if="item.role==='target'" class="material-rule"><label>客户规则<select v-model="item.rule_version_id" :disabled="busy" :aria-label="item.original_filename+'的匹配规则'"><option :value="null">选择匹配规则</option><option v-for="rule in availableRules(item)" :key="rule.id" :value="rule.id">{{ rule.title }}{{ currentRules.some(current=>current.id===rule.id)?'':'（历史版本）' }}</option></select></label><div class="material-rule-meta"><span>{{ item.rule_version_id ? rules.find(rule=>rule.id===item.rule_version_id)?.type || '已选择规则' : '尚未匹配' }}</span><button v-if="item.rule_version_id" class="text-button" :disabled="busy" @click="openRules(item)">查看规则</button></div></div>
        <span v-else class="pc-muted material-rule">仅作参考，不单独检查</span>
        <button class="text-button subtle" :disabled="busy" :aria-label="'移除 '+item.original_filename" @click="items=items.filter(row=>row!==item)">移除</button>
      </article>
      <p v-if="targetCount" class="scope-note">自动检查按所选文档类型执行；客户规则中的自定义条目供人工复核。</p>
      <div class="action-bar sticky-actions"><div><strong>{{ targetCount }} 份待审</strong><span v-if="missingRules" class="status-NG"> · {{ missingRules }} 份未匹配规则</span><small class="pc-muted">{{ draftDirty?'有未保存的修改':'草稿已保存' }}</small></div><div class="button-group"><button :disabled="busy || !draftDirty" @click="action(async()=>{await saveDraft();message='草稿已保存。'})">保存草稿</button><button class="pc-primary" :disabled="busy || !targetCount || !!missingRules" @click="submit">{{ busy?'处理中…':'开始审核' }}</button></div></div>
    </section>
    <section v-if="!items.length && drafts.length" class="recent-drafts">
      <div class="section-heading"><h2>继续草稿</h2></div>
      <a v-for="draft in drafts" :key="draft.id" class="draft-row" :href="'/?draft='+draft.id"><div><strong>{{ draft.items.filter(item=>item.role==='target').map(item=>item.original_filename).join('、') || draft.items.map(item=>item.original_filename).join('、') }}</strong><small>{{ draft.items.length }} 份资料 · {{ dateLabel(draft.updated_at) }}{{ draft.predecessor_id?' · 再次审核':'' }}</small></div><span>继续 →</span></a>
      <button v-if="draftNext!==null" class="load-more" :disabled="draftLoading" @click="action(()=>loadDrafts(true))">更多草稿</button>
    </section>
  </div>
  <div v-else class="round-page">
    <div class="round-heading"><div><a class="back-link" href="/audit-files">← 审核结果</a><h2>{{ round.inputs.filter(file=>file.role==='target').length }} 份资料 <span class="badge" :class="{'status-ERR':round.execution==='failed'}">{{ executionLabels[round.execution] || round.execution }}</span></h2><p class="pc-muted">{{ dateLabel(round.created_at) }}<template v-if="round.findings.length"> · 已保存复核 {{ reviewed }} / {{ round.findings.length }} 项</template></p></div><div class="button-group"><a v-if="round.predecessor_id" class="button" :href="'/?round='+round.predecessor_id">上一轮</a><button :disabled="busy || ['queued','running'].includes(round.execution)" @click="prepareAgain">修改资料再审核</button></div></div>
    <div v-if="round.findings.length" class="result-summary"><span v-for="count in counts" :key="count.status" :class="'status-'+count.status"><strong>{{ count.count }}</strong>{{ resultLabels[count.status] }}</span></div>
    <nav class="view-tabs" aria-label="审核视图"><button :aria-pressed="roundView==='review'" @click="roundView='review'">检查与复核</button><button :aria-pressed="roundView==='report'" @click="roundView='report'">审核报告</button></nav>
    <div v-show="roundView==='review'">
      <div class="workspace-heading"><label>审核资料<select :value="selectedFileId" :disabled="busy" @change="selectFile"><option v-for="file in round.inputs" :key="file.file_version_id" :value="file.file_version_id">{{ file.original_filename }}{{ file.role==='reference'?' · 参考资料':'' }}</option></select></label><div class="workspace-switch"><button :aria-pressed="workspacePanel==='checks'" @click="workspacePanel='checks'">检查结果</button><button :aria-pressed="workspacePanel==='original'" @click="workspacePanel='original'">查看原件</button></div></div>
      <div v-if="workspacePanel==='original' && selectedFinding" class="mobile-finding-context"><strong>{{ selectedFinding.item }}</strong><p>{{ selectedFinding.suggestions }}</p><button @click="workspacePanel='checks'">返回复核</button></div>
      <div ref="workspace" class="audit-workspace" :class="{'is-resizing':resizing}" :data-panel="workspacePanel" :style="{'--preview-width':previewWidth+'%'}">
        <section class="audit-checks">
          <template v-if="selectedFile?.role==='reference'"><div class="empty-state"><h3>参考资料</h3><button @click="workspacePanel='original'">查看原件</button></div></template>
          <template v-else>
            <nav class="audit-filters" aria-label="结果筛选"><button v-for="entry in filters" :key="entry.id" :disabled="busy" :aria-pressed="filter===entry.id" @click="chooseFilter(entry.id)">{{ entry.label }} <span>{{ filterCount(entry.id) }}</span></button></nav>
            <div v-if="!findings.length" class="empty-state compact"><h3>{{ ['queued','running'].includes(round.execution)?'正在检查资料…':round.execution==='failed'?'本次检查未完成':'当前筛选下没有检查项' }}</h3><p v-if="['queued','running'].includes(round.execution)" class="pc-muted">可以离开页面，稍后从审核结果继续查看。</p><button v-else-if="fileFindings.length" @click="chooseFilter('')">查看全部检查项</button></div>
            <div class="finding-list" aria-label="检查项">
              <button v-for="finding in findings" :key="finding.item_key" class="finding-row" :disabled="busy" :aria-pressed="selectedKey===finding.item_key" @click="selectFinding(finding)"><span class="status-dot" :class="'status-'+finding.status" aria-hidden="true"></span><span>{{ finding.item }}<small>{{ resultLabels[finding.status] }}{{ finding.review?' · 已保存复核':'' }}</small></span><span aria-hidden="true">›</span></button>
            </div>
            <article v-if="selectedFinding" class="finding-detail">
              <div class="section-heading"><span class="badge" :class="'status-'+selectedFinding.status">{{ resultLabels[selectedFinding.status] }}</span><button class="text-button mobile-original-link" @click="workspacePanel='original'">核对原件 →</button></div>
              <h3 ref="detailHeading" tabindex="-1">{{ selectedFinding.item }}</h3>
              <p class="finding-description">{{ selectedFinding.suggestions }}</p>
              <p class="evidence"><span class="field-label">依据位置</span>{{ selectedFinding.locator?.locator || '未提供精确位置，请核对原件。' }}</p>
              <form class="audit-review" @submit.prevent="saveReview">
                <div class="section-heading"><h3>人工复核</h3><span class="pc-muted">{{ reviewDirty?'未保存':selectedFinding.review?'已保存':'' }}</span></div>
                <fieldset class="plain-fieldset" :disabled="busy">
                  <label>判断<input v-model="review.judgment" placeholder="填写你的复核判断" @input="reviewDirty=true" /></label>
                  <label>理由<textarea v-model="review.rationale" rows="3" @input="reviewDirty=true" /></label>
                  <label>下一步<textarea v-model="review.next_step" rows="2" @input="reviewDirty=true" /></label>
                  <div class="button-group"><button class="pc-primary" :disabled="!review.judgment.trim() || !reviewDirty">保存复核</button><button v-if="findings.findIndex(item=>item.item_key===selectedKey)<findings.length-1" type="button" :disabled="reviewDirty && !review.judgment.trim()" @click="reviewDirty?saveAndNext():nextFinding()">{{ reviewDirty?'保存并下一项':'下一项 →' }}</button></div>
                </fieldset>
              </form>
            </article>
          </template>
        </section>
        <div class="preview-resizer" role="separator" tabindex="0" aria-label="调整原件预览宽度" aria-orientation="vertical" :aria-valuenow="Math.round(previewWidth)" aria-valuemin="28" aria-valuemax="50" @pointerdown.prevent="startResize" @pointermove="resizePreview" @pointerup="resizing=false" @pointercancel="resizing=false" @lostpointercapture="resizing=false" @keydown="resizeKey" />
        <OriginalPreview :file="selectedFile" :round-id="round.id" />
      </div>
    </div>
    <ReportPreview v-if="roundView==='report'" :round="round" :unsaved-review="reviewDirty" @review="roundView='review';workspacePanel='checks'" />
  </div>
</template>
