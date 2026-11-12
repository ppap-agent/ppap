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
import {isPreview} from './access'
import {message as errorMessage} from './remote'
import {requirementDirectory, type ReadRequirementVersions, type RequirementDirectoryCollection, type RequirementDirectoryVersion} from './requirement-directory'
import {displayTime} from '../helpers'
import {auditAddressChanged} from '../audit/routes'
import {ProgramWizard, RuleProgramCard} from '../audit/check-points'

const routeSelection = () => ({id: new URLSearchParams(location.search).get('requirement') ?? '', version: new URLSearchParams(location.search).get('reqVersion') ?? ''})
const routeContent = () => new URLSearchParams(location.search).get('reqTab') === 'changes' ? 'changes' : 'clauses'
const routeWizard = (): 'generate' | 'update' | null => {const step = new URLSearchParams(location.search).get('reqStep'); return step === 'generate' || step === 'update' ? step : null}
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
  url.searchParams.delete('reqStep')
  return `${url.pathname}${url.search}${url.hash}`
}

function ClauseDifference({before, after, label}: {before?: RequirementClause; after: RequirementClause; label: string}) {
  return <article className="space-y-3 border-t py-5">
    <h3 className="text-sm font-semibold" title={`条款 ${after.id}`}>{after.title || after.text.slice(0, 40) || '新条款'}</h3>
    <div className="grid gap-4 md:grid-cols-2">
      <div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">之前</p><p className="whitespace-pre-wrap break-words">{before?.text ?? '没有这条'}</p>{before && <p className="break-words text-xs text-muted-foreground">{before.title} · {before.source} · {before.location}</p>}</div>
      <div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">{label}</p><p className="whitespace-pre-wrap break-words">{after.text || '未填写'}</p><p className="break-words text-xs text-muted-foreground">{after.title || '未命名'} · {after.source} · {after.location || '出处未填'}</p></div>
    </div>
    {(before?.original ?? '') !== after.original && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">原文有变化</Button></CollapsibleTrigger><CollapsibleContent><div className="grid gap-4 py-3 md:grid-cols-2"><div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">之前</p><blockquote className="whitespace-pre-wrap break-words border-l pl-3">{before?.original || '没有摘录'}</blockquote></div><div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">{label}</p><blockquote className="whitespace-pre-wrap break-words border-l pl-3">{after.original || '没有摘录'}</blockquote></div></div></CollapsibleContent></Collapsible>}
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
  const [wizard, setWizard] = useState(routeWizard)
  const [feedback, setFeedback] = useState<{text: string; failed: boolean} | null>(null)
  const [attempted, setAttempted] = useState(false), [discard, setDiscard] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [editing, setEditing] = useState<string | null>(null), [sourceClause, setSourceClause] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null), working = useRef(false), mounted = useRef(true)
  const sourceTrigger = useRef<HTMLButtonElement>(null)
  const visibility = useRef({active, revision: 0}), uploadRevision = useRef(0)
  const savedSelections = useRef(new Map<string, {id: string; version: string}>())
  const changeCollections = useRef(onChange); changeCollections.current = onChange
  const [versionRead, setVersionRead] = useState<{key: string; versions: RequirementVersion[]; error: string; loading: boolean}>({key: '', versions: [], error: '', loading: false})
  const [versionRetry, setVersionRetry] = useState(0)
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
  const versionTaken = Boolean(collection?.draft && collection.versions.some(version => version.basis.source.version.trim().toLowerCase() === collection.draft!.basis.source.version.trim().toLowerCase()))
  const missing = (empty: boolean, text = '必填') => attempted && (empty || text !== '必填') ? <span className="text-xs font-normal text-destructive">{text}</span> : null
  if (collection?.draft && catalogCollection?.versions.some(version => version.version.trim().toLowerCase() === collection.draft!.basis.source.version.trim().toLowerCase()) && !issues.includes('这个版本号已经用过了，换一个。')) issues.push('这个版本号已经用过了，换一个。')
  const related = uses.filter(use => collection?.versions.some(version => use.basis.source.id === version.id || use.basis.library?.versionId === version.id))
  useEffect(() => {
    if (!readVersions || !active || !versionIdsKey || loading || readError) return
    const controller = new AbortController(), key = versionKey, owner = selection.id
    const ids = versionIdsKey.split(',')
    setVersionRead({key, versions: [], error: '', loading: true})
    void readVersions(ids, controller.signal).then(versions => {
      if (controller.signal.aborted) return
      if (versions.length !== ids.length || ids.some(id => versions.filter(version => version.id === id).length !== 1)) throw new Error('部分版本没读到，只显示读到的。')
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
    setSelection({id, version}); setAttempted(false); setFeedback(null); setEditing(null); setSourceClause(null)
    history[replace ? 'replaceState' : 'pushState'](history.state, '', requirementHref(id, version))
    setContent(routeContent()); setWizard(routeWizard())
    window.scrollTo({top: 0})
  }
  function navigate(event: MouseEvent<HTMLAnchorElement>, id = '', version = '') {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault(); select(id, version)
  }
  /** Generating or updating a program takes the whole page; leaving it returns to the rule, whose card shows the outcome. */
  function showWizard(step: 'generate' | 'update' | null) {
    setWizard(step)
    const url = new URL(location.href)
    if (step) url.searchParams.set('reqStep', step); else url.searchParams.delete('reqStep')
    history.pushState(history.state, '', url)
    window.scrollTo({top: 0})
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
      if (next.id !== latestSelection.current.id || next.version !== latestSelection.current.version) {setAttempted(false); setFeedback(null); setEditing(null)}
      setSelection(next); setContent(routeContent()); setWizard(routeWizard()); setSourceClause(null)
    }
    // Template pages move between rules by address; follow them as well as Back and Forward.
    window.addEventListener('popstate', sync); window.addEventListener(auditAddressChanged, sync)
    return () => {mounted.current = false; window.removeEventListener('popstate', sync); window.removeEventListener(auditAddressChanged, sync)}
  }, [])
  function edit(update: (draft: RequirementDraft) => RequirementDraft, invalidateAcknowledgement = true) {
    if (working.current || loading || !collection?.draft || !draftSelected) return
    setFeedback(null)
    onChange(items => items.map(item => item.id === collection.id && item.draft ? {...item, draft: update(item.draft)} : item))
  }
  function patchClause(id: string, patch: Partial<RequirementClause>) {
    edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.map(clause => clause.id === id ? {...clause, checked: false, ...patch} : clause)}}))
  }
  function freshDraft(from: RequirementVersion | RequirementDraft): RequirementDraft {
    const id = crypto.randomUUID()
    return {id, baseId: from.id, scope: {...from.scope}, reason: '', basis: {...structuredClone(from.basis), library: undefined, confirmed: null, source: {...from.basis.source, id, version: ''}, clauses: from.basis.clauses.map(clause => ({...clause, checked: false, applicability: 'pending', reason: ''}))}}
  }
  function startVersion() {
    if (working.current || !collection || !current) return
    for (const version of ['', 'draft']) savedSelections.current.delete(canonical({id: collection.id, version}))
    if (collection.draft) {select(collection.id, 'draft'); return}
    const draft = freshDraft(current)
    onChange(items => items.map(item => item.id === collection.id ? {...item, draft} : item)); select(collection.id, 'draft')
  }
  /** A suggested rewording changes the rule itself, so it becomes a new version draft the person checks and saves. */
  function adoptSuggestion(clauseId: string, text: string, note: string) {
    if (working.current || !collection || !current) return
    for (const version of ['', 'draft']) savedSelections.current.delete(canonical({id: collection.id, version}))
    const base = collection.draft ?? freshDraft(current)
    const line = `第 ${clauseId} 条按建议改写：${note}`
    const draft = {...base, reason: base.reason.trim() ? `${base.reason}\n${line}` : line, basis: {...base.basis, clauses: base.basis.clauses.map(clause => clause.id === clauseId ? {...clause, text, checked: false} : clause)}}
    onChange(items => items.map(item => item.id === collection.id ? {...item, draft} : item)); select(collection.id, 'draft')
  }
  async function save() {
    if (working.current || loading || readError || upload.busy || !collection?.draft || libraryDraftIssues(collection).length || saveError) return
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
        setFeedback({text: `版本 ${version.basis.source.version} 已保存。已有审核不变；如果这条规则有检查程序，在上方点“更新检查程序”后，新审核才按新版本检查。`, failed: false})
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
  const originalUnreadable = current?.basis.server?.material.status === 'unreadable'
  // Only uploaded rules get their program here; platform rules are updated by administrators.
  const programmable = Boolean(!draftSelected && selectedVersion?.suiteKey?.startsWith('custom:'))
  const narrowing = current ? scopeFields.filter(({key, flag}) => key !== 'customer' && !current.scope[flag]) : []
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
    {selection.id ? trail ? trail(selectedVersion ?? catalogCollection?.draft ?? undefined) : <nav aria-label="规则导航"><a className="inline-flex min-h-9 items-center gap-1.5 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-ring max-md:min-h-11" href={requirementHref()} aria-disabled={locked || undefined} onClick={event => {if (locked) event.preventDefault(); else navigate(event)}}><ArrowLeft className="size-4"/>返回{title}</a></nav> : headerActions ? <><h1 className="sr-only">{title}</h1>{active && createPortal(uploadAction, headerActions)}</> : <header className="flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">{title}</h1>{uploadAction}</header>}
    {upload.busy && <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4 animate-spin motion-reduce:animate-none"/>正在上传…</p>}
    {readError && <Alert variant="destructive"><AlertTitle>规则没读到</AlertTitle><AlertDescription>{readError}<div><Button variant="outline" disabled={locked} onClick={onReload}>重新读取</Button></div></AlertDescription></Alert>}
    {(upload.error || saveError) && <Alert variant="destructive"><AlertTitle>没保存成功</AlertTitle><AlertDescription>{upload.error || saveError}</AlertDescription></Alert>}
    {feedback && <p role={feedback.failed ? 'alert' : 'status'} className={feedback.failed ? 'text-destructive' : 'text-muted-foreground'}>{feedback.text}</p>}
    {!selection.id ? listView : current && collection ? programmable && wizard ? <ProgramWizard key={current.id} ruleVersionId={current.id} ruleTitle={current.basis.source.title || '规则'} version={current.basis.source.version} updating={wizard === 'update'} onClose={() => showWizard(null)} onAdopt={adoptSuggestion}/> : <>
      <header className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4"><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{current.basis.source.title || '新规则'}</h1>{!draftSelected && <Button variant="outline" disabled={locked} onClick={startVersion}><GitBranch/>{collection.draft ? '继续编辑草稿' : '创建新版本'}</Button>}</div>
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3">
            <Select value={draftSelected ? 'draft' : current.id} onValueChange={value => select(collection.id, value, true)} disabled={locked}>
              <SelectTrigger aria-label="规则版本" className="min-w-0 max-w-full"><SelectValue className="min-w-0">版本 {current.basis.source.version || '待确认'}</SelectValue></SelectTrigger>
              <SelectContent position="popper" align="start" className="max-w-[calc(100vw-2rem)]">{[...(catalogCollection?.versions ?? [])].reverse().map(version => <SelectItem key={version.id} value={version.id} textValue={`${version.version} ${versionDetail(version)}`}><span className="grid min-w-0 gap-0.5 whitespace-normal break-words"><span>{version.version} · 已保存</span>{versionDetail(version) && <span className="text-xs text-muted-foreground">{versionDetail(version)}</span>}</span></SelectItem>)}{collection.draft && <SelectItem value="draft">{collection.draft.basis.source.version || '新版本'} · 草稿</SelectItem>}</SelectContent>
            </Select>
            {draftSelected && <Badge variant="outline">草稿</Badge>}
            <span className="text-muted-foreground">{current.basis.clauses.length} 条条款</span>
            {'at' in current && current.at && <time dateTime={current.at} className="text-muted-foreground">保存于 {displayTime(current.at)}</time>}
            {active && !originalUnreadable ? <RequirementOriginal basis={current.basis} label={current.basis.source.filename} variant="ghost"/> : !draftSelected && <span className="break-words text-muted-foreground">{sourceFileLabel(current.basis.source.filename, current.basis.server?.material.status)}</span>}
        </div>
      </header>
      {draftSelected ? <fieldset disabled={locked} className="min-w-0 space-y-4">
        <p className="flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground"><Info aria-hidden="true" className="size-3.5 shrink-0"/>草稿只存在这台电脑的浏览器里，记得保存。</p>
        <RuleExtraction basis={current.basis} readonly={locked} onChange={basis => edit(draft => ({...draft, basis}), reviewedBasisContent(current.basis) !== reviewedBasisContent(basis))}/>
        <div className="grid gap-4 md:grid-cols-2"><label className="grid gap-1.5">规则名称{missing(!current.basis.source.title.trim())}<Input aria-invalid={attempted && !current.basis.source.title.trim()} value={current.basis.source.title} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, title: value}}}))}}/></label><label className="grid gap-1.5">版本号{missing(!current.basis.source.version.trim(), versionTaken ? '这个版本号已经用过了' : '必填')}<Input aria-invalid={attempted && (!current.basis.source.version.trim() || versionTaken)} value={current.basis.source.version} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, version: value}}}))}}/></label></div>
        <div className="grid gap-4 lg:grid-cols-3">{scopeFields.map(({key, flag, label}) => <div className="min-w-0 space-y-1.5" key={key}><label className="grid gap-1.5">{key === 'customer' ? '所属模板' : `适用${label}`}{missing(current.scope[flag] !== true && !current.scope[key].trim())}<Input aria-invalid={attempted && current.scope[flag] !== true && !current.scope[key].trim()} disabled={locked || current.scope[flag] === true} value={current.scope[key]} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, scope: {...draft.scope, [key]: value}}))}}/></label><label className="flex min-h-9 items-center gap-2 text-muted-foreground max-md:min-h-11"><Checkbox disabled={locked} checked={current.scope[flag] === true} onCheckedChange={value => edit(draft => ({...draft, scope: {...draft.scope, [flag]: value === true}}))}/>{key === 'customer' ? '放入通用规则' : `不限定${label}`}</label></div>)}</div>
        <label className="grid gap-1.5">这次改了什么{missing(!current.reason.trim())}<Textarea aria-invalid={attempted && !current.reason.trim()} value={current.reason} onChange={event => {const value = event.currentTarget.value; edit(draft => ({...draft, reason: value}))}}/></label>
      </fieldset> : narrowing.length > 0 && <dl className="flex flex-wrap gap-x-8 gap-y-2 rounded-lg border bg-card px-4 py-3">{narrowing.map(({key, label}) => <div className="flex min-w-0 items-baseline gap-2" key={key}><dt className="text-xs text-muted-foreground">适用{label}</dt><dd className="break-words">{current.scope[key] || '待确认'}</dd></div>)}</dl>}
      {programmable && active && <RuleProgramCard key={current.id} ruleVersionId={current.id} onPrepare={updating => showWizard(updating ? 'update' : 'generate')}/>}
      {automaticMatching && !current.scope.allCustomers && !current.scope.customer.trim() && <Alert role="status"><AlertTitle>还没选模板</AlertTitle><AlertDescription>{draftSelected ? '填写所属模板，或勾选“放入通用规则”。' : '这个版本没有模板，审核时不会自动匹配到它。新建版本时选一个模板。'}</AlertDescription></Alert>}
      <Tabs value={content} onValueChange={showContent}><div className="flex flex-wrap items-center justify-between gap-3 border-b pb-1"><TabsList variant="line" aria-label="规则内容"><TabsTrigger value="clauses">条款 {current.basis.clauses.length}</TabsTrigger><TabsTrigger value="changes">版本记录</TabsTrigger></TabsList>{draftSelected && <p className="text-xs text-muted-foreground">已核对 {verifiedClauseCount(current.basis)}/{current.basis.clauses.length} 条</p>}</div>
        <TabsContent value="clauses"><section aria-label="规则条款" className="space-y-3 pt-4">{current.basis.clauses.map((clause, index) => {
          const repeated = Boolean(clause.title.trim()) && clause.text.trim().startsWith(clause.title.trim())
          const unchecked = draftSelected && attempted && !(clause.checked && clauseSourceReady(clause, current.basis))
          return <article key={clause.id} aria-label={`条款 ${clause.id}`} data-issue={unchecked || undefined} className={`grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 gap-y-3 rounded-lg border bg-card px-4 py-4 sm:gap-x-4 sm:px-5 ${draftSelected ? 'sm:grid-cols-[1.5rem_minmax(0,1fr)_auto]' : ''} ${unchecked ? 'border-destructive' : ''}`}>
            <span className="pt-0.5 text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <div className="min-w-0 space-y-2">
              {draftSelected && editing === clause.id ? <fieldset disabled={locked} className="grid min-w-0 gap-3 lg:grid-cols-2"><legend className="mb-3 font-medium" title={`条款 ${clause.id}`}>编辑第 {index + 1} 条</legend><label className="grid gap-1.5">条款名称<Input value={clause.title} onChange={event => patchClause(clause.id, {title: event.target.value})}/></label><label className="grid gap-1.5">出处位置<Input readOnly={Boolean(current.basis.server)} value={clause.location} onChange={event => patchClause(clause.id, {location: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">来源文件<Input readOnly={Boolean(current.basis.server)} value={clause.source} onChange={event => patchClause(clause.id, {source: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">条款内容<Textarea value={clause.text} onChange={event => patchClause(clause.id, {text: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">原文<Textarea readOnly={Boolean(current.basis.server)} value={clause.original} onChange={event => patchClause(clause.id, {original: event.target.value})}/></label></fieldset> : <>
                {!repeated && clause.title.trim() && clause.text.trim() && <h2 className="break-words font-medium">{clause.title}</h2>}
                <p className="whitespace-pre-wrap break-words leading-relaxed">{clause.text || clause.title || '还没有条款内容。'}</p>
                <p className="flex flex-wrap items-center justify-between gap-x-2 border-t pt-3 break-words text-xs text-muted-foreground"><span>出处　{clause.source !== current.basis.source.filename && `${clause.source} · `}{clause.location || '待补充'}</span><Button variant="ghost" size="xs" onClick={event => {sourceTrigger.current = event.currentTarget; setSourceClause(clause.id)}}><BookOpen/>核对出处</Button></p>
              </>}
              {draftSelected && <><label className="flex min-h-9 items-center gap-2 max-md:min-h-11"><Checkbox disabled={locked || !clauseSourceReady(clause, current.basis)} aria-label={`${clause.id} 已核对来源`} checked={clause.checked && clauseSourceReady(clause, current.basis)} onCheckedChange={value => patchClause(clause.id, {checked: value === true})}/>已核对内容与出处</label>{!clauseSourceReady(clause, current.basis) && <p className="text-xs text-muted-foreground">先在“核对出处”里选好原文位置。</p>}</>}
            </div>
            {draftSelected && <div className="col-start-2 flex flex-wrap items-center gap-2 sm:col-start-3 sm:flex-col sm:items-stretch"><><Button variant="ghost" size="sm" disabled={locked} aria-expanded={editing === clause.id} onClick={() => setEditing(editing === clause.id ? null : clause.id)}>{editing === clause.id ? '完成编辑' : '编辑条款'}</Button><Button variant="ghost" size="icon-sm" disabled={locked} aria-label={`移除草稿条款 ${clause.id}`} onClick={() => edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.filter(item => item.id !== clause.id)}}))}><Trash2/></Button></></div>}
        </article>})}{draftSelected && <div className="py-4"><Button variant="outline" disabled={locked} onClick={addClause}><Plus/>录入条款</Button></div>}</section></TabsContent>
        <TabsContent value="changes"><section className="space-y-3 pt-4" aria-label="版本差异"><h2 className="font-semibold">{base ? `和 ${base.basis.source.version} 相比` : '首个版本'}</h2>{!draftSelected && current.reason && <p className="whitespace-pre-wrap break-words text-muted-foreground">版本说明：{current.reason}</p>}{base && <p className="text-muted-foreground">新增或修改 {changed.length} 条，移除 {removed.length} 条。</p>}{base && scopeLabel(base.scope) !== scopeLabel(current.scope) && <p className="break-words">适用范围：{scopeLabel(base.scope)} → {scopeLabel(current.scope)}</p>}{base && base.basis.source.title !== current.basis.source.title && <p className="break-words">规则名称：{base.basis.source.title} → {current.basis.source.title}</p>}{changed.map(clause => <ClauseDifference key={clause.id} before={base?.basis.clauses.find(old => old.id === clause.id)} after={clause} label={draftSelected ? '草稿' : current.basis.source.version}/>)}{removed.map(clause => <article key={clause.id} className="space-y-2 border-t py-4"><h3 className="font-semibold" title={`条款 ${clause.id}`}>移除：{clause.title || clause.text.slice(0, 40)}</h3><p className="whitespace-pre-wrap break-words">{clause.text}</p><p className="text-xs text-muted-foreground">{clause.source} · {clause.location}</p></article>)}{!changed.length && !removed.length && <p className="text-muted-foreground">{base ? '条款和出处都没变。' : '还没有条款。'}</p>}</section></TabsContent>
      </Tabs>
      {draftSelected && <footer className="space-y-4 border-t pt-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><DialogTrigger asChild><Button variant="ghost" disabled={locked}>放弃草稿</Button></DialogTrigger>{attempted && issues.length > 0 && <span role="status" className="text-sm text-destructive">还差 {issues.length} 处</span>}<Button disabled={locked || Boolean(readError) || Boolean(saveError)} onClick={() => {
          if (!issues.length) {void save(); return}
          // What is missing is marked where it is; saving goes to the first of them.
          setAttempted(true)
          window.requestAnimationFrame(() => document.querySelector('[aria-invalid="true"], [data-issue]')?.scrollIntoView({behavior: 'smooth', block: 'center'}))
        }}>{publishing ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <Check/>}{publishing ? '正在保存…' : '保存版本'}</Button></div></div>
      </footer>}
      {related.length > 0 && <section className="border-t pt-4"><Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">在这台电脑上用过它的审核 · {related.length}</Button></CollapsibleTrigger><CollapsibleContent><div className="divide-y">{related.map(use => <div className="flex flex-wrap items-center justify-between gap-3 py-3" key={use.id}><div className="min-w-0 space-y-1"><p className="break-words font-medium">{use.title}</p><p className="text-xs text-muted-foreground">{use.basis.source.version} · {use.submitted ? '已用于审核' : '未提交'}</p></div><Button variant="ghost" onClick={() => onOpen(use.id)}>查看审核<ArrowRight/></Button></div>)}</div></CollapsibleContent></Collapsible></section>}
    </> : remoteRequired && !loading && !readError ? versionRead.key === versionKey && versionRead.error ? <Alert variant="destructive"><AlertTitle>这个版本没读到</AlertTitle><AlertDescription><p>{versionRead.error}</p><Button variant="outline" onClick={() => setVersionRetry(value => value + 1)}>重新读取</Button></AlertDescription></Alert> : <ReadSkeleton label="正在读取…" kind="detail" rows={6}/> : loading && selection.id ? <ReadSkeleton label="正在读取…" kind="detail" rows={6}/> : !loading && !readError && <div className="space-y-3 py-12 text-center"><h1 className="text-xl font-semibold">打不开这个版本</h1><p className="text-muted-foreground">可能已被删除，或你没有权限。</p><Button asChild variant="outline"><a href={requirementHref()} onClick={event => navigate(event)}>返回规则列表</a></Button></div>}
    <Sheet open={active && Boolean(source)} onOpenChange={open => !open && setSourceClause(null)}><SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-full sm:data-[side=right]:max-w-lg" onCloseAutoFocus={event => {if (active && sourceTrigger.current?.isConnected) {event.preventDefault(); sourceTrigger.current.focus()}}}>
      <div className="pr-12"><SheetHeader><SheetTitle title={source ? `条款 ${source.id}` : undefined}>{source ? `第 ${sourceNumber} 条 · 核对出处` : '条款出处'}</SheetTitle><SheetDescription>{source ? `${source.source} · ${source.location || '出处未填'}` : '对照原件核对条款。'}</SheetDescription></SheetHeader></div>
      {source && current && <div className="space-y-6 py-2 text-sm">{draftSelected && <RequirementSourceSelect basis={current.basis} clause={source} disabled={locked} onChange={clause => patchClause(source.id, clause)}/>}<section className="space-y-2"><h2 className="text-sm font-medium">原文</h2><blockquote className="whitespace-pre-wrap break-words border-l pl-3 leading-relaxed">{clauseSourceReady(source, current.basis) ? source.original : '还没选原文位置。选好后对照原件看一遍。'}</blockquote></section><section className="space-y-2"><h2 className="text-sm font-medium">条款内容</h2><p className="whitespace-pre-wrap break-words leading-relaxed">{source.text || '还没有条款内容。'}</p></section></div>}
      <SheetFooter>{active && current && <RequirementOriginal basis={current.basis} label="查看原件"/>}<SheetClose asChild><Button variant="outline">返回条款</Button></SheetClose></SheetFooter>
    </SheetContent></Sheet>
    <DialogContent><DialogHeader><DialogTitle>放弃这份草稿？</DialogTitle><DialogDescription>只删除草稿，已保存的版本和已有审核不变。</DialogDescription></DialogHeader><div className="flex justify-end gap-2"><Button variant="outline" disabled={locked} onClick={() => setDiscard(false)}>继续编辑</Button><Button disabled={locked} onClick={() => {if (working.current || !collection) return; onChange(items => items.flatMap(item => item.id !== collection.id ? [item] : item.versions.length ? [{...item, draft: null}] : [])); select(collection.versions.length ? collection.id : '', collection.versions.at(-1)?.id ?? '', true); setDiscard(false)}}>放弃草稿</Button></div></DialogContent>
  </section></Dialog>
}
