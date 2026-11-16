// What a finished audit says, in the words the PPAP web app uses, and the native document
// content that states it. Pure: no host, no network.
import type {components} from '../generated/openapi'
import {clauseObservation} from '../audit/result-projection.ts'
import {needsAttention} from '../audit/review-navigation.ts'
import {conclusionLabel, resultLabel, uncoveredLabel} from '../audit/status-labels.ts'

export type Task = components['schemas']['AuditTask']
export type TaskFile = components['schemas']['AuditTaskFile']
export type Check = components['schemas']['AuditClauseEvaluation']
export type Verdict = 'PASS' | 'NG' | 'UNKNOWN' | 'ERROR' | 'NONE' | 'RUNNING'

export const verdictLabel: Record<Verdict, string> = {PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项', ERROR: '审核异常', NONE: '未审核', RUNNING: '审核中'}

export const audited = (task: Task) => task.files.filter(row => row.selection === 'selected' && row.audit)

/** A failed or unfinished audit is never stated as conforming. */
export function verdict(task: Task): Verdict {
  if (task.completion_reason === 'no_matching_rules') return 'NONE'
  if (task.state !== 'completed' || task.execution !== 'completed' && task.execution !== 'failed') return 'RUNNING'
  const audits = audited(task).map(row => row.audit!)
  if (task.execution === 'failed' || task.has_execution_error || audits.some(audit => audit.execution === 'review_failed')) return 'ERROR'
  if (!audits.length || audits.some(audit => !audit.conclusion)) return 'UNKNOWN'
  if (audits.some(audit => audit.conclusion === 'NG')) return 'NG'
  return audits.some(audit => audit.conclusion === 'UNKNOWN') ? 'UNKNOWN' : 'PASS'
}

export function statusOf(check: Check): string {
  if (check.execution === 'failed' || check.finding?.status === 'ERR') return resultLabel.ERR
  if (check.coverage !== 'executed') return uncoveredLabel
  if (!check.finding || check.finding.status === 'UNABLE') return resultLabel.UNABLE
  return resultLabel[check.finding.status as keyof typeof resultLabel] ?? check.finding.status
}

const urgency = (check: Check) => check.finding?.status === 'NG' ? 0 : check.execution === 'failed' || check.finding?.status === 'ERR' ? 1 : check.coverage !== 'executed' ? 3 : 2

/** Requirements a person still has to look at, most urgent first. */
export function attention(task: Task): {row: TaskFile; check: Check}[] {
  return audited(task)
    .flatMap(row => row.audit!.checks.filter(check => ['completed', 'failed'].includes(check.execution) && needsAttention(check)).map(check => ({row, check})))
    .sort((left, right) => urgency(left.check) - urgency(right.check))
}

export function totals(task: Task) {
  let ng = 0, unsure = 0
  for (const row of audited(task)) {
    const counts = row.audit!.counts
    ng += counts.ng_count
    unsure += counts.unknown_count + counts.not_covered_count + counts.error_count
  }
  return {ng, unsure}
}

export function when(raw: string | null | undefined): string {
  const date = raw ? new Date(raw) : null
  if (!date || Number.isNaN(date.getTime())) return ''
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()} 年 ${date.getMonth() + 1} 月 ${date.getDate()} 日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** The heading that marks one audit's result in a document; reading it back tells whether it was already written. */
export const marker = (task: Task) => `PPAP 审核结果 · ${task.number}`

const cell = (text: string) => text.replace(/\s+/g, ' ').replace(/\|/g, '｜').trim()
const brief = (text: string, limit: number) => { const flat = cell(text); return flat.length > limit ? flat.slice(0, limit - 1) + '…' : flat }

/** The result as Markdown the document turns into its own headings, table and to-do items. */
export function resultMarkdown(task: Task, link: string): string {
  const overall = verdict(task)
  if (!['PASS', 'NG', 'UNKNOWN'].includes(overall)) throw new Error('round_incomplete')
  const sum = totals(task), open = attention(task)
  const lines = [
    `## ${marker(task)}`,
    '',
    `**总体结论：${verdictLabel[overall]}**　不符合 ${sum.ng} 项，尚未确定 ${sum.unsure} 项。审核时间：${when(task.round_started_at ?? task.created_at)}`,
    '',
    '| 文件 | 结论 | 不符合 | 尚未确定 |',
    '| --- | --- | --- | --- |',
    ...audited(task).map(row => {
      const counts = row.audit!.counts
      return `| ${cell(row.file.original_filename)} | ${conclusionLabel[row.audit!.conclusion ?? ''] ?? '尚有未确定项'} | ${counts.ng_count} | ${counts.unknown_count + counts.not_covered_count + counts.error_count} |`
    }),
    '',
  ]
  if (open.length) {
    lines.push('### 待处理项', '')
    for (const {row, check} of open.slice(0, 50)) lines.push(`- [ ] 【${statusOf(check)}】${cell(row.file.original_filename)}：${brief(check.rule_item.rule_to_check, 120)}——${brief(clauseObservation(check), 160)}`)
    if (open.length > 50) lines.push('', `其余 ${open.length - 50} 项请在 PPAP 中查看。`)
    lines.push('')
  }
  lines.push(`[在 PPAP 中查看原件依据并复核](${link})`, '')
  return lines.join('\n')
}
