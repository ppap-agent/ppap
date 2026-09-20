<script setup lang="ts">
import {computed, onBeforeUnmount, ref, watch} from 'vue'
import {type Round, apiUrl, originalUrl, request} from './api'
const props=defineProps<{round:Round;unsavedReview?:boolean}>()
interface Export {id:string; status:string; document_id:string|null;error_code?:string|null}
const report=ref<Export|null>(null), exporting=ref(false), error=ref('')
const path=computed(()=>`/integrations/feishu/rounds/${props.round.id}/report`)
const needsConsent=computed(()=>report.value?.error_code?.endsWith(':99991679'))
const consentUrl=computed(()=>`${apiUrl}/login/feishu?purpose=report&return_to=${encodeURIComponent(`${location.origin}/?round=${props.round.id}`)}`)
let revision=0
async function readReport(){const current=++revision;try{const result=await request<{report:Export|null}>(path.value);if(current===revision){report.value=result.report;error.value=''}}catch{if(current===revision)error.value='无法读取报告状态，请稍后重新读取。'}}
async function exportReport(){if(exporting.value || props.unsavedReview)return;exporting.value=true;error.value='';const current=++revision;try{const result=await request<{report:Export|null}>(path.value,'POST',{});if(current===revision)report.value=result.report}catch(e){if(current===revision)error.value=e instanceof Error?e.message:'报告创建失败，请重新读取状态后确认。'}finally{exporting.value=false}}
watch(()=>props.round,()=>{revision++;report.value=null;if(props.round.execution==='completed')void readReport()}, {immediate:true})
onBeforeUnmount(()=>{revision++})
const groups=computed(()=>props.round.inputs.filter(file=>file.role==='target').map(file=>({file,findings:props.round.findings.filter(finding=>finding.locator?.file_version_id===file.file_version_id)})))
const incomplete=computed(()=>props.round.execution!=='completed' || !groups.value.length || groups.value.some(group=>!group.findings.length || group.findings.some(finding=>finding.status==='ERR')))
</script>

<template>
  <details class="report-preview">
    <summary>审核报告预览</summary>
    <div class="audit-actions"><button class="pc-primary" :disabled="incomplete || exporting || (!!report && report.status!=='failed') || unsavedReview || !!error" @click="exportReport">{{ exporting?'正在生成报告…':report?.status==='failed'?'重试生成飞书报告':'生成飞书报告' }}</button><button v-if="report && report.status!=='ready' || error" :disabled="exporting" @click="readReport">重新读取状态</button><a v-if="report?.document_id" :href="`https://feishu.cn/docx/${encodeURIComponent(report.document_id)}`" target="_blank" rel="noopener">{{ report.status==='ready'?'打开飞书报告':'查看已创建文档（内容可能不完整）' }}</a></div>
    <p v-if="unsavedReview" role="status">请先保存人工复核，再生成报告。预览仅包含已保存的结果。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="report && report.status!=='ready'" role="status">{{ report.status==='failed'?'飞书拒绝创建报告。请检查登录授权、应用文档权限或限流情况，处理后可重试。':report.status==='unknown'?'创建或写入结果不确定，请先核对飞书文档，不要重复创建。':'报告正在创建或写入；若长时间未完成，请核对飞书文档。' }}</p>
    <p v-if="report?.error_code" role="alert" class="error">{{ report.error_code.startsWith('feishu_report_write_')?'报告内容写入失败':'报告创建失败' }}（诊断码：{{ report.error_code }}）</p>
    <p v-if="needsConsent">生成报告需要你授权 PPAP 创建和编辑飞书文档。<a :href="consentUrl">授权生成报告</a>；返回后再重试。授权不会自动创建报告。</p>
    <p v-if="report?.status==='ready'">报告已生成。请在飞书文档中使用分享功能；后续复核修改不会自动覆盖这份报告。</p>
    <p v-if="incomplete" role="status">审核尚未完整完成或存在执行错误。以下为当前结果，不能作为完整通过结论。</p>
    <p>机器结论与人工复核分别记录，人工判断不覆盖原始检查结果。</p>
    <p v-if="round.created_at">提交时间：{{ new Date(round.created_at).toLocaleString() }}</p>
    <section v-for="group in groups" :key="group.file.file_version_id">
      <h2>{{ group.file.original_filename }}</h2>
      <p>匹配规则：{{ group.file.rule?.title || '未记录规则名称' }}</p>
      <a :href="originalUrl(group.file,round.id)" target="_blank" rel="noopener">查看原始文件</a>
      <p v-if="!group.findings.length">尚无检查结果。</p>
      <article v-for="finding in group.findings" :key="finding.item_key" class="rule-item">
        <h3><span :class="`status-${finding.status}`">{{ finding.status }}</span> {{ finding.item }}</h3>
        <p v-if="finding.suggestions">{{ finding.suggestions }}</p>
        <p v-if="finding.locator?.locator">依据：{{ finding.locator.locator }}</p>
        <p v-else class="pc-muted">未提供精确位置，请核对原件。</p>
        <div v-if="finding.review" class="report-review">
          <p>人工复核：{{ finding.review.judgment }}</p>
          <p v-if="finding.review.rationale">理由：{{ finding.review.rationale }}</p>
          <p v-if="finding.review.next_step">下一步：{{ finding.review.next_step }}</p>
        </div>
      </article>
    </section>
    <p v-if="round.inputs.some(file=>file.role==='reference')">参考资料：{{ round.inputs.filter(file=>file.role==='reference').map(file=>file.original_filename).join('、') }}。参考资料不单独生成通过结论。</p>
  </details>
</template>
