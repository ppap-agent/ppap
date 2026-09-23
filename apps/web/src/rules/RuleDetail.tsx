import {useEffect, useState} from 'react'
import {dateLabel, originalUrl, request} from '../api'
import Help from '../Help'
import {Badge, Button} from '../ui/primitives'
import type {RuleDetail as Detail} from './types'

function href(params: Record<string, string>, returnTo: string) {
  const query = new URLSearchParams(params)
  if (returnTo) query.set('return_to', returnTo)
  return '/rules' + (query.size ? '?' + query : '')
}

export default function RuleDetail({
  ruleId,
  returnTo,
  returnLabel,
  saved,
}: {
  ruleId: string
  returnTo: string
  returnLabel: string
  saved: boolean
}) {
  const listHref = href({}, returnTo)
  return (
    <section className="page-content rules-page">
      <a className="back-link" data-testid="rule-return" href={returnTo || listHref}>← {returnTo ? returnLabel : '规则库'}</a>
      <RuleDetailContent ruleId={ruleId} returnTo={returnTo} returnLabel={returnLabel} saved={saved} />
    </section>
  )
}

function RuleDetailContent({
  ruleId,
  returnTo,
  returnLabel,
  saved,
}: {
  ruleId: string
  returnTo: string
  returnLabel: string
  saved: boolean
}) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    void request<Detail>(`/rules/${ruleId}`)
      .then(result => { if (active) setDetail(result) })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : '无法读取规则') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [ruleId])

  if (busy) return <p className="loading-state" role="status">正在读取规则…</p>
  if (!detail) return <p className="error" role="alert" id="rule-message" data-testid="rule-message">{error || '规则不存在'}</p>

  const body = detail.body
  const sourceFile = {
    file_id: detail.source_file_id,
    file_version_id: detail.source_file_version_id,
    original_filename: detail.title,
    role: 'reference' as const,
    rule_version_id: null,
  }

  return (
    <>
      {error && <p className="error" role="alert" id="rule-message" data-testid="rule-message">{error}</p>}
      {saved && <p role="status" className="notice" id="rule-message" data-testid="rule-message">已保存规则</p>}
      <div className="section-heading">
        <h2>{detail.title}</h2>
        <div className="scope-inline"><Badge>机审清单</Badge><Help title="客户规则用途"><p>保存后的规则条目是审核清单。能映射到内置检查的条目会机器执行；其余条目明确标为未覆盖，需人工核对。</p></Help></div>
      </div>
      <p id="rule-origin" className="pc-muted">版本 · {dateLabel(detail.created_at)}</p>
      <dl className="rule-metadata">
        <div><dt>文件类型</dt><dd>{body.type}</dd></div>
        {!!body.customers?.length && <div><dt>适用客户</dt><dd>{body.customers.join('、')}</dd></div>}
        {!!body.models?.length && <div><dt>型号</dt><dd>{body.models.join('、')}</dd></div>}
        {!!body.parts?.length && <div><dt>零件</dt><dd>{body.parts.join('、')}</dd></div>}
        {!body.customers?.length && !body.models?.length && !body.parts?.length && <div><dt>适用范围</dt><dd>未填写</dd></div>}
      </dl>
      <div className="toolbar rule-detail-actions">
        <a id="rule-source" data-testid="rule-source" href={originalUrl(sourceFile)} target="_blank" rel="noopener">原始文件 ↗</a>
        {detail.predecessor_id && <Button type="button" disabled={busy} onClick={() => { location.href = href({rule: detail.predecessor_id!}, returnTo) }}>上一版本</Button>}
        <Button type="button" disabled={busy} onClick={() => {
          location.href = href({new: '1', predecessor: detail.id, type: body.type}, returnTo)
        }}>上传新版本</Button>
      </div>
      <div className="section-heading"><h3>客户要求</h3><span className="pc-muted">{body.rules?.length || 0} 项</span></div>
      {!body.rules?.length && <p className="empty-state">未解析出规则条目，请核对原始文件。</p>}
      <div data-testid="rule-items" id="rule-items">
        {body.rules.map((item, index) => (
          <article key={item.id} className="rule-item">
            <h3><span className="rule-number">{String(index + 1).padStart(2, '0')}</span>{item.category} {item.priority && <Badge>{item.priority}</Badge>}</h3>
            <p>{item.rule_to_check}</p>
          </article>
        ))}
      </div>
    </>
  )
}
