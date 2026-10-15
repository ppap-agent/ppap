import {useEffect, useRef, useState, type ReactNode} from 'react'
import type {components} from '../generated/openapi'
import {sourceLocation} from '../helpers'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ResizableHandle, ResizablePanel, ResizablePanelGroup} from '../components/ui/resizable'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {FileName} from '../journey/file-name'
import {frozenResultError, sameSnapshot} from './result-projection'

type File = components['schemas']['AuditTaskFile']
type Evaluation = components['schemas']['AuditClauseEvaluation']
export type AuditEvidence = components['schemas']['AuditEvidenceReference']
export type OriginalRenderer = (file: components['schemas']['FileVersion'], evidence: AuditEvidence | null) => ReactNode
const statusLabel = {PASS: '满足', NG: '不符合', UNABLE: '无法判断', ERR: '技术错误', NA: '不适用'}
const executionLabel = {queued: '排队中', running: '审核中', completed: '审核结束', review_failed: '异常'}
const reportLabel = {not_eligible: '无正式报告', pending: '报告待生成', generating: '报告生成中', ready: '报告已就绪', failed: '报告生成失败'}
function checkLabel(check: Evaluation) {
  if (check.execution === 'queued') return '排队中'
  if (check.execution === 'running') return '审核中'
  if (check.execution === 'failed') return '技术错误'
  if (check.coverage !== 'executed') return '未覆盖'
  if (check.finding && ['PASS', 'NG', 'NA'].includes(check.finding.status) && !check.evidence_verified) return '依据待核对'
  return check.finding ? statusLabel[check.finding.status] : '结果未知'
}

