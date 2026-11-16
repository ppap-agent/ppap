// PPAP Audit inside a Feishu cloud document. The panel only chooses, confirms and watches;
// the result is written into the document as its own blocks, and messages, theme and
// permissions come from the document host. It drives the same audit-task flow as the web app.
import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {clauseObservation} from '../audit/result-projection'
import {taskAddress} from '../audit/routes'
import {conclusionLabel} from '../audit/status-labels'
import type {components} from '../generated/openapi'
import {resolveHost, type AddonHost} from './host'
import {OfficeView, type Evidence} from './office'
import {attention, audited, marker, resultMarkdown, statusOf, totals, verdict, verdictLabel, type Task, type TaskFile} from './report'
import '../index.css'

type DocLink = components['schemas']['DocLink']
type FileVersion = components['schemas']['FileVersion']
type Job = components['schemas']['FileValidationJob']
type Quota = components['schemas']['QuotaEstimate']
type Summary = components['schemas']['AuditTaskSummary']
type Primary = {label: string; disabled?: boolean; busy?: boolean; run?: () => void}

class ApiError extends Error {
  constructor(public code: string, public status: number) { super(code) }
}

const messages: Record<string, string> = {
  tenant_not_allowed: '你的企业尚未开通这个入口，请联系管理员。',
  feishu_user_token_missing: '飞书授权已失效，请关闭面板后重新打开。',
  feishu_doc_links_failed: '暂时读不到这篇文档里的文件，请稍后重试。',
  feishu_download_failed: '有文件无法从飞书读取，请确认你有它的查看权限。',
  feishu_export_failed: '有飞书文档无法导出，请稍后重试。',
  unsupported_doc_type: '有文件的类型暂不支持审核。',
  too_large: '有文件超过 200 MB，无法审核。',
  access_denied: '你没有权限查看这项审核。',
  task_revision_conflict: '审核内容刚发生变化，请核对后再确认。',
  round_incomplete: '审核尚未全部完成，暂时不能写入文档。',
  round_has_errors: '审核出现技术错误，不能写入文档。',
  feishu_writeback_failed: '写入文档没有成功，请确认你有这篇文档的编辑权限。',
  wait_timeout: '处理时间较长，可以先关闭面板，稍后回来查看。',
  validation_failed: '有文件无法读取，请检查后重试。',
}
const say = (cause: unknown) => cause instanceof ApiError && messages[cause.code] || (cause instanceof Error && (cause as Error & {reason?: string}).reason ? cause.message : '操作没有完成，请稍后重试。')

const kindLabel: Record<string, string> = {file: '文件', sheet: '电子表格', bitable: '多维表格', doc: '文档', docx: '文档'}
const matchLabel: Record<string, string> = {matched: '已匹配规则', reference: '作为参考资料', unmatched: '没有可用规则', failed: '识别失败'}
const newId = () => crypto.randomUUID()
const tones: Record<string, string> = {
  PASS: 'bg-success/10 text-success',
  NG: 'bg-destructive/10 text-destructive',
  NONE: 'bg-muted text-muted-foreground',
  RUNNING: 'bg-muted text-muted-foreground',
}
const tag = (tone: string) => 'shrink-0 rounded px-2 text-xs leading-5 whitespace-nowrap ' + (tones[tone] ?? 'bg-warning/10 text-warning')
const moving = (task: Task) => task.state === 'matching' || task.state === 'reviewing'

async function readJson(url: string, init: RequestInit = {}) {
  const response = await fetch(url, {credentials: 'include', ...init, headers: {Accept: 'application/json', ...(init.body ? {'Content-Type': 'application/json'} : {})}})
  return {status: response.status, ok: response.ok, body: await response.json().catch(() => ({}))}
}

async function hostLogin(api: string, host: AddonHost) {
  const timeout = new Promise<never>((_, reject) => window.setTimeout(() => reject(Object.assign(new Error('获取飞书登录状态超时，请重新打开面板。'), {reason: 'host-timeout'})), 31_000))
  const code = await Promise.race([host.login(), timeout])
  if (typeof code !== 'string' || !code.trim()) throw Object.assign(new Error('没有取得飞书登录状态，请重新打开面板。'), {reason: 'host-code'})
  const logged = await readJson(`${api}/login/feishu/host`, {method: 'POST', body: JSON.stringify({code: code.trim()})})
  if (!logged.ok) throw new ApiError(String(logged.body.error || 'login_failed'), logged.status)
  return logged.body as components['schemas']['PrincipalPublic']
}

