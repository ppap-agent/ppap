export interface ImportSource {
  file_id: string
  file_version_id: string
  original_filename: string
}

export interface ImportJob<C> {
  id: string
  status: string
  result?: C | null
  error?: string
}

export type ParseProgress = 'waiting' | 'queued' | 'leased'

export type RuleImportState<C> =
  | {kind: 'idle'; documentType: string}
  | {kind: 'uploading'; documentType: string; source?: ImportSource}
  | {kind: 'parsing'; jobId: string; documentType: string; source?: ImportSource; progress: ParseProgress}
  | {kind: 'unknown'; jobId: string; documentType: string; source?: ImportSource; message: string}
  | {kind: 'failed'; jobId?: string; documentType: string; source?: ImportSource; message: string}
  | {kind: 'reviewing'; jobId?: string; documentType: string; source?: ImportSource; candidate: C; leavePrompt: boolean; message: string}
  | {kind: 'publishing'; jobId?: string; documentType: string; source?: ImportSource; candidate: C; leaveAfter: boolean}

export const UNKNOWN_PARSE_MESSAGE = '解析结果暂不确定，请稍后刷新查看，不要重复提交。原任务可能仍会完成，再次解析可能再次消耗模型费用。'

export type RuleImportEvent<C> =
  | {type: 'set-type'; documentType: string}
  | {type: 'begin-upload'}
  | {type: 'upload-failed'; message: string}
  | {type: 'catalog-failed'; message: string}
  | {type: 'begin-parse'; jobId: string; source?: ImportSource; documentType?: string}
  | {type: 'record-source'; source?: ImportSource; documentType?: string}
  | {type: 'observed'; job: ImportJob<C>}
  | {type: 'read-back'}
  | {type: 'begin-rerun'}
  | {type: 'edit'; candidate: C}
  | {type: 'prompt-leave'}
  | {type: 'dismiss-leave'}
  | {type: 'begin-publish'; leaveAfter: boolean}
  | {type: 'publish-failed'; message: string}

function sourceOf<C>(state: RuleImportState<C>) {
  return state.kind === 'idle' ? undefined : state.source
}

export function ruleImportReducer<C>(state: RuleImportState<C>, event: RuleImportEvent<C>): RuleImportState<C> {
  if (event.type === 'set-type') {
    if (state.kind === 'uploading' || state.kind === 'parsing' || state.kind === 'publishing') return state
    return {...state, documentType: event.documentType}
  }
  if (event.type === 'begin-upload') {
    if (state.kind !== 'idle' && state.kind !== 'failed' && state.kind !== 'unknown') return state
    return {kind: 'uploading', documentType: state.documentType, source: sourceOf(state)}
  }
  if (event.type === 'upload-failed') {
    if (state.kind !== 'uploading') return state
    return {kind: 'failed', documentType: state.documentType, source: state.source, message: event.message}
  }
  if (event.type === 'catalog-failed') {
    if (state.kind !== 'idle') return state
    return {kind: 'failed', documentType: state.documentType, message: event.message}
  }
  if (event.type === 'begin-parse') {
    if (state.kind !== 'uploading' && state.kind !== 'unknown' && state.kind !== 'failed') return state
    return {
      kind: 'parsing',
      jobId: event.jobId,
      documentType: event.documentType || state.documentType,
      source: event.source || sourceOf(state),
      progress: 'waiting',
    }
  }
  if (event.type === 'record-source') {
    if (state.kind === 'idle' || state.kind === 'uploading') return state
    return {...state, documentType: event.documentType ?? state.documentType, source: event.source ?? state.source}
  }
  if (event.type === 'observed') {
    if (state.kind !== 'parsing') return state
    const job = event.job
    if (job.status === 'succeeded' && job.result) {
      return {kind: 'reviewing', jobId: state.jobId, documentType: state.documentType, source: state.source, candidate: job.result, leavePrompt: false, message: ''}
    }
    if (job.status === 'queued' || job.status === 'leased') return {...state, progress: job.status}
    if (job.status === 'unknown') {
      return {kind: 'unknown', jobId: state.jobId, documentType: state.documentType, source: state.source, message: UNKNOWN_PARSE_MESSAGE}
    }
    const message = job.error === 'rule_type_required'
      ? '请选择文档类型后再解析。'
      : job.error || '解析失败，请检查原件与文档类型。'
    return {kind: 'failed', jobId: state.jobId, documentType: state.documentType, source: state.source, message}
  }
  if (event.type === 'read-back') {
    if (state.kind !== 'unknown' && state.kind !== 'failed') return state
    if (!state.jobId) return state
    return {kind: 'parsing', jobId: state.jobId, documentType: state.documentType, source: state.source, progress: 'waiting'}
  }
  if (event.type === 'begin-rerun') {
    if ((state.kind !== 'unknown' && state.kind !== 'failed') || !state.source) return state
    return {kind: 'uploading', documentType: state.documentType, source: state.source}
  }
  if (event.type === 'edit') {
    if (state.kind !== 'reviewing') return state
    return {...state, candidate: event.candidate, message: ''}
  }
  if (event.type === 'prompt-leave') {
    if (state.kind !== 'reviewing') return state
    return {...state, leavePrompt: true}
  }
  if (event.type === 'dismiss-leave') {
    if (state.kind !== 'reviewing') return state
    return {...state, leavePrompt: false}
  }
  if (event.type === 'begin-publish') {
    if (state.kind !== 'reviewing') return state
    return {kind: 'publishing', jobId: state.jobId, documentType: state.documentType, source: state.source, candidate: state.candidate, leaveAfter: event.leaveAfter}
  }
  if (event.type === 'publish-failed') {
    if (state.kind !== 'publishing') return state
    return {kind: 'reviewing', jobId: state.jobId, documentType: state.documentType, source: state.source, candidate: state.candidate, leavePrompt: state.leaveAfter, message: event.message}
  }
  return state
}

export function importBusy<C>(state: RuleImportState<C>) {
  return state.kind === 'uploading' || state.kind === 'parsing' || state.kind === 'publishing'
}
