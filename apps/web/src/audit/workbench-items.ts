import type {components} from '../generated/openapi.ts'
import type {WorkItem} from '../journey/work-items-table'
import type {TaskSummary} from './task-summary.ts'
import {hasExecutionError} from './task-summary.ts'
import {taskAddress} from './routes.ts'

export function workbenchItems(tasks: TaskSummary[], drafts: components['schemas']['Draft'][] = [], rounds: components['schemas']['RoundSummary'][] = []): WorkItem[] {
  const taskRounds = new Set(tasks.flatMap(task => task.round_id ? [task.round_id] : []))
  const acceptedDrafts = new Set(rounds.flatMap(round => 'source_draft_id' in round && typeof round.source_draft_id === 'string' ? [round.source_draft_id] : []))
  return [
    ...tasks.map(task => ({
      id: `task:${task.id}`, href: taskAddress(task.id), title: task.number, context: '', detail: '',
      state: hasExecutionError(task) ? '存在执行异常' : ({matching: '识别中', pending_confirm: '待确认规则', reviewing: '审核中', completed: '已完成'})[task.state],
      next: task.state === 'completed' ? '查看结果' : task.state === 'pending_confirm' ? '确认规则' : '查看进度',
      task: (hasExecutionError(task) ? 'attention' : task.state === 'completed' ? 'completed' : task.state === 'pending_confirm' ? 'attention' : 'running') as WorkItem['task'],
      counts: task.result_counts ? {...task.result_counts, uncovered: task.uncovered_count || 0} : undefined,
      review: task.result_counts ? {saved: task.reviewed_count || 0, total: Object.entries(task.result_counts).reduce((total, [status, count]) => total + (status === 'NA' ? 0 : count), task.uncovered_count || 0)} : undefined,
      received: Boolean(task.round_id), updatedAt: task.updated_at, mode: task.analysis_mode === 'deep' ? '深度审核' : '标准审核',
      files: task.files.filter(row => row.selection === 'selected').map(row => ({id: row.file.id, name: row.file.original_filename})),
    })),
    ...drafts.filter(draft => draft.status !== 'accepted' && !acceptedDrafts.has(draft.id)).map(draft => ({
      id: `draft:${draft.id}`, href: `/?case=${encodeURIComponent(draft.id)}`, title: '审核准备', context: '', state: '审核准备', next: '继续准备', detail: '', received: false,
      updatedAt: draft.updated_at, task: 'attention' as const,
      files: draft.items.map(row => ({id: row.file_version_id, name: row.original_filename, role: row.role})),
    })),
    ...rounds.filter(round => !taskRounds.has(round.id) && !('task_id' in round && round.task_id)).map(round => ({
      id: `round:${round.id}`, href: `/audit?round=${encodeURIComponent(round.id)}`, title: round.case_title || '历史审核', context: round.case_scope,
      state: hasExecutionError(round) ? '存在执行异常' : round.execution === 'completed' ? '已完成' : '审核中',
      next: round.execution === 'completed' ? '查看结果' : '查看进度', detail: '', received: true, updatedAt: round.updated_at || round.created_at || '',
      task: (hasExecutionError(round) ? 'attention' : round.execution === 'completed' ? 'completed' : 'running') as WorkItem['task'],
      counts: {...round.result_counts, uncovered: round.uncovered_count},
      review: {saved: round.reviewed_count, total: Object.entries(round.result_counts).reduce((total, [status, count]) => total + (status === 'NA' ? 0 : count), round.uncovered_count || 0)},
      files: round.inputs.map(row => ({id: row.file_version_id, name: row.original_filename, role: row.role, roundId: round.id})),
    })),
  ]
}