function AddonApp() {
  const [stage, setStage] = useState<{kind: 'loading'; text: string} | {kind: 'error'; text: string} | {kind: 'ready'}>({kind: 'loading', text: '正在连接飞书…'})
  const [name, setName] = useState('')
  const [canEdit, setCanEdit] = useState(true)
  const [task, setTask] = useState<Task | null>(null)
  const [history, setHistory] = useState<Summary[]>([])
  const [choosing, setChoosing] = useState(false)
  const [primary, setPrimary] = useState<Primary>({label: '开始识别', disabled: true})
  const session = useRef<{api: string; host: AddonHost; doc: string} | null>(null)
  const watching = useRef(0)

  const call = useCallback(async <T,>(path: string, init: {method?: string; body?: unknown} = {}): Promise<T> => {
    const current = session.current
    if (!current) throw new ApiError('not_connected', 0)
    const send = () => readJson(current.api + path, {method: init.method || 'GET', body: init.body === undefined ? undefined : JSON.stringify(init.body)})
    let reply = await send()
    if (reply.status === 401) {
      await hostLogin(current.api, current.host)
      reply = await send()
    }
    if (!reply.ok) throw new ApiError(String(reply.body.error || 'request_failed'), reply.status)
    return reply.body as T
  }, [])

  const notify = useCallback((type: 'success' | 'error' | 'info' | 'warning', message: string) => session.current?.host.notify?.(type, message), [])
  const open = useCallback((path: string) => {
    const url = new URL('/enter-current?return_to=' + encodeURIComponent(new URL(path, location.origin).href), location.origin).href
    if (session.current?.host.open) session.current.host.open(url)
    else window.open(url, '_blank', 'noopener')
  }, [])

  const watch = useCallback(async (taskId: string) => {
    const ticket = ++watching.current
    const started = Date.now()
    let delay = 1500
    for (;;) {
      const value = await call<Task>(`/audit-tasks/${taskId}`)
      if (watching.current !== ticket) return
      setTask(value)
      if (!moving(value)) return
      if (Date.now() - started > 30 * 60 * 1000) throw new ApiError('wait_timeout', 0)
      await new Promise(resolve => window.setTimeout(resolve, delay))
      delay = Math.min(delay * 1.5, 10_000)
    }
  }, [call])

  const refresh = useCallback(async () => {
    const listed = await call<{tasks: Summary[]}>(`/integrations/feishu/docs/audits?file_token=${encodeURIComponent(session.current!.doc)}`)
    setHistory(listed.tasks)
    return listed.tasks
  }, [call])

  const boot = useCallback(async () => {
    setStage({kind: 'loading', text: '正在连接飞书…'})
    try {
      const runtime = (await readJson('/runtime-config.json')).body as {api_url: string}
      const api = ((await readJson(`${runtime.api_url}/public-config`)).body as components['schemas']['PublicConfig']).api_url
      const host = await resolveHost()
      host.watchDarkMode?.(dark => { document.documentElement.classList.toggle('dark', dark) })
      const me = await readJson(`${api}/me`)
      const principal = me.ok && me.body.identity ? me.body as components['schemas']['PrincipalPublic'] : await hostLogin(api, host)
      const doc = await host.documentToken()
      if (!doc) throw Object.assign(new Error('没有读到当前文档，请重新打开面板。'), {reason: 'no-document'})
      session.current = {api, host, doc}
      setName(principal.display_name || '')
      void host.canEdit?.().then(setCanEdit, () => undefined)
      setStage({kind: 'loading', text: '正在读取这篇文档的审核记录…'})
      const audits = await refresh()
      setStage({kind: 'ready'})
      if (audits[0]) await watch(audits[0].id)
    } catch (cause) {
      setStage({kind: 'error', text: say(cause)})
    }
  }, [refresh, watch])
  useEffect(() => { void boot() }, [boot])

  const started = useCallback(async (taskId: string) => {
    setChoosing(false)
    setTask(null)
    await refresh().catch(() => undefined)
    await watch(taskId).catch(cause => notify('error', say(cause)))
  }, [notify, refresh, watch])

  const showIntake = stage.kind === 'ready' && (choosing || !task)
  const latest = history.find(row => row.round_id)
  return (
    <div className="flex h-dvh flex-col bg-background text-sm text-foreground">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-2.5">
        <strong>PPAP Audit</strong>
        {name && <span className="text-muted-foreground min-w-0 truncate" title={name} data-testid="addon-user">{name}</span>}
      </header>
      <main className="min-h-0 flex-1 overflow-auto px-4 py-3">
        {stage.kind === 'loading' && <p role="status" className="text-muted-foreground" data-testid="addon-status">{stage.text}</p>}
        {stage.kind === 'error' && <div role="alert" className="mb-2 rounded-md bg-warning/10 px-3 py-2 text-warning" data-testid="addon-error"><p>{stage.text}</p></div>}
        {showIntake && <Intake call={call} doc={session.current!.doc} host={session.current!.host} predecessor={choosing ? latest?.round_id ?? null : null} onStarted={started} onCancel={task ? () => setChoosing(false) : undefined} setPrimary={setPrimary} notify={notify} />}
        {stage.kind === 'ready' && !showIntake && task?.state === 'matching' && <Waiting text="正在识别文件类型和规则…" setPrimary={setPrimary} />}
        {stage.kind === 'ready' && !showIntake && task?.state === 'pending_confirm' && <Confirm task={task} call={call} open={open} onReselect={() => setChoosing(true)} onConfirmed={() => void watch(task.id).catch(cause => notify('error', say(cause)))} setPrimary={setPrimary} notify={notify} />}
        {stage.kind === 'ready' && !showIntake && task?.state === 'reviewing' && <Running task={task} setPrimary={setPrimary} />}
        {stage.kind === 'ready' && !showIntake && task?.state === 'completed' && <Result task={task} call={call} host={session.current!.host} canEdit={canEdit} open={open} onReaudit={() => setChoosing(true)} setPrimary={setPrimary} notify={notify} />}
      </main>
      <footer className="flex shrink-0 items-center justify-between gap-3 border-t px-4 py-2.5">
        <a data-testid="addon-history" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" href="/files" target="_blank" rel="noreferrer" onClick={event => { if (session.current) { event.preventDefault(); open('/files') } }}>全部审核记录</a>
        {stage.kind === 'error'
          ? <button type="button" className="rounded-md bg-primary px-4 py-1.5 whitespace-nowrap text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50" data-testid="addon-submit" disabled={!window.__PPAP_DOCS_ADDON_HOST__ && window.self === window.top} onClick={() => void boot()}>重试</button>
          : <button type="button" className="rounded-md bg-primary px-4 py-1.5 whitespace-nowrap text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50" data-testid="addon-submit" disabled={stage.kind !== 'ready' || primary.disabled || primary.busy || !primary.run} onClick={primary.run}>{primary.busy ? '请稍候…' : primary.label}</button>}
      </footer>
    </div>
  )
}

