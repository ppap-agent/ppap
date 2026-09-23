import {useEffect, useState} from 'react'
import {dateLabel, originalUrl, request} from '../api'
import Help from '../Help'
import {productHref, type ReturnTarget} from '../navigation/route'
import {Badge, Button, Callout, FeedbackState, Heading, Inline, Link, Page, ReportBlock, Stack, Text} from '../ui'
import type {RuleDetail as Detail} from './types'

export default function RuleDetail({
  ruleId,
  returnTo,
  returnLabel,
  saved,
}: {
  ruleId: string
  returnTo?: ReturnTarget
  returnLabel: string
  saved: boolean
}) {
  const listHref = productHref(returnTo ? {page: 'rule-library', returnTo} : {page: 'rule-library'})
  return (
    <Page>
      <Link data-testid="rule-return" href={returnTo ? productHref(returnTo) : listHref}>← {returnTo ? returnLabel : '规则库'}</Link>
      <RuleDetailContent ruleId={ruleId} returnTo={returnTo} saved={saved} />
    </Page>
  )
}

function RuleDetailContent({
  ruleId,
  returnTo,
  saved,
}: {
  ruleId: string
  returnTo?: ReturnTarget
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

  if (busy) return <Callout tone="loading">正在读取规则…</Callout>
  if (!detail) return <Callout tone="error" id="rule-message" testId="rule-message">{error || '规则不存在'}</Callout>

  const body = detail.body
  const sourceFile = {
    file_id: detail.source_file_id,
    file_version_id: detail.source_file_version_id,
    original_filename: detail.title,
    role: 'reference' as const,
    rule_version_id: null,
  }

  return (
    <Stack gap="md">
      {error && <Callout tone="error" id="rule-message" testId="rule-message">{error}</Callout>}
      {saved && <Callout id="rule-message" testId="rule-message">已保存规则</Callout>}
      <Inline justify="between" align="center" wrap>
        <Heading level={1}>{detail.title}</Heading>
        <Inline gap="xs" align="center"><Badge>机审清单</Badge><Help title="客户规则用途"><Text>保存后的规则条目是审核清单。能映射到内置检查的条目会机器执行；其余条目明确标为未覆盖，需人工核对。</Text></Help></Inline>
      </Inline>
      <Text id="rule-origin" tone="muted">版本 · {dateLabel(detail.created_at)}</Text>
      <Stack id="rule-metadata" gap="xs">
        <Text>文件类型 · {body.type}</Text>
        {!!body.customers?.length && <Text>适用客户 · {body.customers.join('、')}</Text>}
        {!!body.models?.length && <Text>型号 · {body.models.join('、')}</Text>}
        {!!body.parts?.length && <Text>零件 · {body.parts.join('、')}</Text>}
        {!body.customers?.length && !body.models?.length && !body.parts?.length && <Text>适用范围 · 未填写</Text>}
      </Stack>
      <Inline gap="sm" wrap>
        <Link id="rule-source" data-testid="rule-source" href={originalUrl(sourceFile)} target="_blank" rel="noopener">原始文件 ↗</Link>
        {detail.predecessor_id && <Button type="button" disabled={busy} onClick={() => { location.href = productHref({page: 'rule-detail', ruleId: detail.predecessor_id!, returnTo}) }}>上一版本</Button>}
        <Button type="button" disabled={busy} onClick={() => {
          location.href = productHref({page: 'rule-import', predecessorId: detail.id, documentType: body.type, returnTo})
        }}>上传新版本</Button>
      </Inline>
      <Inline justify="between"><Heading level={3}>客户要求</Heading><Text tone="muted">{body.rules?.length || 0} 项</Text></Inline>
      {!body.rules?.length && <FeedbackState title="未解析出规则条目" detail="请核对原始文件。" />}
      <Stack id="rule-items" testId="rule-items" gap="md">
        {(body.rules || []).map((item, index) => (
          <ReportBlock key={item.id}>
            <Inline gap="sm" align="center">
              <Text as="span" tone="muted">{String(index + 1).padStart(2, '0')}</Text>
              <Heading level={3}>{item.category}</Heading>
              {item.priority && <Badge>{item.priority}</Badge>}
            </Inline>
            <Text>{item.rule_to_check}</Text>
          </ReportBlock>
        ))}
      </Stack>
    </Stack>
  )
}
