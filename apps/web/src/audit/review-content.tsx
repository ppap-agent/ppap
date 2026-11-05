import {useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowRight, BookOpen, ChevronDown, ChevronUp, FileSearch, FileText, Info, Search} from 'lucide-react'
import type {components} from '../generated/openapi'
import {sourceLocation} from '../helpers'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {HighlightText} from '../components/highlight-text'
import {FileName} from '../journey/file-name'
import {clauseObservation, frozenResultError, sameSnapshot, type ReviewFile} from './result-projection'
import {displayFields, fileDisplayValues, validDisplayInfo} from './display-info-contract'
import {RecordedReview, frozenOriginal} from './recorded-review'
import {HistoricalFindings} from './historical-findings'
import {ReviewEditorContext} from './review-editor'
import {ResizableHandle, ResizablePanel, ResizablePanelGroup} from '../components/ui/resizable'
import {filterChecks, needsAttention, nextUnreviewed, reviewable, reviewed} from './review-navigation'
import {diffRounds} from './round-diff'
import {readAuditRoute, writeAuditAddress} from './routes'
import {conclusionLabel, resultLabel, resultTone, uncoveredLabel, type ResultTone} from './status-labels'

type File = ReviewFile
type Evaluation = components['schemas']['AuditClauseEvaluation']
type ReviewCitation = components['schemas']['ReviewCitation']
type SelectedSource = {kind: 'machine'; reference: AuditEvidence} | {kind: 'review'; reference: ReviewCitation}
export type AuditEvidence = components['schemas']['AuditEvidenceReference']
export type OriginalRenderer = (file: components['schemas']['FileVersion'], evidence: AuditEvidence | null, roundId?: string) => ReactNode
const executionLabel = {queued: '排队中', running: '审核中', completed: '审核结束', review_failed: '异常'}
const reportLabel = {not_eligible: '无正式报告', pending: '报告待生成', generating: '报告生成中', ready: '报告已就绪', failed: '报告生成失败'}
const evidenceText = (evidence: AuditEvidence, verified: boolean) => evidence.excerpt || (verified ? '该位置内容为空。' : '未提供摘录。')
function checkLabel(check: Evaluation) {
  if (check.execution === 'queued') return '排队中'
  if (check.execution === 'running') return '审核中'
  if (check.execution === 'failed') return resultLabel.ERR
  if (check.coverage !== 'executed') return uncoveredLabel
  return check.finding ? resultLabel[check.finding.status] : '结果未知'
}
function checkTone(check: Evaluation): ResultTone {
  if (check.execution === 'queued' || check.execution === 'running') return 'outline'
  if (check.execution === 'failed') return resultTone('ERR')
  if (check.coverage !== 'executed') return resultTone('uncovered')
  return check.finding ? resultTone(check.finding.status) : 'warning'
}

