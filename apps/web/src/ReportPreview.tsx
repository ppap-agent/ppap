import {useEffect, useMemo, useRef, useState} from 'react'
import {type FindingStatus, type Round, apiUrl, originalUrl, bundleUrl, request, resultLabels} from './api'
import {findingShortTitle, sourceLocation} from './helpers'
import {productHref} from './navigation/route'
import Help from './Help'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {ReportBlock, ReviewNote} from './ui/domain'
import {DataSurface} from './ui/PageLayout'

interface Export {id: string; status: string; document_id: string | null; error_code?: string | null}

function machineVariant(status: string): 'default' | 'outline' | 'destructive' | 'secondary' {
  if (status === 'PASS') return 'default'
  if (status === 'NG' || status === 'UNABLE') return 'outline'
  if (status === 'ERR') return 'destructive'
  return 'secondary'
}

const resultOrder: FindingStatus[] = ['NG', 'ERR', 'UNABLE', 'NA', 'PASS']

export default function ReportPreview({round, unsavedReview, onReview}: {round: Round; unsavedReview?: boolean; onReview: () => void}) {
  const [report, setReport] = useState<Export | null>(null)
  const [exporting, setExporting] = useState(false)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  const path = `/integrations/feishu/rounds/${round.id}/report`
  const needsConsent = report?.error_code?.endsWith(':99991679')
  const consentUrl = `${apiUrl}/login/feishu?purpose=report&return_to=${encodeURIComponent(location.origin + productHref({page: 'audit-round', roundId: round.id}))}`
  const revision = useRef(0)

  async function readReport() {
    const current = ++revision.current
    setReading(true)
    try {
      const result = await request<{report: Export | null}>(path)
      if (current === revision.current) { setReport(result.report); setError('') }
    } catch {
      if (current === revision.current) setError('无法读取报告状态，请稍后重新读取。')
    } finally {
      if (current === revision.current) setReading(false)
    }
  }

  async function exportReport() {
    if (exporting || reading || unsavedReview) return
    setExporting(true)
    setError('')
    const current = ++revision.current
    try {
      const result = await request<{report: Export | null}>(path, 'POST', {})
      if (current === revision.current) setReport(result.report)
    } catch (e) {
      if (current === revision.current) setError(e instanceof Error ? e.message : '报告创建失败，请重新读取状态后确认。')
    } finally { setExporting(false) }
  }

  const groups = useMemo(
    () => round.inputs.filter(file => file.role === 'target').map(file => ({file, findings: round.findings.filter(finding => finding.locator?.file_version_id === file.file_version_id)})),
    [round],
  )
  const findingPositions = new Map(groups.flatMap(group => group.findings.map((finding, index) => [finding.item_key, index + 1] as const)))
  const findingKeys = new Set(round.findings.map(finding => finding.item_key))
  const ruleItems = round.coverage?.rule_items || []
  const deepCoverageIncomplete = round.analysis_mode === 'deep' && (
    !ruleItems.length || ruleItems.some(item => item.coverage !== 'executed' || !item.item_key || !findingKeys.has(item.item_key))
    || round.findings.some(finding => !finding.locator?.semantic)
  )
  const incomplete = round.execution !== 'completed' || !!round.failure || deepCoverageIncomplete || !groups.length || groups.some(group => !group.findings.length || group.findings.some(finding => finding.status === 'ERR'))
  const reviewed = round.findings.filter(finding => finding.review).length
  const counts = resultOrder.map(status => ({status, count: round.findings.filter(item => item.status === status).length}))
  const judged = round.findings.filter(item => item.status === 'PASS' || item.status === 'NG').length
  const issues = round.findings.filter(item => ['NG', 'ERR', 'UNABLE'].includes(item.status))
  const unsupportedRules = ruleItems.filter(item => item.coverage === 'unsupported')
  const unsupported = unsupportedRules.length
  const otherUnchecked = (round.coverage?.unchecked || []).filter(note => !unsupportedRules.some(item => note.startsWith(item.rule_to_check + '（未自动检查：')))
  const total = Math.max(round.coverage?.rule_items?.length || 0, round.findings.length + unsupported)

  useEffect(() => {
    revision.current += 1
    setReport(null)
    setError('')
    setReading(false)
    if (!incomplete) void readReport()
    return () => { revision.current += 1 }
  }, [round.id, round.execution, incomplete])

  const statusMessage = unsavedReview
    ? {text: '复核尚未保存。保存后才能生成最新报告。'}
    : error
      ? {text: error, destructive: true}
      : needsConsent
        ? {text: '需要授权飞书后生成报告。'}
        : report?.status === 'failed'
          ? {text: '报告生成失败，请确认飞书权限后重试。', destructive: true}
          : report?.status === 'unknown'
            ? {text: '报告状态未确认，请先核对飞书文档。'}
            : report && report.status !== 'ready'
              ? {text: '报告正在生成，请稍后刷新状态。'}
              : report?.status === 'ready'
                ? {text: '报告已生成，后续复核不会覆盖它。'}
                : incomplete
                  ? {text: '检查尚未完成，暂不能生成报告。'}
                  : null

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">飞书报告</h2>
          <p className="m-0 text-sm text-muted-foreground">已复核 {reviewed} / {round.findings.length} 项</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {report?.status === 'ready' && report.document_id
            ? <Button asChild><a href={'https://feishu.cn/docx/' + encodeURIComponent(report.document_id)} target="_blank" rel="noopener">打开飞书报告 ↗</a></Button>
            : unsavedReview
              ? <Button type="button" onClick={onReview}>返回保存复核</Button>
              : error || (report && ['unknown', 'creating', 'writing'].includes(report.status))
                ? <Button type="button" variant="outline" disabled={exporting || reading} onClick={() => void readReport()}>刷新报告状态</Button>
                : <Button type="button" disabled={incomplete || exporting || reading || (!!report && report.status !== 'failed')} onClick={() => void exportReport()}>{reading ? '正在读取状态…' : exporting ? '正在生成…' : report?.status === 'failed' ? '重试生成报告' : '生成飞书报告'}</Button>}
          {needsConsent && !exporting && <Button asChild variant="link"><a href={consentUrl}>授权生成报告</a></Button>}
        </div>
      </div>
      {statusMessage && <p className={statusMessage.destructive ? 'm-0 border-l-2 border-destructive pl-3 text-sm text-destructive' : 'm-0 border-l-2 border-muted-foreground/30 pl-3 text-sm text-muted-foreground'} role={statusMessage.destructive ? 'alert' : 'status'}>{statusMessage.text}</p>}
      {report?.document_id && report.status !== 'ready' && <Button asChild variant="link"><a href={'https://feishu.cn/docx/' + encodeURIComponent(report.document_id)} target="_blank" rel="noopener">核对已创建的文档 ↗</a></Button>}

      <DataSurface className="flex flex-col gap-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge>总条款 {total}</Badge>
            <Badge variant="secondary">已判断 {judged}</Badge>
            {counts.filter(count => count.count > 0).map(count => <Badge key={count.status} variant={machineVariant(count.status)}>{resultLabels[count.status]} {count.count}</Badge>)}
          </div>
          {unsupported > 0 && <div className="flex flex-col gap-2"><span className="text-sm text-muted-foreground">未覆盖 {unsupported} 项</span><ul className="m-0 flex list-none flex-col gap-2 p-0">{unsupportedRules.map((item, index) => <li key={item.item_key || `${item.file_version_id}-${item.id}-${index}`} className="flex items-center gap-2"><span>{item.id} · {item.category || '规则条款'}</span><Help title="完整规则"><p className="m-0">{item.rule_to_check}</p></Help></li>)}</ul></div>}
          {issues.length > 8
            ? <ul className="m-0 flex list-none flex-col gap-2 p-0">{issues.map(finding => <li key={finding.item_key} className="flex items-center justify-between gap-2"><Button asChild variant="link" className="h-auto"><a href={'#report-' + finding.item_key}>{findingShortTitle(finding, findingPositions.get(finding.item_key) || 1)}</a></Button><span className="text-sm text-muted-foreground">{finding.review ? '已记录复核' : '未记录复核'}</span></li>)}</ul>
            : !issues.length ? <p className="m-0">{judged > 0 ? '已判断条款未发现不符合；请继续核对不适用项与覆盖范围。' : round.findings.length ? '尚无可判定条款，请核对不适用项与未覆盖范围。' : '尚无检查结果。'}</p> : null}
          {otherUnchecked.map((note, index) => <p key={index} className="m-0 text-sm text-muted-foreground">未检查范围 · {note}</p>)}
          <Button asChild variant="link"><a data-testid="report-download-bundle" href={bundleUrl(round.id)}>打包下载本轮资料</a></Button>
      </DataSurface>

      {groups.map(group => (
        <section key={group.file.file_version_id} className="flex flex-col gap-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-lg font-semibold tracking-tight">{group.file.original_filename}</h3>
              <p className="m-0 text-sm text-muted-foreground">本轮规则 · {group.file.rule_version_id ? <Button asChild variant="link"><a href={productHref({page: 'rule-detail', ruleId: group.file.rule_version_id, returnTo: {page: 'audit-round', roundId: round.id}})}>{group.file.rule?.title || '查看规则版本'}</a></Button> : '未记录'}</p>
            </div>
            <Button asChild variant="link"><a href={originalUrl(group.file, round.id)} target="_blank" rel="noopener">原件 ↗</a></Button>
          </div>
          {!group.findings.length && <p className="m-0">尚无检查结果。</p>}
          {group.findings.map((finding, index) => (
            <ReportBlock key={finding.item_key} id={'report-' + finding.item_key}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={machineVariant(finding.status)}>{resultLabels[finding.status]}</Badge>
                <h3 className="text-lg font-semibold tracking-tight">{findingShortTitle(finding, index + 1)}</h3>
                <Help title="完整规则">
                  {finding.locator?.semantic?.clause_id && <p className="m-0 text-sm text-muted-foreground">条款标识 · {finding.locator.semantic.clause_id}</p>}
                  <p className="m-0">{finding.locator?.semantic?.rule_text || finding.item}</p>
                </Help>
              </div>
              {(finding.classification || finding.priority) && <p className="m-0 text-sm text-muted-foreground">{[finding.classification, finding.priority].filter(Boolean).join(' · ')}</p>}
              {finding.suggestions && <p className="m-0">{finding.suggestions}</p>}
              {finding.locator?.semantic?.diagnostic && finding.locator.semantic.diagnostic !== finding.suggestions && <p className="m-0 text-sm text-muted-foreground">{finding.status === 'UNABLE' ? '无法判断原因' : '补充诊断'} · {finding.locator.semantic.diagnostic}</p>}
              {finding.locator?.semantic?.scope && <p className="m-0 text-sm text-muted-foreground">本条核验记录 · 共 {finding.locator.semantic.scope.total} 条，已判断 {finding.locator.semantic.scope.checked} 条，无法判断 {finding.locator.semantic.scope.unknown} 条</p>}
              {finding.locator?.semantic?.sources?.map((source, index) => <p key={`${source.file_version_id}-${index}`} className="m-0 text-sm text-muted-foreground">{source.role === 'reference' ? '参考 · ' : ''}{source.filename} · {sourceLocation(source)}{source.excerpt ? ` · ${source.excerpt}` : ''}</p>)}
              {!finding.locator?.semantic?.sources?.length && finding.locator?.locator && <p className="m-0 text-sm text-muted-foreground">定位线索 · {finding.locator.locator}</p>}
              {finding.review && <ReviewNote judgment={finding.review.judgment} rationale={finding.review.rationale} nextStep={finding.review.next_step} />}
            </ReportBlock>
          ))}
        </section>
      ))}
    </section>
  )
}
