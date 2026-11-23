import {Fragment, useContext, useEffect, useId, useRef, useState, type ReactNode} from 'react'
import {iconArrowRight, iconArrowTrendDown, iconArrowTrendUp, iconCircleInfo, iconEye, iconFileLines, iconMagnifyingGlass, iconPen} from '../components/icons'
import {Icon} from '../components/ui/icon'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Disclosure} from '../components/disclosure'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle} from '../components/ui/sheet'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {HighlightText} from '../components/highlight-text'
import {FileName} from '../journey/file-name'
import {clauseLabel, clauseObservation, frozenResultError, pendingNote, programNotice, sameSnapshot, type ReviewFile} from './result-projection'
import {displayFields, fileDisplayValues, validDisplayInfo} from './display-info-contract'
import {RecordedReview, frozenOriginal} from './recorded-review'
import {HistoricalFindings} from './historical-findings'
import {ReviewEditorContext} from './review-editor'
import {readReviewHistory} from './native-adapter'
import {ResizableHandle, ResizablePanel, ResizablePanelGroup} from '../components/ui/resizable'
import {filterChecks, nextUnreviewed, reviewable, reviewed} from './review-navigation'
import {diffRounds} from './round-diff'
import {ReviewHintDetails} from './review-hints'
import {ReviewDocumentPairs} from './review-document-pairs'
import {readAuditRoute, writeAuditAddress} from './routes'
import {request} from '../api'
import {documentTypeName, type CustomerCatalog} from './customer-register'
import {checkStatusLabel, conclusionLabel, executionLabel, reportLabel, resultLabel, resultTone, uncoveredLabel, type ResultTone} from './status-labels'

type File = ReviewFile
type Evaluation = components['schemas']['AuditClauseEvaluation']
type ReviewCitation = components['schemas']['ReviewCitation']
type SelectedSource = {kind: 'machine'; reference: AuditEvidence} | {kind: 'review'; reference: ReviewCitation}
export type AuditEvidence = components['schemas']['AuditEvidenceReference']
export type OriginalRenderer = (file: components['schemas']['FileVersion'], evidence: AuditEvidence | null, roundId?: string) => ReactNode
function checkTone(check: Evaluation): ResultTone {
  if (check.execution === 'queued' || check.execution === 'running') return 'outline'
  if (check.execution === 'failed') return resultTone('ERR')
  if (check.coverage !== 'executed') return resultTone('uncovered')
  return check.finding ? resultTone(check.finding.status) : 'warning'
}

const verdictAccent: Partial<Record<ResultTone, string>> = {destructive: 'border-l-destructive', warning: 'border-l-warning', success: 'border-l-success'}

