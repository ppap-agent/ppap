import {useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {ArrowRight, BookOpen, FileSearch, FileText, Info, Search} from 'lucide-react'
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
import {FileName} from '../journey/file-name'
import {clauseObservation, frozenResultError, sameSnapshot, type ReviewFile} from './result-projection'
import {displayFields, fileDisplayValues, validDisplayInfo} from './display-info-contract'
import {RecordedReview, frozenOriginal} from './recorded-review'
import {HistoricalFindings} from './historical-findings'
import {ReviewEditorContext} from './review-editor'
import {ResizableHandle, ResizablePanel, ResizablePanelGroup} from '../components/ui/resizable'
import {filterChecks, nextUnreviewed, reviewable, reviewed} from './review-navigation'
import {readAuditRoute, writeAuditAddress} from './routes'

type File = ReviewFile
type Evaluation = components['schemas']['AuditClauseEvaluation']
type ReviewCitation = components['schemas']['ReviewCitation']
type SelectedSource = {kind: 'machine'; reference: AuditEvidence} | {kind: 'review'; reference: ReviewCitation}
export type AuditEvidence = components['schemas']['AuditEvidenceReference']
export type OriginalRenderer = (file: components['schemas']['FileVersion'], evidence: AuditEvidence | null, roundId?: string) => ReactNode
const statusLabel = {PASS: '满足', NG: '不符合', UNABLE: '无法判断', ERR: '技术错误', NA: '不适用'}
const executionLabel = {queued: '排队中', running: '审核中', completed: '审核结束', review_failed: '异常'}
const reportLabel = {not_eligible: '无正式报告', pending: '报告待生成', generating: '报告生成中', ready: '报告已就绪', failed: '报告生成失败'}
const evidenceText = (evidence: AuditEvidence, verified: boolean) => evidence.excerpt || (verified ? '该位置内容为空。' : '未提供摘录。')
function checkLabel(check: Evaluation) {
  if (check.execution === 'queued') return '排队中'
  if (check.execution === 'running') return '审核中'
  if (check.execution === 'failed') return '技术错误'
  if (check.coverage !== 'executed') return '未覆盖'
  return check.finding ? statusLabel[check.finding.status] : '结果未知'
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
export function AuditReviewContent({file, roundId, taskId, frozenRule = file.match?.rule, frozenFileVersionIds, originals, renderOriginal, onReport, reportAccess, onEditDisplay, showFileTitle = true}: {file: File; roundId: string; taskId?: string; frozenRule?: components['schemas']['BoundRule'] | null; frozenFileVersionIds: string[]; originals: components['schemas']['AuditArchiveOriginal'][]; renderOriginal?: OriginalRenderer; onReport?: () => void; reportAccess?: components['schemas']['AuditArchiveAccess']; onEditDisplay?: () => void; showFileTitle?: boolean}) {
  const reviewEditor = useContext(ReviewEditorContext)
  const [clause, setClause] = useState(() => new URL(location.href).searchParams.get('clause') || ''), [chosenSource, setSource] = useState<SelectedSource | null>(null)
  const [focus, setFocus] = useState(() => new URL(location.href).searchParams.get('focus') === 'review')
  const [originalId, setOriginalId] = useState(file.file.id)
  const editorHost = useRef<HTMLDivElement>(null), currentEditor = useRef(reviewEditor); currentEditor.current = reviewEditor
  const [open, setOpen] = useState(false), [pane, setPane] = useState('details')
  const [query, setQuery] = useState(''), [filter, setFilter] = useState(() => new URL(location.href).searchParams.get('checkFilter') || 'all'), [section, setSection] = useState('checks')
  const trigger = useRef<HTMLElement | null>(null), comparisonTrigger = useRef<HTMLButtonElement>(null)
  const audit = file.audit, check = audit?.checks.find(row => row.rule_item.id === clause) ?? audit?.checks[0]
  const issue = frozenResultError(roundId, file, frozenFileVersionIds, frozenRule)
  const reportAccessIssue = reportAccess && reportAccess.state !== 'allowed' ? reportAccess.reason || '当前账号不能读取此报告。' : ''
  const review = check?.review !== undefined ? check.review : check?.finding?.review
  const chosen = chosenSource && (chosenSource.kind === 'machine' ? check?.evidence : review?.citations)?.some(row => sameSnapshot(row, chosenSource.reference)) ? chosenSource : null
  const source = chosen?.kind === 'machine' ? chosen.reference : null
  const citation = chosen?.kind === 'review' ? chosen.reference : null
  const authorizedOriginal = (id: string) => frozenOriginal(originals, frozenFileVersionIds, id)
  const original = authorizedOriginal(chosen?.reference.file_version_id || originalId)
  const pairedVersions = [...new Set(check?.evidence.map(item => item.file_version_id) || [])]
  const rule = audit?.rule || frozenRule
  const filtered = filterChecks(audit?.checks || [], query, filter)
  const at = audit?.checks.findIndex(row => row.rule_item.id === check?.rule_item.id) ?? -1
  const next = nextUnreviewed(audit?.checks || [], check?.rule_item.id || '')
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
      setClause(url.searchParams.get('clause') || ''); setFocus(url.searchParams.get('focus') === 'review'); setFilter(url.searchParams.get('checkFilter') || 'all')
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
  const basis = check && <article aria-label="条款判定依据" className="space-y-6">
    <div className="flex flex-wrap items-center gap-2"><Badge shape="square" variant={checkLabel(check) === '不符合' ? 'destructive' : checkLabel(check) === '满足' ? 'success' : 'outline'}>{check.finding && check.coverage === 'executed' && check.execution === 'completed' ? check.finding.status + ' · ' : ''}{checkLabel(check)}</Badge>{review && <Badge shape="square" variant="outline">已有人工复核</Badge>}</div>
    <section className="space-y-2"><h3 className="font-medium">机器判定依据</h3><p className="whitespace-pre-wrap text-sm leading-relaxed">{clauseObservation(check)}</p>{check.scope && <p className="text-xs text-muted-foreground">检查范围：已查 {check.scope.checked}/{check.scope.total}，未知 {check.scope.unknown}。</p>}</section>
    <section className="space-y-2"><h3 className="font-medium">审核要求原文</h3><p className="whitespace-pre-wrap border-l-2 pl-4 text-sm leading-relaxed">{check.rule_item.rule_to_check}</p></section>
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
  return <section aria-label="审核详情" className="min-w-0">
    <header className={`min-w-0 border-b px-6 py-4 max-sm:px-4 ${showFileTitle ? 'space-y-3' : 'flex flex-wrap items-center justify-between gap-3'}`}>
      {showFileTitle && <h2 className="break-words text-lg font-semibold">{file.file.original_filename}</h2>}
      <div className={showFileTitle ? 'min-w-0 space-y-2' : 'flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2'}>
      {audit && <div className="flex flex-wrap items-center gap-2"><Badge shape="square" variant={!issue && audit.conclusion === 'NG' ? 'destructive' : !issue && audit.conclusion === 'PASS' ? 'success' : 'outline'}>{issue ? '结果待核对' : <>{audit.execution === 'review_failed' ? executionLabel.review_failed : audit.conclusion === 'UNKNOWN' ? '尚有未确定项' : audit.conclusion || executionLabel[audit.execution]} · 符合 {audit.counts.pass_count} / 不符合 {audit.counts.ng_count}</>}</Badge>{!issue && <><span className="text-xs text-muted-foreground">已结束 {audit.progress.completed}/{audit.progress.total} 项</span>{audit.counts.unknown_count > 0 && <span className="text-xs text-muted-foreground">无法判断 {audit.counts.unknown_count}</span>}{audit.counts.not_covered_count > 0 && <span className="text-xs text-muted-foreground">未覆盖 {audit.counts.not_covered_count}</span>}{audit.counts.error_count > 0 && <span className="text-xs text-muted-foreground">技术错误 {audit.counts.error_count}</span>}</>}</div>}
      {rule && <p className="break-words text-xs text-muted-foreground">本轮规则：{rule.title}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={showOriginal}><FileSearch/>查看原件</Button>
        {audit && <Button variant={focus ? "secondary" : "outline"} onClick={() => navigateClause(check?.rule_item.id || "", !focus)}>{focus ? "返回审核项" : "专注复核（选做）"}</Button>}
        <Button variant="outline" disabled={!onReport || Boolean(issue || reportAccessIssue) || !audit || !['ready', 'failed', 'pending', 'generating'].includes(audit.report.state)} onClick={onReport}><FileText/>查看报告</Button>
        <Sheet><SheetTrigger asChild><Button variant="outline"><Info/>文件信息</Button></SheetTrigger><SheetContent onEscapeKeyDown={event => {if (event.target instanceof Element && event.target.closest('[data-slot="dialog-content"]')) event.preventDefault()}} className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-2xl"><SheetHeader><SheetTitle>文件信息</SheetTitle><SheetDescription className="sr-only">查看文件显示信息与本轮确认的规则</SheetDescription></SheetHeader><FileName name={file.file.original_filename}/><DisplayInformation file={file} onEdit={onEditDisplay}/>{rule && <section aria-label="本轮审核规则" data-rule-version={rule.id} className="space-y-4 border-t pt-4"><h3 className="flex items-center gap-2 font-semibold"><BookOpen className="size-4"/>本轮审核规则</h3><p className="text-sm">{rule.title}</p>{rule.body.kind === 'engine' ? rule.body.rules.map(item => <article key={item.id} className="space-y-2 border-t pt-3"><h4 className="font-medium">{item.id} · {item.category}</h4><p className="whitespace-pre-wrap text-sm">{item.rule_to_check}</p></article>) : <p className="text-sm">文件存在性检查。</p>}</section>}</SheetContent></Sheet>
      </div>
      {audit && (issue || reportAccessIssue || audit.report.state !== 'ready') && <p className="text-xs text-muted-foreground">{issue ? '报告资格待核对。' : reportAccessIssue || (audit.report.state === 'not_eligible' && audit.counts.unknown_count + audit.counts.not_covered_count + audit.counts.error_count > 0 ? '部分审核项尚未取得明确结果，不生成完整 PASS/NG 报告。' : reportLabel[audit.report.state])}{!issue && !reportAccessIssue && audit.report.reason && ' · ' + audit.report.reason}</p>}
    </header>
    {issue && <div className="px-6 pt-4"><Alert variant="destructive"><AlertTitle>结果数据需要核对</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert></div>}
    {!audit ? <p role="status">审核结果尚未取得，请读取当前记录。</p> : <>
      {audit.execution === 'review_failed' && <Alert variant="destructive"><AlertTitle>本文件审核异常，未取得完整结果。</AlertTitle><AlertDescription>{audit.failure?.message || '没有可用结果。'}</AlertDescription></Alert>}
      <Tabs hidden={focus} value={section} onValueChange={setSection}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-3 max-sm:px-4"><TabsList variant="line"><TabsTrigger value="checks">审核项 ({audit.checks.length})</TabsTrigger><TabsTrigger value="summary">审核总结</TabsTrigger></TabsList>
          {section === 'checks' && <div className="flex min-w-0 flex-wrap items-center gap-2"><div className="relative min-w-0 flex-1 sm:w-56"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search className="size-3.5"/></span><Input aria-label="搜索审核项" placeholder="搜索审核项或要求" value={query} onChange={event => setQuery(event.target.value)} iconStart/></div><Select value={filter} onValueChange={value => {setFilter(value); const url = new URL(location.href); if (value === "all") url.searchParams.delete("checkFilter"); else url.searchParams.set("checkFilter", value); writeAuditAddress(url)}}><SelectTrigger aria-label="审核结果筛选"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部审核项</SelectItem><SelectItem value="ng">不符合</SelectItem><SelectItem value="unable">无法判断</SelectItem><SelectItem value="unreviewed">未复核</SelectItem><SelectItem value="attention">需关注</SelectItem><SelectItem value="pass">满足</SelectItem></SelectContent></Select></div>}
        </div>
        <TabsContent value="checks"><div className="space-y-4 px-6 pt-2 pb-6 max-sm:px-4">
          <div className="min-w-0 overflow-hidden rounded-md border"><Table className="min-w-xl table-fixed">
            <TableHeader variant="muted"><TableRow><TableHead className="w-14"><span className="text-xs text-muted-foreground">序号</span></TableHead><TableHead><span className="text-xs text-muted-foreground">审核要求与说明</span></TableHead><TableHead className="w-24"><span className="text-xs text-muted-foreground">审核结果</span></TableHead><TableHead className="w-32 text-right"><span className="text-xs text-muted-foreground">操作</span></TableHead></TableRow></TableHeader>
            <TableBody>{filtered.map(row => <TableRow key={row.rule_item.id} data-state={open && check?.rule_item.id === row.rule_item.id ? 'selected' : undefined}>
              <TableCell><span className="text-xs text-muted-foreground">{row.rule_item.id}</span></TableCell>
              <TableCell className="whitespace-normal"><div className="py-1.5"><p className="line-clamp-1 break-words font-medium" title={row.rule_item.category}>{row.rule_item.category}</p><p className="mt-1 line-clamp-1 break-words text-xs text-muted-foreground">{clauseObservation(row)}</p></div></TableCell>
              <TableCell><Badge shape="square" variant={checkLabel(row) === '不符合' ? 'destructive' : checkLabel(row) === '满足' ? 'success' : 'outline'}>{checkLabel(row)}</Badge></TableCell>
              <TableCell className="w-32 text-right"><Button variant="ghost" size="sm" className="whitespace-nowrap" aria-label={'查看依据 ' + row.rule_item.id} aria-haspopup="dialog" onClick={() => showEvidence(row)}>查看依据<ArrowRight/></Button></TableCell>
            </TableRow>)}</TableBody>
          </Table></div>
          {!filtered.length && <p className="text-muted-foreground">没有符合条件的审核项。</p>}
        </div></TabsContent>
        <TabsContent value="summary"><div className="space-y-4 px-6 pt-2 pb-6 max-sm:px-4"><h3 className="font-semibold">审核总结</h3>{issue ? <p role="alert">{issue}</p> : audit.summary ? <><p>{audit.summary.text}</p>{audit.summary.corrective_actions.length > 0 && <section className="space-y-2"><h4 className="font-medium">核心整改建议</h4><ul className="list-disc space-y-1 pl-5">{audit.summary.corrective_actions.map(text => <li key={text}>{text}</li>)}</ul></section>}{audit.summary.extended_suggestions.length > 0 && <section className="space-y-2"><h4 className="font-medium">AI 延伸建议（不是客户要求）</h4><ul className="list-disc space-y-1 pl-5">{audit.summary.extended_suggestions.map(text => <li key={text}>{text}</li>)}</ul></section>}</> : <p className="text-muted-foreground">{audit.execution === 'review_failed' ? '审核异常，没有通过总结。' : '总结尚未生成。'}</p>}</div></TabsContent>
      </Tabs>
    </>}
    {focus && audit && <section aria-label="专注复核" className="p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">人工已复核 {audit.checks.filter(reviewed).length}/{audit.checks.filter(reviewable).length} · 不改变机器结果</p><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={at <= 0} onClick={() => navigateClause(audit.checks[at - 1].rule_item.id)}>上一条</Button><Select value={check?.rule_item.id} onValueChange={navigateClause}><SelectTrigger aria-label="条款目录" className="max-w-80"><SelectValue/></SelectTrigger><SelectContent>{audit.checks.map(row => <SelectItem key={row.rule_item.id} value={row.rule_item.id}>{row.rule_item.id} · {row.rule_item.category}</SelectItem>)}</SelectContent></Select><Button variant="outline" disabled={!next} onClick={() => next && navigateClause(next.rule_item.id)}>下一待复核</Button></div></div>
      <ResizablePanelGroup orientation="horizontal" className="min-h-[65dvh]"><ResizablePanel defaultSize="45%" minSize="25%"><div className="min-w-0 space-y-4 pr-4"><h2 className="text-lg font-semibold">{check?.rule_item.id} · {check?.rule_item.category}</h2>{basis}<div ref={editorHost}/></div></ResizablePanel><ResizableHandle withHandle/><ResizablePanel defaultSize="55%" minSize="25%"><div className="min-w-0 space-y-4 pl-4"><Select value={chosen?.reference.file_version_id || originalId} onValueChange={id => {setOriginalId(id); setSource(null)}}><SelectTrigger aria-label="选择对照原件"><SelectValue/></SelectTrigger><SelectContent>{originals.filter(row => frozenFileVersionIds.includes(row.file_version_id)).map(row => <SelectItem key={row.file_version_id} value={row.file_version_id}>{row.file?.original_filename || '冻结原件'}</SelectItem>)}</SelectContent></Select>{originalPanel}</div></ResizablePanel></ResizablePanelGroup>
    </section>}
    <HistoricalFindings findings={audit?.unmapped_findings || []} roundId={roundId} resultAttempt={audit?.result_attempt ?? null} versions={frozenFileVersionIds} originals={originals} renderOriginal={renderOriginal} fileVersionId={file.file.id}/>
    <Sheet open={open} onOpenChange={setOpen}><SheetContent className={pane === 'original' || pane === 'comparison' ? 'overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-6xl' : 'overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-[38.75rem]'} onCloseAutoFocus={event => {event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus()}}>
      <div className="pr-8"><SheetHeader><SheetTitle>{check ? check.rule_item.id + ' · ' + check.rule_item.category : file.file.original_filename}</SheetTitle><SheetDescription className="sr-only">查看本轮审核依据与冻结原件</SheetDescription></SheetHeader></div>
      <Tabs value={pane} onValueChange={setPane}><TabsList variant="line"><TabsTrigger value="details">审核详情</TabsTrigger><TabsTrigger value="original">核对原件</TabsTrigger>{pairedVersions.length > 1 && <TabsTrigger value="comparison">关联原件对照</TabsTrigger>}</TabsList><TabsContent value="details"><div className="pt-4">{issue && <Alert variant="destructive"><AlertTitle>结果数据需要核对</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert>}{basis}</div></TabsContent><TabsContent value="original"><div className="pt-4">{originalPanel}</div></TabsContent>{pairedVersions.length > 1 && <TabsContent value="comparison"><div className="pt-4">{comparison}</div></TabsContent>}</Tabs>
    </SheetContent></Sheet>
  </section>
}
