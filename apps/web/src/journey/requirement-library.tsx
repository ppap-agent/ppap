// Stitch: projects/13261964020862199279/screens/9988350685214b589db5c26ca0920ee5
// and projects/13261964020862199279/screens/de9558b8e43347e2a006989d2b41b790.
import {useEffect, useRef, useState, type Dispatch, type MouseEvent, type SetStateAction} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, Check, ChevronDown, ChevronRight, GitBranch, Loader2, Plus, Search, Trash2, UploadCloud} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {Textarea} from '../components/ui/textarea'
import type {RequirementClause} from './requirements-state'
import {clauseContent, libraryDraftIssues, scopeFields, scopeLabel, type RequirementCollection, type RequirementDraft, type RequirementScope, type RequirementUse, type RequirementVersion} from './requirement-library-state'
import {fileAccept, useLocalUploads} from './local-files'
import {RequirementOriginal} from './requirements'
import {receiveRequirement, RuleExtraction} from './rule-extraction'
import {canonical, publishBasis} from './product'
import {isPreview} from './access'
import {message as errorMessage} from './remote'

const routeSelection = () => ({id: new URLSearchParams(location.search).get('requirement') ?? '', version: new URLSearchParams(location.search).get('reqVersion') ?? ''})
const routeQuery = () => (new URLSearchParams(location.search).get('reqQuery') ?? '').slice(0, 200)
const routeContent = () => new URLSearchParams(location.search).get('reqTab') === 'changes' ? 'changes' : 'clauses'
const listScope = (scope: RequirementScope) => scopeFields.every(({flag}) => scope[flag]) ? '不限定客户、零件与修订' : scopeLabel(scope)
function requirementHref(id = '', version = '') {
  const url = new URL(location.href)
  url.searchParams.set('view', 'requirements')
  for (const [key, value] of [['requirement', id], ['reqVersion', version]]) {
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key)
  }
  if (!id || id !== routeSelection().id) url.searchParams.delete('reqTab')
  return `${url.pathname}${url.search}${url.hash}`
}

