import {useEffect, useState} from 'react'
import {dateLabel, originalUrl, request} from '../api'
import {productHref, type ReturnTarget} from '../navigation/route'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {PageHeader, PageLayout} from '../ui/PageLayout'
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
  return (
    <PageLayout>
      <RuleDetailContent ruleId={ruleId} returnTo={returnTo} returnLabel={returnLabel} saved={saved} />
    </PageLayout>
  )
}

function RuleDetailContent({
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
  const [detail, setDetail] = useState<Detail | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const listHref = productHref(returnTo ? {page: 'rule-library', returnTo} : {page: 'rule-library'})
  const back = <a data-testid="rule-return" className="text-primary underline-offset-4 hover:underline" href={returnTo ? productHref(returnTo) : listHref}>← {returnTo ? returnLabel : '规则库'}</a>

  useEffect(() => {
    let active = true
    void request<Detail>(`/rules/${ruleId}`)
      .then(result => { if (active) setDetail(result) })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : '无法读取规则') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [ruleId])

  if (busy) return <><PageHeader title="规则详情" back={back} /><Alert><AlertDescription>正在读取规则…</AlertDescription></Alert></>
  if (!detail) return <><PageHeader title="规则详情" back={back} /><Alert variant="destructive" id="rule-message" data-testid="rule-message"><AlertDescription>{error || '规则不存在'}</AlertDescription></Alert></>

  const body = detail.body
  const isPresence = body.kind === 'presence'
  const sourceFile = {
    file_id: detail.source_file_id,
    file_version_id: detail.source_file_version_id,
    original_filename: detail.title,
    role: 'reference' as const,
    rule_version_id: null,
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={detail.title}
        back={back}
        trailing={isPresence && <Badge>资料就绪检测</Badge>}
        description={isPresence ? '仅确认待审文件可用于审核，不检查文件内容。' : undefined}
        action={!isPresence && <>
          <Button asChild variant="outline" size="sm"><a id="rule-source" data-testid="rule-source" href={originalUrl(sourceFile)} target="_blank" rel="noopener">查看原件 ↗</a></Button>
          {detail.predecessor_id && <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => { location.href = productHref({page: 'rule-detail', ruleId: detail.predecessor_id!, returnTo}) }}>上一版本</Button>}
          <Button type="button" size="sm" disabled={busy} onClick={() => {
            location.href = productHref({page: 'rule-import', predecessorId: detail.id, documentType: body.type, returnTo})
          }}>上传新版本</Button>
        </>}
      />
      {error && <Alert variant="destructive" id="rule-message" data-testid="rule-message"><AlertDescription>{error}</AlertDescription></Alert>}
      {saved && <Alert id="rule-message" data-testid="rule-message"><AlertDescription>已保存规则</AlertDescription></Alert>}
      {!isPresence && <>
        <div id="rule-metadata" className="flex flex-wrap gap-x-5 gap-y-1 border-y py-3 text-sm text-muted-foreground">
          <span id="rule-origin">版本 · {dateLabel(detail.created_at)}</span>
          <span>文件类型 · {body.type || '未指定'}</span>
          {!!body.customers?.length && <span>客户 · {body.customers.join('、')}</span>}
          {!!body.models?.length && <span>型号 · {body.models.join('、')}</span>}
          {!!body.parts?.length && <span>零件 · {body.parts.join('、')}</span>}
        </div>
        <div className="flex items-baseline justify-between"><h2 className="text-base font-semibold">客户要求</h2><span className="text-sm text-muted-foreground">{body.rules?.length || 0} 条</span></div>
        {!body.rules?.length && <Alert><AlertTitle>未解析出规则条目</AlertTitle><AlertDescription>请核对原始文件。</AlertDescription></Alert>}
        <div id="rule-items" data-testid="rule-items" className="divide-y border-y">
          {(body.rules || []).map((item, index) => (
            <article key={item.id} className="flex flex-col gap-1 px-3 py-4">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">{String(index + 1).padStart(2, '0')}</span>
                <h3 className="text-base font-semibold">{item.category}</h3>
                {item.priority && <Badge>{item.priority}</Badge>}
              </div>
              <p className="m-0 leading-relaxed">{item.rule_to_check}</p>
            </article>
          ))}
        </div>
      </>}
    </div>
  )
}
