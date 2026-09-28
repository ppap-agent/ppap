import {useCallback, useEffect, useMemo, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Checkbox} from '@/components/ui/checkbox'
import {Label} from '@/components/ui/label'
import {NativeSelect} from '@/components/ui/native-select'
import {Separator} from '@/components/ui/separator'
import {LoaderCircle, FileSpreadsheet, FileText} from 'lucide-react'
import '../index.css'
import type {
  DocLink,
  DocLinksResponse,
  Draft,
  FileVersion,
  Material,
  PrincipalPublic,
  PublicConfig,
  QuotaEstimate,
  Round,
  Rule,
  RulesList,
  WritebackResponse,
} from '../api'

type HostJson = Record<string, any>
type RuntimeConfig = {api_url: string}
type ApiFailure = {error?: string}
type AddonHost = {login: () => Promise<unknown>; documentToken: () => Promise<string | undefined>}
type AddonItem = Partial<Material> & Partial<DocLink> & {
  token: string
  name: string
  role: Material['role']
  rule_version_id: string | null
}
type Quota = QuotaEstimate | null

const EXCEL_EXT = /\.(xlsx|xls|xlsm)$/i
const HOST_TIMEOUT_MS = 31_000

async function readJson<T = HostJson>(url: string, options: RequestInit = {}) {
  const response = await fetch(url, {credentials: 'include', headers: {Accept: 'application/json'}, ...options})
  const body = await response.json().catch(() => ({}))
  return {ok: response.ok, status: response.status, body: body as T}
}

function hostUnavailableError() {
  const error = new Error('当前环境不支持 docs-addon 登录')
  ;(error as Error & {reason?: string}).reason = 'host-unavailable'
  return error
}

async function resolveHost(): Promise<AddonHost> {
  if (window.__PPAP_DOCS_ADDON_HOST__) return window.__PPAP_DOCS_ADDON_HOST__
  let inHostFrame = false
  try { inHostFrame = window.self !== window.top } catch { inHostFrame = true }
  if (!inHostFrame) throw hostUnavailableError()
  let Client: (new () => {initAPI: () => HostJson}) | null = window.BlockitClient || null
  if (!Client) {
    try {
      const loadModule = new Function('path', 'return import(path)') as (path: string) => Promise<HostJson>
      const mod = await loadModule('/block-docs-addon-api.js')
      Client = mod.BlockitClient || mod.default?.BlockitClient
    } catch { Client = null }
  }
  if (typeof Client !== 'function') throw hostUnavailableError()
  const api = new Client().initAPI()
  return {login: () => api.Service.User.login(), documentToken: async () => (await api.getActiveDocumentRef())?.docToken}
}

async function waitForHostLogin(host: AddonHost) {
  if (typeof host.login !== 'function') throw hostUnavailableError()
  const timeout = new Promise<never>((_, reject) => window.setTimeout(() => {
    const error = new Error('获取飞书登录态超时，请重试')
    ;(error as Error & {reason?: string}).reason = 'host-timeout'
    reject(error)
  }, HOST_TIMEOUT_MS))
  const code = await Promise.race([host.login(), timeout])
  const value = typeof code === 'string' ? code.trim() : ''
  if (!value) throw new Error('未获取到飞书授权码，请重试')
  return value
}

async function loginWithHost(api: string, host: AddonHost) {
  const code = await waitForHostLogin(host)
  const logged = await readJson<PrincipalPublic>(`${api}/login/feishu/host`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})})
  if (!logged.ok) {
    if (logged.status === 403 && (logged.body as ApiFailure).error === 'tenant_not_allowed') throw new Error('无法使用此入口。')
    throw new Error('飞书登录失败，请重试')
  }
  return logged.body
}

async function existingSession(api: string) {
  const me = await readJson<PrincipalPublic>(`${api}/me`)
  return me.ok && me.body.identity ? me.body : null
}

