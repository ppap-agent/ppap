<script setup lang="ts">
import {computed} from 'vue'
import {type Round, originalUrl} from './api'
const props=defineProps<{round:Round}>()
const groups=computed(()=>props.round.inputs.filter(file=>file.role==='target').map(file=>({file,findings:props.round.findings.filter(finding=>finding.locator?.file_version_id===file.file_version_id)})))
const incomplete=computed(()=>props.round.execution!=='completed' || !groups.value.length || groups.value.some(group=>!group.findings.length || group.findings.some(finding=>finding.status==='ERR')))
</script>

<template>
  <details class="report-preview">
    <summary>审核报告预览</summary>
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
