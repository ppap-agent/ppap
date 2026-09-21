<script setup lang="ts">
import {computed, onBeforeUnmount, ref, watch} from 'vue'
import {type Round, apiUrl, originalUrl, request, dateLabel, resultLabels} from './api'
const emit=defineEmits<{review:[]}>()
const props=defineProps<{round:Round;unsavedReview?:boolean}>()
interface Export {id:string; status:string; document_id:string|null;error_code?:string|null}
const report=ref<Export|null>(null), exporting=ref(false), reading=ref(false), error=ref('')
const path=computed(()=>`/integrations/feishu/rounds/${props.round.id}/report`)
const needsConsent=computed(()=>report.value?.error_code?.endsWith(':99991679'))
const consentUrl=computed(()=>`${apiUrl}/login/feishu?purpose=report&return_to=${encodeURIComponent(`${location.origin}/?round=${props.round.id}`)}`)
let revision=0
async function readReport(){const current=++revision;reading.value=true;try{const result=await request<{report:Export|null}>(path.value);if(current===revision){report.value=result.report;error.value=''}}catch{if(current===revision)error.value='无法读取报告状态，请稍后重新读取。'}finally{if(current===revision)reading.value=false}}
async function exportReport(){if(exporting.value || reading.value || props.unsavedReview)return;exporting.value=true;error.value='';const current=++revision;try{const result=await request<{report:Export|null}>(path.value,'POST',{});if(current===revision)report.value=result.report}catch(e){if(current===revision)error.value=e instanceof Error?e.message:'报告创建失败，请重新读取状态后确认。'}finally{exporting.value=false}}
watch(()=>props.round,()=>{revision++;report.value=null;if(props.round.execution==='completed')void readReport()}, {immediate:true})
onBeforeUnmount(()=>{revision++})
const groups=computed(()=>props.round.inputs.filter(file=>file.role==='target').map(file=>({file,findings:props.round.findings.filter(finding=>finding.locator?.file_version_id===file.file_version_id)})))
const incomplete=computed(()=>props.round.execution!=='completed' || !groups.value.length || groups.value.some(group=>!group.findings.length || group.findings.some(finding=>finding.status==='ERR')))
const reviewed=computed(()=>props.round.findings.filter(finding=>finding.review).length)
const counts=computed(()=>['NG','ERR','PASS'].map(status=>({status,count:props.round.findings.filter(item=>item.status===status).length})))
const pending=computed(()=>props.round.findings.filter(item=>!item.review).length)
const issues=computed(()=>props.round.findings.filter(item=>item.status!=='PASS'))
</script>

<template>
  <section class="report-preview">
    <div class="section-heading"><div><h2>审核报告</h2><p class="pc-muted">{{ dateLabel(round.created_at) }} · {{ groups.length }} 份待审资料 · 已保存复核 {{ reviewed }} / {{ round.findings.length }} 项</p></div>
      <div class="button-group">
      <a v-if="report?.status==='ready' && report.document_id" class="button pc-primary" :href="'https://feishu.cn/docx/'+encodeURIComponent(report.document_id)" target="_blank" rel="noopener">打开飞书报告 ↗</a>
      <button v-else-if="unsavedReview" @click="emit('review')">返回保存复核</button>
      <button v-else-if="error || (report && ['unknown','creating','writing'].includes(report.status))" :disabled="exporting || reading" @click="readReport">刷新报告状态</button>
      <button v-else class="pc-primary" :disabled="incomplete || exporting || reading || (!!report && report.status!=='failed')" @click="exportReport">{{ reading?'正在读取状态…':exporting?'正在生成…':report?.status==='failed'?'重试生成报告':'生成飞书报告' }}</button>
      <a v-if="needsConsent && !exporting" class="button" :href="consentUrl">授权生成报告</a>
      </div>
    </div>
    <p v-if="unsavedReview" class="notice" role="status">复核修改尚未保存，报告仅包含已保存的内容。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="needsConsent" class="notice">需要授权创建飞书文档。完成授权后，点击“重试生成报告”。</p>
    <p v-else-if="report?.status==='failed'" class="error" role="alert">报告未能生成，请确认飞书权限后重试。</p>
    <p v-else-if="report?.status==='unknown'" class="notice" role="status">尚未确认报告是否生成。请先核对飞书文档，避免重复创建。</p>
    <p v-else-if="report && report.status!=='ready'" class="notice" role="status">正在生成报告，请稍后刷新状态。</p>
    <a v-if="report?.document_id && report.status!=='ready'" :href="'https://feishu.cn/docx/'+encodeURIComponent(report.document_id)" target="_blank" rel="noopener">核对已创建的文档 ↗</a>
    <p v-if="report?.status==='ready'" class="notice">报告已生成，可在飞书中分享。后续复核修改不会覆盖这份报告。</p>
    <p v-if="incomplete" class="notice" role="status">检查尚未完整完成，暂不能生成完整报告。</p>
    <section class="report-summary" aria-label="本轮摘要">
      <h3>本轮摘要</h3>
      <div class="result-counts"><span v-for="count in counts" :key="count.status" :class="count.count?'status-'+count.status:'pc-muted'">{{ resultLabels[count.status] }} {{ count.count }}</span><span>未记录复核 {{ pending }} 项</span></div>
      <p>自动检查 {{ round.findings.length }} 项；客户自定义要求仍需另行人工核对。已记录复核不等于问题已解决，本报告不代表正式批准。</p>
      <ul v-if="issues.length" class="report-issues"><li v-for="finding in issues" :key="finding.item_key"><a :href="'#report-'+finding.item_key">{{ finding.item }}</a><span>{{ finding.review?'已记录复核':'未记录复核' }}</span></li></ul>
      <p v-else>{{ round.findings.length?'已返回检查项未发现不符合；不代表未检查范围也已通过。':'尚无检查结果。' }}</p>
      <p v-if="round.coverage?.unchecked?.length">未检查范围：{{ round.coverage.unchecked.join('；') }}</p>
    </section>
    <section v-for="group in groups" :key="group.file.file_version_id" class="report-file">
      <div class="section-heading"><div><h3>{{ group.file.original_filename }}</h3><p class="pc-muted">客户要求参考 · <a v-if="group.file.rule_version_id" :href="'/rules?rule='+group.file.rule_version_id+'&return_to='+encodeURIComponent('/?round='+round.id)">{{ group.file.rule?.title || '查看规则版本' }}</a><span v-else>未记录</span></p></div><a :href="originalUrl(group.file,round.id)" target="_blank" rel="noopener">原件 ↗</a></div>
      <p v-if="!group.findings.length">尚无检查结果。</p>
      <article v-for="finding in group.findings" :id="'report-'+finding.item_key" :key="finding.item_key" class="report-finding">
        <h4><span class="badge" :class="'status-'+finding.status">{{ resultLabels[finding.status] }}</span>{{ finding.item }}</h4>
        <p v-if="finding.suggestions">{{ finding.suggestions }}</p>
        <p v-if="finding.locator?.locator" class="pc-muted">依据 · {{ finding.locator.locator }}</p>
        <div v-if="finding.review" class="report-review"><strong>人工复核 · {{ finding.review.judgment }}</strong><p v-if="finding.review.rationale">{{ finding.review.rationale }}</p><p v-if="finding.review.next_step">下一步 · {{ finding.review.next_step }}</p></div>
      </article>
    </section>
    <p v-if="round.inputs.some(file=>file.role==='reference')" class="pc-muted">参考资料 · {{ round.inputs.filter(file=>file.role==='reference').map(file=>file.original_filename).join('、') }}</p>
  </section>
</template>
