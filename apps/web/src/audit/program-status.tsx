import {useEffect, useRef, useState, type ReactNode} from 'react'
import {request} from '../api'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {message} from '../journey/remote'
import {nativeCheckerReleaseAdapter, type CheckerActive, type CheckerRevision} from './checker-contract'

type Related = (suiteKey: string) => {label: string; open: () => void} | undefined

/** The card at the top of a rule page frames a rule's program: whether audits can use it, which version, and the one next step. */
export function StatusCard({badge, tone = 'outline', title, children, action}: {badge: string; tone?: 'success' | 'warning' | 'outline'; title: string; children?: ReactNode; action?: ReactNode}) {
  return <section aria-label="检查程序" className="flex flex-wrap items-start justify-between gap-4 rounded-lg border bg-muted/30 px-5 py-4">
    <div className="min-w-0 flex-1 space-y-1.5">
      <p className="flex flex-wrap items-center gap-2"><Badge shape="square" variant={tone}>{badge}</Badge><span className="font-medium">{title}</span></p>
      {children && <div className="space-y-1 text-sm text-muted-foreground">{children}</div>}
    </div>
    {action && <div className="flex shrink-0 flex-wrap gap-2">{action}</div>}
  </section>
}

/**
 * A program that exists: the version later audits use and, when there is one, a newer version to adopt.
 * Uploaded rules can also update their program after the rule changed. Repairs from audit results arrive here.
 */
export function ProgramStatus({suiteKey, offered, onUpdate, related, extra, onChanged}: {suiteKey: string; offered?: string | null; onUpdate?: () => void; related?: Related; extra?: ReactNode; onChanged?: () => void}) {
  const [active, setActive] = useState<CheckerActive | null>(null)
  const [offer, setOffer] = useState<CheckerRevision | null>(null)
  const [readError, setReadError] = useState('')
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  // A lost response is read back with the same operation; only a refusal frees it for a new one.
  const pending = useRef<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    setReadError('')
    void (async () => {
      const current = await request<CheckerActive>(`/suites/${encodeURIComponent(suiteKey)}/active`, 'GET', undefined, controller.signal)
      const officialUpdate = current.origin === 'official' && current.official_update_available ? current.latest_official_revision_id : null
      const offerId = [offered, current.offered_revision_id, current.current_revision_id ? null : current.candidate_revision_id, officialUpdate].find(id => id && id !== current.current_revision_id)
      const next = offerId ? await request<CheckerRevision>(`/suite-revisions/${encodeURIComponent(offerId)}`, 'GET', undefined, controller.signal) : null
      if (controller.signal.aborted) return
      setActive(current); setOffer(next)
    })().catch(cause => {if (!controller.signal.aborted) setReadError(message(cause))})
    return () => controller.abort()
  }, [suiteKey, offered, attempt])
  if (readError) return <StatusCard badge="暂时读不到" title="检查程序的状态没读到" action={<Button variant="outline" onClick={() => setAttempt(value => value + 1)}>重新读取</Button>}>{readError}</StatusCard>
  if (!active) return <StatusCard badge="正在读取" title="正在读取…"/>
  const verified = offer?.validation.verified === true
  const waiting = offer?.unadopted_related_suite_keys ?? []
  const inUse = Boolean(active.current_revision_id)
  async function adopt() {
    if (!active || !offer || busy) return
    setBusy(true); setError('')
    const operationId = pending.current ?? crypto.randomUUID()
    pending.current = operationId
    try {
      await nativeCheckerReleaseAdapter.adopt(suiteKey, {revision_id: offer.id, expected_revision: active.revision, operation_id: operationId, force_unverified: !verified})
      pending.current = null
      setConfirming(false)
      setAttempt(value => value + 1)
      onChanged?.()
    } catch (cause) {
      const status = (cause as {status?: number}).status
      if (status && status < 500) {
        pending.current = null
        const code = (cause as {code?: string}).code
        setError(code === 'suite_dependencies_not_adopted' ? '要先采用它依赖的其他规则的检查程序。' : code === 'suite_head_conflict' ? '有人刚改过版本，已刷新。' : message(cause))
        setAttempt(value => value + 1)
      } else setError('还不知道是否采用成功。稍后再点一次，不会重复采用。')
    } finally {setBusy(false)}
  }
  const changed = Boolean(onUpdate && active.rule_update)
  const action = offer
    ? <Button disabled={busy || waiting.length > 0} onClick={() => {setError(''); setConfirming(true)}}>{inUse ? '采用新版本' : '采用'}</Button>
    : onUpdate ? <Button variant={changed ? 'default' : 'outline'} onClick={onUpdate}>更新检查程序</Button> : undefined
  return <>
    <StatusCard
      badge={inUse ? '可用于审核' : '还不能用于审核'}
      tone={inUse ? 'success' : 'outline'}
      title={inUse ? `第 ${active.version} 版 · ${active.verification_status === 'verified' ? '已验证' : '未验证'}` : '还没采用'}
      action={action}
    >
      {!inUse && <p>采用后，之后的审核才会用它。</p>}
      {changed && <p>规则已改，检查程序还按上一版规则检查。</p>}
      {offer && <p className="flex flex-wrap items-center gap-2 text-foreground">{inUse ? '有新版本：' : '可以采用：'}第 {offer.version} 版<Badge shape="square" variant={verified ? 'success' : 'outline'}>{verified ? '已验证' : '未验证'}</Badge></p>}
      {offer && waiting.length > 0 && <div className="space-y-2"><p>这个版本依赖下面这些规则的检查程序，先采用它们：</p><div className="flex flex-wrap gap-2">{waiting.map(key => {
        const target = related?.(key)
        return target ? <Button key={key} variant="outline" size="sm" onClick={target.open}>{target.label}</Button> : <span key={key}>{key}</span>
      })}</div></div>}
      {inUse && !offer && !changed && onUpdate && <p>判错了？在审核结果里标出正确答案。要改检查方式，点“更新检查程序”。</p>}
      {extra}
      {error && !confirming && <p role="alert" className="text-destructive">{error}</p>}
    </StatusCard>
    <Dialog open={confirming} onOpenChange={open => !busy && setConfirming(open)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>采用第 {offer?.version} 版？</DialogTitle>
          <DialogDescription>之后的审核会用这个版本，已有结果不变。{verified ? '' : '它还没验证过，结果要人工确认。'}</DialogDescription>
        </DialogHeader>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={() => setConfirming(false)}>取消</Button><Button disabled={busy} onClick={() => void adopt()}>采用</Button></div>
      </DialogContent>
    </Dialog>
  </>
}
