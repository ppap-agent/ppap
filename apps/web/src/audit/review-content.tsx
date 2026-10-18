import {useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import type {components} from '../generated/openapi'
import {sourceLocation} from '../helpers'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Input} from '../components/ui/input'
import {ResizableHandle, ResizablePanel, ResizablePanelGroup} from '../components/ui/resizable'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {FileName} from '../journey/file-name'
import {clauseObservation, checkScopeComplete, frozenResultError, sameSnapshot, type ReviewFile} from './result-projection'
import {displayFields, fileDisplayValues, validDisplayInfo} from './display-info-contract'
import {RecordedReview, frozenOriginal} from './recorded-review'
import {HistoricalFindings} from './historical-findings'
import {ReviewEditorContext} from './review-editor'

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
  if (check.finding?.status === 'PASS' && !checkScopeComplete(check)) return '范围未完成'
  if (check.finding && ['PASS', 'NG', 'NA'].includes(check.finding.status) && !check.evidence_verified) return '依据待核对'
  return check.finding ? statusLabel[check.finding.status] : '结果未知'
}

function DisplayInformation({file, onEdit}: {file: File; onEdit?: () => void}) {
  const values = fileDisplayValues(file)
  const corrected = validDisplayInfo(file.display_info) && file.display_info.revision > 0
  if (file.match === undefined && file.display_info === undefined) return null
  return <section aria-label="本次审核文件信息" className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-muted-foreground">页面显示信息{corrected ? ' · 已更正' : ''}</p>{onEdit && <Button variant="outline" size="sm" disabled={!validDisplayInfo(file.display_info)} onClick={onEdit}>更正显示信息</Button>}</div>
    <dl className="grid grid-cols-2 gap-3 text-sm xl:grid-cols-4">{displayFields.map(([field, label]) => <div className="min-w-0" key={field}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 break-words">{values[field] || '—'}</dd></div>)}</dl>
    {corrected && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">查看原始识别信息</Button></CollapsibleTrigger><CollapsibleContent><section aria-label="原始识别信息" className="space-y-2 pt-2"><p className="text-xs text-muted-foreground">当次审核依据未改变，既有报告不会随显示更正更新。</p><dl className="grid grid-cols-2 gap-3 text-sm">{displayFields.map(([field, label]) => <div key={field}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-words">{file.match?.metadata[field] || '—'}</dd></div>)}</dl></section></CollapsibleContent></Collapsible>}
  </section>
}

