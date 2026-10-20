import type {components} from '../generated/openapi.ts'

export type TaskSummary = components['schemas']['AuditTaskSummary']
type Task = components['schemas']['AuditTask']

export function summarizeTask(task: Task): TaskSummary {
  const {id, number, state, analysis_mode, created_at, updated_at, round_id, completion_reason} = task
  return {id, number, state, analysis_mode, created_at, updated_at, round_id, completion_reason, input_revision: task.input_revision ?? 0,
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
