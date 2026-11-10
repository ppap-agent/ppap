// Stitch: projects/13261964020862199279/screens/9988350685214b589db5c26ca0920ee5
// and projects/13261964020862199279/screens/de9558b8e43347e2a006989d2b41b790.
import {useEffect, useRef, useState, type Dispatch, type MouseEvent, type ReactNode, type SetStateAction} from 'react'
import {createPortal} from 'react-dom'
import {ArrowLeft, ArrowRight, BookOpen, Check, ChevronDown, GitBranch, Info, Loader2, Plus, Trash2, UploadCloud} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle} from '../components/ui/sheet'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {Textarea} from '../components/ui/textarea'
import {ReadSkeleton} from '../components/read-skeleton'
import {clauseSourceReady, type RequirementBasis, type RequirementClause} from './requirements-state'
import {verifiedClauseCount} from './requirement-sources'
import {RequirementSourceSelect} from './requirement-source-select'
import {clauseContent, libraryDraftIssues, scopeFields, scopeLabel, type RequirementCollection, type RequirementDraft, type RequirementUse, type RequirementVersion} from './requirement-library-state'
import {requirementFileAccept, useLocalUploads} from './local-files'
import {RequirementOriginal} from './requirements'
import {receiveRequirement, RuleExtraction} from './rule-extraction'
import {canonical, publishBasis, sourceFileLabel} from './product'
import {ClauseFormalization, ExplainAll, RuleCheckerStatus} from './requirement-formalization'
import {isPreview} from './access'
import {message as errorMessage} from './remote'
import {requirementDirectory, type ReadRequirementVersions, type RequirementDirectoryCollection, type RequirementDirectoryVersion} from './requirement-directory'
import {displayTime} from '../helpers'
import {auditAddressChanged} from '../audit/routes'

const routeSelection = () => ({id: new URLSearchParams(location.search).get('requirement') ?? '', version: new URLSearchParams(location.search).get('reqVersion') ?? ''})
const routeContent = () => new URLSearchParams(location.search).get('reqTab') === 'changes' ? 'changes' : 'clauses'
function reviewedBasisContent(basis: RequirementBasis) {
  return canonical({
    ...basis,
    server: basis.server ? {...basis.server, sources: undefined} : undefined,
    clauses: basis.clauses.map(clause => [clause, clauseSourceReady(clause, basis)]),
  })
}
function requirementHref(id = '', version = '') {
  const url = new URL(location.href)
  if (url.pathname.replace(/\/$/, '') === '/requirements') url.searchParams.delete('view')
  else url.searchParams.set('view', 'requirements')
  for (const [key, value] of [['requirement', id], ['reqVersion', version]]) {
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key)
  }
  if (!id || id !== routeSelection().id) url.searchParams.delete('reqTab')
  return `${url.pathname}${url.search}${url.hash}`
}

