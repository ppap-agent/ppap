import {useEffect, useRef, useState} from 'react'
import {FilePlus, LoaderCircle} from 'lucide-react'
import {readLocalUpload, request, uploadLocalVersion, type FileVersion, type PublicConfig, type QuotaEstimate, type Round, type RuleDetail, type RulesList} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Label} from '../components/ui/label'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {FileName} from '../journey/file-name'
import {writeAuditAddress} from './routes'

const ruleItems = (detail?: RuleDetail) => detail?.body.kind === 'engine' ? detail.body.rules : []

type Upload = {id: string; name: string; state: 'uploading' | 'ready' | 'unknown'; file: FileVersion | null; role: 'target' | 'reference'; ruleId: string; error: string}

/** Explicit file/rule binding uses the existing upload, draft and acceptance path. */
export function DeepPrepare() {
  const [rules, setRules] = useState<RulesList['rules']>([]), [details, setDetails] = useState<Record<string, RuleDetail>>({})
  const [uploads, setUploads] = useState<Upload[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [quota, setQuota] = useState<QuotaEstimate | null>(null), [quotaError, setQuotaError] = useState('')
  const [chargeEnabled, setChargeEnabled] = useState<boolean | null>(null)
  const [draftId] = useState(() => new URL(location.href).searchParams.get('deepDraft') || crypto.randomUUID())
  const [acceptId, setAcceptId] = useState(() => new URL(location.href).searchParams.get('deepAccept'))
  const picker = useRef<HTMLInputElement>(null)
  const targets = uploads.filter(upload => upload.role === 'target')
  const locked = busy || Boolean(acceptId)
  const targetCount = targets.length
  useEffect(() => {
    const controller = new AbortController()
    void request<RulesList>('/rules', 'GET', undefined, controller.signal).then(result => setRules(result.rules.filter(rule => rule.kind === 'engine'))).catch(cause => {if (!controller.signal.aborted) setError(String(cause.message || '规则目录暂不可读。'))})
    void request<PublicConfig>('/public-config', 'GET', undefined, controller.signal).then(config => setChargeEnabled(config.usage_charge_enabled)).catch(cause => {if (!controller.signal.aborted) setQuotaError(cause instanceof Error ? cause.message : '用量配置暂不可读。')})
    return () => controller.abort()
  }, [])
  useEffect(() => {
    setQuota(null); setQuotaError('')
    if (!targetCount || chargeEnabled === null) return
    if (!chargeEnabled) {setQuota({target_count: targetCount, usage_charge_enabled: false, accept_allowed: true}); return}
    const controller = new AbortController()
    void request<QuotaEstimate>(`/integrations/feishu/usage/quota?target_count=${targetCount}`, 'GET', undefined, controller.signal).then(setQuota).catch(cause => {if (!controller.signal.aborted) setQuotaError(cause instanceof Error ? cause.message : '当前用量条件暂不可读。')})
    return () => controller.abort()
  }, [targetCount, chargeEnabled])
  function update(id: string, values: Partial<Upload>) {setUploads(current => current.map(upload => upload.id === id ? {...upload, ...values} : upload))}
  async function addFiles(files: FileList | null) {
    if (!files || locked) return
    const pending = Array.from(files).map(file => ({id: crypto.randomUUID(), file}))
    setUploads(current => [...current, ...pending.map(({id, file}) => ({id, name: file.name, state: 'uploading' as const, file: null, role: 'reference' as const, ruleId: '', error: ''}))])
    for (const {id, file} of pending) {
      try {
        const result = await uploadLocalVersion(file, id)
        if (result.state !== 'ready' || !result.file) throw new Error(result.reason || '上传结果待核对。')
        update(id, {state: 'ready', file: result.file})
      } catch (cause) {update(id, {state: 'unknown', error: cause instanceof Error ? cause.message : '上传结果尚未确定。'})}
    }
  }
  async function readUpload(upload: Upload) {
    try {
      const result = await readLocalUpload(upload.id)
      if (result.state === 'ready' && result.file) update(upload.id, {state: 'ready', file: result.file, error: ''})
      else update(upload.id, {error: result.reason || '原上传尚未就绪，请稍后读取。'})
    } catch (cause) {update(upload.id, {error: cause instanceof Error ? cause.message : '上传结果尚未确定。'})}
  }
  async function selectRule(upload: Upload, id: string) {
    update(upload.id, {ruleId: id})
    if (!id || details[id]) return
    try {const rule = await request<RuleDetail>(`/rules/${encodeURIComponent(id)}`); setDetails(current => ({...current, [id]: rule}))}
    catch (cause) {setError(cause instanceof Error ? cause.message : '规则原文暂不可读。')}
  }
  function openRound(round: Round) {
    if (round.source_draft_id !== draftId || round.analysis_mode !== 'deep') throw new Error('受理回执与本次资料不一致，请核对原请求。')
    location.assign(`/audit?round=${encodeURIComponent(round.id)}`)
  }
  async function recover() {
    setBusy(true); setError('')
    try {
      const round = await request<Round | null>(`/drafts/${encodeURIComponent(draftId)}/round`)
      if (round) openRound(round)
      else setError('尚未读到已受理的分析。请稍后读取原请求，或查看已保存草稿；不要再次发起分析。')
    } catch (cause) {setError(cause instanceof Error ? cause.message : '原受理结果暂不可读。')}
    finally {setBusy(false)}
  }
  const ready = uploads.length > 0 && uploads.every(upload => upload.state === 'ready') && targetCount > 0 && targets.every(upload => Boolean(upload.ruleId && details[upload.ruleId])) && quota?.target_count === targetCount && quota.accept_allowed === true
  async function start() {
    if (!ready || locked) return
    const items = uploads.map(upload => ({file_version_id: upload.file!.id, role: upload.role, rule_version_id: upload.role === 'target' ? upload.ruleId : null}))
    const key = crypto.randomUUID()
    setBusy(true); setError(''); setAcceptId(key)
    const url = new URL(location.href); url.searchParams.set('deepDraft', draftId); url.searchParams.set('deepAccept', key); writeAuditAddress(url)
    try {
      await request('/drafts', 'POST', {request_id: draftId})
      await request(`/drafts/${encodeURIComponent(draftId)}`, 'PUT', {items, analysis_mode: 'deep'})
      const round = await request<Round>(`/drafts/${encodeURIComponent(draftId)}/accept`, 'POST', {idempotency_key: key})
      const actual = round.inputs.map(item => ({file_version_id: item.file_version_id, role: item.role, rule_version_id: item.rule?.id || null}))
      if (JSON.stringify(actual.sort((a, b) => a.file_version_id.localeCompare(b.file_version_id))) !== JSON.stringify([...items].sort((a, b) => a.file_version_id.localeCompare(b.file_version_id)))) throw new Error('受理内容与本次明确选择不同，请核对原请求。')
      openRound(round)
    } catch (cause) {
      const failure = cause as {status?: number}
      if (failure.status !== undefined && [400, 401, 403, 404, 422].includes(failure.status)) {
        setAcceptId(null)
        const current = new URL(location.href); current.searchParams.delete('deepAccept'); writeAuditAddress(current)
        setError(`${cause instanceof Error ? cause.message : '本次分析未受理。'} 请核对资料、规则及权限。`)
      } else setError(`${cause instanceof Error ? cause.message : '提交结果尚未确定。'} 请读取原请求，不要重复开始分析。`)
    }
    finally {setBusy(false)}
  }
  return <main className="mx-auto max-w-screen-lg space-y-7 px-6 py-8 max-sm:px-4">
    <header className="space-y-3"><Button variant="link" asChild><a href="/workbench">返回工作台</a></Button><h1 className="text-2xl font-semibold">按明确规则开始深度分析</h1><p className="text-sm leading-relaxed text-muted-foreground">添加原始资料，明确哪些文件需要产生判断，并为它们选择已核对的规则。比对资料保留为本次上下文。</p></header>
    {error && <Alert variant="destructive"><AlertTitle>{acceptId ? '原受理结果待核对' : '准备尚未完成'}</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {acceptId ? <section aria-label="读取原分析请求" className="space-y-4"><p>本次资料与规则已进入受理流程。只有实际读到分析后才会显示已开始。</p><Button disabled={busy} onClick={() => void recover()}>{busy && <LoaderCircle className="animate-spin"/>}读取原分析请求</Button><Button variant="link" asChild><a href={`/?case=${encodeURIComponent(draftId)}`}>查看已保存草稿</a></Button></section> : <>
      <section className="space-y-4" aria-label="深度分析资料"><div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">本次资料</h2><Button variant="outline" onClick={() => picker.current?.click()} disabled={locked}><FilePlus/>添加资料</Button></div><input ref={picker} type="file" multiple accept=".xlsx,.xlsm,.xls,.docx,.pdf" className="sr-only" aria-label="选择深度分析原始资料" onChange={event => {void addFiles(event.target.files); event.target.value = ''}}/>
        {!uploads.length && <p className="border-y py-8 text-sm text-muted-foreground">添加待审文件和需要对照的原始资料。</p>}
        {uploads.map(upload => <article key={upload.id} aria-label={`资料 ${upload.name}`} className="space-y-3 border-t py-4"><div className="flex flex-wrap items-center justify-between gap-3"><FileName colored name={upload.name}/><div className="flex items-center gap-2"><span role="status" className="text-xs text-muted-foreground">{upload.state === 'ready' ? '原件已保存' : upload.state === 'uploading' ? '正在上传…' : '上传待核对'}</span><Button variant="ghost" size="sm" disabled={locked} aria-label={`移除 ${upload.name}`} onClick={() => setUploads(current => current.filter(row => row.id !== upload.id))}>移除本次资料</Button></div></div>{upload.error && <p role="alert" className="text-sm">{upload.error}</p>}{upload.state === 'unknown' && <Button variant="outline" size="sm" onClick={() => void readUpload(upload)}>读取原上传</Button>}
          <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor={`deep-role-${upload.id}`}>本次文件用途</Label><NativeSelect id={`deep-role-${upload.id}`} className="w-full" disabled={locked} value={upload.role} onChange={event => update(upload.id, {role: event.target.value as Upload['role']})}><NativeSelectOption value="reference">比对资料</NativeSelectOption><NativeSelectOption value="target">待审核文件</NativeSelectOption></NativeSelect></div>{upload.role === 'target' && <div className="space-y-2"><Label htmlFor={`deep-rule-${upload.id}`}>使用的审核规则</Label><NativeSelect id={`deep-rule-${upload.id}`} className="w-full" disabled={locked} value={upload.ruleId} onChange={event => void selectRule(upload, event.target.value)}><NativeSelectOption value="">选择已核对规则</NativeSelectOption>{rules.map(rule => <NativeSelectOption key={rule.id} value={rule.id}>{rule.title} · {rule.type}</NativeSelectOption>)}</NativeSelect></div>}</div>
          {upload.role === 'target' && details[upload.ruleId]?.body.kind === 'engine' && <details open className="text-sm"><summary className="cursor-pointer font-medium">本文件使用的规则原文</summary><div className="mt-3 space-y-3">{ruleItems(details[upload.ruleId]).map(rule => <p key={rule.id} className="whitespace-pre-wrap border-l-2 pl-3 leading-relaxed">{rule.id} · {rule.rule_to_check}</p>)}</div></details>}
        </article>)}
      </section>
      <p className="text-sm text-muted-foreground">需要导入或核对新的规则时，可进入<a href="/requirements" className="underline underline-offset-4">审核规则</a>。这里使用明确选择的已发布规则，不按文件名猜测。</p>
      <section aria-label="本次分析范围" className="space-y-4 border-t pt-5"><p className="text-sm">开始后将分析 {targetCount} 份待审文件{targets.length > 0 && `：${targets.map(upload => upload.name).join('、')}`}；另外 {uploads.length - targetCount} 份作为比对资料。</p>{quotaError && <p role="alert" className="text-sm">{quotaError}</p>}{quota && <p className="text-sm text-muted-foreground">{quota.message || quota.detailed_message || (quota.usage_charge_enabled === false ? '当前环境未启用业务扣费。' : quota.estimated_points !== undefined ? `本次预估 ${quota.estimated_points} 点，以实际受理规则为准。` : '用量条件已核对。')}</p>}<Button disabled={!ready || locked} onClick={() => void start()}>开始深度分析</Button></section>
    </>}
  </main>
}
