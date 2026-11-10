// Stitch: projects/13261964020862199279/screens/4f2a97d0c3ac4281831991348b3e11b4.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, ChevronDown, LoaderCircle, Plus, UploadCloud} from 'lucide-react'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle} from '../components/ui/sheet'
import {Textarea} from '../components/ui/textarea'
import {basisConfirmed, basisIssues, clauseIssues, clauseSourceReady, confirmBasis, demoBasis, resolveBasisConflict, type RequirementBasis, type RequirementClause} from './requirements-state'
import {requirementFileAccept, useLocalUploads} from './local-files'
import {SourceReaderButton} from './source-reader'
import {copyForCase, scopeMatches, scopeLabel, sourceContent, type RequirementCollection, type RequirementScope} from './requirement-library-state'
import {receiveRequirement, RuleExtraction} from './rule-extraction'
import {canonical, publishBasis} from './product'
import {isPreview, useAccess} from './access'
import {message} from './remote'
import {RequirementSourceSelect} from './requirement-source-select'

const applicability = {pending: '待确认', apply: '适用', exclude: '不适用'} as const
const separateTitle = (clause: RequirementClause) => clause.title.trim() && !clause.text.trim().startsWith(clause.title.trim())
export type RequirementCatalogRead = {loading: boolean; error: string; reload: () => void}

export function RequirementOriginal({basis, label = '阅读规则原件'}: {basis: RequirementBasis; label?: string}) {
  // An unreadable original cannot be opened; the source line already says so.
  if (!basis.source.localFile && !basis.server || basis.server?.material.status === 'unreadable') return null
  const file = {name: basis.source.filename, revision: basis.source.version || '待确认', sourceVersion: 1, localFile: basis.source.localFile, material: basis.server?.material}
  return <SourceReaderButton file={file} label={label} input={{receiptId: basis.source.version || '要求草稿', scope: basis.source.title, files: [file], evidence: [], clarification: null}}/>
}

export function BasisSummary({basis, compact = false}: {basis: RequirementBasis | null | undefined; compact?: boolean}) {
  if (!basis) return <p className="text-sm text-muted-foreground">本轮没有可读取的审核依据记录。</p>
  return <Collapsible className={`${compact ? '' : 'my-4'} min-w-0 max-w-full`}>
    <CollapsibleTrigger asChild><Button variant="outline" size="sm" className="h-auto max-w-full justify-start whitespace-normal text-left"><BookOpen/><span className="min-w-0 break-words">审核依据<span className={compact ? 'max-sm:sr-only' : ''}> · {basis.source.title} · {basis.source.version}</span></span><ChevronDown className="shrink-0"/></Button></CollapsibleTrigger>
    <CollapsibleContent><div className="space-y-4 pt-4 text-sm">
      {compact && <p className="break-words font-medium sm:hidden">{basis.source.title} · {basis.source.version}</p>}
      <div className="flex flex-wrap items-center gap-3"><RequirementOriginal basis={basis} label="查看原件"/><span className="text-muted-foreground">{basis.clauses.filter(item => item.applicability === 'apply').length} 条适用</span>{basis.confirmed && <span className="text-xs text-muted-foreground">确认人：{basis.confirmed.by} · {new Date(basis.confirmed.at).toLocaleString('zh-CN')}</span>}</div>
      {basis.clauses.map(clause => <article key={clause.id} className="space-y-2 border-t pt-4"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><span className="mr-2 text-muted-foreground">{clause.id}</span>{separateTitle(clause) && <strong>{clause.title}</strong>}</div><Badge variant="outline">{applicability[clause.applicability]}</Badge></div><p className="whitespace-pre-wrap break-words leading-relaxed">{clause.text}</p><p className="break-words text-xs text-muted-foreground">{clause.source} · {clause.location}</p>{clause.reason && <p className="break-words">不适用理由：{clause.reason}</p>}</article>)}
      {basis.conflict.active && <p>冲突裁定：{basis.conflict.choice === 'internal' ? '内部模板 v1' : basis.conflict.choice === 'customer' ? '客户要求 v2' : '待确认'} · {basis.conflict.reason || '理由待记录'}</p>}
    </div></CollapsibleContent>
  </Collapsible>
}