function ClauseDifference({before, after, label}: {before?: RequirementClause; after: RequirementClause; label: string}) {
  return <article className="space-y-3 border-t py-5">
    <h3 className="text-sm font-semibold" title={`条款 ${after.id}`}>{after.title || after.text.slice(0, 40) || '新条款'}</h3>
    <div className="grid gap-4 md:grid-cols-2">
      <div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">之前</p><p className="whitespace-pre-wrap break-words">{before?.text ?? '无此条款'}</p>{before && <p className="break-words text-xs text-muted-foreground">{before.title} · {before.source} · {before.location}</p>}</div>
      <div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">{label}</p><p className="whitespace-pre-wrap break-words">{after.text || '尚未录入'}</p><p className="break-words text-xs text-muted-foreground">{after.title || '名称待补充'} · {after.source} · {after.location || '出处待补充'}</p></div>
    </div>
    {(before?.original ?? '') !== after.original && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">来源摘录变化</Button></CollapsibleTrigger><CollapsibleContent><div className="grid gap-4 py-3 md:grid-cols-2"><div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">之前</p><blockquote className="whitespace-pre-wrap break-words border-l pl-3">{before?.original || '没有摘录'}</blockquote></div><div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">{label}</p><blockquote className="whitespace-pre-wrap break-words border-l pl-3">{after.original || '没有摘录'}</blockquote></div></div></CollapsibleContent></Collapsible>}
  </article>
}

export function RequirementLibraryPanel({collections, onChange, uses, onOpen, saveError, loading, readError, onReload, title = '审核规则', uploadLabel = '上传规则', onPublished, active = true, automaticMatching = false, directory, readVersions, readRevision = 0, headerActions, listView, trail}: {
  /** What the page shows when no rule is open; the rules page passes its templates. */
  listView?: ReactNode
  /** Where the open rule sits, e.g. 规则模板 / 模板 / 文件类型; replaces the plain back link. */
  trail?: (version: RequirementDirectoryVersion | undefined) => ReactNode
  collections: RequirementCollection[]; onChange: Dispatch<SetStateAction<RequirementCollection[]>>
  uses: RequirementUse[]; onOpen: (id: string) => void; saveError: string
  loading: boolean; readError: string; onReload: () => void
  title?: string; uploadLabel?: string; onPublished?: () => void; active?: boolean; automaticMatching?: boolean
  directory?: RequirementDirectoryCollection[]; readVersions?: ReadRequirementVersions; readRevision?: number
  headerActions?: HTMLElement | null
}) {
  const [selection, setSelection] = useState(routeSelection)
  const [content, setContent] = useState(routeContent)
  const [feedback, setFeedback] = useState<{text: string; failed: boolean} | null>(null)
  const [ack, setAck] = useState(false), [discard, setDiscard] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [editing, setEditing] = useState<string | null>(null), [sourceClause, setSourceClause] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null), working = useRef(false), mounted = useRef(true)
  const sourceTrigger = useRef<HTMLButtonElement>(null)
  const visibility = useRef({active, revision: 0}), uploadRevision = useRef(0)
  const savedSelections = useRef(new Map<string, {id: string; version: string}>())
  const changeCollections = useRef(onChange); changeCollections.current = onChange
  const [versionRead, setVersionRead] = useState<{key: string; versions: RequirementVersion[]; error: string; loading: boolean}>({key: '', versions: [], error: '', loading: false})
  const [versionRetry, setVersionRetry] = useState(0)
  const [explainAll, setExplainAll] = useState(0)
  if (visibility.current.active !== active) visibility.current = {active, revision: visibility.current.revision + 1}
  const latestSelection = useRef(selection); latestSelection.current = selection
  useEffect(() => {if (!active) {setSourceClause(null); setDiscard(false)}}, [active])
  const collection = collections.find(item => item.id === selection.id)
  const catalog = requirementDirectory(collections, directory)
  const catalogCollection = catalog.find(item => item.id === selection.id)
  const draftSelected = Boolean(collection?.draft && (!selection.version || selection.version === 'draft'))
  const selectedVersion = selection.version ? catalogCollection?.versions.find(version => version.id === selection.version) : catalogCollection?.versions.at(-1)
  const storedCurrent = draftSelected ? collection!.draft! : selection.version ? collection?.versions.find(version => version.id === selection.version) : collection?.versions.at(-1)
  const baseId = content === 'changes' ? (draftSelected ? storedCurrent?.baseId : selectedVersion?.baseId) : null
  const versionIds = [...new Set([!draftSelected ? selectedVersion?.id : null, baseId].filter((id): id is string => Boolean(id)))]
  const versionIdsKey = versionIds.join(',')
  const versionKey = `${selection.id}:${versionIdsKey}:${visibility.current.revision}:${readRevision}:${versionRetry}`
  const remoteRequired = Boolean(readVersions && versionIds.length)
  const remoteReady = versionRead.key === versionKey && !versionRead.loading && !versionRead.error && !loading && !readError
  const current = !remoteRequired ? storedCurrent : remoteReady ? draftSelected ? storedCurrent : versionRead.versions.find(version => version.id === selectedVersion?.id) : undefined
  const base = baseId ? readVersions ? versionRead.key === versionKey ? versionRead.versions.find(version => version.id === baseId) : undefined : collection?.versions.find(version => version.id === baseId) : undefined
  const issues = collection ? libraryDraftIssues(collection) : []
  if (collection?.draft && catalogCollection?.versions.some(version => version.version.trim().toLowerCase() === collection.draft!.basis.source.version.trim().toLowerCase()) && !issues.includes('此版本号已存在，请使用新的版本号。')) issues.push('此版本号已存在，请使用新的版本号。')
  const related = uses.filter(use => collection?.versions.some(version => use.basis.source.id === version.id || use.basis.library?.versionId === version.id))
  useEffect(() => {
    if (!readVersions || !active || !versionIdsKey || loading || readError) return
    const controller = new AbortController(), key = versionKey, owner = selection.id
    const ids = versionIdsKey.split(',')
    setVersionRead({key, versions: [], error: '', loading: true})
    void readVersions(ids, controller.signal).then(versions => {
      if (controller.signal.aborted) return
      if (versions.length !== ids.length || ids.some(id => versions.filter(version => version.id === id).length !== 1)) throw new Error('规则版本结果不完整，未显示其他版本。')
      changeCollections.current(items => {
        const found = items.find(item => item.id === owner)
        const next = {...(found ?? {id: owner, draft: null, versions: []}), versions: [...(found?.versions ?? []).filter(version => !ids.includes(version.id)), ...versions].sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))}
        return found ? items.map(item => item.id === owner ? next : item) : [...items, next]
      })
      setVersionRead({key, versions, error: '', loading: false})
    }).catch(cause => {if (!controller.signal.aborted) setVersionRead({key, versions: [], error: errorMessage(cause), loading: false})})
    return () => controller.abort()
  }, [readVersions, active, versionIdsKey, versionKey, selection.id, loading, readError])
  function select(id: string, version = '', replace = false) {
    setSelection({id, version}); setAck(false); setFeedback(null); setEditing(null); setSourceClause(null)
    history[replace ? 'replaceState' : 'pushState'](history.state, '', requirementHref(id, version))
    setContent(routeContent())
    window.scrollTo({top: 0})
  }
  function navigate(event: MouseEvent<HTMLAnchorElement>, id = '', version = '') {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault(); select(id, version)
  }
  function showContent(value: string) {
    if (value !== 'clauses' && value !== 'changes') return
    setContent(value)
    const url = new URL(location.href)
    if (value === 'changes') url.searchParams.set('reqTab', value); else url.searchParams.delete('reqTab')
    history.pushState(history.state, '', url)
  }
  useEffect(() => {
    mounted.current = true
    const sync = () => {
      const requested = routeSelection(), next = savedSelections.current.get(canonical(requested)) ?? requested
      if (next !== requested) history.replaceState(history.state, '', requirementHref(next.id, next.version))
      if (next.id !== latestSelection.current.id || next.version !== latestSelection.current.version) {setAck(false); setFeedback(null); setEditing(null)}
      setSelection(next); setContent(routeContent()); setSourceClause(null)
    }
    // Template pages move between rules by address; follow them as well as Back and Forward.
    window.addEventListener('popstate', sync); window.addEventListener(auditAddressChanged, sync)
    return () => {mounted.current = false; window.removeEventListener('popstate', sync); window.removeEventListener(auditAddressChanged, sync)}
  }, [])
  function edit(update: (draft: RequirementDraft) => RequirementDraft, invalidateAcknowledgement = true) {
    if (working.current || loading || !collection?.draft || !draftSelected) return
    if (invalidateAcknowledgement) setAck(false)
    setFeedback(null)
    onChange(items => items.map(item => item.id === collection.id && item.draft ? {...item, draft: update(item.draft)} : item))
  }
  function patchClause(id: string, patch: Partial<RequirementClause>) {
    edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.map(clause => clause.id === id ? {...clause, checked: false, ...patch} : clause)}}))
  }
  function startVersion() {
    if (working.current || !collection || !current) return
    for (const version of ['', 'draft']) savedSelections.current.delete(canonical({id: collection.id, version}))
    if (collection.draft) {select(collection.id, 'draft'); return}
    const id = crypto.randomUUID()
    const draft: RequirementDraft = {id, baseId: current.id, scope: {...current.scope}, reason: '', basis: {...structuredClone(current.basis), library: undefined, confirmed: null, source: {...current.basis.source, id, version: ''}, clauses: current.basis.clauses.map(clause => ({...clause, checked: false, applicability: 'pending', reason: ''}))}}
    onChange(items => items.map(item => item.id === collection.id ? {...item, draft} : item)); select(collection.id, 'draft')
  }
  async function save() {
    if (working.current || loading || readError || upload.busy || !collection?.draft || libraryDraftIssues(collection).length || !ack || saveError) return
    const owner = collection.id, snapshot = structuredClone(collection.draft), signature = canonical(snapshot), selected = {...selection}
    working.current = true; setPublishing(true); setFeedback(null)
    try {
      const version: RequirementVersion = {...snapshot, at: new Date().toISOString()}
      if (!isPreview()) {
        version.basis = await publishBasis({...version.basis, clauses: version.basis.clauses.map(row => ({...row, applicability: 'apply'})), server: version.basis.server ? {...version.basis.server, body: {...version.basis.server.body, customers: version.scope.allCustomers ? [] : [version.scope.customer], parts: version.scope.allParts ? [] : [version.scope.part]}} : undefined}, {scope: version.scope, reason: version.reason})
        version.id = version.basis.server!.ruleId!
      }
      version.basis.source.version = version.basis.source.version.trim()
      const savedOwner = collection.versions.length ? owner : version.id
      savedSelections.current.set(canonical(selected), {id: savedOwner, version: version.id})
      onChange(items => items.map(item => item.id !== owner ? item : {...item,
        id: savedOwner,
        versions: item.versions.some(existing => existing.id === version.id) ? item.versions : [...item.versions, version],
        draft: item.draft && canonical(item.draft) === signature ? null : item.draft,
      }))
      if (mounted.current && visibility.current.active && canonical(latestSelection.current) === canonical(selected)) {
        select(savedOwner, version.id, true)
        setFeedback({text: `版本 ${version.basis.source.version} 已保存。已有审核不受影响；如果这类文件可以自动审核，新审核要等程序按新规则重新验证并发布后才使用它（“自动审核”中会提示）。`, failed: false})
      }
      onPublished?.()
    } catch (cause) {if (mounted.current) setFeedback({text: errorMessage(cause), failed: true})}
    finally {working.current = false; if (mounted.current) setPublishing(false)}
  }
  const upload = useLocalUploads(async ([entry]) => {
    const revision = uploadRevision.current
    const basis = await receiveRequirement(entry), id = crypto.randomUUID()
    onChange(items => [...items, {id, versions: [], draft: {id: basis.source.id, basis, scope: {customer: '', part: '', revision: ''}, reason: '', baseId: null}}])
    if (mounted.current && visibility.current.active && visibility.current.revision === revision) select(id, 'draft')
  }, 'requirement-library', requirementFileAccept)
  const locked = publishing || upload.busy || loading
  const changed = current?.basis.clauses.filter(clause => !base || clauseContent(clause) !== clauseContent(base.basis.clauses.find(old => old.id === clause.id) ?? {...clause, text: ''})) ?? []
  const removed = base?.basis.clauses.filter(old => !current?.basis.clauses.some(clause => clause.id === old.id)) ?? []
  const source = current?.basis.clauses.find(clause => clause.id === sourceClause)
  const sourceNumber = source && current ? current.basis.clauses.indexOf(source) + 1 : 0
  // Without the original, every clause reading is refused for the same reason; say it once instead of under each clause.
  const originalUnreadable = current?.basis.server?.material.status === 'unreadable'
  const explains = Boolean(current && !draftSelected && !originalUnreadable && current.basis.server)
  function versionDetail(version: RequirementDirectoryVersion) {
    const sameName = catalogCollection?.versions.filter(item => item.version === version.version) ?? []
    if (sameName.length < 2) return ''
    return version.at && sameName.filter(item => item.at === version.at).length === 1 ? `保存于 ${displayTime(version.at)}` : `同名版本 ${sameName.indexOf(version) + 1}`
  }
  function addClause() {
    const id = `M-${crypto.randomUUID().slice(0, 6)}`
    edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, state: 'manual'}, clauses: [...draft.basis.clauses, {id, title: '', text: '', original: '', source: draft.basis.source.filename, location: '', applicability: 'pending', checked: false, reason: ''}]}}))
    setEditing(id)
  }
  const uploadAction = <Button disabled={locked} onClick={() => picker.current?.click()}><UploadCloud/>{uploadLabel}</Button>
  return <Dialog open={active && discard} onOpenChange={open => !working.current && setDiscard(open)}><section className="min-w-0 space-y-6 pb-10 text-sm">
    <input hidden aria-label="上传共用规则文件" ref={picker} type="file" accept={requirementFileAccept} disabled={locked} onChange={event => {uploadRevision.current = visibility.current.revision; void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    {selection.id ? trail ? trail(selectedVersion ?? catalogCollection?.draft ?? undefined) : <nav aria-label="要求导航"><a className="inline-flex min-h-9 items-center gap-1.5 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-ring max-md:min-h-11" href={requirementHref()} aria-disabled={locked || undefined} onClick={event => {if (locked) event.preventDefault(); else navigate(event)}}><ArrowLeft className="size-4"/>返回{title}</a></nav> : headerActions ? <><h1 className="sr-only">{title}</h1>{active && createPortal(uploadAction, headerActions)}</> : <header className="flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">{title}</h1>{uploadAction}</header>}
    {upload.busy && <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4 animate-spin motion-reduce:animate-none"/>正在上传规则原件…</p>}
    {readError && <Alert variant="destructive"><AlertTitle>审核规则未能读取</AlertTitle><AlertDescription>{readError}<div><Button variant="outline" disabled={locked} onClick={onReload}>重新读取</Button></div></AlertDescription></Alert>}
    {(upload.error || saveError) && <Alert variant="destructive"><AlertTitle>未能保存</AlertTitle><AlertDescription>{upload.error || saveError}</AlertDescription></Alert>}
    {feedback && <p role={feedback.failed ? 'alert' : 'status'} className={feedback.failed ? 'text-destructive' : 'text-muted-foreground'}>{feedback.text}</p>}
    {!selection.id ? listView : current && collection ? <>
      <header className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4"><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{current.basis.source.title || '核对新要求'}</h1>{!draftSelected && <Button variant="outline" disabled={locked} onClick={startVersion}><GitBranch/>{collection.draft ? '继续版本草稿' : '创建新版本'}</Button>}</div>
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3">
            <Select value={draftSelected ? 'draft' : current.id} onValueChange={value => select(collection.id, value, true)} disabled={locked}>
              <SelectTrigger aria-label="规则版本" className="min-w-0 max-w-full"><SelectValue className="min-w-0">版本 {current.basis.source.version || '待确认'}</SelectValue></SelectTrigger>
              <SelectContent position="popper" align="start" className="max-w-[calc(100vw-2rem)]">{[...(catalogCollection?.versions ?? [])].reverse().map(version => <SelectItem key={version.id} value={version.id} textValue={`${version.version} ${versionDetail(version)}`}><span className="grid min-w-0 gap-0.5 whitespace-normal break-words"><span>{version.version} · 已保存</span>{versionDetail(version) && <span className="text-xs text-muted-foreground">{versionDetail(version)}</span>}</span></SelectItem>)}{collection.draft && <SelectItem value="draft">{collection.draft.basis.source.version || '新版本'} · 草稿</SelectItem>}</SelectContent>
            </Select>
            {draftSelected && <Badge variant="outline">草稿</Badge>}
            <span className="text-muted-foreground">{current.basis.clauses.length} 条</span>
            {active && <RequirementOriginal basis={current.basis} label="查看原件"/>}
            {'at' in current && current.at && <time dateTime={current.at} className="text-xs text-muted-foreground">保存于 {displayTime(current.at)}</time>}
            {!draftSelected && selectedVersion?.suiteKey && <Button type="button" variant="outline" onClick={() => {const url = new URL(location.href); for (const key of ['requirement', 'reqVersion', 'reqTab', 'template', 'systemRule', 'checkerPath']) url.searchParams.delete(key); url.searchParams.set('rulesArea', 'automation'); url.searchParams.set('checkerSuite', selectedVersion.suiteKey!); history.pushState(history.state, '', `${url.pathname}${url.search}${url.hash}`); window.dispatchEvent(new Event(auditAddressChanged))}}>自动审核程序</Button>}
        </div>
        {!draftSelected && selectedVersion && !selectedVersion.suiteKey && <p role="status">还不能自动审核，审核时由人工逐条判断。</p>}
        {!draftSelected && originalUnreadable && <p role="status" className="text-muted-foreground">规则原件读不到，系统暂时不能解释这些条款。</p>}
      </header>
      {draftSelected ? <fieldset disabled={locked} className="min-w-0 space-y-4">
        <p className="flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground"><Info aria-hidden="true" className="size-3.5 shrink-0"/>草稿只保存在当前浏览器；换设备或清除浏览器数据前，请先保存版本。</p>
        <RuleExtraction basis={current.basis} readonly={locked} onChange={basis => edit(draft => ({...draft, basis}), reviewedBasisContent(current.basis) !== reviewedBasisContent(basis))}/>
        <div className="grid gap-4 md:grid-cols-2"><label className="grid gap-1.5">要求名称<Input value={current.basis.source.title} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, title: value}}}))}}/></label><label className="grid gap-1.5">版本号<Input value={current.basis.source.version} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, version: value}}}))}}/></label></div>
        <div className="grid gap-4 lg:grid-cols-3">{scopeFields.map(({key, flag, label}) => <div className="min-w-0 space-y-1.5" key={key}><label className="grid gap-1.5">{key === 'customer' ? '所属模板' : `适用${label}`}<Input disabled={locked || current.scope[flag] === true} value={current.scope[key]} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, scope: {...draft.scope, [key]: value}}))}}/></label><label className="flex min-h-9 items-center gap-2 text-muted-foreground max-md:min-h-11"><Checkbox disabled={locked} checked={current.scope[flag] === true} onCheckedChange={value => edit(draft => ({...draft, scope: {...draft.scope, [flag]: value === true}}))}/>{key === 'customer' ? '放入通用规则' : `不限定${label}`}</label></div>)}</div>
        <label className="grid gap-1.5">来源说明／变更原因<Textarea value={current.reason} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, reason: value}))}}/></label>
      </fieldset> : <div className="space-y-4">
        {/* Scope and source read as one line of facts; the version note follows as plain text. */}
        {/* The template is in the trail; parts and drawing revisions earn space only when they narrow the rule. */}
        <dl className="flex flex-wrap gap-x-8 gap-y-2 rounded-lg border px-4 py-3">{scopeFields.filter(({key, flag}) => key !== 'customer' && !current.scope[flag]).map(({key, label}) => <div className="flex min-w-0 items-baseline gap-2" key={key}><dt className="text-xs text-muted-foreground">适用{label}</dt><dd className="break-words">{current.scope[key] || '待确认'}</dd></div>)}<div className="flex min-w-0 items-baseline gap-2"><dt className="text-xs text-muted-foreground">来源文件</dt><dd className="min-w-0 break-words">{sourceFileLabel(current.basis.source.filename, current.basis.server?.material.status)}</dd></div></dl>
        {current.reason && <p className="whitespace-pre-wrap break-words text-muted-foreground">版本说明：{current.reason}</p>}
      </div>}
      {automaticMatching && !current.scope.allCustomers && !current.scope.customer.trim() && <Alert role="status"><AlertTitle>还没有选择所属模板</AlertTitle><AlertDescription>{draftSelected ? '请填写所属模板，或勾选“放入通用规则”后保存。' : '此版本暂不参与自动匹配。请创建新版本并选择所属模板。已有审核依据不变。'}</AlertDescription></Alert>}
      <Tabs value={content} onValueChange={showContent}><div className="flex flex-wrap items-center justify-between gap-3 border-b pb-1"><TabsList variant="line" aria-label="要求内容"><TabsTrigger value="clauses">条款</TabsTrigger><TabsTrigger value="changes">版本记录</TabsTrigger></TabsList>{draftSelected && <p className="text-xs text-muted-foreground">已核对 {verifiedClauseCount(current.basis)}/{current.basis.clauses.length} 条</p>}{explains && <div className="flex flex-wrap items-center gap-3"><RuleCheckerStatus suiteRevisionId={current.basis.server?.body.suite_revision_id}/>{current.basis.clauses.length > 1 && <ExplainAll key={current.id} ruleVersionId={current.id} onExplain={() => setExplainAll(value => value + 1)}/>}</div>}</div>
        <TabsContent value="clauses"><section aria-label="要求条款" className="divide-y">{current.basis.clauses.map((clause, index) => {
          const repeated = Boolean(clause.title.trim()) && clause.text.trim().startsWith(clause.title.trim())
          return <article key={clause.id} aria-label={`条款 ${clause.id}`} className={`grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 gap-y-3 py-5 sm:gap-x-4 ${draftSelected ? 'sm:grid-cols-[1.5rem_minmax(0,1fr)_auto]' : ''}`}>
            <span className="pt-0.5 text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <div className="min-w-0 space-y-2">
              {draftSelected && editing === clause.id ? <fieldset disabled={locked} className="grid min-w-0 gap-3 lg:grid-cols-2"><legend className="mb-3 font-medium" title={`条款 ${clause.id}`}>编辑第 {index + 1} 条</legend><label className="grid gap-1.5">条款名称<Input value={clause.title} onChange={event => patchClause(clause.id, {title: event.target.value})}/></label><label className="grid gap-1.5">出处位置<Input readOnly={Boolean(current.basis.server)} value={clause.location} onChange={event => patchClause(clause.id, {location: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">来源文件<Input readOnly={Boolean(current.basis.server)} value={clause.source} onChange={event => patchClause(clause.id, {source: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">要执行的要求<Textarea value={clause.text} onChange={event => patchClause(clause.id, {text: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">来源摘录<Textarea readOnly={Boolean(current.basis.server)} value={clause.original} onChange={event => patchClause(clause.id, {original: event.target.value})}/></label></fieldset> : <>
                {!repeated && clause.title.trim() && clause.text.trim() && <h2 className="break-words font-medium">{clause.title}</h2>}
                <p className="whitespace-pre-wrap break-words leading-relaxed">{clause.text || clause.title || '尚未录入要求正文。'}</p>
                <p className="flex flex-wrap items-center gap-x-2 break-words text-xs text-muted-foreground"><span>{clause.source !== current.basis.source.filename && `${clause.source} · `}{clause.location || '出处待补充'}</span><Button variant="link" size="xs" onClick={event => {sourceTrigger.current = event.currentTarget; setSourceClause(clause.id)}}><BookOpen/>核对出处</Button></p>
                {explains && <ClauseFormalization ruleVersionId={current.id} clauseId={clause.id} suiteRevisionId={current.basis.server?.body.suite_revision_id}
                  sourceVerified={clause.checked && clauseSourceReady(clause, current.basis)} explainAll={explainAll} modelNote={current.basis.clauses.length === 1}/>}
              </>}
              {draftSelected && <><label className="flex min-h-9 items-center gap-2 max-md:min-h-11"><Checkbox disabled={locked || !clauseSourceReady(clause, current.basis)} aria-label={`${clause.id} 已核对来源`} checked={clause.checked && clauseSourceReady(clause, current.basis)} onCheckedChange={value => patchClause(clause.id, {checked: value === true})}/>已核对内容与出处</label>{!clauseSourceReady(clause, current.basis) && <p className="text-xs text-muted-foreground">请先核对出处，选择对应的原文位置。</p>}</>}
            </div>
            {draftSelected && <div className="col-start-2 flex flex-wrap items-center gap-2 sm:col-start-3 sm:flex-col sm:items-stretch"><><Button variant="ghost" size="sm" disabled={locked} aria-expanded={editing === clause.id} onClick={() => setEditing(editing === clause.id ? null : clause.id)}>{editing === clause.id ? '完成编辑' : '编辑条款'}</Button><Button variant="ghost" size="icon-sm" disabled={locked} aria-label={`移除草稿条款 ${clause.id}`} onClick={() => edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.filter(item => item.id !== clause.id)}}))}><Trash2/></Button></></div>}
        </article>})}{draftSelected && <div className="py-4"><Button variant="outline" disabled={locked} onClick={addClause}><Plus/>录入条款</Button></div>}</section></TabsContent>
        <TabsContent value="changes"><section className="space-y-3 pt-4" aria-label="版本差异"><h2 className="font-semibold">{base ? `相对 ${base.basis.source.version}` : '首个版本'}</h2>{base && <p className="text-muted-foreground">新增或修改 {changed.length} 条，移除 {removed.length} 条。</p>}{base && scopeLabel(base.scope) !== scopeLabel(current.scope) && <p className="break-words">适用范围：{scopeLabel(base.scope)} → {scopeLabel(current.scope)}</p>}{base && base.basis.source.title !== current.basis.source.title && <p className="break-words">要求名称：{base.basis.source.title} → {current.basis.source.title}</p>}{changed.map(clause => <ClauseDifference key={clause.id} before={base?.basis.clauses.find(old => old.id === clause.id)} after={clause} label={draftSelected ? '草稿' : current.basis.source.version}/>)}{removed.map(clause => <article key={clause.id} className="space-y-2 border-t py-4"><h3 className="font-semibold" title={`条款 ${clause.id}`}>移除：{clause.title || clause.text.slice(0, 40)}</h3><p className="whitespace-pre-wrap break-words">{clause.text}</p><p className="text-xs text-muted-foreground">{clause.source} · {clause.location}</p></article>)}{!changed.length && !removed.length && <p className="text-muted-foreground">{base ? '条款内容与出处未变化。' : '尚无条款。'}</p>}</section></TabsContent>
      </Tabs>
      {draftSelected && <footer className="space-y-4 border-t pt-5">
        {issues.length > 0 && <Alert><AlertTitle>还有 {issues.length} 项待确认</AlertTitle><AlertDescription><p>{issues[0]}</p>{issues.length > 1 && <Collapsible><CollapsibleTrigger asChild><Button variant="outline" size="sm"><ChevronDown/>查看全部待确认项</Button></CollapsibleTrigger><CollapsibleContent><ul className="list-disc space-y-1 pt-3 pl-4">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></CollapsibleContent></Collapsible>}</AlertDescription></Alert>}
        <label className="flex min-h-9 items-center gap-2 max-md:min-h-11"><Checkbox disabled={locked} checked={ack} onCheckedChange={value => setAck(value === true)}/>已核对本版本的来源、范围和变更</label>
        <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">草稿保存在本机；保存新版不改变已有事项。</p><div className="flex items-center gap-2"><DialogTrigger asChild><Button variant="ghost" disabled={locked}>放弃草稿</Button></DialogTrigger><Button disabled={locked || Boolean(readError) || issues.length > 0 || !ack || Boolean(saveError)} onClick={() => void save()}>{publishing ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <Check/>}{publishing ? '正在保存版本…' : '确认并保存版本'}</Button></div></div>
      </footer>}
      {related.length > 0 && <section className="border-t pt-4"><Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">本浏览器引用 · {related.length} 个事项</Button></CollapsibleTrigger><CollapsibleContent><div className="divide-y">{related.map(use => <div className="flex flex-wrap items-center justify-between gap-3 py-3" key={use.id}><div className="min-w-0 space-y-1"><p className="break-words font-medium">{use.title}</p><p className="text-xs text-muted-foreground">{use.basis.source.version} · {use.submitted ? '依据已固定' : '未登记'}</p></div><Button variant="ghost" onClick={() => onOpen(use.id)}>查看事项<ArrowRight/></Button></div>)}</div></CollapsibleContent></Collapsible></section>}
    </> : remoteRequired && !loading && !readError ? versionRead.key === versionKey && versionRead.error ? <Alert variant="destructive"><AlertTitle>规则版本未能读取</AlertTitle><AlertDescription><p>{versionRead.error}</p><Button variant="outline" onClick={() => setVersionRetry(value => value + 1)}>重新读取版本</Button></AlertDescription></Alert> : <ReadSkeleton label="正在读取规则版本…" kind="detail" rows={6}/> : loading && selection.id ? <ReadSkeleton label="正在读取规则…" kind="detail" rows={6}/> : !loading && !readError && <div className="space-y-3 py-12 text-center"><h1 className="text-xl font-semibold">暂时无法打开此规则版本</h1><p className="text-muted-foreground">记录不可用；未自动改选其他版本。</p><Button asChild variant="outline"><a href={requirementHref()} onClick={event => navigate(event)}>返回要求列表</a></Button></div>}
    <Sheet open={active && Boolean(source)} onOpenChange={open => !open && setSourceClause(null)}><SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-full sm:data-[side=right]:max-w-lg" onCloseAutoFocus={event => {if (active && sourceTrigger.current?.isConnected) {event.preventDefault(); sourceTrigger.current.focus()}}}>
      <div className="pr-12"><SheetHeader><SheetTitle title={source ? `条款 ${source.id}` : undefined}>{source ? `第 ${sourceNumber} 条 · 核对出处` : '条款出处'}</SheetTitle><SheetDescription>{source ? `${source.source} · ${source.location || '出处待确认'}` : '对照原件核对条款。'}</SheetDescription></SheetHeader></div>
      {source && current && <div className="space-y-6 py-2 text-sm">{draftSelected && <RequirementSourceSelect basis={current.basis} clause={source} disabled={locked} onChange={clause => patchClause(source.id, clause)}/>}<section className="space-y-2"><h2 className="text-sm font-medium">来源摘录</h2><blockquote className="whitespace-pre-wrap break-words border-l pl-3 leading-relaxed">{clauseSourceReady(source, current.basis) ? source.original : '尚未关联原文位置。请选择对应原文，并对照原件核对。'}</blockquote></section><section className="space-y-2"><h2 className="text-sm font-medium">本版本条款</h2><p className="whitespace-pre-wrap break-words leading-relaxed">{source.text || '尚未录入条款。'}</p></section></div>}
      <SheetFooter>{active && current && <RequirementOriginal basis={current.basis} label="查看原件"/>}<SheetClose asChild><Button variant="outline">返回条款</Button></SheetClose></SheetFooter>
    </SheetContent></Sheet>
    <DialogContent><DialogHeader><DialogTitle>放弃这个版本草稿？</DialogTitle><DialogDescription>只移除未确认的草稿。已保存版本和已有事项不变。</DialogDescription></DialogHeader><div className="flex justify-end gap-2"><Button variant="outline" disabled={locked} onClick={() => setDiscard(false)}>继续编辑</Button><Button disabled={locked} onClick={() => {if (working.current || !collection) return; onChange(items => items.flatMap(item => item.id !== collection.id ? [item] : item.versions.length ? [{...item, draft: null}] : [])); select(collection.versions.length ? collection.id : '', collection.versions.at(-1)?.id ?? '', true); setDiscard(false)}}>放弃此草稿</Button></div></DialogContent>
  </section></Dialog>
}