/** P5 and P10 share result and original content, not private task chrome. */
export function AuditReviewContent({file, roundId, frozenFileVersionIds, originals, renderOriginal, onReport}: {file: File; roundId: string; frozenFileVersionIds: string[]; originals: components['schemas']['AuditArchiveOriginal'][]; renderOriginal?: OriginalRenderer; onReport: () => void}) {
  const [clause, setClause] = useState(''), [chosenSource, setSource] = useState<AuditEvidence | null>(null)
  const [pane, setPane] = useState('details'), [query, setQuery] = useState(''), [filter, setFilter] = useState('all')
  const container = useRef<HTMLDivElement>(null), [wide, setWide] = useState(false)
  useEffect(() => {const observer = new ResizeObserver(entries => setWide(entries[0].contentRect.width >= 960)); if (container.current) observer.observe(container.current); return () => observer.disconnect()}, [])
  const audit = file.audit, check = audit?.checks.find(row => row.rule_item.id === clause) ?? audit?.checks[0]
  const issue = frozenResultError(roundId, file, frozenFileVersionIds)
  const source = chosenSource && check?.evidence.some(row => sameSnapshot(row, chosenSource)) ? chosenSource : null
  function authorizedOriginal(id: string) {
    const matches = originals.filter(row => row.file_version_id === id)
    return frozenFileVersionIds.includes(id) && matches.length === 1 && (!matches[0].file || matches[0].file.id === id) ? matches[0] : undefined
  }
  const original = authorizedOriginal(source?.file_version_id || file.file.id)
  const filtered = audit?.checks.filter(row => `${row.rule_item.id} ${row.rule_item.category} ${row.rule_item.rule_to_check}`.toLowerCase().includes(query.toLowerCase()) && (filter === 'all' || filter === 'attention' && !['满足', '不适用'].includes(checkLabel(row)))) ?? []
  const originalPanel = <section aria-label="原件预览" className="min-w-0 space-y-4"><h2 className="font-semibold">原件对照</h2><p className="text-sm text-muted-foreground">当前条款：{check?.rule_item.id || '—'} · {check?.rule_item.category || '尚未取得审核项'}</p>{original ? <><FileName name={original.file?.original_filename || '该次证据原件'}/><p className="break-all text-xs text-muted-foreground">冻结版本：{original.file_version_id}</p>{source && <><p className="text-sm">{source.locator ? `证据位置：${sourceLocation({locator: source.locator})}` : '没有可靠定位；请按摘录人工查找，不显示位置高亮。'}</p>{source.location_gap && <p className="text-sm text-muted-foreground">{source.location_gap}</p>}<p className="text-xs text-muted-foreground">{source.rendition === 'derived' ? '派生内容引用，不等于原件定位已验证。' : '冻结原件引用'}</p></>}{original.access.state !== 'allowed' || !original.file ? <Alert><AlertTitle>本次原件不可访问</AlertTitle><AlertDescription>{original.access.reason || '尚未确认读取许可，未改开最新原件。'}</AlertDescription></Alert> : renderOriginal ? renderOriginal(original.file, source) : <Alert><AlertTitle>原件渲染尚未接入</AlertTitle><AlertDescription>不能用示例版本身份请求真实原件。</AlertDescription></Alert>}</> : <Alert variant="destructive"><AlertTitle>不能读取此证据原件</AlertTitle><AlertDescription>引用不在本轮授权的冻结文件中，未扩大访问范围。</AlertDescription></Alert>}</section>
  const details = <section aria-label="审核详情" className="min-w-0 space-y-5">
    <header className="space-y-3"><h2 className="text-lg font-semibold"><FileName name={file.file.original_filename}/></h2><p className="text-xs text-muted-foreground">{file.match?.document_type || '—'} · {file.match?.metadata.customer || '—'} · 冻结规则：{audit?.rule.title || file.match?.rule?.title || '—'}</p><p className="break-all text-xs text-muted-foreground">{audit?.rule.id || '—'} · 执行尝试 {audit?.result_attempt ?? '待执行'}</p></header>
    {issue && <Alert variant="destructive"><AlertTitle>结果数据需要核对</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert>}
    {!audit ? <p role="status">审核结果尚未取得，请读取当前记录。</p> : <>
      <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{executionLabel[audit.execution]}</Badge>{audit.conclusion && !issue && <Badge variant="secondary">{audit.conclusion}</Badge>}<span className="text-muted-foreground">已结束 {audit.progress.completed}/{audit.progress.total} 项</span></div>
      {!issue && <p className="text-sm">{audit.counts.unknown_count ? `不符合 ${audit.counts.ng_count} · 无法判断 ${audit.counts.unknown_count}` : `满足 ${audit.counts.pass_count} · 不符合 ${audit.counts.ng_count}`}{audit.counts.not_covered_count ? ` · 未覆盖 ${audit.counts.not_covered_count}` : ''}{audit.counts.error_count ? ` · 技术错误 ${audit.counts.error_count}` : ''}</p>}
      {audit.execution === 'review_failed' && <Alert variant="destructive"><AlertTitle>本文件审核异常，其他文件结果不受影响。</AlertTitle><AlertDescription>{audit.failure?.message || '没有可用结果。'}<p>本页面不提供重审、重试审核或移除此文件。</p></AlertDescription></Alert>}
      <div className="flex flex-wrap items-center gap-2"><Input aria-label="搜索审核项" placeholder="搜索审核项或要求" className="max-w-sm" value={query} onChange={event => setQuery(event.target.value)}/><Select value={filter} onValueChange={setFilter}><SelectTrigger aria-label="审核结果筛选"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部审核项</SelectItem><SelectItem value="attention">需关注</SelectItem></SelectContent></Select></div>
      <Table className="table-fixed"><TableHeader><TableRow><TableHead className="w-16">序号</TableHead><TableHead>审核项</TableHead><TableHead className="w-24">审核结果</TableHead><TableHead className="w-24">判定依据</TableHead></TableRow></TableHeader><TableBody>{filtered.map(row => <TableRow key={row.rule_item.id} data-state={check?.rule_item.id === row.rule_item.id ? 'selected' : undefined}><TableCell>{row.rule_item.id}</TableCell><TableCell className="whitespace-normal">{row.rule_item.category}</TableCell><TableCell className="whitespace-normal">{checkLabel(row)}</TableCell><TableCell><Button variant="ghost" size="sm" aria-label={`查看依据 ${row.rule_item.id}`} onClick={() => {setClause(row.rule_item.id); setSource(null)}}>查看依据</Button></TableCell></TableRow>)}</TableBody></Table>{!filtered.length && <p className="text-muted-foreground">没有符合条件的审核项。</p>}
      {check && <article className="space-y-4 border-t pt-4"><h3 className="font-semibold">{check.rule_item.id} · {check.rule_item.category}</h3><div className="space-y-2"><h4 className="font-medium">冻结规则要求</h4><p className="whitespace-pre-wrap">{check.rule_item.rule_to_check}</p></div><div className="space-y-2"><h4 className="font-medium">机器观察</h4><p>{check.execution === 'queued' || check.execution === 'running' ? '本项尚未完成，不提前显示审核结论。' : check.finding?.classification || check.scope_notice || '没有可用的机器观察。'}</p>{check.scope && <p className="text-xs text-muted-foreground">检查范围：已查 {check.scope.checked}/{check.scope.total}，未知 {check.scope.unknown}。{check.scope_notice}</p>}</div><div className="space-y-3"><h4 className="font-medium">来源证据</h4>{check.evidence.length ? check.evidence.map((evidence, index) => {
        const target = authorizedOriginal(evidence.file_version_id)
        return <div key={`${evidence.file_version_id}-${index}`} className="space-y-2 border-l pl-3"><p className="whitespace-pre-wrap text-sm">{evidence.excerpt || '未提供摘录。'}</p><p className="break-all text-xs text-muted-foreground">冻结版本：{evidence.file_version_id}</p><Button variant="outline" size="sm" disabled={!target} aria-label={`核对 ${target?.file?.original_filename || '不在本轮的原件'}`} onClick={() => {setClause(check.rule_item.id); setSource(evidence); setPane('original')}}>{target ? <FileName name={target.file?.original_filename || '该次证据原件'}/> : '原件不在冻结范围'}</Button></div>
      }) : <p className="text-muted-foreground">未提供来源证据，不能把空证据当作审核通过。</p>}</div></article>}
      <section className="space-y-3 border-t pt-4"><h3 className="font-semibold">审核总结</h3>{issue ? <p role="alert">{issue}</p> : audit.summary ? <><p>{audit.summary.text}</p>{audit.summary.corrective_actions.length > 0 && <div className="space-y-2"><h4 className="font-medium">核心整改建议</h4><ul className="list-disc space-y-1 pl-5">{audit.summary.corrective_actions.map(text => <li key={text}>{text}</li>)}</ul></div>}{audit.summary.extended_suggestions.length > 0 && <div className="space-y-2"><h4 className="font-medium">AI 延伸建议（不是客户要求）</h4><ul className="list-disc space-y-1 pl-5">{audit.summary.extended_suggestions.map(text => <li key={text}>{text}</li>)}</ul></div>}</> : <p className="text-muted-foreground">{audit.execution === 'review_failed' ? '审核异常，没有通过总结。' : '总结尚未生成。'}</p>}</section>
      <section className="space-y-3 border-t pt-4"><h3 className="font-semibold">报告</h3><p>{issue ? '报告资格待核对。' : audit.report.state === 'not_eligible' ? '未完整验证，不生成正式 PASS/NG 报告。' : reportLabel[audit.report.state]}</p>{!issue && audit.report.reason && <p className="text-muted-foreground">{audit.report.reason}</p>}<Button variant="outline" disabled={Boolean(issue) || !['ready', 'failed', 'pending', 'generating'].includes(audit.report.state)} onClick={onReport}>{audit.report.state === 'failed' ? '查看报告失败原因' : '查看报告'}</Button></section>
    </>}
  </section>
  return <div ref={container} className="min-w-0">{wide ? <ResizablePanelGroup orientation="horizontal"><ResizablePanel defaultSize="40%" minSize="320px"><div className="pr-5">{details}</div></ResizablePanel><ResizableHandle withHandle/><ResizablePanel defaultSize="60%" minSize="480px"><div className="pl-5">{originalPanel}</div></ResizablePanel></ResizablePanelGroup> : <Tabs value={pane} onValueChange={setPane}><TabsList variant="line"><TabsTrigger value="details">审核详情</TabsTrigger><TabsTrigger value="original">核对原件</TabsTrigger></TabsList><TabsContent value="details">{details}</TabsContent><TabsContent value="original">{originalPanel}</TabsContent></Tabs>}</div>
}