/** P5 and P10 share result and original content, not private task chrome. */
export function AuditReviewContent({file, roundId, frozenRule = file.match?.rule, frozenFileVersionIds, originals, renderOriginal, onReport, onEditDisplay}: {file: File; roundId: string; frozenRule?: components['schemas']['BoundRule'] | null; frozenFileVersionIds: string[]; originals: components['schemas']['AuditArchiveOriginal'][]; renderOriginal?: OriginalRenderer; onReport?: () => void; onEditDisplay?: () => void}) {
  const reviewEditor = useContext(ReviewEditorContext)
  const [clause, setClause] = useState(''), [chosenSource, setSource] = useState<SelectedSource | null>(null)
  const [pane, setPane] = useState('details'), [query, setQuery] = useState(''), [filter, setFilter] = useState('all')
  const container = useRef<HTMLDivElement>(null), [wide, setWide] = useState(false)
  useEffect(() => {const observer = new ResizeObserver(entries => setWide(entries[0].contentRect.width >= 960)); if (container.current) observer.observe(container.current); return () => observer.disconnect()}, [])
  const audit = file.audit, check = audit?.checks.find(row => row.rule_item.id === clause) ?? audit?.checks[0]
  const issue = frozenResultError(roundId, file, frozenFileVersionIds, frozenRule)
  const review = check?.review !== undefined ? check.review : check?.finding?.review
  const chosen = chosenSource && (chosenSource.kind === 'machine' ? check?.evidence : review?.citations)?.some(row => sameSnapshot(row, chosenSource.reference)) ? chosenSource : null
  const source = chosen?.kind === 'machine' ? chosen.reference : null
  const citation = chosen?.kind === 'review' ? chosen.reference : null
  function authorizedOriginal(id: string) {
    return frozenOriginal(originals, frozenFileVersionIds, id)
  }
  const original = authorizedOriginal(chosen?.reference.file_version_id || file.file.id)
  const filtered = audit?.checks.filter(row => `${row.rule_item.id} ${row.rule_item.category} ${row.rule_item.rule_to_check}`.toLowerCase().includes(query.toLowerCase()) && (filter === 'all' || filter === 'attention' && !['满足', '不适用'].includes(checkLabel(row)))) ?? []
  const originalPanel = <section aria-label="原件预览" data-file-version={original?.file_version_id} className="min-w-0 space-y-4"><h2 className="font-semibold">原件对照</h2><p className="text-sm text-muted-foreground">当前条款：{check?.rule_item.id || '—'} · {check?.rule_item.category || '尚未取得审核项'}</p>{original ? <><FileName name={original.file?.original_filename || '该次证据原件'}/>{source && <><p className="text-sm">{source.locator ? `证据位置：${sourceLocation({locator: source.locator})}` : '未提供具体位置，请按摘录查找。'}</p>{source.location_gap && <p className="text-sm text-muted-foreground">{source.location_gap}</p>}<p className="text-xs text-muted-foreground">{source.rendition === 'derived' ? '派生内容引用，不等于原件定位已验证。' : '冻结原件引用'}</p><p className="text-sm">{evidenceText(source, check?.evidence_verified === true)}</p></>}{citation && <section aria-label="人工复核引用" className="space-y-2 text-sm"><h3 className="font-medium">人工记录位置</h3><p className="whitespace-pre-wrap">{citation.location}</p><p className="whitespace-pre-wrap text-muted-foreground">{citation.excerpt}</p></section>}{original.access.state !== 'allowed' || !original.file ? <Alert><AlertTitle>本次原件不可访问</AlertTitle><AlertDescription>{original.access.reason || '暂时无法读取此原件。'}</AlertDescription></Alert> : renderOriginal ? renderOriginal(original.file, source, roundId) : <Alert><AlertTitle>原件暂不可预览</AlertTitle><AlertDescription>请下载原件查看。</AlertDescription></Alert>}</> : <Alert variant="destructive"><AlertTitle>不能读取此证据原件</AlertTitle><AlertDescription>此证据不在本次审核的文件中。</AlertDescription></Alert>}</section>
  const details = <section aria-label="审核详情" className="min-w-0 space-y-5">
    <header className="space-y-3"><h2 className="text-lg font-semibold"><FileName name={file.file.original_filename}/></h2><DisplayInformation file={file} onEdit={onEditDisplay}/><p className="text-xs text-muted-foreground">{file.match?.document_type || (audit?.rule.body.kind === 'engine' ? audit.rule.body.type : audit?.rule.body.kind === 'presence' ? '文件存在性' : '—')} · 审核规则：{audit?.rule.title || file.match?.rule?.title || '—'}</p></header>
    {issue && <Alert variant="destructive"><AlertTitle>结果数据需要核对</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert>}
    {!audit ? <p role="status">审核结果尚未取得，请读取当前记录。</p> : <>
      <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{issue ? '结果待核对' : executionLabel[audit.execution]}</Badge>{audit.conclusion && !issue && <Badge variant="secondary">{audit.conclusion}</Badge>}{!issue && <span className="text-muted-foreground">已结束 {audit.progress.completed}/{audit.progress.total} 项</span>}</div>
      {!issue && <p className="text-sm">{audit.counts.unknown_count ? `不符合 ${audit.counts.ng_count} · 无法判断 ${audit.counts.unknown_count}` : `满足 ${audit.counts.pass_count} · 不符合 ${audit.counts.ng_count}`}{audit.counts.not_covered_count ? ` · 未覆盖 ${audit.counts.not_covered_count}` : ''}{audit.counts.error_count ? ` · 技术错误 ${audit.counts.error_count}` : ''}</p>}
      {audit.execution === 'review_failed' && <Alert variant="destructive"><AlertTitle>本文件审核异常，未取得完整结果。</AlertTitle><AlertDescription>{audit.failure?.message || '没有可用结果。'}</AlertDescription></Alert>}
      <div className="flex flex-wrap items-center gap-2"><Input aria-label="搜索审核项" placeholder="搜索审核项或要求" className="max-w-sm" value={query} onChange={event => setQuery(event.target.value)}/><Select value={filter} onValueChange={setFilter}><SelectTrigger aria-label="审核结果筛选"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部审核项</SelectItem><SelectItem value="attention">需关注</SelectItem></SelectContent></Select></div>
      <Table className="table-fixed"><TableHeader><TableRow><TableHead className="w-16">序号</TableHead><TableHead>审核项</TableHead><TableHead className="w-24">审核结果</TableHead><TableHead className="w-24">判定依据</TableHead></TableRow></TableHeader><TableBody>{filtered.map(row => <TableRow key={row.rule_item.id} data-state={check?.rule_item.id === row.rule_item.id ? 'selected' : undefined}><TableCell>{row.rule_item.id}</TableCell><TableCell className="whitespace-normal">{row.rule_item.category}</TableCell><TableCell className="whitespace-normal">{checkLabel(row)}</TableCell><TableCell><Button variant="ghost" size="sm" aria-label={`查看依据 ${row.rule_item.id}`} onClick={() => {setClause(row.rule_item.id); setSource(null)}}>查看依据</Button></TableCell></TableRow>)}</TableBody></Table>{!filtered.length && <p className="text-muted-foreground">没有符合条件的审核项。</p>}
      {check && <article className="space-y-4 border-t pt-4"><h3 className="font-semibold">{check.rule_item.id} · {check.rule_item.category}</h3><div className="space-y-2"><h4 className="font-medium">审核要求</h4><p className="whitespace-pre-wrap">{check.rule_item.rule_to_check}</p></div><div className="space-y-2"><h4 className="font-medium">机器观察</h4><p>{clauseObservation(check)}</p>{check.scope && <p className="text-xs text-muted-foreground">检查范围：已查 {check.scope.checked}/{check.scope.total}，未知 {check.scope.unknown}。</p>}</div><div className="space-y-3"><h4 className="font-medium">来源证据</h4>{check.evidence.length ? check.evidence.map((evidence, index) => {
        const target = authorizedOriginal(evidence.file_version_id)
        return <div key={`${evidence.file_version_id}-${index}`} data-file-version={evidence.file_version_id} className="space-y-2 border-l pl-3"><p className="whitespace-pre-wrap text-sm">{evidenceText(evidence, check.evidence_verified)}</p><Button variant="outline" size="sm" disabled={!target} aria-label={`核对 ${target?.file?.original_filename || '不在本轮的原件'}`} onClick={() => {setClause(check.rule_item.id); setSource({kind: 'machine', reference: evidence}); setPane('original')}}>{target ? <FileName name={target.file?.original_filename || '该次证据原件'}/> : '原件不在冻结范围'}</Button></div>
      }) : <p className="text-muted-foreground">未提供来源证据，请核对原件。</p>}</div>
        {review && <RecordedReview review={review} originalFor={authorizedOriginal} onCitation={cite => {setSource({kind: 'review', reference: cite}); setPane('original')}}/>}
        {reviewEditor && !issue && check.review_item_key && ['completed', 'failed'].includes(check.execution) && <Button variant="outline" size="sm" onClick={() => reviewEditor.open({roundId, itemKey: check.review_item_key!, title: `${check.rule_item.id} · ${check.rule_item.category}`, attempt: audit.result_attempt, files: originals.filter(row => frozenFileVersionIds.includes(row.file_version_id)).map(row => ({id: row.file_version_id, name: row.file?.original_filename || '本轮冻结原件'}))})}>记录人工复核</Button>}
      </article>}
      <section className="space-y-3 border-t pt-4"><h3 className="font-semibold">审核总结</h3>{issue ? <p role="alert">{issue}</p> : audit.summary ? <><p>{audit.summary.text}</p>{audit.summary.corrective_actions.length > 0 && <div className="space-y-2"><h4 className="font-medium">核心整改建议</h4><ul className="list-disc space-y-1 pl-5">{audit.summary.corrective_actions.map(text => <li key={text}>{text}</li>)}</ul></div>}{audit.summary.extended_suggestions.length > 0 && <div className="space-y-2"><h4 className="font-medium">AI 延伸建议（不是客户要求）</h4><ul className="list-disc space-y-1 pl-5">{audit.summary.extended_suggestions.map(text => <li key={text}>{text}</li>)}</ul></div>}</> : <p className="text-muted-foreground">{audit.execution === 'review_failed' ? '审核异常，没有通过总结。' : '总结尚未生成。'}</p>}</section>
      <section className="space-y-3 border-t pt-4"><h3 className="font-semibold">报告</h3><p>{issue ? '报告资格待核对。' : audit.report.state === 'not_eligible' && audit.counts.unknown_count + audit.counts.not_covered_count + audit.counts.error_count > 0 ? '未完整验证，不生成正式 PASS/NG 报告。' : reportLabel[audit.report.state]}</p>{!issue && audit.report.reason && <p className="text-muted-foreground">{audit.report.reason}</p>}<Button variant="outline" disabled={Boolean(issue) || !onReport || !['ready', 'failed', 'pending', 'generating'].includes(audit.report.state)} onClick={onReport}>{audit.report.state === 'failed' ? '查看报告失败原因' : '查看报告'}</Button></section>
    </>}
    <HistoricalFindings findings={audit?.unmapped_findings || []} roundId={roundId} resultAttempt={audit?.result_attempt ?? null} versions={frozenFileVersionIds} originals={originals} renderOriginal={renderOriginal} fileVersionId={file.file.id}/>
  </section>
  return <div ref={container} className="min-w-0">{wide ? <ResizablePanelGroup orientation="horizontal"><ResizablePanel defaultSize="40%" minSize="320px"><div className="pr-5">{details}</div></ResizablePanel><ResizableHandle withHandle/><ResizablePanel defaultSize="60%" minSize="480px"><div className="pl-5">{originalPanel}</div></ResizablePanel></ResizablePanelGroup> : <Tabs value={pane} onValueChange={setPane}><TabsList variant="line"><TabsTrigger value="details">审核详情</TabsTrigger><TabsTrigger value="original">核对原件</TabsTrigger></TabsList><TabsContent value="details">{details}</TabsContent><TabsContent value="original">{originalPanel}</TabsContent></Tabs>}</div>
}