async function ensureSession(api: string, host: AddonHost) {
  return (await existingSession(api)) || loginWithHost(api, host)
}

async function authorizedJson<T = HostJson>(api: string, host: AddonHost, url: string, options: RequestInit = {}) {
  let result = await readJson<T>(url, options)
  if (result.status === 401) {
    await loginWithHost(api, host)
    result = await readJson<T>(url, options)
  }
  return result
}

function isExcelFile(item: DocLink) {
  const type = String(item.doc_type || item.type || '').toLowerCase()
  return type === 'file' && EXCEL_EXT.test(String(item.name || item.url || ''))
}

function splitDocuments(items: DocLink[]) {
  const selectable: DocLink[] = []
  const cloud: DocLink[] = []
  const nonExcel: DocLink[] = []
  for (const item of items || []) {
    const type = String(item.doc_type || item.type || '').toLowerCase()
    if (type !== 'file') cloud.push(item)
    else if (isExcelFile(item)) selectable.push(item)
    else nonExcel.push(item)
  }
  return {selectable, cloud, nonExcel}
}

function writebackTarget(fileToken: string) { return fileToken ? {provider: 'feishu', object_kind: 'docx', object_ref: fileToken} : null }
function quotaBlocksStart(quota: Quota) { return Boolean(quota && !quota.unavailable && quota.accept_allowed === false) }
function quotaLabel(quota: Quota) {
  if (!quota) return ''
  if (quota.unavailable) return '无法读取 Partner AI'
  const parts = []
  if (quota.estimated_points != null) parts.push(`预计 ${quota.estimated_points} 点`)
  if (quota.remaining_trial_ai_points != null) parts.push(`试用剩余 ${quota.remaining_trial_ai_points} 点`)
  parts.push(quota.quota_sufficient ? 'Partner AI 可用' : (quota.detailed_message || quota.message || 'Partner AI 不可用'))
  if (quota.accept_allowed === false && quota.accept_reason) parts.push(quota.accept_reason)
  return parts.filter(Boolean).join(' · ')
}

function subjectOf(principal: PrincipalPublic) { return principal?.identity?.subject || 'anonymous' }
function fileIcon(name: string) { return EXCEL_EXT.test(name) ? FileSpreadsheet : FileText }

declare global {
  interface Window {
    __PPAP_DOCS_ADDON_HOST__?: AddonHost
    BlockitClient?: new () => {initAPI: () => HostJson}
  }
}

