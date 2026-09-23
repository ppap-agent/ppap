import {useEffect, useMemo, useRef, useState} from 'react'
import {type Round, apiUrl, originalUrl, bundleUrl, request, resultLabels} from './api'
import {productHref} from './navigation/route'
import {Badge, Button, Callout, Heading, Inline, Link, Page, ReportBlock, ReviewNote, Stack, Surface, Text} from './ui'

interface Export {id: string; status: string; document_id: string | null; error_code?: string | null}

function machineTone(status: string): 'success' | 'warning' | 'danger' {
  if (status === 'PASS') return 'success'
  if (status === 'NG') return 'warning'
  return 'danger'
}

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

  useEffect(() => {
    revision.current += 1
    setReport(null)
    if (round.execution === 'completed') void readReport()
    return () => { revision.current += 1 }
  }, [round.id, round.execution])

  const groups = useMemo(
    () => round.inputs.filter(file => file.role === 'target').map(file => ({file, findings: round.findings.filter(finding => finding.locator?.file_version_id === file.file_version_id)})),
    [round],
  )
  const incomplete = round.execution !== 'completed' || !groups.length || groups.some(group => !group.findings.length || group.findings.some(finding => finding.status === 'ERR'))
  const reviewed = round.findings.filter(finding => finding.review).length
  const counts = ['NG', 'ERR', 'PASS'].map(status => ({status, count: round.findings.filter(item => item.status === status).length}))
  const pending = round.findings.filter(item => !item.review).length
  const issues = round.findings.filter(item => item.status !== 'PASS')
  const unsupported = (round.coverage?.rule_items || []).filter(item => item.coverage === 'unsupported').length

  return (
    <Page>
      <Inline justify="between" align="start" wrap>
        <Stack gap="xs">
          <Heading level={2}>审核报告</Heading>
          <Text tone="muted">已复核 {reviewed} / {round.findings.length} 项</Text>
        </Stack>
        <Inline gap="sm" wrap>
          {report?.status === 'ready' && report.document_id
            ? <Link variant="primary" href={'https://feishu.cn/docx/' + encodeURIComponent(report.document_id)} target="_blank" rel="noopener">打开飞书报告 ↗</Link>
            : unsavedReview
              ? <Button type="button" onClick={onReview}>返回保存复核</Button>
              : error || (report && ['unknown', 'creating', 'writing'].includes(report.status))
                ? <Button type="button" disabled={exporting || reading} onClick={() => void readReport()}>刷新报告状态</Button>
                : <Button type="button" variant="primary" disabled={incomplete || exporting || reading || (!!report && report.status !== 'failed')} onClick={() => void exportReport()}>{reading ? '正在读取状态…' : exporting ? '正在生成…' : report?.status === 'failed' ? '重试生成报告' : '生成飞书报告'}</Button>}
          {needsConsent && !exporting && <Link href={consentUrl}>授权生成报告</Link>}
        </Inline>
      </Inline>
      {unsavedReview && <Callout>复核修改尚未保存，报告仅包含已保存的内容。</Callout>}
      {error && <Callout tone="error">{error}</Callout>}
      {needsConsent
        ? <Callout>需要授权创建飞书文档。完成授权后，点击“重试生成报告”。</Callout>
        : report?.status === 'failed' ? <Callout tone="error">报告未能生成，请确认飞书权限后重试。</Callout>
          : report?.status === 'unknown' ? <Callout>尚未确认报告是否生成。请先核对飞书文档，避免重复创建。</Callout>
            : report && report.status !== 'ready' ? <Callout>正在生成报告，请稍后刷新状态。</Callout>
              : null}
      {report?.document_id && report.status !== 'ready' && <Link href={'https://feishu.cn/docx/' + encodeURIComponent(report.document_id)} target="_blank" rel="noopener">核对已创建的文档 ↗</Link>}
      {report?.status === 'ready' && <Callout>报告已生成，可在飞书中分享。后续复核修改不会覆盖这份报告。</Callout>}
      {incomplete && <Callout>检查尚未完整完成，暂不能生成完整报告。</Callout>}
      <Surface>
        <Inline gap="md" wrap>
          {counts.map(count => (
            <Badge key={count.status} tone={count.count ? machineTone(count.status) : 'neutral'}>{resultLabels[count.status]} {count.count}</Badge>
          ))}
          <Text as="span" tone="muted">未记录复核 {pending} 项</Text>
        </Inline>
        <Text tone="muted">自动检查 {round.findings.length} 项 · 未覆盖 {unsupported} 项 · 非正式批准</Text>
        {issues.length > 8
          ? <Stack as="ul" gap="sm">{issues.map(finding => <Inline as="li" key={finding.item_key} justify="between"><Link href={'#report-' + finding.item_key}>{finding.item}</Link><Text as="span" size="sm" tone="muted">{finding.review ? '已记录复核' : '未记录复核'}</Text></Inline>)}</Stack>
          : !issues.length ? <Text>{round.findings.length ? '已返回检查项未发现不符合；不代表未检查范围也已通过。' : '尚无检查结果。'}</Text> : null}
        {!!round.coverage?.unchecked?.length && <Text>未检查范围：{round.coverage.unchecked.join('；')}</Text>}
        <Link data-testid="report-download-bundle" href={bundleUrl(round.id)}>打包下载本轮资料</Link>
      </Surface>
      {groups.map(group => (
        <Stack key={group.file.file_version_id} gap="sm">
          <Inline justify="between" align="start">
            <Stack gap="xs">
              <Heading level={3}>{group.file.original_filename}</Heading>
              <Text tone="muted">本轮规则 · {group.file.rule_version_id ? <Link href={productHref({page: 'rule-detail', ruleId: group.file.rule_version_id, returnTo: {page: 'audit-round', roundId: round.id}})}>{group.file.rule?.title || '查看规则版本'}</Link> : <Text as="span">未记录</Text>}</Text>
            </Stack>
            <Link href={originalUrl(group.file, round.id)} target="_blank" rel="noopener">原件 ↗</Link>
          </Inline>
          {!group.findings.length && <Text>尚无检查结果。</Text>}
          {group.findings.map(finding => (
            <ReportBlock key={finding.item_key} id={'report-' + finding.item_key}>
              <Inline gap="sm" align="center">
                <Badge tone={machineTone(finding.status)}>{resultLabels[finding.status]}</Badge>
                <Heading level={3}>{finding.item}</Heading>
              </Inline>
              <Text size="sm" tone="muted">{[finding.classification, finding.priority].filter(Boolean).join(' · ') || '未记录分类/优先级'}</Text>
              {finding.suggestions && <Text>{finding.suggestions}</Text>}
              {finding.locator?.locator && <Text size="sm" tone="muted">依据 · {finding.locator.locator}</Text>}
              {finding.review && <ReviewNote judgment={finding.review.judgment} rationale={finding.review.rationale} nextStep={finding.review.next_step} />}
            </ReportBlock>
          ))}
        </Stack>
      ))}
      {round.inputs.some(file => file.role === 'reference') && <Text tone="muted">参考资料 · {round.inputs.filter(file => file.role === 'reference').map(file => file.original_filename).join('、')}</Text>}
    </Page>
  )
}
