import type {components} from '../generated/openapi.ts'
import {reviewed} from './review-navigation.ts'
import {fileDisplayValues} from './display-info-contract.ts'

export type TaskSummary = components['schemas']['AuditTaskSummary']
type Task = components['schemas']['AuditTask']

/** The server states it for every task with a Round; a task without one has no execution error. */
export const hasExecutionError = (value: {has_execution_error?: boolean}): boolean => value.has_execution_error ?? false

type Subject = NonNullable<TaskSummary['subject']>
/** The task's title in lists: "客户 · 车型 零件名" from the first selected file that names any of them. */
/** The task subject; `customerName` gives the customer the one name form of the current page (PRD §1.4 ③). */
export function subjectLabel(subject: Subject | null | undefined, customerName: (value: string) => string = value => value): string {
  if (!subject) return ''
  const item = [subject.model, subject.part].filter(Boolean).join(' ')
  return [subject.customer ? customerName(subject.customer) : '', item].filter(Boolean).join(' · ')
}
export function taskSubject(task: Pick<Task, 'files'>): Subject | undefined {
  const values = task.files.filter(row => row.selection === 'selected').map(row => fileDisplayValues(row)).find(row => row.customer || row.model || row.part)
  return values && {customer: values.customer, model: values.model, part: values.part}
}

export function summarizeTask(task: Task): TaskSummary {
  const {id, number, state, created_at, updated_at, creator, round_id, completion_reason} = task
  const audits = task.files.flatMap(row => row.audit ? [row.audit] : [])
  const checks = audits.flatMap(audit => audit.checks)
  const counts: Record<string, number> = {PASS: 0, NG: 0, ERR: 0, UNABLE: 0, NA: 0}
  for (const finding of [...checks.flatMap(check => check.finding ? [check.finding] : []), ...audits.flatMap(audit => audit.unmapped_findings || [])]) if (Object.hasOwn(counts, finding.status)) counts[finding.status]++
  return {id, number, state, created_at, updated_at, creator, round_id, completion_reason, deep_mode: task.deep_mode, input_revision: task.input_revision ?? 0,
    ...(round_id ? {execution: task.execution, has_execution_error: hasExecutionError(task), result_counts: counts, uncovered_count: checks.filter(check => check.coverage !== 'executed').length, reviewed_count: checks.filter(reviewed).length} : {}),
    ...(taskSubject(task) ? {subject: taskSubject(task)} : {}),
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
