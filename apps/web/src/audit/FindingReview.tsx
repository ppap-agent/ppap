import type {Ref} from 'react'
import {Search} from 'lucide-react'
import {type Finding, type SemanticSource} from '../api'
import {findingShortTitle, reviewJudgments, sourceLocation} from '../helpers'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '@/components/ui/collapsible'
import {Input} from '@/components/ui/input'
import {Label} from '@/components/ui/label'
import {Separator} from '@/components/ui/separator'
import {Tabs, TabsList, TabsTrigger} from '@/components/ui/tabs'
import {Textarea} from '@/components/ui/textarea'
import {Evidence, FindingArticle, LockedFields, ReviewForm} from '../ui/domain'
import {FindingBadge} from './StatusBadge'

export interface ReviewDraft {
  judgment: string
  rationale: string
  next_step: string
}

function nextStep(status: Finding['status']) {
  if (status === 'UNABLE') return '补齐资料或确认规则后重审。'
  if (status === 'NG') return '核对差异，修改资料后重审。'
  if (status === 'ERR') return '刷新任务状态；仍异常时联系管理员。'
  if (status === 'NA') return '确认本条规则是否适用于此资料。'
  return '记录复核意见，必要时修改资料后重审。'
}

export default function FindingReview({
  finding,
  position,
  busy,
  review,
  customJudgment,
  savingReview,
  reviewDirty,
  reviewFeedback,
  hasNextUnreviewed,
  headingRef,
  onFindEvidence,
  onJudgment,
  onEnableCustomJudgment,
  onReviewChange,
  onSave,
  onContinue,
}: {
  finding: Finding
  position: number
  busy: boolean
  review: ReviewDraft
  customJudgment: boolean
  savingReview: boolean
  reviewDirty: boolean
  reviewFeedback: string
  hasNextUnreviewed: boolean
  headingRef: Ref<HTMLHeadingElement>
  onFindEvidence: (source?: SemanticSource) => void
  onJudgment: (value: string) => void
  onEnableCustomJudgment: () => void
  onReviewChange: (field: keyof ReviewDraft, value: string) => void
  onSave: () => void
  onContinue: () => void
}) {
  const semantic = finding.locator?.semantic
  const actionLabel = savingReview
    ? '正在保存…'
    : reviewDirty
      ? (hasNextUnreviewed ? '保存并继续' : '保存复核')
      : hasNextUnreviewed
        ? '下一未记录项 →'
        : finding.review ? '复核已记录' : '选择复核判断'
  const saveState = savingReview ? '正在保存…' : reviewDirty ? '未保存' : finding.review ? '已保存' : ''

  return (
    <FindingArticle findingKey={finding.item_key}>
      <div className="flex flex-wrap items-center gap-2">
        <FindingBadge status={finding.status} prefix="机器检查" data-testid="finding-status" data-status={finding.status} />
        <h2 ref={headingRef} tabIndex={-1} data-testid="finding-heading" className="text-xl font-semibold tracking-tight">{findingShortTitle(finding, position)}</h2>
      </div>

      <Collapsible>
        <CollapsibleTrigger asChild><Button type="button" variant="ghost" size="sm">查看完整规则</Button></CollapsibleTrigger>
        <CollapsibleContent><div className="rounded-lg border bg-muted/20 p-3 text-sm">
          {semantic?.clause_id && <p className="m-0 text-muted-foreground">条款标识 · {semantic.clause_id}</p>}
          <p className="m-0">{semantic?.rule_text || finding.item}</p>
        </div></CollapsibleContent>
      </Collapsible>

      <div className="flex flex-col gap-2">
        <p data-testid="finding-suggestion" className="m-0 break-words">{finding.suggestions || semantic?.diagnostic || '未提供判断原因。'}</p>
        {semantic?.diagnostic && !!finding.suggestions && semantic.diagnostic !== finding.suggestions && <p className="m-0 text-sm text-muted-foreground">{finding.status === 'UNABLE' ? '无法判断原因' : '补充诊断'} · {semantic.diagnostic}</p>}
        {semantic?.scope && <p className="m-0 text-sm text-muted-foreground">本条核验记录 · 共 {semantic.scope.total} 条，已判断 {semantic.scope.checked} 条，无法判断 {semantic.scope.unknown} 条</p>}
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-base font-semibold">证据</h3>
        {semantic?.sources?.length ? <Evidence testId="finding-evidence">
          <ul className="m-0 flex w-full list-none flex-col gap-2 p-0">
            {semantic.sources.map((source, index) => <li key={`${source.file_version_id}-${index}`} className="flex flex-col gap-2">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 flex-col gap-1">
                  <strong>{sourceLocation(source)}</strong>
                  {(index === 0 || semantic.sources[index - 1].file_version_id !== source.file_version_id) && <span className="text-sm text-muted-foreground">{source.role === 'reference' ? '参考 · ' : ''}{source.filename}</span>}
                  {source.excerpt && <span className="break-words">{source.excerpt}</span>}
                </div>
                <Button type="button" variant="ghost" size="sm" onClick={() => onFindEvidence(source)}><Search className="size-4" aria-hidden="true" />查看来源</Button>
              </div>
              {index < semantic.sources.length - 1 && <Separator />}
            </li>)}
          </ul>
        </Evidence> : (
          <Evidence testId="finding-evidence">
            <span>{finding.locator?.locator || <Badge variant="outline" data-testid="locator-gap">未提供可定位来源</Badge>}</span>
            <Button type="button" variant="ghost" size="sm" onClick={() => onFindEvidence()}><Search className="size-4" aria-hidden="true" />在原件中查找</Button>
          </Evidence>
        )}
      </div>

      {['UNABLE', 'ERR', 'NA'].includes(finding.status) && <div className="flex flex-col gap-2"><h3 className="text-base font-semibold">下一步</h3><p className="m-0">{nextStep(finding.status)}</p></div>}

      <ReviewForm onSubmit={onContinue}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-base font-semibold">人工复核</h3>
          {saveState ? <span className="text-sm text-muted-foreground">{saveState}</span> : null}
        </div>
        <LockedFields disabled={busy}>
          <fieldset className="grid gap-1.5 border-0 p-0">
            <Label>复核判断</Label>
            <Tabs value={customJudgment ? 'custom' : review.judgment} onValueChange={value => value === 'custom' ? onEnableCustomJudgment() : onJudgment(value)}>
              <TabsList>
                {[...reviewJudgments.map(value => ({value, label: value})), {value: 'custom', label: '其他'}].map(option => <TabsTrigger key={option.value} value={option.value}>{option.label}</TabsTrigger>)}
              </TabsList>
            </Tabs>
            <input type="hidden" name={`review-judgment-${finding.item_key}`} value={customJudgment ? review.judgment : review.judgment} readOnly />
          </fieldset>
          {customJudgment && (
            <label className="grid gap-1.5 text-sm font-medium">判断<Input data-testid="review-judgment" value={review.judgment} placeholder="填写你的复核判断" onChange={event => onReviewChange('judgment', event.target.value)} /></label>
          )}
          <label className="grid gap-1.5 text-sm font-medium">理由<Textarea data-testid="review-rationale" value={review.rationale} rows={3} onChange={event => onReviewChange('rationale', event.target.value)} /></label>
          <label className="grid gap-1.5 text-sm font-medium">下一步<Textarea data-testid="review-next" value={review.next_step} rows={2} onChange={event => onReviewChange('next_step', event.target.value)} /></label>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm text-muted-foreground">{reviewFeedback || (finding.review && !reviewDirty ? '已记录此项' : '')}</span>
            <div className="flex items-center gap-2">
              {reviewDirty && hasNextUnreviewed && <Button type="button" data-testid="save-review" disabled={!review.judgment.trim()} onClick={onSave}>仅保存</Button>}
              <Button type="submit" data-testid={reviewDirty && !hasNextUnreviewed ? 'save-review' : 'save-and-continue'} disabled={reviewDirty ? !review.judgment.trim() : !hasNextUnreviewed}>{actionLabel}</Button>
            </div>
          </div>
        </LockedFields>
      </ReviewForm>
    </FindingArticle>
  )
}