function ClauseDifference({before, after, label}: {before?: RequirementClause; after: RequirementClause; label: string}) {
  return <article className="space-y-3 border-t py-5">
    <h3 className="text-sm font-semibold">{after.id} · {after.title || '新条款'}</h3>
    <div className="grid gap-4 md:grid-cols-2">
      <div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">之前</p><p className="whitespace-pre-wrap break-words">{before?.text ?? '无此条款'}</p>{before && <p className="break-words text-xs text-muted-foreground">{before.title} · {before.source} · {before.location}</p>}</div>
      <div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">{label}</p><p className="whitespace-pre-wrap break-words">{after.text || '尚未录入'}</p><p className="break-words text-xs text-muted-foreground">{after.title || '名称待补充'} · {after.source} · {after.location || '出处待补充'}</p></div>
    </div>
    {(before?.original ?? '') !== after.original && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">来源摘录变化</Button></CollapsibleTrigger><CollapsibleContent><div className="grid gap-4 py-3 md:grid-cols-2"><div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">之前</p><blockquote className="whitespace-pre-wrap break-words border-l pl-3">{before?.original || '没有摘录'}</blockquote></div><div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">{label}</p><blockquote className="whitespace-pre-wrap break-words border-l pl-3">{after.original || '没有摘录'}</blockquote></div></div></CollapsibleContent></Collapsible>}
  </article>
}

export function RequirementLibraryPanel({collections, onChange, uses, onOpen, saveError, loading, readError, onReload}: {
  collections: RequirementCollection[]; onChange: Dispatch<SetStateAction<RequirementCollection[]>>
  uses: RequirementUse[]; onOpen: (id: string) => void; saveError: string
  loading: boolean; readError: string; onReload: () => void
}) {
  const [selection, setSelection] = useState(routeSelection)
  const [query, setQuery] = useState(routeQuery)
  const [content, setContent] = useState(routeContent)
  const [feedback, setFeedback] = useState<{text: string; failed: boolean} | null>(null)
  const [ack, setAck] = useState(false), [discard, setDiscard] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [editing, setEditing] = useState<string | null>(null), [sourceClause, setSourceClause] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null), working = useRef(false), mounted = useRef(true)
  const sourceTrigger = useRef<HTMLButtonElement>(null)
  const latestSelection = useRef(selection); latestSelection.current = selection
  const collection = collections.find(item => item.id === selection.id)
  const draftSelected = Boolean(collection?.draft && (!selection.version || selection.version === 'draft'))
  const current = draftSelected ? collection!.draft! : selection.version ? collection?.versions.find(version => version.id === selection.version) : collection?.versions.at(-1)
  const base = current?.baseId ? collection?.versions.find(version => version.id === current.baseId) : undefined
  const issues = collection ? libraryDraftIssues(collection) : []
  const related = uses.filter(use => collection?.versions.some(version => use.basis.source.id === version.id || use.basis.library?.versionId === version.id))
  const shown = collections.filter(item => {
    const content = item.draft ?? item.versions.at(-1)
    return content && `${content.basis.source.title} ${scopeLabel(content.scope)}`.toLowerCase().includes(query.trim().toLowerCase())
  })
  function select(id: string, version = '', replace = false) {
    setSelection({id, version}); setAck(false); setFeedback(null); setEditing(null); setSourceClause(null)
    history[replace ? 'replaceState' : 'pushState'](null, '', requirementHref(id, version))
    setContent(routeContent())
    window.scrollTo({top: 0})
  }
  function navigate(event: MouseEvent<HTMLAnchorElement>, id = '', version = '') {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault(); select(id, version)
  }
  function search(value: string) {
    setQuery(value)
    const url = new URL(location.href)
    if (value) url.searchParams.set('reqQuery', value); else url.searchParams.delete('reqQuery')
    history.replaceState(null, '', url)
  }
  function showContent(value: string) {
    if (value !== 'clauses' && value !== 'changes') return
    setContent(value)
    const url = new URL(location.href)
    if (value === 'changes') url.searchParams.set('reqTab', value); else url.searchParams.delete('reqTab')
    history.pushState(null, '', url)
  }
  useEffect(() => {
    mounted.current = true
    const sync = () => {
      const next = routeSelection()
      if (next.id !== latestSelection.current.id || next.version !== latestSelection.current.version) {setAck(false); setFeedback(null); setEditing(null)}
      setSelection(next); setQuery(routeQuery()); setContent(routeContent()); setSourceClause(null)
    }
    window.addEventListener('popstate', sync)
    return () => {mounted.current = false; window.removeEventListener('popstate', sync)}
  }, [])
  function edit(update: (draft: RequirementDraft) => RequirementDraft) {
    if (working.current || loading || !collection?.draft || !draftSelected) return
    setAck(false); setFeedback(null)
    onChange(items => items.map(item => item.id === collection.id && item.draft ? {...item, draft: update(item.draft)} : item))
  }
  function patchClause(id: string, patch: Partial<RequirementClause>) {
    edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.map(clause => clause.id === id ? {...clause, checked: false, ...patch} : clause)}}))
  }
  function startVersion() {
    if (working.current || !collection || !current) return
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
      onChange(items => items.map(item => item.id !== owner ? item : {...item,
        id: savedOwner,
        versions: item.versions.some(existing => existing.id === version.id) ? item.versions : [...item.versions, version],
        draft: item.draft && canonical(item.draft) === signature ? null : item.draft,
      }))
      if (mounted.current && canonical(latestSelection.current) === canonical(selected)) {
        select(savedOwner, version.id, true)
        setFeedback({text: `版本 ${version.basis.source.version} 已保存。已有事项仍使用各自的版本。`, failed: false})
      }
    } catch (cause) {if (mounted.current) setFeedback({text: errorMessage(cause), failed: true})}
    finally {working.current = false; if (mounted.current) setPublishing(false)}
  }
  const upload = useLocalUploads(async ([entry]) => {
    const basis = await receiveRequirement(entry), id = crypto.randomUUID()
    onChange(items => [...items, {id, versions: [], draft: {id: basis.source.id, basis, scope: {customer: '', part: '', revision: ''}, reason: '', baseId: null}}])
    if (mounted.current) {search(''); select(id, 'draft')}
  }, 'requirement-library')
  const locked = publishing || upload.busy || loading
  const changed = current?.basis.clauses.filter(clause => !base || clauseContent(clause) !== clauseContent(base.basis.clauses.find(old => old.id === clause.id) ?? {...clause, text: ''})) ?? []
  const removed = base?.basis.clauses.filter(old => !current?.basis.clauses.some(clause => clause.id === old.id)) ?? []
  const source = current?.basis.clauses.find(clause => clause.id === sourceClause)
  function versionDetail(version: RequirementVersion) {
    const sameName = collection?.versions.filter(item => item.basis.source.version === version.basis.source.version) ?? []
    if (sameName.length < 2) return ''
    return version.at && sameName.filter(item => item.at === version.at).length === 1 ? `保存于 ${new Date(version.at).toLocaleString('zh-CN')}` : `记录 ${version.id.slice(0, 8)}`
  }
  function addClause() {
    const id = `M-${crypto.randomUUID().slice(0, 6)}`
    edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, state: 'manual'}, clauses: [...draft.basis.clauses, {id, title: '', text: '', original: '', source: draft.basis.source.filename, location: '', applicability: 'pending', checked: false, reason: ''}]}}))
    setEditing(id)
  }
  return <section className="min-w-0 space-y-6 pb-10 text-sm">
    <input hidden aria-label="上传共用要求文件" ref={picker} type="file" accept={fileAccept} disabled={locked} onChange={event => {void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    {selection.id ? <nav aria-label="要求导航"><a className="inline-flex min-h-9 items-center gap-1.5 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-ring max-md:min-h-11" href={requirementHref()} aria-disabled={locked || undefined} onClick={event => {if (locked) event.preventDefault(); else navigate(event)}}><ArrowLeft className="size-4"/>返回审核要求</a></nav> : <header className="flex items-center justify-between gap-4"><h1 className="text-2xl font-semibold tracking-tight">审核要求</h1><Button disabled={locked} onClick={() => picker.current?.click()}><UploadCloud/>上传要求</Button></header>}
    {upload.busy && <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4 animate-spin motion-reduce:animate-none"/>正在上传要求原件…</p>}
    {loading && <p role="status" className="text-muted-foreground">正在读取审核要求…</p>}
    {readError && <Alert variant="destructive"><AlertTitle>审核要求未能读取</AlertTitle><AlertDescription>{readError}<div><Button variant="outline" disabled={locked} onClick={onReload}>重新读取</Button></div></AlertDescription></Alert>}
    {(upload.error || saveError) && <Alert variant="destructive"><AlertTitle>未能保存</AlertTitle><AlertDescription>{upload.error || saveError}</AlertDescription></Alert>}
    {feedback && <p role={feedback.failed ? 'alert' : 'status'} className={feedback.failed ? 'text-destructive' : 'text-muted-foreground'}>{feedback.text}</p>}
    {!selection.id ? <>
      <div className="space-y-3"><div role="search" aria-label="筛选审核要求" className="flex max-w-xl items-center gap-2"><Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true"/><Input aria-label="搜索审核要求" type="search" placeholder="搜索要求、客户或零件" maxLength={200} value={query} onChange={event => search(event.target.value)}/>{query && <Button variant="ghost" onClick={() => search('')}>清除</Button>}</div>{!loading && !readError && <p className="text-xs text-muted-foreground" role="status">{query ? `找到 ${shown.length} 份要求` : `${shown.length} 份要求`}</p>}</div>
      {shown.length ? <>
        <div className="max-lg:hidden"><Table className="table-fixed"><TableHeader><TableRow><TableHead className="w-[40%]">要求名称</TableHead><TableHead>适用范围</TableHead><TableHead className="w-36">最近版本</TableHead><TableHead className="w-16">条款</TableHead><TableHead className="w-10"><span className="sr-only">打开</span></TableHead></TableRow></TableHeader><TableBody>{shown.map(item => {
          const latest = item.draft ?? item.versions.at(-1)!
          return <TableRow key={item.id} className="relative"><TableCell className="whitespace-normal"><a className="flex items-start gap-2.5 py-1 text-left font-medium underline-offset-4 after:absolute after:inset-0 hover:underline focus-visible:outline-ring" href={requirementHref(item.id)} onClick={event => navigate(event, item.id)}><BookOpen className="mt-0.5 size-4 shrink-0 text-muted-foreground"/><span className="min-w-0 break-words">{latest.basis.source.title || '待命名要求'}</span></a></TableCell><TableCell className="whitespace-normal break-words"><span className="text-muted-foreground">{listScope(latest.scope)}</span></TableCell><TableCell className="whitespace-normal"><div className="flex flex-wrap items-center gap-2"><span className="break-words">{latest.basis.source.version || '版本待核对'}</span>{item.draft && <Badge variant="outline">草稿</Badge>}</div></TableCell><TableCell><span className="tabular-nums">{latest.basis.clauses.length}</span></TableCell><TableCell><ChevronRight className="size-4 text-muted-foreground" aria-hidden="true"/></TableCell></TableRow>
        })}</TableBody></Table></div>
        <ul className="divide-y border-y lg:hidden" aria-label="要求目录">{shown.map(item => {const latest = item.draft ?? item.versions.at(-1)!; return <li key={item.id}><a className="flex items-center gap-3 py-4 text-left focus-visible:outline-ring" href={requirementHref(item.id)} onClick={event => navigate(event, item.id)}><div className="min-w-0 flex-1 space-y-1.5"><p className="break-words font-medium">{latest.basis.source.title || '待命名要求'}</p><p className="break-words text-muted-foreground">{listScope(latest.scope)}</p><div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>{latest.basis.source.version || '版本待核对'} · {latest.basis.clauses.length} 条</span>{item.draft && <Badge variant="outline">草稿</Badge>}</div></div><ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true"/></a></li>})}</ul>
      </> : !loading && !readError && <div className="space-y-3 py-12 text-center"><BookOpen className="mx-auto size-7 text-muted-foreground"/><h2 className="text-lg font-semibold">{query ? '没有匹配的要求' : '还没有审核要求'}</h2><Button variant="outline" onClick={() => query ? search('') : picker.current?.click()}>{query ? '清除搜索' : '选择要求文件'}</Button></div>}
    </> : current && collection ? <>
      <header className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4"><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{current.basis.source.title || '核对新要求'}</h1>{!draftSelected && <Button disabled={locked} onClick={startVersion}><GitBranch/>{collection.draft ? '继续版本草稿' : '创建新版本'}</Button>}</div>
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3">
            <Select value={draftSelected ? 'draft' : current.id} onValueChange={value => select(collection.id, value, true)} disabled={locked}>
              <SelectTrigger aria-label="要求版本" className="min-w-0 max-w-full"><SelectValue className="min-w-0">版本 {current.basis.source.version || '待核对'}</SelectValue></SelectTrigger>
              <SelectContent position="popper" align="start" className="max-w-[calc(100vw-2rem)]">{[...collection.versions].reverse().map(version => <SelectItem key={version.id} value={version.id} textValue={`${version.basis.source.version} ${versionDetail(version)}`}><span className="grid min-w-0 gap-0.5 whitespace-normal break-words"><span>{version.basis.source.version} · 已保存</span>{versionDetail(version) && <span className="text-xs text-muted-foreground">{versionDetail(version)}</span>}</span></SelectItem>)}{collection.draft && <SelectItem value="draft">{collection.draft.basis.source.version || '新版本'} · 草稿</SelectItem>}</SelectContent>
            </Select>
            <Badge variant="outline">{draftSelected ? '草稿' : '已保存'}</Badge>
            <span className="text-muted-foreground">{current.basis.clauses.length} 条</span>
            <RequirementOriginal basis={current.basis} label="查看原件"/>
            {'at' in current && current.at && <time dateTime={current.at} className="text-xs text-muted-foreground">保存于 {new Date(current.at).toLocaleString('zh-CN')}</time>}
        </div>
      </header>
      {draftSelected ? <fieldset disabled={locked} className="min-w-0 space-y-4">
        <RuleExtraction basis={current.basis} readonly={locked} onChange={basis => edit(draft => ({...draft, basis}))}/>
        <div className="grid gap-4 md:grid-cols-2"><label className="grid gap-1.5">要求名称<Input value={current.basis.source.title} onChange={event => edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, title: event.target.value}}}))}/></label><label className="grid gap-1.5">版本号<Input value={current.basis.source.version} onChange={event => edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, version: event.target.value}}}))}/></label></div>
        <div className="grid gap-4 lg:grid-cols-3">{scopeFields.map(({key, flag, label}) => <div className="min-w-0 space-y-1.5" key={key}><label className="grid gap-1.5">适用{label}<Input disabled={locked || current.scope[flag] === true} value={current.scope[key]} onChange={event => edit(draft => ({...draft, scope: {...draft.scope, [key]: event.target.value}}))}/></label><label className="flex min-h-9 items-center gap-2 text-muted-foreground max-md:min-h-11"><Checkbox disabled={locked} checked={current.scope[flag] === true} onCheckedChange={value => edit(draft => ({...draft, scope: {...draft.scope, [flag]: value === true}}))}/>不限定{label}</label></div>)}</div>
        <label className="grid gap-1.5">来源说明／变更原因<Textarea value={current.reason} onChange={event => edit(draft => ({...draft, reason: event.target.value}))}/></label>
      </fieldset> : <div className="space-y-4">
        <dl className="grid grid-cols-3 gap-x-4 gap-y-4">{scopeFields.map(({key, flag, label}) => <div className="min-w-0 space-y-1" key={key}><dt className="text-xs text-muted-foreground">适用{label}</dt><dd className="break-words">{current.scope[flag] ? '不限定' : current.scope[key] || '待核对'}</dd></div>)}</dl>
        <dl className="space-y-3 text-xs"><div className="flex flex-wrap gap-x-3 gap-y-1"><dt className="text-muted-foreground">来源文件</dt><dd className="min-w-0 break-words">{current.basis.source.filename}</dd></div>{current.reason && <div className="space-y-1"><dt className="text-muted-foreground">版本说明</dt><dd className="whitespace-pre-wrap break-words text-sm">{current.reason}</dd></div>}</dl>
      </div>}
      <Tabs value={content} onValueChange={showContent}><div className="flex flex-wrap items-center justify-between gap-3 border-b pb-1"><TabsList variant="line" aria-label="要求内容"><TabsTrigger value="clauses">条款</TabsTrigger><TabsTrigger value="changes">版本变化</TabsTrigger></TabsList>{draftSelected && <p className="text-xs text-muted-foreground">已核对 {current.basis.clauses.filter(clause => clause.checked).length}/{current.basis.clauses.length} 条</p>}</div>
        <TabsContent value="clauses"><section aria-label="要求条款" className="divide-y">{current.basis.clauses.map((clause, index) => {
          const repeated = Boolean(clause.title.trim()) && clause.text.trim().startsWith(clause.title.trim())
          return <article key={clause.id} aria-label={`条款 ${clause.id}`} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 gap-y-3 py-5 sm:grid-cols-[1.5rem_minmax(0,1fr)_auto] sm:gap-x-4">
            <span className="pt-0.5 text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <div className="min-w-0 space-y-2">
              {draftSelected && editing === clause.id ? <fieldset disabled={locked} className="grid min-w-0 gap-3 lg:grid-cols-2"><legend className="mb-3 font-medium">编辑条款 {clause.id}</legend><label className="grid gap-1.5">条款名称<Input value={clause.title} onChange={event => patchClause(clause.id, {title: event.target.value})}/></label><label className="grid gap-1.5">出处位置<Input value={clause.location} onChange={event => patchClause(clause.id, {location: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">来源文件<Input value={clause.source} onChange={event => patchClause(clause.id, {source: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">要执行的要求<Textarea value={clause.text} onChange={event => patchClause(clause.id, {text: event.target.value})}/></label><label className="grid gap-1.5 lg:col-span-2">来源摘录<Textarea value={clause.original} onChange={event => patchClause(clause.id, {original: event.target.value})}/></label></fieldset> : <>
                {!repeated && clause.title.trim() && clause.text.trim() && <h2 className="break-words font-medium">{clause.title}</h2>}
                <p className="whitespace-pre-wrap break-words leading-relaxed">{clause.text || clause.title || '尚未录入要求正文。'}</p>
                <p className="break-words text-xs text-muted-foreground">{clause.source !== current.basis.source.filename && `${clause.source} · `}{clause.location || '出处待补充'}</p>
              </>}
              {draftSelected && <label className="flex min-h-9 items-center gap-2 max-md:min-h-11"><Checkbox disabled={locked} aria-label={`${clause.id} 已核对来源`} checked={clause.checked} onCheckedChange={value => patchClause(clause.id, {checked: value === true})}/>已核对内容与出处</label>}
            </div>
            <div className="col-start-2 flex flex-wrap items-center gap-2 sm:col-start-3 sm:flex-col sm:items-stretch"><Button variant="outline" size="sm" onClick={event => {sourceTrigger.current = event.currentTarget; setSourceClause(clause.id)}}><BookOpen/>核对出处</Button>{draftSelected && <><Button variant="ghost" size="sm" disabled={locked} aria-expanded={editing === clause.id} onClick={() => setEditing(editing === clause.id ? null : clause.id)}>{editing === clause.id ? '完成编辑' : '编辑条款'}</Button><Button variant="ghost" size="icon-sm" disabled={locked} aria-label={`移除草稿条款 ${clause.id}`} onClick={() => edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.filter(item => item.id !== clause.id)}}))}><Trash2/></Button></>}</div>
        </article>})}{draftSelected && <div className="py-4"><Button variant="outline" disabled={locked} onClick={addClause}><Plus/>录入条款</Button></div>}</section></TabsContent>
        <TabsContent value="changes"><section className="space-y-3 pt-4" aria-label="版本差异"><h2 className="font-semibold">{base ? `相对 ${base.basis.source.version}` : '首个版本'}</h2>{base && <p className="text-muted-foreground">新增或修改 {changed.length} 条，移除 {removed.length} 条。</p>}{base && scopeLabel(base.scope) !== scopeLabel(current.scope) && <p className="break-words">适用范围：{scopeLabel(base.scope)} → {scopeLabel(current.scope)}</p>}{base && base.basis.source.title !== current.basis.source.title && <p className="break-words">要求名称：{base.basis.source.title} → {current.basis.source.title}</p>}{changed.map(clause => <ClauseDifference key={clause.id} before={base?.basis.clauses.find(old => old.id === clause.id)} after={clause} label={draftSelected ? '草稿' : current.basis.source.version}/>)}{removed.map(clause => <article key={clause.id} className="space-y-2 border-t py-4"><h3 className="font-semibold">移除：{clause.id} · {clause.title}</h3><p className="whitespace-pre-wrap break-words">{clause.text}</p><p className="text-xs text-muted-foreground">{clause.source} · {clause.location}</p></article>)}{!changed.length && !removed.length && <p className="text-muted-foreground">{base ? '条款内容与出处未变化。' : '尚无条款。'}</p>}</section></TabsContent>
      </Tabs>
      {draftSelected && <footer className="space-y-4 border-t pt-5">
        {issues.length > 0 && <Alert><AlertTitle>还有 {issues.length} 项待核对</AlertTitle><AlertDescription><p>{issues[0]}</p>{issues.length > 1 && <Collapsible><CollapsibleTrigger asChild><Button variant="outline" size="sm"><ChevronDown/>查看全部待核对项</Button></CollapsibleTrigger><CollapsibleContent><ul className="list-disc space-y-1 pt-3 pl-4">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></CollapsibleContent></Collapsible>}</AlertDescription></Alert>}
        <label className="flex min-h-9 items-center gap-2 max-md:min-h-11"><Checkbox disabled={locked} checked={ack} onCheckedChange={value => setAck(value === true)}/>已核对本版本的来源、范围和变更</label>
        <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">草稿保存在本机；保存新版不改变已有事项。</p><div className="flex items-center gap-2"><Button variant="ghost" disabled={locked} onClick={() => setDiscard(true)}>放弃草稿</Button><Button disabled={locked || Boolean(readError) || issues.length > 0 || !ack || Boolean(saveError)} onClick={() => void save()}>{publishing ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <Check/>}{publishing ? '正在保存版本…' : '确认并保存版本'}</Button></div></div>
      </footer>}
      {related.length > 0 && <section className="border-t pt-4"><Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">本浏览器引用 · {related.length} 个事项</Button></CollapsibleTrigger><CollapsibleContent><div className="divide-y">{related.map(use => <div className="flex flex-wrap items-center justify-between gap-3 py-3" key={use.id}><div className="min-w-0 space-y-1"><p className="break-words font-medium">{use.title}</p><p className="text-xs text-muted-foreground">{use.basis.source.version} · {use.submitted ? '依据已固定' : '未登记'}</p></div><Button variant="ghost" onClick={() => onOpen(use.id)}>查看事项<ArrowRight/></Button></div>)}</div></CollapsibleContent></Collapsible></section>}
    </> : !loading && !readError && <div className="space-y-3 py-12 text-center"><h1 className="text-xl font-semibold">暂时无法打开此要求版本</h1><p className="text-muted-foreground">记录不可用；未自动改选其他版本。</p><Button asChild variant="outline"><a href={requirementHref()} onClick={event => navigate(event)}>返回要求列表</a></Button></div>}
    <Sheet open={Boolean(source)} onOpenChange={open => !open && setSourceClause(null)}><SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-full sm:data-[side=right]:max-w-lg" onCloseAutoFocus={event => {if (sourceTrigger.current?.isConnected) {event.preventDefault(); sourceTrigger.current.focus()}}}>
      <div className="pr-12"><SheetHeader><SheetTitle>{source ? `条款 ${source.id} · 核对出处` : '条款出处'}</SheetTitle><SheetDescription>{source ? `${source.source} · ${source.location || '出处待核对'}` : '对照原件核对条款。'}</SheetDescription></SheetHeader></div>
      {source && <div className="space-y-6 py-2 text-sm"><section className="space-y-2"><h2 className="text-sm font-medium">来源摘录</h2><blockquote className="whitespace-pre-wrap break-words border-l pl-3 leading-relaxed">{source.original || '未保存摘录，请阅读要求原件。'}</blockquote></section>{source.original.trim() !== source.text.trim() && <section className="space-y-2"><h2 className="text-sm font-medium">本版本条款</h2><p className="whitespace-pre-wrap break-words leading-relaxed">{source.text || '尚未录入条款。'}</p></section>}</div>}
      <SheetFooter>{current && <RequirementOriginal basis={current.basis} label="查看原件"/>}<SheetClose asChild><Button variant="outline">返回条款</Button></SheetClose></SheetFooter>
    </SheetContent></Sheet>
    <Dialog open={discard} onOpenChange={open => !working.current && setDiscard(open)}><DialogContent><DialogHeader><DialogTitle>放弃这个版本草稿？</DialogTitle><DialogDescription>只移除未确认的草稿。已保存版本和已有事项不变。</DialogDescription></DialogHeader><div className="flex justify-end gap-2"><Button variant="outline" disabled={locked} onClick={() => setDiscard(false)}>继续编辑</Button><Button disabled={locked} onClick={() => {if (working.current || !collection) return; onChange(items => items.flatMap(item => item.id !== collection.id ? [item] : item.versions.length ? [{...item, draft: null}] : [])); select(collection.versions.length ? collection.id : '', collection.versions.at(-1)?.id ?? '', true); setDiscard(false)}}>放弃此草稿</Button></div></DialogContent></Dialog>
  </section>
}
