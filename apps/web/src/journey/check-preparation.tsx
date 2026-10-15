import {useEffect, useState, type SetStateAction} from 'react'
import {ArrowRight, LoaderCircle} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
import {canStartCheck, modeName, observeCheck, startCheck, type CheckPlan, type CheckPreparationState} from './check-preparation-state'
import {readUsage, type CheckOwner} from './usage-state'
import {isPreview} from './access'
import type {LiveCheck} from './live-audit'

export function CheckPreparation({caseId, state, plan, onChange, onContinue, continueLabel = '查看审核进度', onHome, saveError, recheck, live, compact = false, disabledReason, onStart, nextAction}: {
  caseId: string; state: CheckPreparationState; plan: CheckPlan; onChange: (state: SetStateAction<CheckPreparationState | undefined>) => void
  onContinue: () => void; continueLabel?: string; onHome: () => void; saveError: string; live?: LiveCheck
  recheck?: {owner: CheckOwner; description: string; note: string; backLabel?: string}
  compact?: boolean; disabledReason?: string; onStart?: () => void
  nextAction?: {label: string; run: () => void}
}) {
  const record = state.request, frozen = record?.plan ?? plan
  const [simulator, setSimulator] = useState(false)
  const update = (patch: Partial<CheckPreparationState>) => onChange(current => ({...state, ...current, ...patch}))
  useEffect(() => {
    if (live || record?.phase !== 'requesting' || state.outcome === 'waiting') return
    const timer = setTimeout(() => onChange(current => current ? observeCheck(current, record.id, record.attempt, false) : current), 800)
    return () => clearTimeout(timer)
  }, [live, record?.id, record?.phase, record?.attempt, state.outcome])
  const pending = record && ['requesting', 'unknown'].includes(record.phase)
  const canStart = live ? live.ready && !live.busy && live.targets.length > 0 && Boolean(live.quota) && live.quota?.accept_allowed !== false : canStartCheck(state, plan, readUsage({caseId, check: state, owner: recheck?.owner}).available)
  const modes = (['standard', 'deep'] as const).filter(mode => mode === 'standard' || !live || live.deepOffered || frozen.mode === 'deep')
  const modePicker = modes.length > 1 && (!record || record.phase === 'absent') ? <RadioGroup value={frozen.mode} onValueChange={mode => update({mode: mode as CheckPreparationState['mode']})} aria-label="审核方式" className="flex"><div className="flex gap-4">{modes.map(mode => <label key={mode} className="flex min-h-9 items-center gap-2 text-sm max-md:min-h-11"><RadioGroupItem value={mode} disabled={Boolean(live?.busy) || mode === 'deep' && live?.deepOffered === false}/>{modeName(mode)}</label>)}</div></RadioGroup> : <span className="text-sm font-medium">{modeName(frozen.mode)}</span>
  const quotaError = record?.phase !== 'accepted' && live?.quotaError && <Alert variant="destructive"><AlertDescription><p>{live.quotaError}</p><Button variant="outline" size="sm" onClick={live.refreshQuota}>重新读取启动条件</Button></AlertDescription></Alert>
  if (compact) return <section aria-label="开始审核" className="space-y-3">
    {(saveError || live?.error) && <Alert variant="destructive"><AlertDescription>{saveError || live?.error}</AlertDescription></Alert>}
    {quotaError}
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex flex-wrap items-center gap-3">{modePicker}{record?.phase !== 'accepted' && (!live || live.quota?.usage_charge_enabled !== false) && <span className="text-sm text-muted-foreground">{live ? live.quota?.estimated_points !== undefined ? `${live.quota.estimated_points} 点` : live.quotaError ? '' : '正在核对启动条件…' : `${frozen.estimate.low}–${frozen.estimate.high} 点（演示）`}</span>}</div>
      {record?.phase === 'accepted' ? <Button onClick={onContinue}>{continueLabel}<ArrowRight/></Button> : pending ? <Button disabled={live?.busy} onClick={live ? live.reconcile : () => onChange(current => current ? observeCheck(current, record.id, record.attempt, true) : current)}>{live?.busy ? record.phase === 'requesting' ? '正在提交…' : '正在核对…' : '核对受理结果'}</Button> : disabledReason && nextAction ? <Button onClick={nextAction.run}>{nextAction.label}<ArrowRight/></Button> : <Button className="max-sm:w-full" disabled={Boolean(saveError || disabledReason) || !canStart} onClick={onStart ?? (live ? live.start : () => onChange(current => startCheck(current ?? state, plan)))}>{live?.busy && <LoaderCircle className="animate-spin motion-reduce:animate-none"/>}{record ? '重试原请求' : '开始审核'}<ArrowRight/></Button>}
    </div>
    {record?.phase !== 'accepted' && <p role="status" className="text-sm text-muted-foreground">{pending ? record.phase === 'requesting' && live?.busy ? '正在提交本次资料与审核依据…' : '受理结果尚未确认；本次资料已保留，请先查询原请求。' : live?.quota?.accept_allowed === false ? live.quota.message || '本次额度不可用' : disabledReason || (frozen.mode === 'standard' ? 'Standard 执行已适配检查，其余条款保留为未覆盖。' : 'Deep 调查条款与原件，并核验证据。')}</p>}
    {!live && isPreview() && <NativeSelect aria-label="演示受理情形" value={state.outcome} onChange={event => update({outcome: event.target.value as CheckPreparationState['outcome']})}><NativeSelectOption value="accepted">正常受理</NativeSelectOption><NativeSelectOption value="unknown_accepted">回执丢失，已受理</NativeSelectOption><NativeSelectOption value="unknown_absent">回执丢失，未受理</NativeSelectOption><NativeSelectOption value="rejected">明确未受理</NativeSelectOption><NativeSelectOption value="waiting">等待</NativeSelectOption></NativeSelect>}
  </section>
  return <section className={"flex flex-col gap-6 pb-12"}>
    <header className={"flex flex-wrap items-start justify-between gap-3"}><div><h1 className={"text-2xl font-semibold tracking-tight"}>{record ? '本次检查方案' : recheck ? '复查准备' : '检查准备'}</h1><p className={"mt-2 text-sm text-muted-foreground"}>{frozen.basis} · {frozen.files.length} 份原件</p></div><Badge variant="outline">{modeName(frozen.mode)}</Badge></header>
    {(saveError || live?.error) && <Alert variant="destructive"><AlertDescription>{saveError || live?.error}</AlertDescription></Alert>}
    {quotaError}
    {record && <div className={"flex flex-col gap-3 border-l-2 pl-4"} role="status"><h2 className={"font-semibold"}>{record.phase === 'accepted' ? '检查已受理' : record.phase === 'absent' ? '尚未受理' : record.phase === 'requesting' ? '正在确认受理' : '受理结果待核对'}</h2>
      <p className={"text-sm text-muted-foreground"}>{record.phase === 'accepted' ? '可以查看检查进度，也可以稍后从工作台继续。' : pending ? '先核对原请求，避免重复启动检查。' : '可按原文件和规则重新提交。'}</p>
      {pending && <Button variant="outline" className={"w-fit"} disabled={live?.busy} onClick={live ? live.reconcile : () => onChange(current => current ? observeCheck(current, record.id, record.attempt, true) : current)}>核对受理状态</Button>}
      {record.phase === 'accepted' && <Button className={"w-fit"} onClick={onContinue}>查看检查进度<ArrowRight/></Button>}
    </div>}
    <div className={"grid items-start gap-8 lg:grid-cols-[minmax(0,3fr)_minmax(16rem,2fr)]"}>
      <section className={"min-w-0"}><h2 className={"mb-3 font-semibold"}>本次检查范围</h2><Table><TableHeader><TableRow><TableHead>审核要求</TableHead><TableHead>处理范围</TableHead></TableRow></TableHeader><TableBody>{frozen.rows.map(row => <TableRow key={row.id}><TableCell><p className={"font-medium"}>{row.title}</p><p className={"mt-1 text-xs text-muted-foreground"}>{row.sources.join('、')}</p></TableCell><TableCell className={""}>{row.state === 'excluded' ? row.detail : live ? '覆盖情况以执行结果为准' : row.detail}</TableCell></TableRow>)}</TableBody></Table>
        {live && <div className={"mt-4 flex flex-col gap-2 text-sm"}><strong>待审文件</strong>{live.targets.length ? live.targets.map(name => <p key={name} className={"break-all"}>{name}</p>) : <p className={"text-destructive"}>请返回核对资料类型，至少一份文件须对应本次要求。</p>}<p className={"text-muted-foreground"}>其余选中的资料作为联合审核的参考原件。</p></div>}
      </section>
      <section className={"flex flex-col gap-5"}><div><h2 className={"mb-3 font-semibold"}>检查方式</h2>{modePicker}<p className={"mt-3 text-sm text-muted-foreground"}>{frozen.mode === 'standard' ? '执行已有适配检查，未支持的条款保留为未覆盖。' : '调查条款与原件的对应关系，核验证据后形成结果。'}</p></div>
        <div><h2 className={"font-semibold"}>本次用量</h2><p className={"mt-2 text-sm"}>{live ? live.quota?.usage_charge_enabled === false ? '当前环境不计费' : live.quota?.estimated_points !== undefined ? `${live.quota.estimated_points} 点` : '正在核对额度…' : `${frozen.estimate.low}–${frozen.estimate.high} 点（演示）`}</p>{live?.quota?.accept_allowed === false && <p role="alert" className={"mt-2 text-sm text-destructive"}>{live.quota.message || live.quota.accept_reason || '额度暂不可用'}</p>}</div>
        {(!record || record.phase === 'absent') && <Button disabled={Boolean(saveError) || !canStart} onClick={live ? live.start : () => onChange(current => startCheck(current ?? state, plan))}>{live?.busy && <LoaderCircle className={""}/>}{record ? '重试原请求' : recheck ? '开始复查' : '开始检查'}<ArrowRight/></Button>}
        <Button variant="ghost" onClick={onHome}>{recheck?.backLabel || '返回工作台'}</Button>
      </section>
    </div>
    {recheck?.note && <p className={"text-sm text-muted-foreground"}>{recheck.note}</p>}
    <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">查看本次文件版本</Button></CollapsibleTrigger><CollapsibleContent><ul className={"mt-2 space-y-2 text-sm"}>{frozen.files.map(file => <li key={file}>{file}</li>)}</ul></CollapsibleContent></Collapsible>
    {!live && isPreview() && <Collapsible open={simulator} onOpenChange={setSimulator}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">演示受理情形</Button></CollapsibleTrigger><CollapsibleContent><NativeSelect value={state.outcome} onChange={event => update({outcome: event.target.value as CheckPreparationState['outcome']})}><NativeSelectOption value="accepted">正常受理</NativeSelectOption><NativeSelectOption value="unknown_accepted">回执丢失，已受理</NativeSelectOption><NativeSelectOption value="unknown_absent">回执丢失，未受理</NativeSelectOption><NativeSelectOption value="rejected">明确未受理</NativeSelectOption><NativeSelectOption value="waiting">等待</NativeSelectOption></NativeSelect></CollapsibleContent></Collapsible>}
  </section>
}