function useMinWidth(width: number) {
  const [matches, setMatches] = useState(() => window.matchMedia(`(min-width: ${width}px)`).matches)
  useEffect(() => {
    const media = window.matchMedia(`(min-width: ${width}px)`)
    const update = () => setMatches(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [width])
  return matches
}

/** The conclusion and what to fix sit above the list; the fuller material stays one click away. */
function AuditSummary({audit, issue}: {audit: NonNullable<File['audit']>; issue: string | null | undefined}) {
  const summary = audit.summary, actions = summary?.corrective_actions ?? []
  return <section aria-label="审核总结" className="border-b px-6 py-3 text-sm max-sm:px-4">
    {issue ? <p role="alert">{issue}</p> : summary ? <div className="grid gap-x-8 gap-y-2 lg:grid-cols-2">
      <div className="min-w-0 space-y-1"><h3 className="font-medium">审核总结</h3><p className="leading-relaxed text-muted-foreground">{summary.text}</p>
        {summary.extended_suggestions.length > 0 && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="group"><ChevronDown className="transition-transform group-data-[state=open]:rotate-180"/>AI 延伸建议（不是客户要求）</Button></CollapsibleTrigger><CollapsibleContent><ul className="list-disc space-y-1 pl-5 pt-1">{summary.extended_suggestions.map(text => <li key={text}>{text}</li>)}</ul></CollapsibleContent></Collapsible>}</div>
      {actions.length > 0 && <div className="min-w-0 space-y-1"><h3 className="font-medium">核心整改建议</h3><ul className="list-disc space-y-1 pl-5">{actions.slice(0, 3).map(text => <li key={text}>{text}</li>)}</ul>{actions.length > 3 && <Collapsible><CollapsibleContent><ul className="list-disc space-y-1 pl-5 pt-1">{actions.slice(3).map(text => <li key={text}>{text}</li>)}</ul></CollapsibleContent><CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="group"><span className="group-data-[state=open]:hidden">查看其余 {actions.length - 3} 条</span><span className="hidden group-data-[state=open]:inline">收起</span></Button></CollapsibleTrigger></Collapsible>}</div>}
    </div> : <p className="text-muted-foreground">{audit.execution === 'review_failed' ? '审核异常，没有通过总结。' : '总结尚未生成。'}</p>}
  </section>
}

function DisplayInformation({file, onEdit}: {file: File; onEdit?: () => void}) {
  const values = fileDisplayValues(file)
  const corrected = validDisplayInfo(file.display_info) && file.display_info.revision > 0
  if (file.match === undefined && file.display_info === undefined) return null
  return <section aria-label="本次审核文件信息" className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium">页面显示信息{corrected ? ' · 已更正' : ''}</p>{onEdit && <Button variant="outline" size="sm" disabled={!validDisplayInfo(file.display_info)} onClick={onEdit}>更正显示信息</Button>}</div>
    <dl className="grid grid-cols-2 gap-4 text-sm">{displayFields.map(([field, label]) => <div className="min-w-0" key={field}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 break-words">{values[field] || '—'}</dd></div>)}</dl>
    {corrected && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">查看原始识别信息</Button></CollapsibleTrigger><CollapsibleContent><section aria-label="原始识别信息" className="space-y-2 pt-2"><p className="text-xs text-muted-foreground">当次审核依据未改变，既有报告不会随显示更正更新。</p><dl className="grid grid-cols-2 gap-3 text-sm">{displayFields.map(([field, label]) => <div key={field}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-words">{file.match?.metadata[field] || '—'}</dd></div>)}</dl></section></CollapsibleContent></Collapsible>}
  </section>
}

/** P5 and historical readers share the same frozen results, evidence and review actions. */
export function AuditReviewContent({file, roundId, taskId, frozenRule = file.match?.rule, frozenFileVersionIds, originals, renderOriginal, onReport, reportAccess, onEditDisplay, showFileTitle = true, previous}: {file: File; roundId: string; taskId?: string; frozenRule?: components['schemas']['BoundRule'] | null; frozenFileVersionIds: string[]; originals: components['schemas']['AuditArchiveOriginal'][]; renderOriginal?: OriginalRenderer; onReport?: () => void; reportAccess?: components['schemas']['AuditArchiveAccess']; onEditDisplay?: () => void; showFileTitle?: boolean; previous?: {label: string; checks: Evaluation[]} | null}) {
  const reviewEditor = useContext(ReviewEditorContext)
  const [clause, setClause] = useState(() => new URL(location.href).searchParams.get('clause') || ''), [chosenSource, setSource] = useState<SelectedSource | null>(null)
  const [focus, setFocus] = useState(() => new URL(location.href).searchParams.get('focus') === 'review')
  const [originalId, setOriginalId] = useState(file.file.id)
  const editorHost = useRef<HTMLDivElement>(null), currentEditor = useRef(reviewEditor); currentEditor.current = reviewEditor
  const [open, setOpenState] = useState(() => new URL(location.href).searchParams.get('basis') === '1'), [pane, setPane] = useState('details')
  // An issue link opens its basis directly; closing drops that one-shot request from the address.
  const setOpen = (value: boolean) => {
    setOpenState(value)
    if (!value && new URL(location.href).searchParams.has('basis')) {const url = new URL(location.href); url.searchParams.delete('basis'); writeAuditAddress(url)}
  }
  const [query, setQuery] = useState(''), [filter, setFilter] = useState(() => new URL(location.href).searchParams.get('checkFilter') || (file.audit && filterChecks(file.audit.checks, '', 'attention').length ? 'attention' : 'all')), [infoOpen, setInfoOpen] = useState(false)
  const wide = useMinWidth(1280)
  // Wide enough for the basis and the original to sit side by side without burying the list.
  const sideBySide = useMinWidth(1680)
  const trigger = useRef<HTMLElement | null>(null), comparisonTrigger = useRef<HTMLButtonElement>(null)
  const audit = file.audit, check = audit?.checks.find(row => row.rule_item.id === clause) ?? audit?.checks[0]
  const issue = frozenResultError(roundId, file, frozenFileVersionIds, frozenRule)
  const reportAccessIssue = reportAccess && reportAccess.state !== 'allowed' ? reportAccess.reason || '当前账号不能读取此报告。' : ''
  const review = check?.review !== undefined ? check.review : check?.finding?.review
  const chosen = chosenSource && (chosenSource.kind === 'machine' ? check?.evidence : review?.citations)?.some(row => sameSnapshot(row, chosenSource.reference)) ? chosenSource : null
  const citation = chosen?.kind === 'review' ? chosen.reference : null
  const authorizedOriginal = (id: string) => frozenOriginal(originals, frozenFileVersionIds, id)
  // Side by side, stepping through items keeps the original on the current item's evidence.
  const followed = sideBySide && open && !chosen ? check?.evidence.find(row => authorizedOriginal(row.file_version_id)) : undefined
  const source = chosen?.kind === 'machine' ? chosen.reference : followed ?? null
  const activePane = sideBySide && pane === 'original' ? 'details' : pane
  const original = authorizedOriginal((chosen?.reference ?? followed)?.file_version_id || originalId)
  const pairedVersions = [...new Set(check?.evidence.map(item => item.file_version_id) || [])]
  const rule = audit?.rule || frozenRule
  const filtered = filterChecks(audit?.checks || [], query, filter)
  const filterCount = (value: string) => filterChecks(audit?.checks || [], '', value).length
  const at = audit?.checks.findIndex(row => row.rule_item.id === check?.rule_item.id) ?? -1
  const next = nextUnreviewed(audit?.checks || [], check?.rule_item.id || '')
  const listed = filtered.findIndex(row => row.rule_item.id === check?.rule_item.id)
  const stepTo = (offset: number) => {
    const target = filtered[listed + offset]
    if (target) {navigateClause(target.rule_item.id); setPane('details')}
  }
  // J/K also work from the list while the evidence is docked beside it.
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest('[data-slot="dialog-content"]')) return
      if (event.metaKey || event.ctrlKey || event.altKey || event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable], [role="combobox"]')) return; if (event.key === 'j') {event.preventDefault(); stepTo(1)} else if (event.key === 'k') {event.preventDefault(); stepTo(-1)}
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })
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
  function showEvidence(row: Evaluation) {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    navigateClause(row.rule_item.id); setPane('details'); setOpen(true)
  }
  function showOriginal() {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setSource(null); setPane('original'); setOpen(true)
  }
  const originalPanel = <section aria-label="原件预览" data-file-version={original?.file_version_id} className="min-w-0 space-y-4">
    <h2 className="font-semibold">原件对照</h2><p className="text-sm text-muted-foreground">当前条款：{check?.rule_item.id || '—'} · {check?.rule_item.category || '尚未取得审核项'}</p>
    {original ? <><FileName name={original.file?.original_filename || '该次证据原件'}/>{source && <div className="space-y-2 text-sm"><p className="text-muted-foreground">{source.locator ? '证据位置：' + sourceLocation({locator: source.locator}) : source.location_gap || '未提供具体位置，可对照本轮原件。'}{source.rendition === 'derived' && ' · 派生内容引用，不等于原件定位已验证。'}</p>{source.locator && source.location_gap && <p className="text-muted-foreground">{source.location_gap}</p>}{(source.excerpt || check?.evidence_verified) && <p>{evidenceText(source, check?.evidence_verified === true)}</p>}</div>}{citation && <section aria-label="人工复核引用" className="space-y-2 text-sm"><h3 className="font-medium">人工记录位置</h3><p className="whitespace-pre-wrap">{citation.location}</p><p className="whitespace-pre-wrap text-muted-foreground">{citation.excerpt}</p></section>}{original.access.state !== 'allowed' || !original.file ? <Alert><AlertTitle>本次原件不可访问</AlertTitle><AlertDescription>{original.access.reason || '暂时无法读取此原件。'}</AlertDescription></Alert> : renderOriginal ? renderOriginal(original.file, source, roundId) : <Alert><AlertTitle>原件暂不可预览</AlertTitle><AlertDescription>请下载原件查看。</AlertDescription></Alert>}</> : <Alert variant="destructive"><AlertTitle>不能读取此证据原件</AlertTitle><AlertDescription>此证据不在本次审核的文件中。</AlertDescription></Alert>}
  </section>
  function showComparison() {
    if (!open) trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setPane('comparison'); setOpen(true)
  }
  const comparison = <section aria-label="关联原件对照" className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm">同一条款：{check?.rule_item.id} · {check?.rule_item.category}</p><Button variant="outline" onClick={() => {setPane('details'); requestAnimationFrame(() => comparisonTrigger.current?.focus())}}>返回判定依据</Button></div>
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">{pairedVersions.map(version => {
      const target = authorizedOriginal(version)
      const evidence = check!.evidence.filter(item => item.file_version_id === version)
      const name = target?.file?.original_filename || '本轮证据原件'
      return <section key={version} aria-label={'关联原件 ' + name} data-file-version={version} className="min-w-0 space-y-3">
        <h3 className="font-semibold"><FileName name={name}/></h3>
        <div className="space-y-2 text-sm">{evidence.map((item, index) => <div key={index} className="space-y-1"><p className="text-muted-foreground">{item.locator ? '证据位置：' + sourceLocation({locator: item.locator}) : item.location_gap || '未提供具体位置，可对照本轮原件。'}{item.rendition === 'derived' && ' · 派生内容引用，不等于原件定位已验证。'}</p>{item.locator && item.location_gap && <p className="text-muted-foreground">{item.location_gap}</p>}<p className="whitespace-pre-wrap">{evidenceText(item, check!.evidence_verified)}</p></div>)}</div>
        {!target ? <Alert variant="destructive"><AlertTitle>不能读取此证据原件</AlertTitle><AlertDescription>此证据不在本次审核的文件中。</AlertDescription></Alert> : target.access.state !== 'allowed' || !target.file ? <Alert><AlertTitle>本次原件不可访问</AlertTitle><AlertDescription>{target.access.reason || '暂时无法读取此原件。'}</AlertDescription></Alert> : renderOriginal ? renderOriginal(target.file, evidence[0], roundId) : <Alert><AlertTitle>原件暂不可预览</AlertTitle><AlertDescription>请下载原件查看。</AlertDescription></Alert>}
      </section>
    })}</div>
  </section>
  const basis = check && <article key={check.rule_item.id} aria-label="条款判定依据" className="space-y-6 animate-in fade-in-0 duration-200 motion-reduce:animate-none">
    <div className="flex flex-wrap items-center gap-2"><Badge shape="square" variant={checkTone(check)}>{checkLabel(check)}</Badge>{review && <Badge shape="square" variant="outline">已有人工复核</Badge>}</div>
    <section className="space-y-2"><h3 className="font-medium">机器判定依据</h3><p className="whitespace-pre-wrap text-sm leading-relaxed">{clauseObservation(check)}</p>{check.scope && <p className="text-xs text-muted-foreground">检查范围：已查 {check.scope.checked}/{check.scope.total}，未知 {check.scope.unknown}。</p>}</section>
    <section className="space-y-2"><h3 className="font-medium">审核规则原文</h3><p className="whitespace-pre-wrap border-l-2 pl-4 text-sm leading-relaxed">{check.rule_item.rule_to_check}</p></section>
    <section className="space-y-3"><h3 className="font-medium">本轮原件与来源证据</h3>{pairedVersions.length > 1 && <Button ref={comparisonTrigger} variant="outline" onClick={showComparison}><FileSearch/>同时核对关联原件</Button>}{check.evidence.length ? check.evidence.map((evidence, index) => {
      const target = authorizedOriginal(evidence.file_version_id)
      return <div key={evidence.file_version_id + '-' + index} data-file-version={evidence.file_version_id} className="space-y-3 border-t pt-3">
        <FileName name={target?.file?.original_filename || '本轮证据原件'}/>
        {evidence.excerpt || check.evidence_verified ? <p className="whitespace-pre-wrap text-sm">{evidenceText(evidence, check.evidence_verified)}</p> : <p className="text-sm text-muted-foreground">{evidence.location_gap || '未提供具体位置，可对照本轮原件。'}</p>}
        {evidence.location_gap && (evidence.excerpt || check.evidence_verified) && <p className="text-xs text-muted-foreground">{evidence.location_gap}</p>}
        <Button variant="outline" size="sm" disabled={!target} aria-label={'核对 ' + (target?.file?.original_filename || '不在本轮的原件')} onClick={() => {setSource({kind: 'machine', reference: evidence}); setPane('original')}}><FileSearch/>{target ? '打开原件' : '原件不在冻结范围'}</Button>
      </div>
    }) : <div className="space-y-3"><p className="text-sm text-muted-foreground">未提供来源证据，请核对原件。</p><Button variant="outline" size="sm" onClick={() => {setSource(null); setPane('original')}}>打开原件</Button></div>}</section>
    {review && <RecordedReview review={review} originalFor={authorizedOriginal} onCitation={cite => {setSource({kind: 'review', reference: cite}); setPane('original')}}/>}
    {reviewEditor && !issue && check.review_item_key && ['completed', 'failed'].includes(check.execution) && <div className="border-t pt-4"><Button variant="outline" size="sm" onClick={() => reviewEditor.open({roundId, itemKey: check.review_item_key!, title: check.rule_item.id + ' · ' + check.rule_item.category, attempt: audit!.result_attempt, files: originals.filter(row => frozenFileVersionIds.includes(row.file_version_id)).map(row => ({id: row.file_version_id, name: row.file?.original_filename || '本轮冻结原件'}))}, focus ? {container: editorHost.current || undefined, onContinue: next ? () => navigateClause(next.rule_item.id, true) : undefined} : undefined)}>记录人工复核</Button></div>}
  </article>
  const docked = wide && open && activePane === 'details'
  const diff = previous && audit?.execution === 'completed' && !issue ? diffRounds(audit.checks, previous.checks) : null
  const clearFilters = () => {setQuery(''); chooseFilter('all')}
  return <section aria-label="审核详情" className={`min-w-0 transition-all duration-300 motion-reduce:transition-none ${docked ? sideBySide ? 'pr-192 min-[1920px]:pr-256' : 'pr-112' : ''}`}>
    <header className={`min-w-0 border-b px-6 py-4 max-sm:px-4 ${showFileTitle ? 'space-y-3' : 'flex flex-wrap items-center justify-between gap-3'}`}>
      {showFileTitle && <h2 className="break-words text-lg font-semibold">{file.file.original_filename}</h2>}
      <div className={showFileTitle ? 'min-w-0 space-y-2' : 'flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2'}>
      {audit && <div className="flex flex-wrap items-center gap-2"><Badge shape="square" variant={issue ? 'warning' : audit.execution === 'review_failed' ? 'destructive' : audit.conclusion ? resultTone(audit.conclusion) : 'outline'}>{issue ? '结果待核对' : audit.execution === 'review_failed' ? executionLabel.review_failed : audit.conclusion ? conclusionLabel[audit.conclusion] ?? '结果未知' : executionLabel[audit.execution]}</Badge>{!issue && <><span className="text-xs tabular-nums">{resultLabel.NG} {audit.counts.ng_count} · {resultLabel.PASS} {audit.counts.pass_count}</span><span className="text-xs text-muted-foreground">已结束 {audit.progress.completed}/{audit.progress.total} 项</span>{audit.counts.unknown_count > 0 && <span className="text-xs text-warning">{resultLabel.UNABLE} {audit.counts.unknown_count}</span>}{audit.counts.not_covered_count > 0 && <span className="text-xs text-warning">{uncoveredLabel} {audit.counts.not_covered_count}</span>}{audit.counts.error_count > 0 && <span className="text-xs text-warning">{resultLabel.ERR} {audit.counts.error_count}</span>}</>}</div>}
      {rule && <p className="break-words text-xs text-muted-foreground">本轮规则：<button type="button" className="underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-ring" onClick={() => setInfoOpen(true)}>{rule.title}</button></p>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={showOriginal}><FileSearch/>查看原件</Button>
        {audit && <Button variant={focus ? "secondary" : "outline"} onClick={() => navigateClause(check?.rule_item.id || "", !focus)}>{focus ? "返回审核项" : "专注复核（选做）"}</Button>}
        <Button variant={audit?.report.state === 'ready' && !issue && !reportAccessIssue ? 'default' : 'outline'} disabled={!onReport || Boolean(issue || reportAccessIssue) || !audit || !['ready', 'failed', 'pending', 'generating'].includes(audit.report.state)} onClick={onReport}><FileText/>查看报告</Button>
        <Sheet open={infoOpen} onOpenChange={setInfoOpen}><SheetTrigger asChild><Button variant="ghost"><Info/>文件信息</Button></SheetTrigger><SheetContent onEscapeKeyDown={event => {if (event.target instanceof Element && event.target.closest('[data-slot="dialog-content"]')) event.preventDefault()}} className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-2xl"><SheetHeader><SheetTitle>文件信息</SheetTitle><SheetDescription className="sr-only">查看文件显示信息与本轮确认的规则</SheetDescription></SheetHeader><FileName name={file.file.original_filename}/><DisplayInformation file={file} onEdit={onEditDisplay}/>{rule && <section aria-label="本轮审核规则" data-rule-version={rule.id} className="space-y-4 border-t pt-4"><h3 className="flex items-center gap-2 font-semibold"><BookOpen className="size-4"/>本轮审核规则</h3><p className="text-sm">{rule.title}</p>{rule.body.kind === 'engine' ? rule.body.rules.map(item => <article key={item.id} className="space-y-2 border-t pt-3"><h4 className="font-medium">{item.id} · {item.category}</h4><p className="whitespace-pre-wrap text-sm">{item.rule_to_check}</p></article>) : <p className="text-sm">文件存在性检查。</p>}</section>}</SheetContent></Sheet>
      </div>
      {audit && (issue || reportAccessIssue || audit.report.state !== 'ready') && <p className="text-xs text-muted-foreground">{issue ? '报告资格待核对。' : reportAccessIssue || (audit.report.state === 'not_eligible' && audit.counts.unknown_count + audit.counts.not_covered_count + audit.counts.error_count > 0 ? '部分审核项尚未取得明确结果，不生成完整 PASS/NG 报告。' : reportLabel[audit.report.state])}{!issue && !reportAccessIssue && audit.report.reason && ' · ' + audit.report.reason}</p>}
    </header>
    {issue && <div className="px-6 pt-4"><Alert variant="destructive"><AlertTitle>结果数据需要核对</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert></div>}
    {!audit ? <p role="status">审核结果尚未取得，请读取当前记录。</p> : <>
      {audit.execution === 'review_failed' && <Alert variant="destructive"><AlertTitle>本文件审核异常，未取得完整结果。</AlertTitle><AlertDescription>{audit.failure?.message || '没有可用结果。'}</AlertDescription></Alert>}
      <div hidden={focus}>
        <AuditSummary audit={audit} issue={issue}/>
        {diff && <section aria-label="与上次审核对比" className="space-y-2 border-b px-6 py-3 text-sm max-sm:px-4">
          <p><span className="font-medium">与{previous!.label}相比</span><span className="ml-2 tabular-nums text-muted-foreground">{diff.added.length || diff.resolved.length || diff.persisted.length || diff.uncomparable ? `新增问题 ${diff.added.length} · 已解决 ${diff.resolved.length} · 仍未解决 ${diff.persisted.length}${diff.uncomparable ? ` · 无法对比 ${diff.uncomparable}` : ''}` : '结果没有变化'}</span></p>
          {diff.added.length + diff.resolved.length > 0 && <ul className="flex flex-wrap gap-1.5">{[...diff.added.map(row => ({row, label: '新增问题', tone: 'destructive' as const})), ...diff.resolved.map(row => ({row, label: '已解决', tone: 'success' as const}))].map(({row, label, tone}) => <li key={row.rule_item.id} className="min-w-0"><Button variant="ghost" size="sm" className="h-auto max-w-full whitespace-normal text-left" onClick={() => showEvidence(row)}><Badge shape="square" variant={tone}>{label}</Badge>{row.rule_item.id} {row.rule_item.category}</Button></li>)}</ul>}
        </section>}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-3 max-sm:px-4"><h3 className="font-semibold">审核项 ({audit.checks.length})</h3>
          <div className="flex min-w-0 flex-wrap items-center gap-2"><div className="relative min-w-0 flex-1 sm:w-56"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search className="size-3.5"/></span><Input aria-label="搜索审核项" placeholder="搜索审核项、要求或依据" value={query} onChange={event => setQuery(event.target.value)} iconStart/></div><Select value={filter} onValueChange={chooseFilter}><SelectTrigger aria-label="审核结果筛选"><SelectValue/></SelectTrigger><SelectContent>{([['attention', '需关注'], ['ng', resultLabel.NG], ['unable', resultLabel.UNABLE], ['unreviewed', '未复核'], ['pass', resultLabel.PASS], ['all', '全部审核项']] as const).map(([value, label]) => <SelectItem key={value} value={value}>{label} ({filterCount(value)})</SelectItem>)}</SelectContent></Select></div>
        </div>
        <section aria-label="审核项" className="space-y-4 px-6 pt-2 pb-6 max-sm:px-4">
          <div className="min-w-0 overflow-hidden rounded-md border"><Table className={docked ? 'table-fixed' : 'min-w-3xl table-fixed'}>
            <TableHeader variant="muted"><TableRow><TableHead className="w-14"><span className="text-xs text-muted-foreground">序号</span></TableHead><TableHead className={docked ? undefined : 'w-1/4'}><span className="text-xs text-muted-foreground">审核规则</span></TableHead><TableHead className="w-24"><span className="text-xs text-muted-foreground">审核结果</span></TableHead>{!docked && <TableHead><span className="text-xs text-muted-foreground">判定依据</span></TableHead>}<TableHead className={docked ? 'w-12 text-right' : 'w-28 text-right'}><span className={docked ? 'sr-only' : 'text-xs text-muted-foreground'}>操作</span></TableHead></TableRow></TableHeader>
            <TableBody>{filtered.map(row => <TableRow key={row.rule_item.id} data-state={open && check?.rule_item.id === row.rule_item.id ? 'selected' : undefined} className="cursor-pointer" onClick={event => {if (!(event.target instanceof Element && event.target.closest('button, a'))) showEvidence(row)}}>
              <TableCell className="align-top"><span className="text-xs text-muted-foreground"><HighlightText text={row.rule_item.id} query={query}/></span></TableCell>
              <TableCell className="whitespace-normal align-top"><p className="break-words font-medium" title={row.rule_item.category}><HighlightText text={row.rule_item.category} query={query}/></p>{query.trim() && row.rule_item.rule_to_check.toLowerCase().includes(query.trim().toLowerCase()) && <p className="mt-1 line-clamp-2 break-words text-xs text-muted-foreground"><HighlightText text={row.rule_item.rule_to_check} query={query}/></p>}</TableCell>
              <TableCell className="align-top"><div className="flex flex-col items-start gap-1"><Badge shape="square" variant={checkTone(row)}>{checkLabel(row)}</Badge>{diff?.added.includes(row) && <Badge shape="square" variant="outline">新增问题</Badge>}</div></TableCell>
              {!docked && <TableCell className="whitespace-normal align-top">{needsAttention(row) && <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-muted-foreground"><HighlightText text={clauseObservation(row)} query={query}/></p>}</TableCell>}
              <TableCell className={docked ? 'w-12 text-right align-top' : 'w-28 text-right align-top'}><Button variant="ghost" size={docked ? 'icon-sm' : 'sm'} className="whitespace-nowrap" aria-label={'查看依据 ' + row.rule_item.id} aria-haspopup="dialog" onClick={() => showEvidence(row)}>{!docked && '查看依据'}<ArrowRight/></Button></TableCell>
            </TableRow>)}</TableBody>
          </Table></div>
          {!filtered.length && <div className="flex flex-wrap items-center gap-3"><p className="text-muted-foreground">{filter === 'attention' && !query ? '没有需关注的审核项。' : '没有符合条件的审核项。'}</p>{(query || filter !== 'all') && <Button variant="outline" size="sm" onClick={clearFilters}>{filter === 'attention' && !query ? '查看全部审核项' : '清空条件'}</Button>}</div>}
        </section>
      </div>
    </>}
    {focus && audit && <section aria-label="专注复核" className="p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">人工已复核 {audit.checks.filter(reviewed).length}/{audit.checks.filter(reviewable).length} · 不改变机器结果</p><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={at <= 0} onClick={() => navigateClause(audit.checks[at - 1].rule_item.id)}>上一条</Button><Select value={check?.rule_item.id} onValueChange={navigateClause}><SelectTrigger aria-label="条款目录" className="max-w-80"><SelectValue/></SelectTrigger><SelectContent>{audit.checks.map(row => <SelectItem key={row.rule_item.id} value={row.rule_item.id}>{row.rule_item.id} · {row.rule_item.category}</SelectItem>)}</SelectContent></Select><Button variant="outline" disabled={!next} onClick={() => next && navigateClause(next.rule_item.id)}>下一待复核</Button></div></div>
      <ResizablePanelGroup orientation="horizontal" className="min-h-[65dvh]"><ResizablePanel defaultSize="45%" minSize="25%"><div className="min-w-0 space-y-4 pr-4"><h2 className="text-lg font-semibold">{check?.rule_item.id} · {check?.rule_item.category}</h2>{basis}<div ref={editorHost}/></div></ResizablePanel><ResizableHandle withHandle/><ResizablePanel defaultSize="55%" minSize="25%"><div className="min-w-0 space-y-4 pl-4"><Select value={chosen?.reference.file_version_id || originalId} onValueChange={id => {setOriginalId(id); setSource(null)}}><SelectTrigger aria-label="选择对照原件"><SelectValue/></SelectTrigger><SelectContent>{originals.filter(row => frozenFileVersionIds.includes(row.file_version_id)).map(row => <SelectItem key={row.file_version_id} value={row.file_version_id}>{row.file?.original_filename || '冻结原件'}</SelectItem>)}</SelectContent></Select>{originalPanel}</div></ResizablePanel></ResizablePanelGroup>
    </section>}
    <HistoricalFindings findings={audit?.unmapped_findings || []} roundId={roundId} resultAttempt={audit?.result_attempt ?? null} versions={frozenFileVersionIds} originals={originals} renderOriginal={renderOriginal} fileVersionId={file.file.id}/>
    <Sheet modal={!wide} open={open} onOpenChange={setOpen}><SheetContent overlay={!wide} onInteractOutside={event => {if (wide) event.preventDefault()}} className={activePane === 'original' || activePane === 'comparison' ? 'overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-6xl' : sideBySide ? 'overflow-hidden data-[side=right]:w-full data-[side=right]:max-w-3xl min-[1920px]:data-[side=right]:max-w-5xl' : wide ? 'overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-md' : 'overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-[38.75rem]'} onCloseAutoFocus={event => {event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus()}}>
      <div className="flex items-start gap-2 pr-8"><div className="min-w-0 flex-1"><SheetHeader><SheetTitle>{check ? check.rule_item.id + ' · ' + check.rule_item.category : file.file.original_filename}</SheetTitle><SheetDescription className="sr-only">查看本轮审核依据与冻结原件</SheetDescription></SheetHeader></div>{listed >= 0 && filtered.length > 1 && <div className="flex shrink-0 items-center gap-1" aria-label="切换审核项"><span className="mr-1 text-xs tabular-nums text-muted-foreground">{listed + 1}/{filtered.length}</span><Button variant="outline" size="icon-sm" aria-label="上一项" title="上一项（K）" disabled={listed <= 0} onClick={() => stepTo(-1)}><ChevronUp/></Button><Button variant="outline" size="icon-sm" aria-label="下一项" title="下一项（J）" disabled={listed >= filtered.length - 1} onClick={() => stepTo(1)}><ChevronDown/></Button></div>}</div>
      <Tabs value={activePane} onValueChange={setPane}><TabsList variant="line"><TabsTrigger value="details">审核详情</TabsTrigger>{!sideBySide && <TabsTrigger value="original">核对原件</TabsTrigger>}{pairedVersions.length > 1 && <TabsTrigger value="comparison">关联原件对照</TabsTrigger>}</TabsList><TabsContent value="details"><div className={sideBySide ? 'grid grid-cols-5 gap-6 pt-4' : 'pt-4'}><div className={sideBySide ? 'col-span-2 max-h-[calc(100dvh-8rem)] overflow-y-auto pr-2' : undefined}>{issue && <Alert variant="destructive"><AlertTitle>结果数据需要核对</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert>}{basis}</div>{sideBySide && <div className="col-span-3 max-h-[calc(100dvh-8rem)] overflow-y-auto">{originalPanel}</div>}</div></TabsContent>{!sideBySide && <TabsContent value="original"><div className="pt-4">{originalPanel}</div></TabsContent>}{pairedVersions.length > 1 && <TabsContent value="comparison"><div className="pt-4">{comparison}</div></TabsContent>}</Tabs>
    </SheetContent></Sheet>
  </section>
}
