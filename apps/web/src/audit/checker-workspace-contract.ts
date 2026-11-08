import type {AdoptChecker, CheckerRevision} from './checker-contract.ts'

export type CheckerSelection = {revisionId: string; purpose: 'publish' | 'rollback'; operationId: string | null; explicit: boolean}
export type CheckerWorkspace = CheckerSelection & {pending: AdoptChecker | null; editPending: {base: string; operationId: string} | null; legacy: boolean; legacyBlocked: boolean}

/** The workspace identity never changes when a newer candidate is selected. */
export function readCheckerWorkspace(url: URL, scope: string, initialRevision: string, legacyScope?: string): CheckerWorkspace {
  const params = url.searchParams
  const oldScopeMatches = Boolean(legacyScope && params.get('checkerFeedbackScope') === legacyScope)
  const oldSelection = !params.has('checkerDetailScope') && oldScopeMatches && Boolean(params.get('checkerDetailRoot'))
  const selected = params.get('checkerDetailScope') === scope || oldSelection ? params.get('checkerDetailRevision') : null
  const expected = params.get('checkerPublishExpected'), revision = params.get('checkerPublishRevision'), operation = params.get('checkerPublish')
  const oldPublication = !params.has('checkerPublishScope') && oldScopeMatches && Boolean(operation)
  const pending = (params.get('checkerPublishScope') === scope || oldPublication) && revision && operation && expected !== null && /^\d+$/.test(expected) && Number.isSafeInteger(Number(expected))
    ? {revision_id: revision, operation_id: operation, expected_revision: Number(expected), force_unverified: params.get('checkerPublishUnverified') === '1'} : null
  const editBase = params.get('checkerEditBase'), editOperation = params.get('checkerEditOperation')
  const oldEdit = !params.has('checkerEditScope') && oldScopeMatches && Boolean(editOperation)
  const editPending = (params.get('checkerEditScope') === scope || oldEdit) && editBase && editOperation ? {base: editBase, operationId: editOperation} : null
  return {
    revisionId: pending?.revision_id || editPending?.base || selected || initialRevision,
    purpose: selected && params.get('checkerDetailAction') === 'rollback' ? 'rollback' : 'publish',
    operationId: selected ? params.get('checkerDetailOperation') || (oldSelection ? params.get('checkerOptimization') : null) : null,
    explicit: Boolean(pending || editPending || selected),
    pending,
    editPending,
    legacy: Boolean(oldSelection && selected || oldPublication && pending || oldEdit && editPending),
    legacyBlocked: Boolean((operation && !params.has('checkerPublishScope') || editOperation && !params.has('checkerEditScope')) && !oldScopeMatches),
  }
}

export function checkerSelectionAddress(url: URL, scope: string, selection: CheckerSelection): URL {
  const next = new URL(url)
  const {pending, editPending} = readCheckerWorkspace(next, scope, selection.revisionId)
  if (editPending && editPending.base !== selection.revisionId) throw new Error('请先核对原保存操作，不能切换检查版本。')
  if (pending && pending.revision_id !== selection.revisionId) throw new Error('请先核对原发布操作，不能切换检查版本。')
  next.searchParams.delete('checkerDetailRoot')
  next.searchParams.set('checkerDetailScope', scope)
  next.searchParams.set('checkerDetailRevision', selection.revisionId)
  next.searchParams.set('checkerDetailAction', selection.purpose)
  if (selection.operationId) next.searchParams.set('checkerDetailOperation', selection.operationId)
  else next.searchParams.delete('checkerDetailOperation')
  return next
}

export function checkerPublicationAddress(url: URL, scope: string, input: AdoptChecker | null): URL {
  let next = new URL(url)
  if (input) {
    const selection = readCheckerWorkspace(next, scope, input.revision_id)
    if (selection.pending && selection.pending.operation_id !== input.operation_id) throw new Error('请先读取原发布操作，不能再次发布。')
    next = checkerSelectionAddress(next, scope, {...selection, revisionId: input.revision_id, explicit: true})
    next.searchParams.set('checkerPublishScope', scope)
    next.searchParams.set('checkerPublish', input.operation_id)
    next.searchParams.set('checkerPublishRevision', input.revision_id)
    next.searchParams.set('checkerPublishExpected', String(input.expected_revision))
    next.searchParams.set('checkerPublishUnverified', input.force_unverified ? '1' : '0')
  } else if (next.searchParams.get('checkerPublishScope') === scope) {
    for (const field of ['checkerPublishScope', 'checkerPublish', 'checkerPublishRevision', 'checkerPublishExpected', 'checkerPublishUnverified']) next.searchParams.delete(field)
  }
  return next
}

/** Discovery may fill an empty workspace, but cannot discard an explicit choice or later edits. */
export function optimizationSelection(selection: CheckerSelection, operation: {operationId: string; revisionId: string; explicit: boolean}, blocked = false): CheckerSelection | null {
  return blocked || selection.operationId === operation.operationId || selection.explicit && !operation.explicit ? null : {revisionId: operation.revisionId, purpose: 'publish', operationId: operation.operationId, explicit: true}
}

export function checkedWorkspaceRevision(value: CheckerRevision, suiteKey: string, revisionId: string): CheckerRevision {
  if (value.id !== revisionId || value.suite_key !== suiteKey) throw new Error('检查版本不属于当前工作区，请核对原版本。')
  return value
}

/** A feedback refresh keeps the open editor only for the same scope, suite, and frozen revision. */
export function refreshKeepsEditor(shown: {scope: string; suite: string; id: string} | null, requested: {scope: string; suite: string; id: string}) {
  return shown !== null && shown.scope === requested.scope && shown.suite === requested.suite && shown.id === requested.id
}

/** The same frozen revision id cannot be repainted from another tenant. A different id is a real switch. */
export function sameRevisionTenant(shown: {id: string; tenant: string | null} | null, next: {id: string; tenant_id?: string | null}) {
  return shown === null || shown.id !== next.id || shown.tenant === (next.tenant_id ?? null)
}