export default function AddonApp() {
  const query = useMemo(() => new URLSearchParams(window.location.search), [])
  const [api, setApi] = useState('')
  const [host, setHost] = useState<AddonHost | null>(null)
  const [fileToken, setFileToken] = useState('')
  const [user, setUser] = useState('anonymous')
  const [status, setStatus] = useState('正在建立飞书登录态…')
  const [error, setError] = useState('')
  const [quota, setQuota] = useState<Quota>(null)
  const [documents, setDocuments] = useState<AddonItem[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [abnormal, setAbnormal] = useState<string[]>([])
  const [round, setRound] = useState<Round | null>(null)
  const [draftId, setDraftId] = useState(query.get('draft') || '')
  const [roundId, setRoundId] = useState(query.get('round') || '')
  const [draftAccepted, setDraftAccepted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [writebackResult, setWritebackResult] = useState('')

  const request = useCallback(async <T,>(path: string, body?: Record<string, unknown>, method?: string): Promise<T> => {
    if (!api || !host) throw new Error('尚未建立飞书连接')
    const verb = method || (body === undefined ? 'GET' : 'POST')
    const result = await authorizedJson<T>(api, host, `${api}${path}`, verb === 'GET' ? {} : {method: verb, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
    if (!result.ok) throw new Error((result.body as T & ApiFailure).error || '操作失败，请重试')
    return result.body
  }, [api, host])

  const remember = useCallback((nextDraft = '', nextRound = '') => {
    const url = new URL(window.location.href)
    if (nextDraft) url.searchParams.set('draft', nextDraft)
    if (nextRound) url.searchParams.set('round', nextRound)
    window.history.replaceState({}, '', url)
  }, [])

  const targets = useMemo(() => documents.filter(item => selected.has(item.token) && item.role === 'target'), [documents, selected])
  const refreshQuota = useCallback(async () => {
    if (!api || !host || !documents.length) return
    const result = await authorizedJson<QuotaEstimate>(api, host, `${api}/integrations/feishu/usage/quota?target_count=${targets.length}`)
    setQuota(result.ok ? result.body : {unavailable: true})
  }, [api, host, documents.length, targets.length])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const runtime = (await readJson<RuntimeConfig>('/runtime-config.json')).body
        const config = (await readJson<PublicConfig>(`${runtime.api_url}/public-config`)).body
        const nextApi = config.api_url
        const nextHost = await resolveHost()
        const principal = await ensureSession(nextApi, nextHost)
        const fileToken = await nextHost.documentToken()
        if (!fileToken) throw new Error('缺少 fileToken，无法加载关联文件。')
        const nextRules = (await authorizedJson<RulesList>(nextApi, nextHost, `${nextApi}/rules`)).body.rules
        let nextItems: AddonItem[] = []
        let nextDraftAccepted = false
        if (query.get('draft')) {
          const draft = (await authorizedJson<Draft>(nextApi, nextHost, `${nextApi}/drafts/${encodeURIComponent(query.get('draft')!)}`)).body
          nextDraftAccepted = draft.status === 'accepted'
          setDraftAccepted(nextDraftAccepted)
          nextItems = (draft.items || []).map(item => ({...item, token: item.file_version_id, name: item.original_filename}))
        } else if (!query.get('round')) {
          const links = (await authorizedJson<DocLinksResponse>(nextApi, nextHost, `${nextApi}/integrations/feishu/docs/links?file_token=${encodeURIComponent(fileToken)}`)).body
          const split = splitDocuments(links.doc_links || [])
          nextItems = split.selectable.map(item => ({...item, role: 'target', rule_version_id: null, token: item.token, name: item.name || item.token}))
          setAbnormal([...split.cloud.map(item => `飞书云文档：${item.name || item.token}`), ...split.nonExcel.map(item => `非 Excel 文件：${item.name || item.token}`)])
        }
        if (cancelled) return
        setApi(nextApi); setHost(nextHost); setFileToken(fileToken); setUser(subjectOf(principal)); setRules(nextRules); setDocuments(nextItems); setSelected(new Set(nextItems.map(item => item.token)))
        setStatus('为每个待审文件选择规则')
        if (!query.get('round') && nextItems.length && !query.get('draft')) {
          const draft = await authorizedJson<Draft>(nextApi, nextHost, `${nextApi}/drafts`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
          if (draft.ok) { setDraftId(draft.body.id); remember(draft.body.id, '') }
        }
        if (query.get('draft') && fileToken && !nextDraftAccepted) await authorizedJson(nextApi, nextHost, `${nextApi}/drafts/${encodeURIComponent(query.get('draft')!)}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({writeback_target: writebackTarget(fileToken)})})
      } catch (caught) {
        if (!cancelled) { const message = caught instanceof Error ? caught.message : '飞书登录失败，请重试'; setStatus(message); setError(message) }
      }
    })()
    return () => { cancelled = true }
  }, [query, remember])

  useEffect(() => { if (api && host && documents.length) void refreshQuota() }, [api, host, documents.length, refreshQuota, selected])

  useEffect(() => {
    if (!api || !host || !roundId || round) return
    void pollRound(roundId).catch(caught => setError(caught instanceof Error ? caught.message : '审核结果读取失败'))
  }, [api, host, roundId, round])

  function updateDocument(token: string, changes: Partial<AddonItem>) { setDocuments(current => current.map(item => item.token === token ? {...item, ...changes} : item)) }
  function toggleSelected(token: string, checked: boolean) { setSelected(current => { const next = new Set(current); if (checked) next.add(token); else next.delete(token); return next }) }
  function refreshUrl(nextRound: string, nextDraft = draftId) { setRoundId(nextRound); remember(nextDraft, nextRound) }

  async function pollRound(id: string) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (!api || !host) return null
      const result = await authorizedJson<Round>(api, host, `${api}/rounds/${encodeURIComponent(id)}`)
      if (!result.ok) return null
      setRound(result.body)
      setStatus(result.body.execution === 'completed' ? '审核完成' : result.body.execution === 'failed' ? '审核失败' : '正在审核…')
      if (result.body.execution === 'completed' || result.body.execution === 'failed') return result.body
      await new Promise(resolve => window.setTimeout(resolve, 500))
    }
    throw new Error('审核结果读取超时')
  }

  async function writeCurrentDocument() {
    if (!roundId || !api || !host) return
    const result = await authorizedJson<WritebackResponse>(api, host, `${api}/integrations/feishu/rounds/${encodeURIComponent(roundId)}/writeback`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
    setWritebackResult(result.ok ? (result.body.inserted ? '已写回当前文档' : '未写回') : (result.body.error || '写回失败'))
  }

  async function submit() {
    if (!targets.length || quotaBlocksStart(quota) || busy) return
    if (targets.some(item => !item.rule_version_id)) { setError('请为每个待审文件选择规则版本'); return }
    setBusy(true); setError(''); setStatus('正在发起审核…')
    try {
      let activeDraft = draftId
      if (!activeDraft) { activeDraft = (await request<Draft>('/drafts', {})).id; setDraftId(activeDraft) }
      const prepared = []
      for (const item of documents.filter(item => selected.has(item.token))) {
        if (!item.file_version_id) {
          const imported = await request<FileVersion>('/integrations/feishu/files/import', {file_token: item.token, doc_type: 'file'})
          item.file_version_id = imported.id
          item.file_id = imported.file_id
        }
        prepared.push({file_version_id: item.file_version_id, role: item.role, rule_version_id: item.rule_version_id || null})
      }
      const items = prepared
      await request<Draft>(`/drafts/${encodeURIComponent(activeDraft)}`, {items, writeback_target: writebackTarget(fileToken)}, 'PUT')
      const accepted = await request<Round>(`/drafts/${encodeURIComponent(activeDraft)}/accept`, {idempotency_key: `addon:${activeDraft}`})
      refreshUrl(accepted.id, activeDraft); setDraftAccepted(true)
      const completed = await pollRound(accepted.id)
      if (completed?.execution === 'completed') await writeCurrentDocument()
    } catch (caught) { setError(caught instanceof Error ? caught.message : '发起审核失败，请重试') }
    finally { setBusy(false) }
  }

  const submitDisabled = busy || Boolean(roundId) || !targets.length || quotaBlocksStart(quota) || draftAccepted
  return <div id="addon-root" className="mx-auto flex min-h-dvh max-h-dvh w-full max-w-lg flex-col bg-background text-foreground">
    <header className="flex shrink-0 items-center justify-between border-b px-4 py-3"><div><strong className="text-sm">PPAP Audit</strong><p data-testid="addon-status" className="m-0 text-xs text-muted-foreground">{status}</p></div><Badge data-testid="addon-user" variant="outline">{user}</Badge></header>
    <main className="min-h-0 flex-1 overflow-auto p-4"><div className="grid gap-3">
      {error && <Alert variant="destructive" data-testid="addon-error"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      {quotaLabel(quota) && <Alert data-testid="addon-quota"><AlertDescription>{quotaLabel(quota)}</AlertDescription></Alert>}
      {abnormal.length > 0 && <Alert data-testid="addon-abnormal"><AlertTitle>无法参与审核的文件</AlertTitle><AlertDescription>{abnormal.join('；')}</AlertDescription></Alert>}
      <section data-testid="addon-documents" className="flex flex-col gap-3 border-y py-3">
        <div className="flex items-baseline justify-between gap-2"><strong>选择待审文件</strong><span className="text-xs text-muted-foreground">{documents.length ? `${targets.length} 个待审` : '暂无可识别文件'}</span></div>
        <div className="divide-y">{documents.length ? documents.map(item => { const Glyph = fileIcon(item.name); return <div key={item.token} data-token={item.token} className="grid gap-2 py-3 first:pt-0 last:pb-0"><div className="flex items-center gap-2"><Checkbox id={`addon-doc-${item.token}`} checked={selected.has(item.token)} disabled={busy || Boolean(roundId) || draftAccepted} onCheckedChange={checked => toggleSelected(item.token, checked === true)} /><Label htmlFor={`addon-doc-${item.token}`}><span className="flex min-w-0 items-center gap-2"><Glyph className="size-4 shrink-0 text-muted-foreground" /><span className="truncate">{item.name}</span></span></Label></div><div className="grid grid-cols-2 gap-2"><NativeSelect aria-label="文件角色" value={item.role} disabled={busy || Boolean(roundId) || draftAccepted} onChange={event => updateDocument(item.token, {role: event.target.value as AddonItem['role'], rule_version_id: null})}><option value="target">待审</option><option value="reference">参考</option></NativeSelect><NativeSelect aria-label="规则版本" value={item.rule_version_id || ''} disabled={busy || Boolean(roundId) || draftAccepted || item.role === 'reference'} onChange={event => updateDocument(item.token, {rule_version_id: event.target.value || null})}><option value="">选择规则</option>{rules.map(rule => <option key={rule.id} value={rule.id}>{rule.title}</option>)}</NativeSelect></div></div> }) : <p className="m-0 text-sm text-muted-foreground">请从飞书文档选择 Excel 文件。</p>}</div>
      </section>
      {round && <section data-testid="addon-round" className="flex flex-col gap-3 border-y py-3"><div className="flex items-baseline justify-between gap-2"><strong>审核结果</strong><Badge variant={round.execution === 'completed' ? 'secondary' : round.execution === 'failed' ? 'destructive' : 'outline'}>{round.execution}</Badge></div><p className="m-0 text-xs text-muted-foreground">轮次 <code data-testid="addon-round-id">{round.id}</code></p><div data-testid="addon-findings" className="divide-y">{round.findings.map((item, index) => <div key={item.item_key || index} className="flex items-center gap-2 py-2 text-sm"><Badge variant={item.status === 'NG' ? 'destructive' : 'secondary'}>{item.status}</Badge><span>{item.classification || item.item || '检查项'}</span></div>)}</div><div className="flex flex-wrap items-center justify-between gap-2"><a data-testid="addon-open-round" className="text-sm text-primary underline-offset-4 hover:underline" href={`/?round=${encodeURIComponent(round.id)}`}>回访审核结果</a>{round.execution === 'completed' && <div className="flex flex-wrap items-center gap-2"><Button data-testid="addon-writeback" type="button" variant="outline" onClick={() => void writeCurrentDocument()}>写回当前文档</Button><span data-testid="addon-writeback-result" className="text-xs text-muted-foreground">{writebackResult}</span></div>}</div></section>}
    </div></main>
    <Separator /><footer className="flex shrink-0 items-center justify-between gap-2 p-3"><a data-testid="addon-history" className="text-sm text-primary underline-offset-4 hover:underline" href="/audit-files">历史审核记录</a><Button data-testid="addon-submit" type="button" disabled={submitDisabled} onClick={() => void submit()}>{busy ? <><LoaderCircle className="animate-spin" />正在识别…</> : '开始识别'}</Button></footer>
  </div>
}

createRoot(document.getElementById('addon-root')!).render(<AddonApp />)
