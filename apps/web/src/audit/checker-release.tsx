import {displayTime} from '../helpers'
import {RuleImpactSummary} from './rule-impact'
import {useCallback, useEffect, useId, useRef, useState} from 'react'
import {CheckerEditor} from './checker-edit'
import {CheckerUpgrade} from './checker-upgrade'
import {checkerReason} from './checker-feedback-contract'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Label} from '../components/ui/label'
import {writeAuditAddress} from './routes'
import {checkerPublicationAddress, checkerSelectionAddress, checkedWorkspaceRevision, optimizationSelection, readCheckerWorkspace, refreshKeepsEditor, sameRevisionTenant, type CheckerSelection} from './checker-workspace-contract'
import {checkedAdoption, definiteCheckerRejection, nativeCheckerReleaseAdapter, requiresUnverifiedConfirmation, verificationText, type AdoptChecker, type AdoptionReceipt, type CheckerActive, type CheckerReleaseAdapter, type CheckerRevision} from './checker-contract'
import {Disclosure} from '../components/disclosure'
import {ReadSkeleton} from '../components/read-skeleton'

const errorText = (cause: unknown) => (cause as {code?: string})?.code === 'suite_dependencies_not_adopted' ? '关联检查尚未采用，请先核对关联检查版本。' : cause instanceof Error ? cause.message : '检查版本暂不可读。'
/** Publish and rollback share the same server CAS command. Existing rounds are never rewritten. */
export function CheckerRelease({scope, legacyScope, suiteKey, revisionId, optimization, onReaudit, onRevision, onVerify, verificationBusy = false, operationRunning = false, refreshKey = '', preferCurrent = false, onBusyChange, onWriteBlockedChange, onUpgradeResult, onRuleDraft, onPublished, onDependency, adapter = nativeCheckerReleaseAdapter}: {scope: string; legacyScope?: string; suiteKey: string; revisionId: string; optimization?: {operationId: string; revisionId: string; explicit: boolean}; onReaudit?: () => void; onRevision?: (id: string) => void; onVerify?: (id: string) => void; verificationBusy?: boolean; operationRunning?: boolean; refreshKey?: string; preferCurrent?: boolean; onBusyChange?: (busy: boolean) => void; onWriteBlockedChange?: (blocked: boolean) => void; onUpgradeResult?: (runTests: boolean) => void; onRuleDraft?: (id: string) => void; onPublished?: () => void; onDependency?: (key: string) => void; adapter?: CheckerReleaseAdapter}) {
  const formId = useId(), locked = useRef(false), live = useRef(true), generation = useRef(0), holdWrites = useRef(false)
  const shown = useRef<{scope: string; suite: string; id: string; tenant: string | null; validation: string} | null>(null)
  const [revision, setRevision] = useState<CheckerRevision | null>(null), [active, setActive] = useState<CheckerActive | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [confirm, setConfirm] = useState(false), [acknowledged, setAcknowledged] = useState(false), [writesHeld, setWritesHeld] = useState(false)
  const [seenRefresh, setSeenRefresh] = useState(refreshKey)
  if (seenRefresh !== refreshKey) {
    setSeenRefresh(refreshKey)
    holdWrites.current = true
    setWritesHeld(true)
  }
  const [selection, setSelection] = useState(() => readCheckerWorkspace(new URL(location.href), scope, revisionId, legacyScope))
  const [pending, setPending] = useState(selection.pending), [receipt, setReceipt] = useState<AdoptionReceipt | null>(null)
  const {revisionId: selected, purpose, explicit: explicitSelection} = selection
  const [upgradeLocked, setUpgradeLocked] = useState(false), [editorBlocked, setEditorBlocked] = useState(false), [editorMutating, setEditorMutating] = useState(false)
  const mutationBusy = busy || upgradeLocked || selection.legacyBlocked
  // A held authoritative read may be retried with dirty text, but never over a child mutation.
  const recoveryBlocked = mutationBusy || editorMutating || operationRunning || editorBlocked && !writesHeld
  useEffect(() => {live.current = true; return () => {live.current = false}}, [])
  // A failed read still fences local writes, but must allow another source.
  // Mutations, unknown publication receipts and unsaved edits still block leaving.
  useEffect(() => {onBusyChange?.(mutationBusy || Boolean(pending) || editorBlocked || !revision && !error)}, [onBusyChange, mutationBusy, pending, editorBlocked, revision, error])
  useEffect(() => () => onBusyChange?.(false), [onBusyChange])
  useEffect(() => {onWriteBlockedChange?.(mutationBusy || Boolean(pending) || editorBlocked || writesHeld || !revision)}, [onWriteBlockedChange, mutationBusy, pending, editorBlocked, writesHeld, revision])
  useEffect(() => () => onWriteBlockedChange?.(false), [onWriteBlockedChange])
  const select = useCallback((next: CheckerSelection) => {
    writeAuditAddress(checkerSelectionAddress(new URL(location.href), scope, next))
    setSelection({...next, pending: null, editPending: null, legacy: false, legacyBlocked: false}); setReceipt(null); onRevision?.(next.revisionId)
  }, [scope, onRevision])
  useEffect(() => {
    if (!optimization || !revision) return
    // Pending edit identity exists before the editor mounts and must fence incoming optimizer receipts.
    const saved = readCheckerWorkspace(new URL(location.href), scope, selected, legacyScope)
    const blocked = writesHeld || mutationBusy || Boolean(pending || saved.editPending) || editorBlocked || saved.legacy || saved.legacyBlocked
    const next = optimizationSelection(selection, optimization, blocked)
    if (next) select(next)
  }, [optimization?.operationId, optimization?.revisionId, optimization?.explicit, selection, writesHeld, mutationBusy, pending, editorBlocked, revision, scope, selected, legacyScope, select])
  function keepPublish(input: AdoptChecker | null) {writeAuditAddress(checkerPublicationAddress(new URL(location.href), scope, input))}
  const read = useCallback(async (keepSelected = false) => {
    let next = checkedWorkspaceRevision(await adapter.revision(selected), suiteKey, selected)
    const current = await adapter.active(next.suite_key)
    if (current.suite_key !== next.suite_key) throw new Error('企业正在使用的检查与当前版本不一致。')
    // A historical report locates the Suite; it must not silently replace newer enterprise edits.
    if (!keepSelected && preferCurrent && !explicitSelection && current.current_revision_id && current.current_revision_id !== next.id) {
      next = checkedWorkspaceRevision(await adapter.revision(current.current_revision_id), suiteKey, current.current_revision_id)
    }
    return {next, current}
  }, [adapter, selected, suiteKey, preferCurrent, explicitSelection])
  function setWriteHold(held: boolean) {holdWrites.current = held; setWritesHeld(held)}
  function commitLoaded(next: CheckerRevision, current: CheckerActive, requestedScope: string, requestedSuite: string) {
    if (next.suite_key !== requestedSuite || current.suite_key !== requestedSuite) throw new Error('检查版本不属于当前工作区，请核对原版本。')
    const tenant = next.tenant_id ?? null
    const validation = JSON.stringify(next.validation)
    const visible = shown.current
    const same = refreshKeepsEditor(visible, {scope: requestedScope, suite: requestedSuite, id: next.id})
    if (!sameRevisionTenant(visible, next)) throw new Error('检查版本不属于当前工作区，请核对原版本。')
    if (!same) {setConfirm(false); setAcknowledged(false)}
    else if (visible!.validation !== validation) setAcknowledged(false)
    shown.current = {scope: requestedScope, suite: requestedSuite, id: next.id, tenant, validation}
    setRevision(next); setActive(current)
  }
  useEffect(() => {
    const token = ++generation.current
    const requestedScope = scope, requestedSuite = suiteKey, requestedId = selected
    const retain = refreshKeepsEditor(shown.current, {scope: requestedScope, suite: requestedSuite, id: requestedId})
    // Late feedback for this same frozen revision refetches validation without unmounting the editor.
    if (!retain) {
      shown.current = null
      setRevision(null); setActive(null); setConfirm(false); setAcknowledged(false)
    }
    setWriteHold(true); setError('')
    void read().then(({next, current}) => {
      if (generation.current !== token || !live.current) return
      try {
        if (selection.legacy) {
          let url = checkerSelectionAddress(new URL(location.href), requestedScope, {...selection, revisionId: next.id})
          if (selection.pending) url = checkerPublicationAddress(url, requestedScope, selection.pending)
          if (selection.editPending) url.searchParams.set('checkerEditScope', requestedScope)
          writeAuditAddress(url)
        }
        commitLoaded(next, current, requestedScope, requestedSuite)
        if (next.id !== requestedId || selection.legacy) setSelection(value => ({...value, revisionId: next.id, legacy: false}))
        onRevision?.(next.id)
        if (generation.current !== token || !live.current) return
        setWriteHold(false)
      } catch (cause) {
        if (generation.current !== token || !live.current) return
        setError(errorText(cause))
      }
    }).catch(cause => {if (generation.current === token && live.current) setError(errorText(cause))})
    return () => {if (generation.current === token) generation.current += 1}
  }, [read, refreshKey, onRevision, selection.legacy, scope, suiteKey, selected])
  const unverified = revision ? requiresUnverifiedConfirmation(revision) : true
  const unadoptedRelated = revision?.unadopted_related_suite_keys || []
  const adopted = revision && active?.current_revision_id === revision.id
  const firstAdoption = active?.selection_mode === 'candidate_only'
  const needsAdoption = !adopted || unverified && !active?.force_unverified
  async function recover() {
    if (locked.current || recoveryBlocked) return
    const token = ++generation.current
    const requestedScope = scope, requestedSuite = suiteKey
    locked.current = true; setBusy(true); setWriteHold(true); setError('')
    try {
      const {next, current} = await read(editorBlocked)
      if (generation.current !== token || !live.current) return
      commitLoaded(next, current, requestedScope, requestedSuite)
      if (pending) {
        const saved = current.history.find(item => item.operation_id === pending.operation_id)
        if (!saved) {setError('尚未读到原发布结果。不要重复发布，可以稍后继续读取。'); return}
        setReceipt(checkedAdoption(saved, next.suite_key, pending)); setPending(null); keepPublish(null); onPublished?.()
      }
      if (generation.current !== token || !live.current) return
      setError(''); setWriteHold(false)
    } catch (cause) {if (generation.current === token && live.current) setError(errorText(cause))}
    finally {locked.current = false; if (live.current) setBusy(false)}
  }
  async function selectRuleUpdate() {
    if (!active?.rule_update || locked.current || mutationBusy || editorBlocked || pending || holdWrites.current) return
    locked.current = true; setBusy(true); setError('')
    try {
      const candidate = checkedWorkspaceRevision(await adapter.revision(active.rule_update.target_revision_id), suiteKey, active.rule_update.target_revision_id)
      if (!live.current) return
      select({...selection, revisionId: candidate.id, purpose: 'publish', explicit: true})
      onRevision?.(candidate.id); onRuleDraft?.(candidate.id)
    } catch (cause) {if (live.current) setError(errorText(cause))}
    finally {locked.current = false; if (live.current) setBusy(false)}
  }
  async function publish() {
    if (holdWrites.current || !revision || !active || unadoptedRelated.length || pending || editorBlocked || selection.legacyBlocked || upgradeLocked || locked.current || !confirm || unverified && !acknowledged) return
    const input: AdoptChecker = {revision_id: revision.id, expected_revision: active.revision, operation_id: crypto.randomUUID(), force_unverified: unverified}
    locked.current = true
    setPending(input); keepPublish(input); setBusy(true); setError('')
    try {
      const saved = await adapter.adopt(revision.suite_key, input)
      if (!live.current) return
      checkedAdoption(saved, revision.suite_key, input)
      setReceipt(saved); setPending(null); keepPublish(null); setConfirm(false); onPublished?.()
      try {const token = ++generation.current; const requestedScope = scope, requestedSuite = suiteKey; const {next, current} = await read(); if (generation.current !== token || !live.current) return; commitLoaded(next, current, requestedScope, requestedSuite)} catch {if (live.current) setError('发布结果已确认，但企业当前采用版本暂不可读。请读取最新版本。')}
    } catch (cause) {
      if (!live.current) return
      if (definiteCheckerRejection(cause)) {setPending(null); keepPublish(null); setConfirm(false); setActive(null); setError(`${errorText(cause)} 请读取最新版本后重新核对。`)}
      else setError('发布结果尚未确定。请查看原操作，不要重复发布或回滚。')
    } finally {locked.current = false; setBusy(false)}
  }
  return <section aria-label="检查发布与版本记录" className="space-y-4 border-t pt-4">
    <h3 className="font-medium">检查版本</h3>
    {selection.legacyBlocked && <Alert variant="destructive"><AlertTitle>旧修改操作需要核对</AlertTitle><AlertDescription>原修改操作缺少当前工作区标识。请回到发起操作的原审核和条款读取；这里不会清除它或重复提交。</AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertTitle>{pending ? '发布结果待确认' : '版本需要核对'}</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {!revision && !error && <ReadSkeleton label="正在读取检查版本…" kind="inline"/>}
    {revision && <>
      <p className="text-sm">{revision.title || '文件检查'} · {revision.tenant_id ? '本企业版本' : '官方版本'}{revision.version ? ` v${revision.version}` : ''}{adopted ? ' · 当前使用' : purpose === 'rollback' ? ' · 历史版本' : firstAdoption ? ' · 待采用' : ' · 待发布的修改'}</p>
      <p role="status" className="text-sm">{verificationText(revision.validation)}</p>{revision.validation.samples_not_rerun && <p className="text-sm text-muted-foreground">平台已更新，样本未重跑。</p>}{active && <p className="text-xs text-muted-foreground">{active.selection_mode === 'automatic_official' ? '当前自动跟随官方最新检查。发布后会固定采用所选版本。' : active.selection_mode === 'pinned' ? '当前已固定采用一个版本，不会自动升级。' : '尚未采用检查版本。发布后，本企业的新审核才会使用此版本。'}</p>}
      {unadoptedRelated.length > 0 && <Alert><AlertTitle>先采用关联检查版本</AlertTitle><AlertDescription><p>本检查读取以下关联检查。请先逐项核对并采用，再返回发布当前版本。</p><div className="mt-2 flex flex-wrap gap-2">{unadoptedRelated.map(key => onDependency ? <Button key={key} variant="outline" size="sm" disabled={mutationBusy || editorBlocked || Boolean(pending)} onClick={() => onDependency(key)}>查看 {key}</Button> : <span key={key}>{key}</span>)}</div></AlertDescription></Alert>}
      {active?.rule_update && <div className="space-y-3 rounded-md border p-3"><Alert><AlertTitle>规则已更新，需要重新验证</AlertTitle><AlertDescription>当前已发布检查仍基于旧规则。新审核继续使用旧规则和旧检查，直到你明确发布新版；已有结果不变。</AlertDescription></Alert><RuleImpactSummary impact={active.rule_update}/><Button disabled={mutationBusy || editorBlocked || Boolean(pending) || writesHeld || !onRuleDraft} onClick={() => void selectRuleUpdate()}>按新版规则起草修改</Button><p className="text-xs text-muted-foreground">先核对规则和样本，再确认使用自己的 Key。现在不会调用模型或发布。</p></div>}
      {revision.rule_impact?.changed && <RuleImpactSummary impact={revision.rule_impact}/>}
      {active?.official_rule_impact?.changed && <RuleImpactSummary impact={active.official_rule_impact}/>}
      {active && <CheckerUpgrade key={active.suite_key} scope={scope} active={active} revision={revision} disabled={busy || Boolean(pending) || verificationBusy || editorBlocked || writesHeld} editingBlocked={editorBlocked} canVerify={Boolean(onVerify)} onLockChange={setUpgradeLocked} onRefresh={() => {setActive(null); void recover()}} onCandidate={(value, runTests) => {select({...selection, revisionId: value.id, purpose: 'publish', explicit: true}); onRevision?.(value.id); onUpgradeResult?.(runTests); if (runTests) onVerify?.(value.id)}}/>}
      {unverified && <div className="space-y-2 text-sm"><p className="font-medium text-warning">需要重新验证</p>{revision.validation.reasons.map((reason, index) => <p key={index}>{checkerReason(reason)}</p>)}</div>}
      <Disclosure className="text-sm" summary={<>查看修改摘要与高级内容</>}><div className="space-y-3"><p>修改文件：{Object.keys(revision.files).join('、') || '未提供修改文件'}</p><p className="sm:hidden text-muted-foreground">手机上仅展示摘要，可在电脑查看完整规则和代码。</p><div className="hidden sm:block">{revision.diff.map(change => <Disclosure key={change.path} summary={<>{change.path}</>}><p className="mt-2">修改前</p><pre tabIndex={0} className="max-h-60 overflow-auto rounded border p-3 text-xs">{change.before ?? '新增文件'}</pre><p className="mt-2">修改后</p><pre tabIndex={0} className="max-h-60 overflow-auto rounded border p-3 text-xs">{change.after ?? '已移除'}</pre></Disclosure>)}</div></div></Disclosure>
      <CheckerEditor key={revision.id} scope={scope} revision={revision} onBlockedChange={setEditorBlocked} onMutationChange={setEditorMutating} disabled={mutationBusy || Boolean(pending) || operationRunning} saveDisabled={writesHeld} onSaved={value => select({...selection, revisionId: value.id, purpose: 'publish', explicit: true})}/>{onVerify && <Button variant="outline" disabled={mutationBusy || editorBlocked || Boolean(pending) || verificationBusy || writesHeld} onClick={() => onVerify(revision.id)}>重新运行验证样本</Button>}
      {receipt && <Alert><AlertTitle>发布操作已完成</AlertTitle><AlertDescription>当次采用已保存。{active && active.revision > receipt.revision ? '企业当前采用版本可能已再次变更，请核对版本记录。' : '本企业之后的新审核使用此版本。'} 已有报告不变。</AlertDescription></Alert>}
      {adopted && onReaudit && <Button disabled={mutationBusy || editorBlocked || Boolean(pending) || writesHeld} onClick={onReaudit}>用新版本重审这份文件</Button>}
      {needsAdoption && !confirm && <Button disabled={mutationBusy || editorBlocked || Boolean(pending) || writesHeld || !active || unadoptedRelated.length > 0} onClick={() => {setConfirm(true); setAcknowledged(false)}}>{adopted ? '核对并继续使用未验证版本' : firstAdoption ? '采用此版本' : purpose === 'publish' ? '发布此修改' : '回滚到此版本'}</Button>}
      {confirm && <div role="group" aria-label="确认采用检查版本" className="space-y-3 rounded-md border p-3">
        <p className="text-sm">之后的新审核使用此版本，已有报告不变。读取它的其他检查需要重新验证时，会在对应检查中标明原因。</p>
        <p className="text-sm">{verificationText(revision.validation)}</p>
        {unverified && <><p className="text-sm text-warning">当前检查尚未验证通过。采用后，受影响的新结果和报告会持续带有“需要重新验证”标记。</p><ul className="list-inside list-disc text-sm">{revision.validation.reasons.map((reason, index) => <li key={index}>{checkerReason(reason)}</li>)}{revision.validation.new_errors > 0 && <li>新增错误 {revision.validation.new_errors} 条</li>}</ul><div className="flex items-start gap-2"><Checkbox id={`${formId}-unverified`} checked={acknowledged} onCheckedChange={value => setAcknowledged(value === true)}/><Label htmlFor={`${formId}-unverified`}>我已核对上述未验证项和新增错误，仍要启用</Label></div></>}
        <div className="flex flex-wrap gap-2"><Button disabled={mutationBusy || editorBlocked || Boolean(pending) || writesHeld || unadoptedRelated.length > 0 || unverified && !acknowledged} onClick={() => void publish()}>{busy ? '正在提交…' : firstAdoption ? '确认采用' : purpose === 'publish' ? '确认发布' : '确认回滚'}</Button><Button variant="ghost" disabled={mutationBusy || editorBlocked} onClick={() => setConfirm(false)}>取消</Button></div>
      </div>}
      <Disclosure className="text-sm" summary={<>版本记录与受影响审核</>}><div className="space-y-3"><p>该版本用于 {revision.round_count} 轮你有权限查看的审核；回滚不会改写它们。</p>{revision.rounds.map(round => <a key={round.id} className="block underline" href={`/audit?round=${encodeURIComponent(round.id)}`}>{round.created_at ? displayTime(round.created_at) : '查看审核'}</a>)}{active?.history.map(item => <div key={item.operation_id} className="flex flex-wrap items-center justify-between gap-2 border-t pt-2"><p>采用记录 {item.revision}{item.published_at ? ` · ${displayTime(item.published_at)}` : ''}{item.published_by_name ? ` · 发布人 ${item.published_by_name}` : ''}{item.force_unverified ? ' · 需要重新验证' : ''}</p><Button variant="outline" size="sm" disabled={mutationBusy || editorBlocked || Boolean(pending)} onClick={() => {select({...selection, revisionId: item.revision_id, purpose: 'rollback', explicit: true})}}>查看此版本／回滚</Button></div>)}</div></Disclosure>
    </>}
    <Button variant="outline" size="sm" disabled={recoveryBlocked} onClick={() => void recover()}>{pending ? '查看原发布操作' : '读取最新版本'}</Button>
  </section>
}