export function useMinWidth(width: number) {
  const [matches, setMatches] = useState(() => window.matchMedia(`(min-width: ${width}px)`).matches)
  useEffect(() => {
    const media = window.matchMedia(`(min-width: ${width}px)`)
    const update = () => setMatches(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [width])
  return matches
}

/** The customer register, read once per page load; until it arrives (or if it cannot), a type shows as recorded. */
let registerRead: Promise<CustomerCatalog> | null = null
export function useCustomerCatalog() {
  const [catalog, setCatalog] = useState<CustomerCatalog | null>(null)
  useEffect(() => {
    let active = true
    registerRead ??= request<CustomerCatalog>('/audit-customers')
    registerRead.then(value => {if (active) setCatalog(value)}, () => {registerRead = null})
    return () => {active = false}
  }, [])
  return catalog
}

/** Document type names from the customer register; a type shows as recorded until the register is read. */
export function useDocumentTypeLabel() {
  const catalog = useCustomerCatalog()
  return (type: string) => documentTypeName(catalog?.type_names, type)
}

/** What recognition read before the displayed values were corrected; the corrected values themselves sit in the file overview. */
function RecognizedValues({file}: {file: File}) {
  if (!(validDisplayInfo(file.display_info) && file.display_info.revision > 0)) return null
  return <section aria-label="原始识别信息" className="space-y-2">
    <h3 className="text-sm font-medium">原始识别信息</h3>
    <p className="text-xs text-muted-foreground">页面显示的信息已更正，以下是识别时的原值。</p>
    <dl className="grid grid-cols-2 gap-3 text-sm">{displayFields.map(([field, label]) => <div key={field}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-words">{file.match?.metadata[field] || '—'}</dd></div>)}</dl>
  </section>
}

/** P5 and historical readers share the same frozen results, evidence and review actions. `onReport` adds a report entry to the file header only where the page has none of its own (the archive history). */
export function AuditReviewContent({file, roundId, taskId, frozenRule = file.match?.rule, frozenFileVersionIds, originals, renderOriginal, onReport, onEditDisplay, showFileTitle = true, heading, previous, facts = [], onOpenTask, editable = true}: {facts?: {label: string; value: string}[]; file: File; roundId: string; taskId?: string; frozenRule?: components['schemas']['BoundRule'] | null; frozenFileVersionIds: string[]; originals: components['schemas']['AuditArchiveOriginal'][]; renderOriginal?: OriginalRenderer; onReport?: () => void; onEditDisplay?: () => void; heading?: ReactNode; showFileTitle?: boolean; previous?: {label: string; checks: Evaluation[]} | null; onOpenTask?: () => void; editable?: boolean}) {
  // Only the task's creator records reviews; everyone else in the enterprise reads them.
  const editor = useContext(ReviewEditorContext), reviewEditor = editable ? editor : null
  const filterId = useId()
  const [clause, setClause] = useState(() => new URL(location.href).searchParams.get('clause') || ''), [chosenSource, setSource] = useState<SelectedSource | null>(null)
  const [focus, setFocus] = useState(() => new URL(location.href).searchParams.get('focus') === 'review')
  const [originalId, setOriginalId] = useState(file.file.id)
  const editorHost = useRef<HTMLDivElement>(null), currentEditor = useRef(reviewEditor); currentEditor.current = reviewEditor
  // The original preview docks beside the list; a location in the basis column or 查看原件 opens it.
  const [originalOpen, setOriginalOpen] = useState(false), [pane, setPane] = useState('original')
  const [query, setQuery] = useState(''), [filter, setFilter] = useState(() => new URL(location.href).searchParams.get('checkFilter') || 'all'), [infoOpen, setInfoOpen] = useState(false)
  const trigger = useRef<HTMLElement | null>(null), linkedRow = useRef<HTMLTableRowElement>(null)
  const [handledHints, setHandledHints] = useState<components['schemas']['ReviewHint'][]>([])
  const audit = file.audit ? {...file.audit, checks: file.audit.checks.map(row => row.finding?.review_hint && handledHints.some(hint => sameSnapshot(hint, row.finding?.review_hint)) ? {...row, finding: {...row.finding, review_hint: undefined}} : row)} : file.audit, check = audit?.checks.find(row => row.rule_item.id === clause) ?? audit?.checks[0]
  const clauses = audit?.checks.map(row => row.rule_item) ?? []
  const issue = frozenResultError(roundId, file, frozenFileVersionIds, frozenRule)
  const review = check?.review !== undefined ? check.review : check?.finding?.review
  const chosen = chosenSource && (chosenSource.kind === 'machine' ? check?.evidence : review?.citations)?.some(row => sameSnapshot(row, chosenSource.reference)) ? chosenSource : null
  const citation = chosen?.kind === 'review' ? chosen.reference : null
  const authorizedOriginal = (id: string) => frozenOriginal(originals, frozenFileVersionIds, id)
  const source = chosen?.kind === 'machine' ? chosen.reference : null
  const original = authorizedOriginal(chosen?.reference.file_version_id || originalId)
  // Several originals side by side only for a clause the person picked.
  const pairedVersions = check && check.rule_item.id === clause ? [...new Set(check.evidence.map(item => item.file_version_id))] : []
  const rule = audit?.rule || frozenRule
  const diff = previous && audit?.execution === 'completed' && !issue ? diffRounds(audit.checks, previous.checks) : null
  const searched = filterChecks(audit?.checks || [], query, 'all')
  const filtered = filter === 'added' || filter === 'resolved' ? searched.filter(row => (diff ? diff[filter] : audit?.checks || []).includes(row)) : filterChecks(audit?.checks || [], query, filter)
  const filterCount = (value: string) => filterChecks(audit?.checks || [], '', value).length
  const at = audit?.checks.findIndex(row => row.rule_item.id === check?.rule_item.id) ?? -1
  const next = nextUnreviewed(audit?.checks || [], check?.rule_item.id || '')
  function chooseFilter(value: string) {
    setFilter(value)
    const url = new URL(location.href); url.searchParams.set('checkFilter', value); writeAuditAddress(url)
  }
  function navigateClause(id: string, focused = focus) {
    setClause(id); setSource(null); setFocus(focused)
    const url = new URL(location.href); url.searchParams.set('clause', id)
    if (focused) url.searchParams.set('focus', 'review'); else url.searchParams.delete('focus')
    writeAuditAddress(url)
  }
  useEffect(() => {
    const itemKey = check?.review_item_key
    return () => {if (itemKey) currentEditor.current?.release(roundId, itemKey)}
  }, [focus, check?.review_item_key, roundId])
  useEffect(() => {
    const restore = () => {
      const url = new URL(location.href), route = readAuditRoute(url)
      if (taskId && (route.surface !== 'audit' || route.taskId !== taskId || url.searchParams.get('file') !== file.file.id)) return
      setClause(url.searchParams.get('clause') || ''); setFocus(url.searchParams.get('focus') === 'review'); setFilter(url.searchParams.get('checkFilter') || (file.audit && filterChecks(file.audit.checks, '', 'attention').length ? 'attention' : 'all'))
    }
    window.addEventListener('popstate', restore); return () => window.removeEventListener('popstate', restore)
  }, [taskId, file.file.id])
  // A link to one clause (from the overview or a review suggestion) brings its row into view once.
  useEffect(() => {linkedRow.current?.scrollIntoView({block: 'center'})}, [])
  /** Opens the frozen original at one evidence location, or the file itself when there is none. */
  function openOriginal(row: Evaluation | null, evidence: AuditEvidence | null) {
    if (!originalOpen) trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (row && row.rule_item.id !== clause) navigateClause(row.rule_item.id)
    setSource(evidence ? {kind: 'machine', reference: evidence} : null); setOriginalId(evidence?.file_version_id || file.file.id)
    setPane('original'); setOriginalOpen(true)
  }
  const locationText = (item: AuditEvidence) => (item.label ? '证据位置：' + item.label : item.location_gap || '未提供具体位置，可对照本轮原件。') + (item.locator && item.rendition === 'derived' ? ' · 位置来自转换后的内容，请对照原件确认。' : '')
  const originalPanel = (heading: boolean) => <section aria-label="原件预览" data-file-version={original?.file_version_id} className="min-w-0 space-y-4">
    {heading && <h2 className="font-semibold">原件对照</h2>}
    {original ? <><FileName name={original.file?.original_filename || '该次证据原件'}/>{source && <div className="space-y-2 text-sm text-muted-foreground"><p>{locationText(source)}</p>{source.locator && source.location_gap && <p>{source.location_gap}</p>}</div>}{citation && <section aria-label="人工复核引用" className="space-y-2 text-sm"><h3 className="font-medium">人工记录位置</h3><p className="whitespace-pre-wrap">{citation.location}</p><p className="whitespace-pre-wrap text-muted-foreground">{citation.excerpt}</p></section>}{!original.file ? <Alert><AlertTitle>本次原件不可访问</AlertTitle><AlertDescription>暂时无法读取此原件。</AlertDescription></Alert> : renderOriginal ? renderOriginal(original.file, source, roundId) : <Alert><AlertTitle>原件暂不可预览</AlertTitle><AlertDescription>请下载原件查看。</AlertDescription></Alert>}</> : <Alert variant="destructive"><AlertTitle>不能读取此证据原件</AlertTitle><AlertDescription>此证据不在本次审核的文件中。</AlertDescription></Alert>}
  </section>
  function showComparison() {
    if (!originalOpen) trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setPane('comparison'); setOriginalOpen(true)
  }
  const comparison = <section aria-label="关联原件对照" className="space-y-4">
    <p className="text-sm" title={check?.rule_item.id}>同一条款：{check && clauseLabel(clauses, check.rule_item.id)}</p>
    <div className="grid grid-cols-2 gap-6">{pairedVersions.map(version => {
      const target = authorizedOriginal(version)
      const evidence = check!.evidence.filter(item => item.file_version_id === version)
      const name = target?.file?.original_filename || '本轮证据原件'
      return <section key={version} aria-label={'关联原件 ' + name} data-file-version={version} className="min-w-0 space-y-3">
        <h3 className="font-semibold"><FileName name={name}/></h3>
        <div className="space-y-2 text-sm">{evidence.map((item, index) => <div key={index} className="space-y-1"><p className="text-muted-foreground">{locationText(item)}</p>{item.locator && item.location_gap && <p className="text-muted-foreground">{item.location_gap}</p>}<p className="whitespace-pre-wrap">{item.excerpt || '未提供摘录。'}</p></div>)}</div>
        {!target ? <Alert variant="destructive"><AlertTitle>不能读取此证据原件</AlertTitle><AlertDescription>此证据不在本次审核的文件中。</AlertDescription></Alert> : !target.file ? <Alert><AlertTitle>本次原件不可访问</AlertTitle><AlertDescription>暂时无法读取此原件。</AlertDescription></Alert> : renderOriginal ? renderOriginal(target.file, evidence[0], roundId) : <Alert><AlertTitle>原件暂不可预览</AlertTitle><AlertDescription>请下载原件查看。</AlertDescription></Alert>}
      </section>
    })}</div>
  </section>
  /** The reason, its scope and every evidence location, read in the row itself; a location opens the original there. */
  const basisCell = (row: Evaluation) => {
    const observation = clauseObservation(row)
    return <div className="space-y-1.5">
      <p className="whitespace-pre-wrap break-words"><HighlightText text={observation} query={query}/></p>
      {row.scope && (row.scope.unknown > 0 || row.scope.checked < row.scope.total) && <p className="text-xs text-muted-foreground">检查范围：已查 {row.scope.checked}/{row.scope.total}，未知 {row.scope.unknown}。</p>}
      {/* Places the checker program read from the original are its own reading; only other evidence asks to be checked. */}
      {row.finding && row.evidence.length > 0 && row.verification_scope !== 'checker_execution' && <p className="text-xs text-warning">证据未经核实，请对照原件确认。</p>}
      {row.evidence.length > 0 && <ul aria-label="证据位置" className="space-y-1 text-xs">{row.evidence.map((item, index) => {
        const target = authorizedOriginal(item.file_version_id)
        const name = item.file_version_id !== file.file.id ? target?.file?.original_filename || '本轮证据原件' : ''
        const excerpt = item.excerpt || ''
        return <li key={item.file_version_id + '-' + index} data-file-version={item.file_version_id} className="min-w-0 break-words">
          {name && <span className="text-muted-foreground">{name} · </span>}
          {!item.label ? <span className="text-muted-foreground">无法定位：{item.location_gap || '未提供具体位置，请对照原件。'}</span> : target ? <Button variant="link" size="xs" className="h-auto whitespace-normal text-left" onClick={() => openOriginal(row, item)}>{item.label}</Button> : <span>{item.label}<span className="text-muted-foreground">（原件不在本次审核中，无法打开）</span></span>}
          {item.locator && item.rendition === 'derived' && <span className="text-muted-foreground">（位置来自转换后的内容，请对照原件确认）</span>}
          {item.locator && item.location_gap && <span className="text-muted-foreground">（{item.location_gap}）</span>}
          {excerpt && excerpt !== observation && <span className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-muted-foreground" title={excerpt}>{excerpt}</span>}
        </li>
      })}</ul>}
      {/* A checker finding without a place is one about something absent: it leaves the line blank. Other findings ask to be checked. */}
      {row.execution === 'completed' && row.coverage === 'executed' && !row.evidence.length && row.verification_scope !== 'checker_execution' && <p className="text-xs text-muted-foreground">未提供来源证据，请核对原件。</p>}
    </div>
  }
  const basis = check && <article key={check.rule_item.id} aria-label="条款判定依据" className="flex flex-col gap-6 animate-in fade-in-0 duration-200 motion-reduce:animate-none">
    {/* The verdict and its reason lead; everything else supports them. */}
    <section className={`space-y-2 rounded-lg border-l-4 bg-muted/40 p-4 ${verdictAccent[checkTone(check)] ?? 'border-l-border'}`}><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-medium text-muted-foreground">判定理由</h3><Badge shape="square" variant={checkTone(check)}>{checkStatusLabel(check)}</Badge></div><p className="whitespace-pre-wrap text-base leading-relaxed">{clauseObservation(check)}</p>{check.scope && (check.scope.unknown > 0 || check.scope.checked < check.scope.total) && <p className="text-xs text-muted-foreground">检查范围：已查 {check.scope.checked}/{check.scope.total}，未知 {check.scope.unknown}。</p>}</section>
    {check.finding?.review_hint && <ReviewHintDetails key={`${roundId}:${check.finding.item_key}`} roundId={roundId} hint={check.finding.review_hint} onHandled={() => setHandledHints(hints => [...hints, check.finding!.review_hint!])}/>}
    {/* A rule that only repeats the title says nothing new. */}
    {check.rule_item.rule_to_check.trim() !== check.rule_item.category.trim() && <section className="space-y-2"><h3 className="text-xs font-medium text-muted-foreground">审核规则原文</h3><p className="whitespace-pre-wrap border-l-2 pl-4 text-sm leading-relaxed">{check.rule_item.rule_to_check}</p></section>}
    <section className="space-y-3"><h3 className="text-xs font-medium text-muted-foreground">证据</h3>{pairedVersions.length > 1 && <Button variant="outline" onClick={showComparison}><Icon icon={iconEye}/>同时核对关联原件</Button>}{check.evidence.length ? check.evidence.map((evidence, index) => {
      const target = authorizedOriginal(evidence.file_version_id)
      return <div key={evidence.file_version_id + '-' + index} data-file-version={evidence.file_version_id} className="space-y-3 border-t pt-3">
        <FileName name={target?.file?.original_filename || '本轮证据原件'}/>
        {evidence.excerpt === clauseObservation(check) ? null : evidence.excerpt ? <p className="whitespace-pre-wrap text-sm">{evidence.excerpt}</p> : <p className="text-sm text-muted-foreground">{evidence.location_gap || '未提供具体位置，可对照本轮原件。'}</p>}
        {evidence.location_gap && evidence.excerpt && <p className="text-xs text-muted-foreground">{evidence.location_gap}</p>}
        <Button variant="outline" size="sm" disabled={!target} aria-label={'核对 ' + (target?.file?.original_filename || '不在本轮的原件')} onClick={() => setSource({kind: 'machine', reference: evidence})}><Icon icon={iconEye}/>{target ? '打开原件' : '原件不在本次审核中'}</Button>
      </div>
    }) : <div className="space-y-3"><p className="text-sm text-muted-foreground">未提供来源证据，请核对原件。</p><Button variant="outline" size="sm" onClick={() => setSource(null)}>打开原件</Button></div>}</section>
    {programNotice(check) && <Disclosure summary="程序执行说明"><p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-muted-foreground">{programNotice(check)}</p></Disclosure>}
    {review && <RecordedReview review={review} originalFor={authorizedOriginal} onCitation={cite => setSource({kind: 'review', reference: cite})} loadHistory={check.review_item_key ? () => readReviewHistory(roundId, check.review_item_key!) : undefined}/>}
    {/* One action row: recording a review is the step for a result that looks wrong. */}
    {reviewEditor && !issue && check.review_item_key && ['completed', 'failed'].includes(check.execution) && <div className="sticky bottom-0 mt-auto flex flex-wrap items-center gap-2 border-t bg-background py-3"><Button size="sm" onClick={() => reviewEditor.open({roundId, itemKey: check.review_item_key!, title: clauseLabel(clauses, check.rule_item.id), attempt: audit!.result_attempt, files: originals.filter(row => frozenFileVersionIds.includes(row.file_version_id)).map(row => ({id: row.file_version_id, name: row.file?.original_filename || '本轮原件'}))}, {container: editorHost.current || undefined, onContinue: next ? () => navigateClause(next.rule_item.id, true) : undefined})}>{review ? '更正复核' : '记录人工复核'}</Button></div>}
  </article>
  const attentionCount = filterCount('attention'), ngCount = filterCount('ng')
  // When everything that needs attention is non-conforming, one chip says it; the default filter lands on it.
  const activeFilter = filter === 'attention' && attentionCount === ngCount ? 'ng' : filter
  const chipSpec: [string, string][] = [['all', '全部'], ['suggested', '建议复核'], ...(activeFilter === 'attention' ? [['attention', '需关注'] as [string, string]] : []), ['ng', resultLabel.NG], ['unable', resultLabel.UNABLE], ['error', resultLabel.ERR], ['uncovered', uncoveredLabel], ['pass', resultLabel.PASS], ['na', resultLabel.NA], ['unreviewed', '未复核']]
  const filterChips = chipSpec.map(([value, label]) => ({value, label, count: filterCount(value)})).filter(chip => (chip.value === 'all' || chip.count > 0 || chip.value === activeFilter) && (chip.value !== 'unreviewed' || chip.count !== filterCount('all')))
  // Review status filters sit apart from verdict filters; they answer a different question.
  const reviewChips = ['suggested', 'unreviewed']
  const clearFilters = () => {setQuery(''); chooseFilter('all')}
  const displayValues = fileDisplayValues(file), catalog = useCustomerCatalog()
  // The overview grid names the file and its rule, so the lines below do not repeat them.
  const overview = !focus && (file.match !== undefined || file.display_info !== undefined)
  return <section aria-label="审核详情" className={`min-w-0 transition-all duration-300 motion-reduce:transition-none ${originalOpen ? 'pr-168' : ''}`}>
    <header className="min-w-0 space-y-1.5 border-b bg-card px-6 py-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
      {heading}
      {showFileTitle && <h2 className="min-w-0 break-words text-base font-semibold">{file.file.original_filename}</h2>}
      {audit && showFileTitle && <div className="flex flex-wrap items-center gap-2"><Badge shape="square" variant={issue ? 'warning' : audit.execution === 'review_failed' ? 'destructive' : audit.conclusion ? resultTone(audit.conclusion) : 'outline'}>{issue ? '结果待确认' : audit.execution === 'review_failed' ? executionLabel.review_failed : audit.conclusion ? conclusionLabel[audit.conclusion] ?? '结果未知' : executionLabel[audit.execution]}</Badge>{!issue && audit.execution === 'completed' && pendingNote(audit, true) && <span className="text-xs font-medium tabular-nums">{pendingNote(audit, true)}</span>}{!issue && audit.execution !== 'completed' && audit.execution !== 'review_failed' && <span className="text-xs tabular-nums text-muted-foreground">已结束 {audit.progress.completed}/{audit.progress.total} 项</span>}</div>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {onOpenTask && <Button variant="outline" size="sm" onClick={onOpenTask}>打开来源任务<Icon icon={iconArrowRight}/></Button>}
        {onReport && <Button variant={audit?.report.state === 'ready' && !issue ? 'default' : 'outline'} size="sm" disabled={Boolean(issue) || !audit || !['ready', 'failed', 'pending', 'generating'].includes(audit.report.state)} onClick={onReport}><Icon icon={iconFileLines}/>查看报告</Button>}
        <Dialog open={infoOpen} onOpenChange={setInfoOpen}><DialogTrigger asChild><Button variant="ghost" size="icon-sm" aria-label="文件信息" title="文件信息"><Icon icon={iconCircleInfo}/></Button></DialogTrigger><DialogContent onEscapeKeyDown={event => {if (event.target instanceof Element && event.target.closest('[data-slot="dialog-content"]')) event.preventDefault()}} className="max-h-[88dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>文件信息</DialogTitle><DialogDescription className="sr-only">查看原始识别信息与本轮审核规则</DialogDescription></DialogHeader><FileName name={file.file.original_filename}/><RecognizedValues file={file}/>{rule && <section aria-label="本轮审核规则" data-rule-version={rule.id} className="space-y-4 border-t pt-4"><h3 className="font-semibold">本轮审核规则</h3><p className="text-sm">{rule.title}</p>{rule.body.kind === 'engine' ? rule.body.rules.map((item, _, items) => <article key={item.id} className="space-y-2 border-t pt-3"><h4 className="font-medium" title={`来源编号 ${item.id}`}>{clauseLabel(items, item.id)}</h4><p className="whitespace-pre-wrap text-sm">{item.rule_to_check}</p></article>) : <p className="text-sm">文件存在性检查。</p>}</section>}{/* Pairing only means something when this round holds both a PDF and an Excel original. */}{audit?.execution === 'completed' && !issue && [/\.pdf$/i, /\.xlsx?$/i].every(format => originals.some(row => format.test(row.file?.original_filename || ''))) && <div className="border-t pt-4"><ReviewDocumentPairs key={roundId} roundId={roundId}/></div>}</DialogContent></Dialog>
      </div>
      </div>
      {/* The file's facts in one compact grid, as on the prototype's detail panel; the pencil is the one place to correct the displayed values. */}
      {overview && <section aria-label="文件信息概览" className="flex items-start gap-2 py-0.5">
        <h3 className="sr-only">文件信息</h3>
        <dl className={`grid min-w-0 flex-1 ${originalOpen ? 'grid-cols-2' : 'grid-cols-4'} gap-x-5 gap-y-1 text-xs [&>div]:flex [&>div]:min-w-0 [&>div]:items-baseline [&>div]:gap-1.5 [&_dd]:min-w-0 [&_dd]:truncate [&_dt]:shrink-0 [&_dt]:text-muted-foreground`}>
          {([['匹配规则', rule?.title || ''], ['文件类型', file.match?.document_type && !file.file.original_filename.includes(documentTypeName(catalog?.type_names, file.match.document_type)) ? documentTypeName(catalog?.type_names, file.match.document_type) : ''], ['客户名称', displayValues.customer || ''], ['车型', displayValues.model || ''], ['项目名称', displayValues.project || ''], ['零件号', displayValues.part_number || ''], ['零件名称', displayValues.part || ''], ...facts.map(fact => [fact.label, fact.value] as [string, string])].filter(([, value]) => value).map(([label, value]) => <div key={label}><dt>{label}</dt><dd title={value}>{label === '匹配规则' && rule ? <Button variant="link" size="xs" className="h-auto max-w-full" title={rule.title} onClick={() => setInfoOpen(true)}><span className="truncate">{rule.title}</span></Button> : value}</dd></div>))}
        </dl>
        {onEditDisplay && <Button variant="ghost" size="icon-xs" aria-label="更正显示信息" title="更正显示信息" disabled={!validDisplayInfo(file.display_info)} onClick={onEditDisplay}><Icon icon={iconPen}/></Button>}
      </section>}
      {/* Rule and report status share one quiet line under the verdict. */}
      <p className="break-words text-xs text-muted-foreground empty:hidden">{rule && !overview && <>本轮规则：{rule.title}</>}{rule && !overview && audit && (issue || audit.report.state !== 'ready') && ' · '}{audit && (issue || audit.report.state !== 'ready') && <>{issue ? '报告资格待确认。' : (audit.report.state === 'not_eligible' && audit.counts.unknown_count + audit.counts.not_covered_count + audit.counts.error_count > 0 ? '部分审核项尚未取得明确结果，不生成完整报告。' : ['pending', 'generating'].includes(audit.report.state) ? reportLabel[audit.report.state] : audit.report.reason || reportLabel[audit.report.state])}</>}</p>
    </header>
    {issue && <div className="px-6 pt-4"><Alert variant="destructive"><AlertTitle>结果数据需要核对</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert></div>}
    {!audit ? <p role="status" className="px-6 py-8 text-sm text-muted-foreground">还没有这份文件的审核结果，请点右上角的刷新按钮重新读取。</p> : <>
      {audit.execution === 'review_failed' && <div className="px-6 pt-4"><Alert variant="destructive"><AlertTitle>本文件审核异常，未取得完整结果。</AlertTitle><AlertDescription>{audit.failure?.message || '没有可用结果。'}</AlertDescription></Alert></div>}
      <div hidden={focus}>
        {/* One row: what is shown and how it compares on the left; finding a clause and the originals on the right. */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-header px-6 py-3">
          <div role="group" aria-label="审核结果筛选" className="flex min-w-0 flex-wrap items-center gap-0.5 rounded-lg bg-secondary p-0.5">{[filterChips.filter(chip => !reviewChips.includes(chip.value)), filterChips.filter(chip => reviewChips.includes(chip.value))].map((chips, group) => chips.length > 0 && <Fragment key={group}>{group > 0 && <span aria-hidden="true" className="mx-1 h-4 w-px bg-border"/>}{chips.map(({value, label, count}) => <Button key={value} variant={activeFilter === value ? 'default' : 'ghost'} size="xs" aria-pressed={activeFilter === value} aria-label={label} aria-describedby={`${filterId}-${value}`} onClick={() => chooseFilter(value)}><span className="tabular-nums">{label} <span id={`${filterId}-${value}`}>{count}</span></span></Button>)}</Fragment>)}</div>
          {diff ? <section aria-label="与上次审核对比" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"><span>与{previous!.label}相比，{diff.added.length || diff.resolved.length ? '有变化' : diff.uncomparable ? '可对比项相同' : '结果相同'}{diff.uncomparable > 0 ? `，${diff.uncomparable} 项无法对比` : ''}。</span>{diff.added.length > 0 && <Button variant={filter === 'added' ? 'secondary' : 'ghost'} size="xs" aria-pressed={filter === 'added'} onClick={() => chooseFilter('added')}><Icon icon={iconArrowTrendUp} aria-hidden="true"/><span className="tabular-nums text-destructive">新增问题 {diff.added.length}</span></Button>}{diff.resolved.length > 0 && <Button variant={filter === 'resolved' ? 'secondary' : 'ghost'} size="xs" aria-pressed={filter === 'resolved'} onClick={() => chooseFilter('resolved')}><Icon icon={iconArrowTrendDown} aria-hidden="true"/><span className="tabular-nums text-success">已解决 {diff.resolved.length}</span></Button>}</section> : null}
          <div className="ml-auto flex flex-wrap items-center gap-2"><div className="relative w-56 min-w-0"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Icon icon={iconMagnifyingGlass} className="size-3.5"/></span><Input aria-label="搜索审核项" placeholder="搜索审核项、要求或依据" value={query} onChange={event => setQuery(event.target.value)} iconStart/></div><div className="flex flex-wrap items-center gap-2"><Button variant="outline" size="sm" onClick={() => openOriginal(null, null)}><Icon icon={iconEye}/>查看原件</Button>{audit && <Button variant="outline" size="sm" onClick={() => navigateClause(check?.rule_item.id || "", true)}>{editable ? '逐条复核' : '逐条查看'}</Button>}</div></div>
        </div>
        {/* Four columns only; the basis is read in place, and a location in it opens the original. */}
        <section aria-label="审核项" className="min-w-0 border-t bg-card"><Table className="min-w-[40rem] table-fixed">
            <TableHeader variant="muted"><TableRow><TableHead className="w-14">序号</TableHead><TableHead className="w-[30%]">审核项</TableHead><TableHead className={originalOpen ? 'w-20' : 'w-24'}>审核结果</TableHead><TableHead>判定依据</TableHead></TableRow></TableHeader>
            <TableBody>{filtered.map(row => {
              const linked = clause === row.rule_item.id
              return <TableRow key={row.rule_item.id} ref={linked ? linkedRow : undefined} data-state={linked ? 'selected' : undefined}>
                <TableCell className="align-top"><span className="text-xs tabular-nums text-muted-foreground" title={`来源编号 ${row.rule_item.id}`}>{audit.checks.indexOf(row) + 1}</span></TableCell>
                <TableCell className="whitespace-normal align-top"><p className="break-words font-medium" title={row.rule_item.category}><HighlightText text={row.rule_item.category} query={query}/></p>{query.trim() && row.rule_item.rule_to_check.toLowerCase().includes(query.trim().toLowerCase()) && <p className="mt-1 line-clamp-2 break-words text-xs text-muted-foreground"><HighlightText text={row.rule_item.rule_to_check} query={query}/></p>}</TableCell>
                <TableCell className="align-top"><div className="flex flex-col items-start gap-1"><Badge shape="square" variant={checkTone(row)}>{checkStatusLabel(row)}</Badge>{diff?.added.includes(row) && <Badge shape="square" variant="outline">新增问题</Badge>}{diff?.resolved.includes(row) && <Badge shape="square" variant="outline">已解决</Badge>}</div></TableCell>
                <TableCell className="whitespace-normal align-top">{basisCell(row)}</TableCell>
              </TableRow>
            })}</TableBody>
          </Table>
          {!filtered.length && <div className="flex flex-wrap items-center gap-3 px-6 py-4"><p className="text-sm text-muted-foreground">{filter === 'attention' && !query ? '没有需关注的审核项。' : '没有符合条件的审核项。'}</p>{(query || filter !== 'all') && <Button variant="outline" size="sm" onClick={clearFilters}>{filter === 'attention' && !query ? '查看全部审核项' : '清除筛选'}</Button>}</div>}
        </section>
      </div>
    </>}
    {focus && audit && <section aria-label="专注复核" className="p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">人工已复核 {audit.checks.filter(reviewed).length}/{audit.checks.filter(reviewable).length} · 不改变机器结果</p><div className="flex flex-wrap gap-2"><Button variant="secondary" onClick={() => navigateClause(check?.rule_item.id || "", false)}>返回审核项</Button><Button variant="outline" disabled={at <= 0} onClick={() => navigateClause(audit.checks[at - 1].rule_item.id)}>上一条</Button><Select value={check?.rule_item.id} onValueChange={navigateClause}><SelectTrigger aria-label="条款目录" className="max-w-80"><SelectValue/></SelectTrigger><SelectContent>{audit.checks.map(row => <SelectItem key={row.rule_item.id} value={row.rule_item.id}>{clauseLabel(clauses, row.rule_item.id)}</SelectItem>)}</SelectContent></Select><Button variant="outline" disabled={!next} onClick={() => next && navigateClause(next.rule_item.id)}>下一待复核</Button></div></div>
      <ResizablePanelGroup orientation="horizontal" className="min-h-[65dvh]"><ResizablePanel defaultSize="45%" minSize="25%"><div className="min-w-0 space-y-4 pr-4"><h2 className="text-lg font-semibold" title={check?.rule_item.id}>{check && clauseLabel(clauses, check.rule_item.id)}</h2>{basis}<div ref={editorHost}/></div></ResizablePanel><ResizableHandle withHandle/><ResizablePanel defaultSize="55%" minSize="25%"><div className="min-w-0 space-y-4 pl-4"><Select value={chosen?.reference.file_version_id || originalId} onValueChange={id => {setOriginalId(id); setSource(null)}}><SelectTrigger aria-label="选择对照原件"><SelectValue/></SelectTrigger><SelectContent>{originals.filter(row => frozenFileVersionIds.includes(row.file_version_id)).map(row => <SelectItem key={row.file_version_id} value={row.file_version_id}>{row.file?.original_filename || '本次原件'}</SelectItem>)}</SelectContent></Select>{originalPanel(true)}</div></ResizablePanel></ResizablePanelGroup>
    </section>}
    <HistoricalFindings editable={editable} findings={audit?.unmapped_findings || []} roundId={roundId} resultAttempt={audit?.result_attempt ?? null} versions={frozenFileVersionIds} originals={originals} renderOriginal={renderOriginal} fileVersionId={file.file.id}/>
    <Sheet modal={false} open={originalOpen} onOpenChange={setOriginalOpen}><SheetContent overlay={false} onInteractOutside={event => event.preventDefault()} className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-2xl" onCloseAutoFocus={event => {event.preventDefault(); /* The non-modal sheet unmounts after its exit animation; if the person already moved focus (e.g. opened support), do not take it back. */ if (trigger.current?.isConnected && (!document.activeElement || document.activeElement === document.body)) trigger.current.focus()}}>
      <div className="pr-8"><SheetHeader><SheetTitle>原件对照</SheetTitle><SheetDescription className="sr-only">对照本轮审核固定的原件</SheetDescription></SheetHeader></div>
      {pairedVersions.length > 1 ? <Tabs value={pane} onValueChange={setPane}><TabsList variant="line"><TabsTrigger value="original">原件</TabsTrigger><TabsTrigger value="comparison">关联原件对照</TabsTrigger></TabsList><TabsContent value="original"><div className="pt-4">{originalPanel(false)}</div></TabsContent><TabsContent value="comparison"><div className="pt-4">{comparison}</div></TabsContent></Tabs> : originalPanel(false)}
    </SheetContent></Sheet>
  </section>
}
