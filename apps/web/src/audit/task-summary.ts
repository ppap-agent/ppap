import type {components} from '../generated/openapi.ts'
import {reviewed} from './review-navigation.ts'

export type TaskSummary = components['schemas']['AuditTaskSummary']
type Task = components['schemas']['AuditTask']

export function hasExecutionError(value: {has_execution_error?: boolean; execution?: string; state?: string; result_counts?: Record<string, number>}): boolean {
  if (value.has_execution_error !== undefined) return value.has_execution_error
  const finished = value.execution ? ['completed', 'failed'].includes(value.execution) : value.state === 'completed'
  return finished && (value.execution === 'failed' || (value.result_counts?.ERR ?? 0) > 0)
}

export function summarizeTask(task: Task): TaskSummary {
  const {id, number, state, analysis_mode, created_at, updated_at, round_id, completion_reason} = task
  const audits = task.files.flatMap(row => row.audit ? [row.audit] : [])
  const checks = audits.flatMap(audit => audit.checks)
  const counts: Record<string, number> = {PASS: 0, NG: 0, ERR: 0, UNABLE: 0, NA: 0}
  for (const finding of [...checks.flatMap(check => check.finding ? [check.finding] : []), ...audits.flatMap(audit => audit.unmapped_findings || [])]) if (Object.hasOwn(counts, finding.status)) counts[finding.status]++
  const finished = task.execution ? ['completed', 'failed'].includes(task.execution) : task.state === 'completed'
  return {id, number, state, analysis_mode, created_at, updated_at, round_id, completion_reason, input_revision: task.input_revision ?? 0,
    ...(round_id ? {execution: task.execution ?? (finished && audits.length > 0 && audits.every(audit => audit.execution === 'completed') ? 'completed' as const : undefined), has_execution_error: task.has_execution_error ?? (finished && (task.execution === 'failed' || audits.some(audit => audit.execution === 'review_failed') || counts.ERR > 0)), result_counts: counts, uncovered_count: checks.filter(check => check.coverage !== 'executed').length, reviewed_count: checks.filter(reviewed).length} : {}),
    files: task.files.map(({file, selection}) => ({file, selection}))}
}

export function reconcileTaskSummary(previous: TaskSummary | undefined, next: TaskSummary): TaskSummary {
  if (!previous || previous.id !== next.id) return next
  if (previous.input_revision > next.input_revision) return previous
  const states = ['matching', 'pending_confirm', 'reviewing', 'completed']
  if (previous.input_revision === next.input_revision && states.indexOf(previous.state) > states.indexOf(next.state)) return previous
  return next
}

export function taskIsBehindSummary(task: Task, summary: TaskSummary | undefined): boolean {
  const next = summarizeTask(task)
  return reconcileTaskSummary(summary, next) !== next
}
