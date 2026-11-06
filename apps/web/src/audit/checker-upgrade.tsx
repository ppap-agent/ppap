import {useEffect, useRef, useState} from 'react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {definiteCheckerRejection, type CheckerActive, type CheckerRevision} from './checker-contract'
import {checkedUpgrade, nativeCheckerUpgradeAdapter, savedUpgrade, upgradeAddress, upgradeAvailable, type CheckerUpgradeAdapter, type PendingUpgrade} from './checker-upgrade-contract'
import {writeAuditAddress} from './routes'

const errorText = (cause: unknown) => {
  const messages: Record<string, string> = {suite_official_head_conflict: '官方版本已更新。', suite_head_conflict: '企业采用的版本已变更。', suite_upgrade_not_available: '当前所选版本没有可用的官方升级。', suite_operation_conflict: '原升级操作已绑定其他内容。'}
  return messages[String((cause as {code?: string})?.code)] || (cause instanceof Error ? cause.message : '官方更新暂不可读。')
}

/** Official upgrades create a candidate; only an explicit click may enter the existing test loop. */
export function CheckerUpgrade({active, revision, disabled, canVerify, editingBlocked = false, onLockChange, onCandidate, onRefresh, adapter = nativeCheckerUpgradeAdapter}: {
  active: CheckerActive; revision: CheckerRevision; disabled: boolean; canVerify: boolean; editingBlocked?: boolean
  onLockChange: (locked: boolean) => void
  onCandidate: (value: CheckerRevision, runTests: boolean) => void
  onRefresh: () => void
  adapter?: CheckerUpgradeAdapter
}) {
  const live = useRef(true), locked = useRef(false), attemptedNotices = useRef(new Set<string>())
  const [pending, setPending] = useState(() => savedUpgrade(new URL(location.href), active.suite_key))
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [stale, setStale] = useState(false)
  const [notice, setNotice] = useState(active.official_update_notice ? active.latest_official_revision_id : null)
  const [noticeError, setNoticeError] = useState('')
  const target = active.latest_official_revision_id
  const applicable = upgradeAvailable(active, revision)
  useEffect(() => {live.current = true; return () => {live.current = false}}, [])
  useEffect(() => {onLockChange(busy || Boolean(pending)); return () => onLockChange(false)}, [busy, pending, onLockChange])
  useEffect(() => {
    if (!applicable || !active.official_update_notice || !target || attemptedNotices.current.has(target)) return
    if (notice !== target) {setNotice(target); setNoticeError(''); return}
    let current = true
    attemptedNotices.current.add(target)
    // This acknowledges the notice being displayed, not consent to upgrade or publish.
    void adapter.seen(active.suite_key, target).then(value => {
      if (!current || !live.current) return
      if (value.suite_key !== active.suite_key || value.latest_official_revision_id !== target || value.official_update_notice) throw new Error('更新提示的已读回执不一致。')
      setNoticeError('')
    }).catch(() => {if (current && live.current) setNoticeError('更新提示的已读状态暂未确认。下次进入时可能再次提示。')})
    return () => {current = false}
  }, [adapter, active.suite_key, active.official_update_notice, applicable, target, notice])
  function remember(value: PendingUpgrade | null) {setPending(value); writeAuditAddress(upgradeAddress(new URL(location.href), value))}
  async function start() {
    if (locked.current || disabled || pending || stale || !target || !canVerify || !applicable) return
    const command: PendingUpgrade = {suite: active.suite_key, input: {source_revision_id: revision.id, target_revision_id: target, expected_revision: active.revision, operation_id: crypto.randomUUID()}}
    locked.current = true; setBusy(true); onLockChange(true); remember(command); setError('')
    try {
      const candidate = checkedUpgrade(await adapter.upgrade(command.suite, command.input), command.suite, command.input)
      if (!live.current) return
      remember(null); setNotice(null); onCandidate(candidate, true)
    } catch (cause) {
      if (!live.current) return
      if (definiteCheckerRejection(cause)) {remember(null); setStale(true); setError(`${errorText(cause)} 请读取最新版本后重新核对。`)}
      else setError('升级结果尚未确定。请读取原升级操作，不要再次升级。')
    } finally {locked.current = false; if (live.current) setBusy(false)}
  }
  async function recover() {
    if (!pending || locked.current) return
    locked.current = true; setBusy(true); setError('')
    try {
      const candidate = checkedUpgrade(await adapter.read(pending.suite, pending.input), pending.suite, pending.input)
      if (!live.current) return
      remember(null); setNotice(null); onCandidate(candidate, false)
    } catch (cause) {if (live.current) setError(`尚未确认原升级回执，不会重试升级。${errorText(cause)}`)}
    finally {locked.current = false; if (live.current) setBusy(false)}
  }
  if (!applicable && !pending && !error) return null
  return <section aria-label="官方检查升级" className="space-y-3 rounded-md border p-3">
    {notice === target && <Alert><AlertTitle>官方检查有新版本</AlertTitle><AlertDescription>官方已发布{active.latest_official_version ? ` v${active.latest_official_version}` : '新版本'}。本企业修改仍固定在原版本，不会自动替换。</AlertDescription></Alert>}
    {noticeError && <p role="status" className="text-xs text-muted-foreground">{noticeError}</p>}
    <p className="text-sm">升级会把当前所选版本的企业修改带到官方新版本，生成待发布修改，并用同一批样本验证。不会调用模型或自动发布，已有报告不变。</p>
    {editingBlocked && <p role="status" className="text-sm">先保存当前修改再升级；保存结果未确认时，请先读取原保存操作。</p>}
    {error && <Alert variant="destructive"><AlertTitle>{pending ? '升级结果待核对' : '升级需要核对'}</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {pending ? <><p role="status" className="text-sm">原升级操作尚待核对；刷新或返回页面不会重复提交。</p><Button variant="outline" disabled={busy} onClick={() => void recover()}>{busy ? '正在读取…' : '读取原升级操作'}</Button></> : stale ? <Button variant="outline" disabled={disabled} onClick={onRefresh}>读取最新版本后核对升级</Button> : <Button variant="outline" disabled={disabled || busy || !canVerify || !applicable} onClick={() => void start()}>{busy ? '正在创建升级候选…' : '升级并验证'}</Button>}
  </section>
}
