import {useEffect, useMemo, useRef, useState} from 'react'
import {type Round, apiUrl, originalUrl, request, resultLabels} from './api'

interface Export {id: string; status: string; document_id: string | null; error_code?: string | null}

export default function ReportPreview({round, unsavedReview, onReview}: {round: Round; unsavedReview?: boolean; onReview: () => void}) {
  const [report, setReport] = useState<Export | null>(null)
  const [exporting, setExporting] = useState(false)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  const path = `/integrations/feishu/rounds/${round.id}/report`
  const needsConsent = report?.error_code?.endsWith(':99991679')
  const consentUrl = `${apiUrl}/login/feishu?purpose=report&return_to=${encodeURIComponent(`${location.origin}/?round=${round.id}`)}`
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

  return (
    <section className="report-preview">
      <div className="section-heading">
        <div>
          <h2>审核报告</h2>
          <p className="pc-muted">已复核 {reviewed} / {round.findings.length} 项</p>
        </div>
        <div className="button-group">
          {report?.status === 'ready' && report.document_id
            ? <a className="button pc-primary" href={'https://feishu.cn/docx/' + encodeURIComponent(report.document_id)} target="_blank" rel="noopener">打开飞书报告 ↗</a>
            : unsavedReview
              ? <button type="button" onClick={onReview}>返回保存复核</button>
              : error || (report && ['unknown', 'creating', 'writing'].includes(report.status))
                ? <button type="button" disabled={exporting || reading} onClick={() => void readReport()}>刷新报告状态</button>
                : <button type="button" className="pc-primary" disabled={incomplete || exporting || reading || (!!report && report.status !== 'failed')} onClick={() => void exportReport()}>{reading ? '正在读取状态…' : exporting ? '正在生成…' : report?.status === 'failed' ? '重试生成报告' : '生成飞书报告'}</button>}
          {needsConsent && !exporting && <a className="button" href={consentUrl}>授权生成报告</a>}
        </div>
      </div>
      {unsavedReview && <p className="notice" role="status">复核修改尚未保存，报告仅包含已保存的内容。</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {needsConsent
        ? <p className="notice">需要授权创建飞书文档。完成授权后，点击“重试生成报告”。</p>
        : report?.status === 'failed' ? <p className="error" role="alert">报告未能生成，请确认飞书权限后重试。</p>
          : report?.status === 'unknown' ? <p className="notice" role="status">尚未确认报告是否生成。请先核对飞书文档，避免重复创建。</p>
            : report && report.status !== 'ready' ? <p className="notice" role="status">正在生成报告，请稍后刷新状态。</p>
              : null}
      {report?.document_id && report.status !== 'ready' && <a href={'https://feishu.cn/docx/' + encodeURIComponent(report.document_id)} target="_blank" rel="noopener">核对已创建的文档 ↗</a>}
      {report?.status === 'ready' && <p className="notice">报告已生成，可在飞书中分享。后续复核修改不会覆盖这份报告。</p>}
      {incomplete && <p className="notice" role="status">检查尚未完整完成，暂不能生成完整报告。</p>}
      <section className="report-summary" aria-label="本轮摘要">
        <div className="result-counts">
          {counts.map(count => <span key={count.status} className={count.count ? 'status-' + count.status : 'pc-muted'}>{resultLabels[count.status]} {count.count}</span>)}
          <span>未记录复核 {pending} 项</span>
        </div>
        <p>自动检查 {round.findings.length} 项 · 客户自定义要求需人工核对 · 非正式批准</p>
        {issues.length > 8
          ? <ul className="report-issues">{issues.map(finding => <li key={finding.item_key}><a href={'#report-' + finding.item_key}>{finding.item}</a><span>{finding.review ? '已记录复核' : '未记录复核'}</span></li>)}</ul>
          : !issues.length ? <p>{round.findings.length ? '已返回检查项未发现不符合；不代表未检查范围也已通过。' : '尚无检查结果。'}</p> : null}
        {!!round.coverage?.unchecked?.length && <p>未检查范围：{round.coverage.unchecked.join('；')}</p>}
      </section>
      {groups.map(group => (
        <section key={group.file.file_version_id} className="report-file">
          <div className="section-heading">
            <div>
              <h3>{group.file.original_filename}</h3>
              <p className="pc-muted">客户要求参考 · {group.file.rule_version_id ? <a href={'/rules?rule=' + group.file.rule_version_id + '&return_to=' + encodeURIComponent('/?round=' + round.id)}>{group.file.rule?.title || '查看规则版本'}</a> : <span>未记录</span>}</p>
            </div>
            <a href={originalUrl(group.file, round.id)} target="_blank" rel="noopener">原件 ↗</a>
          </div>
          {!group.findings.length && <p>尚无检查结果。</p>}
          {group.findings.map(finding => (
            <article key={finding.item_key} id={'report-' + finding.item_key} className="report-finding">
              <h4><span className={'badge status-' + finding.status}>{resultLabels[finding.status]}</span>{finding.item}</h4>
              {finding.suggestions && <p>{finding.suggestions}</p>}
              {finding.locator?.locator && <p className="pc-muted">依据 · {finding.locator.locator}</p>}
              {finding.review && <div className="report-review"><strong>人工复核 · {finding.review.judgment}</strong>{finding.review.rationale && <p>{finding.review.rationale}</p>}{finding.review.next_step && <p>下一步 · {finding.review.next_step}</p>}</div>}
            </article>
          ))}
        </section>
      ))}
      {round.inputs.some(file => file.role === 'reference') && <p className="pc-muted">参考资料 · {round.inputs.filter(file => file.role === 'reference').map(file => file.original_filename).join('、')}</p>}
    </section>
  )
}