export function Requirements({basis, scopeKey, scope, scopeReady = true, collections, catalog, context, readonly = false, onChange, onConfirm, onBack, backLabel}: {
  basis: RequirementBasis | null; scopeKey: string; scope: RequirementScope; scopeReady?: boolean
  collections: RequirementCollection[]; catalog?: RequirementCatalogRead; context: string; readonly?: boolean; backLabel?: string
  onChange: (basis: RequirementBasis) => void; onConfirm: (basis: RequirementBasis) => void; onBack: () => void
}) {
  const [sourceOpen, setSourceOpen] = useState(false), [showSourceHistory, setShowSourceHistory] = useState(false)
  const [candidate, setCandidate] = useState<RequirementBasis | null>(null), [query, setQuery] = useState('')
  const [editing, setEditing] = useState<string | null>(null), [sourceClause, setSourceClause] = useState<string | null>(null)
  const [publishing, setPublishing] = useState(false), [remoteError, setRemoteError] = useState('')
  const picker = useRef<HTMLInputElement>(null), page = useRef<HTMLElement>(null), sourceTrigger = useRef<HTMLButtonElement>(null)
  const mounted = useRef(true), working = useRef(false), latest = useRef({basis, scopeKey, readonly, onChange, onConfirm})
  latest.current = {basis, scopeKey, readonly, onChange, onConfirm}
  const access = useAccess()
  useEffect(() => {
    mounted.current = true
    const url = new URL(location.href)
    if (url.searchParams.has('basisLayout')) {url.searchParams.delete('basisLayout'); history.replaceState(null, '', url)}
    return () => {mounted.current = false}
  }, [])
  const upload = useLocalUploads(async ([entry]) => {
    const owner = scopeKey
    const receive = (next: RequirementBasis) => {
      if (mounted.current && latest.current.scopeKey === owner && !latest.current.readonly && !working.current) latest.current.onChange({...next, confirmed: null})
    }
    receive(await receiveRequirement(entry, receive))
    if (mounted.current) {setEditing(null); setSourceClause(null)}
  }, scopeKey, requirementFileAccept)
  const locked = readonly || publishing || upload.busy
  const collection = collections.find(item => item.id === basis?.library?.collectionId || item.versions.some(version => version.id === basis?.source.id || version.id === basis?.library?.versionId))
  const selectedVersion = collection?.versions.find(version => version.id === basis?.library?.versionId || version.id === basis?.source.id)
  const latestVersion = collection?.versions.at(-1)
  const issues = basisIssues(basis)
  if (!readonly && basis?.library) {
    if (catalog?.loading) issues.push('正在读取所选规则版本。')
    else if (catalog?.error) issues.push('暂时无法核对所选版本，请重新读取要求目录。')
    else if (!selectedVersion) issues.push('找不到此来源版本，请重新选择可读取的要求来源。')
  }
  if (!readonly && selectedVersion && !scopeMatches(scope, selectedVersion.scope)) issues.push('当前事项不在所选版本的声明范围内，请重新选择适用版本或上传本次要求。')
  const confirmed = basisConfirmed(basis, scopeKey)
  const checked = basis?.clauses.filter(clause => !clauseIssues(clause, basis).length).length ?? 0
  const source = basis?.clauses.find(clause => clause.id === sourceClause)
  const mixedApplicability = basis?.clauses.some(clause => clause.applicability !== 'apply')
  const versions = collections.flatMap(family => (showSourceHistory ? [...family.versions].reverse() : family.versions.slice(-1)).map(version => ({family, version})))
    .filter(({version}) => `${version.basis.source.title} ${scopeLabel(version.scope)} ${version.basis.source.version}`.toLowerCase().includes(query.trim().toLowerCase()))
  function change(next: RequirementBasis) {
    if (mounted.current && !latest.current.readonly && !working.current) {onChange({...next, confirmed: null}); setRemoteError('')}
  }
  function patch(id: string, next: Partial<RequirementClause>) {
    if (basis) change({...basis, clauses: basis.clauses.map(clause => clause.id === id ? {...clause, ...next} : clause)})
  }
  function addClause() {
    if (!basis || locked) return
    const id = `M-${crypto.randomUUID().slice(0, 6)}`
    change({...basis, source: {...basis.source, state: basis.source.origin === 'local' ? 'manual' : basis.source.state}, clauses: [...basis.clauses, {id, title: '', text: '', original: '', source: basis.source.filename, location: '', applicability: 'pending', reason: '', checked: false}]})
    setEditing(id)
  }
  async function confirm() {
    if (!basis || working.current || locked || !scopeReady || issues.length) return
    const snapshot = structuredClone(basis), owner = scopeKey
    working.current = true; setPublishing(true); setRemoteError('')
    try {
      const published = isPreview() ? snapshot : await publishBasis(snapshot)
      const current = latest.current
      if (mounted.current && !current.readonly && current.scopeKey === owner && canonical(current.basis) === canonical(snapshot)) current.onConfirm(confirmBasis(published, owner, access?.principal?.display_name))
    } catch (cause) {if (mounted.current) setRemoteError(message(cause))}
    finally {working.current = false; if (mounted.current) setPublishing(false)}
  }
  function continueChecking() {
    if (!basis) return
    if (basis.library && catalog?.error) {catalog.reload(); return}
    if (basis.library && !selectedVersion || selectedVersion && !scopeMatches(scope, selectedVersion.scope)) {setCandidate(null); setSourceOpen(true); return}
    const missing = basis.clauses.find(clause => clauseIssues(clause, basis).length)
    let setup = ''
    if (!basis.source.title.trim() || !basis.source.version.trim()) setup = 'metadata'
    else if (['unparsed', 'failed'].includes(basis.source.state)) setup = basis.server ? 'extraction' : 'metadata'
    else if (!basis.clauses.length) setup = basis.source.origin === 'local' && !basis.server ? 'metadata' : 'clauses'
    else if (!missing && basis.conflict.active) setup = 'conflict'
    const target = setup ? page.current?.querySelector<HTMLElement>(`[data-basis-setup="${setup}"]`) : missing ? document.getElementById(`basis-clause-${missing.id}`) : page.current?.querySelector<HTMLElement>('[aria-label^="条款 "]')
    target?.scrollIntoView({block: 'center'})
    if (missing && !clauseSourceReady(missing, basis)) {
      if (basis.server) {setSourceClause(missing.id); return}
      setEditing(missing.id)
    }
    requestAnimationFrame(() => {
      const control = target?.querySelector<HTMLElement>('[data-basis-missing="true"]:not(:disabled)') ?? target?.querySelector<HTMLElement>('input:not(:disabled),textarea:not(:disabled),select:not(:disabled),button:not(:disabled)') ?? target
      control?.focus({preventScroll: true})
    })
  }
  return <section ref={page} className="min-w-0 space-y-6 pb-8 text-sm">
    <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft/>{backLabel || '返回审核准备'}</Button>
    <header className="space-y-2"><div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold tracking-tight">{readonly ? '本次审核依据' : '确认本次审核依据'}</h1>{(readonly || confirmed) && <Badge variant="outline">{readonly ? '只读' : '已确认'}</Badge>}</div><p className="break-words text-muted-foreground">{context}</p></header>
    <input hidden ref={picker} type="file" aria-label="规则文件选择器" accept={requirementFileAccept} disabled={locked} onChange={event => {void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    {basis ? <section className="flex min-w-0 flex-wrap items-start justify-between gap-4 border-b pb-5" aria-label="本次要求来源">
      <div className="min-w-0 space-y-2"><div className="flex flex-wrap items-center gap-2"><h2 className="break-words text-base font-semibold">{basis.source.title || '要求名称待补充'}</h2><Badge variant="outline">{basis.source.version || '版本待确认'}</Badge><span className="text-muted-foreground">{basis.clauses.length} 条</span></div><p className="break-words text-xs text-muted-foreground">{basis.source.filename}</p></div>
      <div className="flex flex-wrap items-center gap-2"><RequirementOriginal basis={basis} label="查看原件"/>{!readonly && <><Button variant="outline" size="sm" disabled={locked} onClick={() => {setCandidate(null); setQuery(''); setSourceOpen(true)}}>更换来源</Button><Button variant="ghost" size="sm" disabled={locked} onClick={() => picker.current?.click()}><UploadCloud/>上传新要求</Button></>}</div>
    </section> : <section className="space-y-4 border-y py-8"><h2 className="text-base font-semibold">选择本次使用的审核规则</h2><div className="flex flex-wrap gap-3"><Button variant="outline" disabled={locked} onClick={() => {setCandidate(null); setQuery(''); setSourceOpen(true)}}><BookOpen/>选择已有版本</Button><Button variant="outline" disabled={locked} onClick={() => picker.current?.click()}><UploadCloud/>上传规则文件</Button></div></section>}
    {upload.busy && <p role="status" className="flex items-center gap-2 text-muted-foreground"><LoaderCircle className="size-4 animate-spin motion-reduce:animate-none"/>正在保存规则原件…</p>}
    {(upload.error || remoteError) && <Alert variant="destructive"><AlertDescription>{upload.error || remoteError}</AlertDescription></Alert>}
    {basis && <>
      <div data-basis-setup="extraction"><RuleExtraction basis={basis} readonly={locked} onChange={change}/></div>
      {basis.library && basis.library.content !== sourceContent(basis) && <p className="text-muted-foreground">本次条款有调整，不覆盖来源版本。</p>}
      {latestVersion && latestVersion.id !== basis.source.id && <p className="text-muted-foreground">已有版本 {latestVersion.basis.source.version}；本次仍使用 {basis.source.version}，未自动替换。</p>}
      {!readonly && basis.source.origin === 'local' && (!basis.server || !basis.source.version.trim() || !basis.source.title.trim()) && <section data-basis-setup="metadata" className="space-y-4" aria-label="要求登记信息">
        {!basis.server && <p className="text-muted-foreground">{basis.source.state === 'failed' ? '要求提取未完成，可按原件补录。' : '此预览不提取真实条款，请按原件录入。'}</p>}
        <div className="grid gap-3 sm:grid-cols-2"><label className="grid gap-1.5">要求名称<Input data-basis-missing={!basis.source.title.trim()} disabled={locked} value={basis.source.title} onChange={event => change({...basis, source: {...basis.source, title: event.target.value}})}/></label><label className="grid gap-1.5">版本<Input data-basis-missing={!basis.source.version.trim()} disabled={locked} value={basis.source.version} onChange={event => change({...basis, source: {...basis.source, version: event.target.value}})}/></label></div>
        {!basis.server && <div className="flex flex-wrap gap-2"><Button data-basis-missing={!basis.clauses.length} variant="outline" disabled={locked} onClick={addClause}><Plus/>录入条款</Button>{isPreview() && basis.source.state === 'unparsed' && <Button variant="ghost" disabled={locked} onClick={() => change({...basis, source: {...basis.source, state: 'failed'}})}>模拟提取失败</Button>}</div>}
      </section>}
      {basis.conflict.active && <section data-basis-setup="conflict" className="space-y-3 border-l-2 pl-4" aria-label="公差冲突裁定"><h2 className="text-base font-semibold">{readonly ? '本轮公差依据' : '确认公差依据'}</h2>{readonly ? <><p>{basis.conflict.choice === 'internal' ? '内部模板 v1 · ±0.05 mm' : '客户要求 v2 · ±0.10 mm'}</p><p className="whitespace-pre-wrap break-words">{basis.conflict.reason}</p></> : <><RadioGroup value={basis.conflict.choice} disabled={locked} onValueChange={value => change(resolveBasisConflict(basis, value as RequirementBasis['conflict']['choice']))} aria-label="本次采用的要求"><div className="flex flex-wrap gap-x-5">{[{id: 'pending', label: '待确认'}, {id: 'customer', label: '客户 v2 · ±0.10 mm'}, {id: 'internal', label: '内部 v1 · ±0.05 mm'}].map(item => <label key={item.id} className="flex min-h-9 items-center gap-2 max-md:min-h-11"><RadioGroupItem value={item.id}/>{item.label}</label>)}</div></RadioGroup><label className="grid gap-1.5">裁定依据与理由<Textarea disabled={locked} value={basis.conflict.reason} onChange={event => change({...basis, conflict: {...basis.conflict, reason: event.target.value}})}/></label></>}</section>}
      <section data-basis-setup="clauses" aria-label="本次审核条款">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3"><h2 className="text-base font-semibold">审核条款 <span className="ml-2 text-sm font-normal text-muted-foreground">{readonly ? `${basis.clauses.filter(clause => clause.applicability === 'apply').length} 条适用` : `${checked}/${basis.clauses.length} 条已核对`}</span></h2>{!readonly && basis.clauses.some(clause => clause.applicability === 'pending') && <Button variant="outline" size="sm" disabled={locked} onClick={() => change({...basis, clauses: basis.clauses.map(clause => clause.applicability === 'pending' ? {...clause, applicability: 'apply'} : clause)})}>将待确认条款设为适用</Button>}</div>
        {basis.clauses.map((clause, index) => <article id={`basis-clause-${clause.id}`} key={clause.id} tabIndex={-1} aria-label={`条款 ${clause.id}`} className="grid min-w-0 scroll-mt-20 grid-cols-[1.5rem_minmax(0,1fr)] gap-3 border-b py-5 outline-none focus-visible:ring-2 focus-visible:ring-ring sm:gap-4">
          <span className="pt-1 text-xs tabular-nums text-muted-foreground">{String(index + 1).padStart(2, '0')}</span>
          <div className="min-w-0 space-y-3">
            {editing === clause.id && !readonly ? <div className="grid gap-3"><label className="grid gap-1.5">条款名称<Input data-basis-missing={!clause.title.trim()} disabled={locked} value={clause.title} onChange={event => patch(clause.id, {title: event.target.value, checked: false})}/></label><label className="grid gap-1.5">条款正文<Textarea data-basis-missing={!clause.text.trim()} disabled={locked} value={clause.text} onChange={event => patch(clause.id, {text: event.target.value, checked: false})}/></label><div className="grid gap-3 sm:grid-cols-2"><label className="grid gap-1.5">来源文件<Input data-basis-missing={!clause.source.trim()} disabled={locked} value={clause.source} onChange={event => patch(clause.id, {source: event.target.value, checked: false})}/></label><label className="grid gap-1.5">页码或单元格位置<Input data-basis-missing={!clause.location.trim() || clause.location.includes('待确认')} disabled={locked} value={clause.location} placeholder="如第 3 页 §2，或工作表 B4" onChange={event => patch(clause.id, {location: event.target.value, checked: false})}/></label></div><Button variant="outline" size="sm" className="w-fit" onClick={() => setEditing(null)}>完成编辑</Button></div> : <>{(separateTitle(clause) || readonly && mixedApplicability) && <div className="flex flex-wrap items-start justify-between gap-2">{separateTitle(clause) && <h3 className="break-words font-semibold">{clause.title}</h3>}{readonly && mixedApplicability && <Badge variant="outline">{applicability[clause.applicability]}</Badge>}</div>}<p className="whitespace-pre-wrap break-words leading-relaxed">{clause.text || '条款正文待录入'}</p><p className="break-words text-xs text-muted-foreground">{clause.source === basis.source.filename || clause.source === basis.source.title ? '' : `${clause.source} · `}{clause.location || '出处待补充'}</p></>}
            <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2">
              {!readonly && <><RadioGroup value={clause.applicability} disabled={locked} onValueChange={value => patch(clause.id, {applicability: value as RequirementClause['applicability']})} aria-label={`${clause.id} 本次适用性`} className="w-auto"><div className="flex flex-wrap gap-x-4">{Object.entries(applicability).map(([value, label]) => <label key={value} className="flex min-h-9 items-center gap-2 max-md:min-h-11"><RadioGroupItem data-basis-missing={clause.applicability === 'pending' && value === 'pending'} value={value}/>{label}</label>)}</div></RadioGroup><label className="flex min-h-9 items-center gap-2 max-md:min-h-11"><Checkbox data-basis-missing={!clause.checked || !clauseSourceReady(clause, basis)} aria-label={`${clause.id} 已核对条款与出处`} disabled={locked || !clauseSourceReady(clause, basis)} checked={clause.checked && clauseSourceReady(clause, basis)} onCheckedChange={value => patch(clause.id, {checked: value === true})}/>原文已核对</label></>}
              <div className="flex flex-wrap items-center gap-2"><Button variant="ghost" size="sm" onClick={event => {sourceTrigger.current = event.currentTarget; setSourceClause(clause.id)}}><BookOpen/>核对出处</Button>{!readonly && editing !== clause.id && <Button variant="ghost" size="sm" disabled={locked} onClick={() => setEditing(clause.id)}>编辑</Button>}</div>
            </div>
            {clause.applicability === 'exclude' && (readonly ? <p className="whitespace-pre-wrap break-words">不适用理由：{clause.reason}</p> : <label className="grid gap-1.5">不适用理由<Textarea data-basis-missing={!clause.reason.trim()} disabled={locked} value={clause.reason} onChange={event => patch(clause.id, {reason: event.target.value})} placeholder="说明本事项为何不适用，并提供依据"/></label>)}
            {!readonly && clause.id.startsWith('M-') && <Button variant="ghost" size="sm" disabled={locked} onClick={() => change({...basis, clauses: basis.clauses.filter(item => item.id !== clause.id)})}>移除此未提交条款</Button>}
          </div>
        </article>)}
        {!readonly && (basis.source.origin === 'demo' || basis.server) && <Button variant="ghost" size="sm" className="mt-3" disabled={locked} onClick={addClause}><Plus/>补充要求</Button>}
      </section>
      <footer className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 border-t bg-background py-4">
        <div className="min-w-0 flex-1 space-y-1">{readonly ? null : <><p role="status" className="break-words text-muted-foreground">{publishing ? '正在保存本次审核依据…' : !scopeReady ? '先补全事项范围，再确认本次适用性。' : issues[0] || '条款与适用范围已核对。'}</p>{issues.length > 1 && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="h-auto">查看 {issues.length} 项待处理<ChevronDown/></Button></CollapsibleTrigger><CollapsibleContent><ul className="max-h-40 space-y-1 overflow-auto py-2">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></CollapsibleContent></Collapsible>}</> }</div>
        {readonly ? <Button variant="outline" onClick={onBack}>{backLabel || '返回审核准备'}</Button> : !scopeReady ? <Button onClick={onBack}>补全事项范围<ArrowRight/></Button> : issues.length && !publishing ? <Button variant="outline" disabled={upload.busy || catalog?.loading} onClick={continueChecking}>{basis.library && catalog?.error ? '重新读取要求目录' : basis.library && !selectedVersion || selectedVersion && !scopeMatches(scope, selectedVersion.scope) ? '更换规则版本' : '继续核对'}<ArrowRight/></Button> : <Button disabled={locked || issues.length > 0} onClick={() => void confirm()}>{publishing && <LoaderCircle className="animate-spin motion-reduce:animate-none"/>}确认本次要求<ArrowRight/></Button>}
      </footer>
    </>}
    <Dialog open={sourceOpen} onOpenChange={setSourceOpen}><DialogContent className="max-h-[calc(100dvh-32px)] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>选择审核规则</DialogTitle><DialogDescription>{!scopeReady ? '事项范围尚未完整。先补全范围，才能确认有限定条件的要求是否适用。' : basis ? '使用新来源会替换当前未提交的要求草稿。' : '选择本次适用的已发布版本。'}</DialogDescription></DialogHeader>
      <Input type="search" maxLength={200} aria-label="搜索规则版本" placeholder="搜索要求、客户或版本" value={query} onChange={event => setQuery(event.target.value)}/>
      <label className="flex min-h-9 items-center gap-2 max-md:min-h-11"><Checkbox checked={showSourceHistory} onCheckedChange={value => setShowSourceHistory(value === true)}/>显示历史版本</label>
      {catalog?.loading && <ReadSkeleton label="正在读取要求目录…" kind={versions.length ? 'inline' : 'list'}/>}
      {catalog?.error && <Alert variant="destructive"><AlertDescription>{catalog.error}<div className="mt-2"><Button variant="outline" size="sm" onClick={catalog.reload}>重新读取要求目录</Button></div></AlertDescription></Alert>}
      <RadioGroup value={candidate?.library?.versionId || (candidate?.conflict.active ? 'demo-conflict' : '')} onValueChange={id => {if (id === 'demo-conflict') {setCandidate(demoBasis(true)); return} const selected = versions.find(item => item.version.id === id); if (selected && scopeMatches(scope, selected.version.scope)) setCandidate(copyForCase(selected.family, selected.version))}} aria-label="审核规则版本">
        {versions.map(({version}) => <label key={version.id} className="flex items-start gap-3 border-b py-3 text-sm"><RadioGroupItem value={version.id} disabled={locked || catalog?.loading || Boolean(catalog?.error) || !scopeMatches(scope, version.scope)} className="mt-1"/><span className="min-w-0 space-y-1 text-left"><span className="block break-words font-medium">{version.basis.source.title}</span><span className="block break-words text-muted-foreground">{scopeLabel(version.scope)}</span><span className="block text-xs text-muted-foreground">{version.basis.source.version} · {version.basis.clauses.length} 条{!scopeMatches(scope, version.scope) ? scopeReady ? ' · 范围不符' : ' · 范围待确认' : ''}{showSourceHistory && version.at ? ` · ${new Date(version.at).toLocaleString('zh-CN')}` : ''}</span></span></label>)}
        {isPreview() && !query && scopeMatches(scope, {customer: '北辰汽车', part: '支架 P-204', revision: 'B'}) && <label className="flex items-start gap-3 py-3 text-sm"><RadioGroupItem value="demo-conflict"/><span><span className="block font-medium">冲突示例：客户 v2 与内部模板 v1</span><span className="text-muted-foreground">同一孔径公差不同，需裁定来源。</span></span></label>}
      </RadioGroup>
      {!catalog?.loading && !catalog?.error && !versions.length && <p className="text-sm text-muted-foreground">{query ? '没有匹配的规则版本。' : '还没有已发布的要求，可上传本次要求。'}</p>}
      <div className="flex flex-wrap justify-between gap-3">{!scopeReady ? <Button variant="outline" onClick={() => {setSourceOpen(false); onBack()}}>补全事项范围</Button> : <Button variant="ghost" disabled={locked} onClick={() => {setSourceOpen(false); picker.current?.click()}}>上传本次要求</Button>}<Button disabled={!candidate || locked || catalog?.loading || Boolean(catalog?.error)} onClick={() => {if (!candidate) return; change(candidate); setEditing(null); setSourceOpen(false)}}>使用此版本<ArrowRight/></Button></div>
    </DialogContent></Dialog>
    <Sheet open={sourceClause !== null} onOpenChange={open => !open && setSourceClause(null)}><SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-full sm:data-[side=right]:max-w-160" onCloseAutoFocus={event => {if (sourceTrigger.current?.isConnected) {event.preventDefault(); sourceTrigger.current.focus()}}}>
      <div className="pr-8"><SheetHeader><SheetTitle>核对条款 {source?.id} 的出处</SheetTitle><SheetDescription>{source?.source} · {source?.location || '位置待补充'}</SheetDescription></SheetHeader></div>
      {source && basis && <div className="space-y-5 py-2 text-sm leading-relaxed">{!readonly && <RequirementSourceSelect basis={basis} clause={source} disabled={locked} onChange={clause => patch(source.id, clause)}/>}<section className="space-y-2"><h3 className="font-medium">来源摘录</h3><blockquote className="whitespace-pre-wrap break-words border-l-2 pl-4">{clauseSourceReady(source, basis) ? source.original : '尚未关联原文位置。请选择对应原文，并对照原件核对。'}</blockquote></section>{source.text !== source.original && <section className="space-y-2"><h3 className="font-medium">本次条款</h3><p className="whitespace-pre-wrap break-words">{source.text || '正文待录入'}</p></section>}{basis.source.origin === 'demo' && <p className="text-xs text-muted-foreground">这是固定示例的来源片段。</p>}</div>}
      <div className="mt-auto border-t pt-4"><SheetFooter>{basis && <RequirementOriginal basis={basis} label="查看规则原件"/>}{source && !readonly && <Button disabled={locked || !clauseSourceReady(source, basis ?? undefined)} onClick={() => {patch(source.id, {checked: true}); setSourceClause(null)}}>标记已核对并返回</Button>}<SheetClose asChild><Button variant="outline">返回条款</Button></SheetClose></SheetFooter></div>
    </SheetContent></Sheet>
  </section>
}