type Shared = {setPrimary: (value: Primary) => void}
type Calls = {call: <T>(path: string, init?: {method?: string; body?: unknown}) => Promise<T>; notify: (type: 'success' | 'error' | 'info' | 'warning', message: string) => void}

function Waiting({text, setPrimary}: {text: string} & Shared) {
  useEffect(() => setPrimary({label: '识别中', disabled: true}), [setPrimary])
  return <p role="status" className="text-muted-foreground">{text}</p>
}

function Intake({call, doc, host, predecessor, onStarted, onCancel, setPrimary, notify}: {doc: string; host: AddonHost; predecessor: string | null; onStarted: (taskId: string) => Promise<void>; onCancel?: () => void} & Shared & Calls) {
  const [links, setLinks] = useState<DocLink[] | null>(null)
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [customer, setCustomer] = useState<string | null | undefined>(undefined)
  const [progress, setProgress] = useState('')
  const [skipped, setSkipped] = useState<string[]>([])
  const [failure, setFailure] = useState('')
  const seen = useRef<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const listed = (await call<{doc_links: DocLink[]}>(`/integrations/feishu/docs/links?file_token=${encodeURIComponent(doc)}`)).doc_links.filter(item => kindLabel[String(item.doc_type || item.type)])
      setLinks(listed)
      // Newly added files are selected; a person's earlier choices stay as they were.
      setChosen(previous => { const next = new Set(previous); for (const item of listed) if (!seen.current.has(item.token)) next.add(item.token); return next })
      for (const item of listed) seen.current.add(item.token)
      setFailure('')
    } catch (cause) { setFailure(say(cause)) }
  }, [call, doc])
  useEffect(() => {
    void load()
    void call<{customer_preference: string | null}>('/audit-customers/default').then(value => setCustomer(value.customer_preference), () => setCustomer(null))
    return host.onDocumentChange?.(() => void load())
  }, [call, host, load])

  const start = useCallback(async () => {
    if (!links) return
    setFailure(''); setSkipped([])
    try {
      const picked = links.filter(item => chosen.has(item.token))
      const files: {file_version_id: string; validation_id: string; selection: 'selected'; removal_reason: null}[] = []
      const left: string[] = []
      for (const [index, item] of picked.entries()) {
        setProgress(`正在读取文件 ${index + 1}/${picked.length}…`)
        const version = await call<FileVersion>('/integrations/feishu/files/import', {method: 'POST', body: {file_token: item.token, doc_type: item.doc_type || item.type, request_id: newId()}})
        const job = await call<Job>('/file-validations', {method: 'POST', body: {request_id: newId(), source_file_id: version.file_id, source_file_version_id: version.id}})
        let checked = job
        for (let delay = 1000; ['queued', 'leased'].includes(checked.status); delay = Math.min(delay * 1.5, 8000)) {
          await new Promise(resolve => window.setTimeout(resolve, delay))
          checked = await call<Job>(`/file-validations/${job.id}`)
        }
        const result = checked.result as {state?: string; reasons?: string[]} | null
        if (checked.status !== 'succeeded') throw new ApiError('validation_failed', 0)
        if (result?.state !== 'valid') { left.push(`${item.name}（${(result?.reasons ?? []).join('；') || '无法审核'}）`); continue }
        files.push({file_version_id: version.id, validation_id: checked.id, selection: 'selected', removal_reason: null})
      }
      setSkipped(left)
      if (!files.length) return
      setProgress('正在识别文件类型和规则…')
      const taskId = newId()
      await call('/audit-tasks', {method: 'POST', body: {request_id: taskId, customer_preference: customer ?? null, files, ...(predecessor ? {predecessor_id: predecessor} : {})}})
      await call(`/integrations/feishu/tasks/${taskId}/document`, {method: 'PUT', body: {file_token: doc}})
      await onStarted(taskId)
    } catch (cause) {
      setFailure(say(cause))
      notify('error', say(cause))
    } finally { setProgress('') }
  }, [call, chosen, customer, doc, links, notify, onStarted, predecessor])

  const count = links?.filter(item => chosen.has(item.token)).length ?? 0
  useEffect(() => setPrimary({label: `开始识别（${count} 份）`, disabled: !count || customer === undefined, busy: Boolean(progress), run: () => void start()}), [count, customer, progress, setPrimary, start])

  return (
    <section aria-label="选择要审核的文件" data-testid="addon-documents">
      <h2 className="mb-2 text-base font-semibold">{predecessor ? '选择再次审核的文件' : '选择要审核的文件'}</h2>
      {predecessor && <p className="text-muted-foreground">上一次的审核结果会保留。</p>}
      {progress && <p role="status" className="text-muted-foreground">{progress}</p>}
      {failure && <p role="alert" className="mb-2 rounded-md bg-warning/10 px-3 py-2 text-warning">{failure}</p>}
      {skipped.length > 0 && <p role="alert" className="mb-2 rounded-md bg-warning/10 px-3 py-2 text-warning">以下文件无法审核，已跳过：{skipped.join('、')}</p>}
      {links === null && !failure && <p role="status" className="text-muted-foreground">正在读取这篇文档里的文件…</p>}
      {links?.length === 0 && <p className="text-muted-foreground">这篇文档里还没有可审核的文件。把 PPAP 文件插入或链接到文档后，这里会自动出现。</p>}
      {links && links.length > 0 && (
        <ul className="mb-2 divide-y border-b [&>li]:flex [&>li]:items-center [&>li]:justify-between [&>li]:gap-3 [&>li]:py-2 [&_label]:flex [&_label]:w-full [&_label]:min-w-0 [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2">
          {links.map(item => (
            <li key={item.token}>
              <label title={item.name}>
                <input type="checkbox" checked={chosen.has(item.token)} disabled={Boolean(progress)} onChange={event => setChosen(previous => { const next = new Set(previous); if (event.target.checked) next.add(item.token); else next.delete(item.token); return next })} />
                <span className="min-w-0 truncate">{item.name}</span>
                <span className="text-muted-foreground text-xs">{kindLabel[String(item.doc_type || item.type)]}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {customer !== undefined && <p className="text-muted-foreground text-xs">审核客户：{customer ?? '自动识别'}（企业默认）</p>}
      {onCancel && <button type="button" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" disabled={Boolean(progress)} onClick={onCancel}>返回上一次审核</button>}
    </section>
  )
}

function Confirm({task, call, open, onReselect, onConfirmed, setPrimary, notify}: {task: Task; open: (path: string) => void; onReselect: () => void; onConfirmed: () => void} & Shared & Calls) {
  const [busy, setBusy] = useState(false)
  const [quota, setQuota] = useState<Quota | 'unavailable' | null>(null)
  const matched = task.files.filter(row => row.selection === 'selected' && row.match?.state === 'matched').length
  useEffect(() => {
    call<Quota>(`/integrations/feishu/usage/quota?target_count=${matched}&deep_mode=${task.deep_mode ? 'true' : 'false'}`).then(setQuota, () => setQuota('unavailable'))
  }, [call, matched, task.deep_mode])
  const blocked = quota !== null && quota !== 'unavailable' && !quota.unavailable && quota.accept_allowed === false
  const refusal = quota !== null && quota !== 'unavailable' ? quota.accept_reason || quota.detailed_message || quota.message || '' : ''
  const confirm = useCallback(async () => {
    setBusy(true)
    try {
      await call(`/audit-tasks/${task.id}/confirm`, {method: 'POST', body: {request_id: newId(), expected_revision: task.input_revision, expected_confirmation_basis_digest: task.confirmation_basis_digest}})
      onConfirmed()
    } catch (cause) { notify('error', say(cause)) } finally { setBusy(false) }
  }, [call, notify, onConfirmed, task.confirmation_basis_digest, task.id, task.input_revision])
  const ready = Boolean(task.confirmation_basis_digest) && matched > 0
  useEffect(() => setPrimary(ready ? {label: '确认并开始审核', disabled: blocked, busy, run: () => void confirm()} : {label: '在 PPAP 中核对', run: () => open(taskAddress(task.id))}), [blocked, busy, confirm, open, ready, setPrimary, task.id])

  return (
    <section aria-label="核对后开始审核">
      <h2 className="mb-2 text-base font-semibold">核对后开始审核</h2>
      <ul className="mb-2 divide-y border-b [&>li]:flex [&>li]:items-center [&>li]:justify-between [&>li]:gap-3 [&>li]:py-2 [&_label]:flex [&_label]:w-full [&_label]:min-w-0 [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2">
        {task.files.map(row => (
          <li key={row.file.id}>
            <span className="min-w-0 truncate" title={row.file.original_filename}>{row.file.original_filename}</span>
            <span className="text-muted-foreground text-xs">{row.selection === 'removed' ? `不审核：${row.removal_reason ?? ''}` : row.match?.rule ? row.match.rule.title : matchLabel[row.match?.state ?? ''] ?? '识别中'}</span>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground" data-testid="addon-quota">
        预计用量：{task.estimated_points == null ? '无法预估' : `${task.estimated_points} 点`}
        {quota && quota !== 'unavailable' && quota.remaining_trial_ai_points != null && ` · 试用剩余 ${quota.remaining_trial_ai_points} 点`}
      </p>
      {blocked && <p role="alert" className="mb-2 rounded-md bg-warning/10 px-3 py-2 text-warning">{refusal || '当前额度不足，不能开始审核。'}</p>}
      {!ready && <p className="text-muted-foreground">这些文件还需要在 PPAP 中核对规则后才能开始审核。</p>}
      <button type="button" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" disabled={busy} onClick={onReselect}>重新选择文件</button>
    </section>
  )
}

function Running({task, setPrimary}: {task: Task} & Shared) {
  useEffect(() => setPrimary({label: '审核中', disabled: true}), [setPrimary])
  const rows = task.files.filter(row => row.selection === 'selected')
  return (
    <section aria-label="审核进行中">
      <h2 className="mb-2 text-base font-semibold">审核进行中</h2>
      <p role="status" className="text-muted-foreground">{task.progress ? `已完成 ${task.progress.completed}/${task.progress.total} 份文件。` : '正在审核…'}可以关闭面板，审核会继续，回来时在这里查看结果。</p>
      <ul className="mb-2 divide-y border-b [&>li]:flex [&>li]:items-center [&>li]:justify-between [&>li]:gap-3 [&>li]:py-2 [&_label]:flex [&_label]:w-full [&_label]:min-w-0 [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2">
        {rows.map(row => (
          <li key={row.file.id}>
            <span className="min-w-0 truncate" title={row.file.original_filename}>{row.file.original_filename}</span>
            <span className="text-muted-foreground text-xs">{!row.audit ? matchLabel[row.match?.state ?? 'reference'] ?? '作为参考资料' : row.audit.execution === 'completed' ? '已审完' : row.audit.execution === 'review_failed' ? '审核异常' : '审核中'}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Result({task, call, host, canEdit, open, onReaudit, setPrimary, notify}: {task: Task; host: AddonHost; canEdit: boolean; open: (path: string) => void; onReaudit: () => void} & Shared & Calls) {
  const overall = verdict(task)
  const sum = totals(task)
  const issues = useMemo(() => attention(task), [task])
  const [all, setAll] = useState(false)
  const [written, setWritten] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [viewing, setViewing] = useState<Evidence | null>(null)
  const writable = ['PASS', 'NG', 'UNKNOWN'].includes(overall) && Boolean(task.round_id)
  const mark = marker(task)

  // Read the document back: it is the only record of whether this result is already in it.
  const inDocument = useCallback(async () => {
    const text = await host.documentText?.()
    return text === undefined ? null : text.includes(mark) || text.includes(`/tasks/${task.id}`)
  }, [host, mark, task.id])
  useEffect(() => { void inDocument().then(setWritten, () => setWritten(null)) }, [inDocument])

  const write = useCallback(async () => {
    setBusy(true)
    try {
      if (await inDocument().catch(() => null)) { setWritten(true); await host.reveal?.(mark); return }
      const link = new URL('/enter-current?return_to=' + encodeURIComponent(new URL(taskAddress(task.id), location.origin).href), location.origin).href
      let done = false
      try { done = await host.insertMarkdown?.(resultMarkdown(task, link)) ?? false } catch { done = Boolean(await inDocument().catch(() => false)) }
      // A host that cannot insert blocks itself gets the same result through the server.
      if (!done && !(await inDocument().catch(() => false))) await call(`/integrations/feishu/rounds/${task.round_id}/writeback`, {method: 'POST', body: {}})
      setWritten(true)
      notify('success', '审核结果已写入文档')
      await host.reveal?.(mark).catch(() => false)
    } catch (cause) { notify('error', say(cause)) } finally { setBusy(false) }
  }, [call, host, inDocument, mark, notify, task])

  useEffect(() => {
    if (!writable) setPrimary({label: '再次审核', run: onReaudit})
    else if (written) setPrimary({label: '在文档中查看结果', busy, run: () => void host.reveal?.(mark).then(found => { if (!found) notify('info', '结果已在文档中，请在正文里查找“PPAP 审核结果”。') })})
    else setPrimary({label: '写入文档', disabled: !canEdit, busy, run: () => void write()})
  }, [busy, canEdit, host, mark, notify, onReaudit, setPrimary, writable, write, written])

  const shown = all ? issues : issues.slice(0, 8)
  const fileOf = (id: string): TaskFile | undefined => task.files.find(row => row.file.id === id)
  if (viewing) return <OfficeView evidence={viewing} roundId={task.round_id!} call={call} onClose={() => setViewing(null)} onOpenWeb={() => open(taskAddress(task.id))} />
  return (
    <section aria-label="审核结果" data-testid="addon-round">
      <h2 className="mb-2 text-base font-semibold">审核结果</h2>
      <p className="mb-2 flex flex-wrap items-center gap-2.5"><span className={tag(overall)}>{verdictLabel[overall]}</span>{audited(task).length > 0 && <span>不符合 <strong>{sum.ng}</strong> 项 · 尚未确定 <strong>{sum.unsure}</strong> 项</span>}</p>
      {overall === 'NONE' && <p className="text-muted-foreground">没有可用的审核规则，这一次没有审核，也没有扣点。</p>}
      {overall === 'ERROR' && <p role="alert" className="mb-2 rounded-md bg-warning/10 px-3 py-2 text-warning">审核出现技术错误，结果不完整，不能写入文档。</p>}
      {writable && !canEdit && !written && <p className="text-muted-foreground">你没有这篇文档的编辑权限，不能把结果写入文档。</p>}
      <ul className="mb-2 divide-y border-b [&>li]:flex [&>li]:items-center [&>li]:justify-between [&>li]:gap-3 [&>li]:py-2 [&_label]:flex [&_label]:w-full [&_label]:min-w-0 [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2">
        {audited(task).map(row => (
          <li key={row.file.id}>
            <span className="min-w-0 truncate" title={row.file.original_filename}>{row.file.original_filename}</span>
            <span className={tag(row.audit!.execution === 'review_failed' ? 'ERROR' : row.audit!.conclusion ?? 'UNKNOWN')}>{row.audit!.execution === 'review_failed' ? '审核异常' : conclusionLabel[row.audit!.conclusion ?? ''] ?? '尚有未确定项'}</span>
          </li>
        ))}
      </ul>
      {issues.length > 0 && (
        <>
          <h3 className="mt-4 mb-1.5 text-xs font-medium text-muted-foreground">待处理项</h3>
          <ul className="mb-2" data-testid="addon-findings">
            {shown.map(({row, check}) => {
              const source = check.evidence.find(item => fileOf(item.file_version_id))
              const file = source ? fileOf(source.file_version_id)!.file : row.file
              return (
                <li key={row.file.id + check.rule_item.id}>
                  <button type="button" className="flex w-full flex-col gap-0.5 border-b py-2 text-left hover:bg-muted" title="对照原件查看" onClick={() => setViewing({file, locator: source?.locator ?? null, excerpt: source?.excerpt ?? '', clause: check.rule_item.rule_to_check})}>
                    <span className="flex min-w-0 items-center gap-2"><span className={tag(check.finding?.status === 'NG' ? 'NG' : 'UNKNOWN')}>{statusOf(check)}</span><span className="text-muted-foreground text-xs min-w-0 truncate" title={row.file.original_filename}>{row.file.original_filename}</span></span>
                    <span className="line-clamp-3">{check.rule_item.rule_to_check}</span>
                    <span className="text-muted-foreground text-xs">{clauseObservation(check)}</span>
                  </button>
                </li>
              )
            })}
          </ul>
          {issues.length > 8 && <button type="button" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" aria-expanded={all} onClick={() => setAll(!all)}>{all ? '收起' : `展开其余 ${issues.length - 8} 项`}</button>}
        </>
      )}
      <div className="mt-3 flex flex-wrap gap-4">
        <button type="button" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" data-testid="addon-open-round" onClick={() => open(taskAddress(task.id))}>在 PPAP 中复核</button>
        {writable && <button type="button" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" onClick={onReaudit}>补件后再次审核</button>}
      </div>
    </section>
  )
}

createRoot(document.getElementById('addon-root')!).render(<AddonApp />)
