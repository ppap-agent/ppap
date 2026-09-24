import type {Ref} from 'react'
import {Search} from 'lucide-react'
import {type Finding} from '../api'
import {reviewJudgments} from '../helpers'
import Help from '../Help'
import {Badge, Button, Evidence, Field, FindingArticle, Heading, Icon, Inline, Input, LockedFields, ReviewForm, Text, Textarea, Toggle, ToggleRow} from '../ui'
import {FindingBadge} from './StatusBadge'

export interface ReviewDraft {
  judgment: string
  rationale: string
  next_step: string
}

export default function FindingReview({
  finding,
  position,
  total,
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
  total: number
  busy: boolean
  review: ReviewDraft
  customJudgment: boolean
  savingReview: boolean
  reviewDirty: boolean
  reviewFeedback: string
  hasNextUnreviewed: boolean
  headingRef: Ref<HTMLHeadingElement>
  onFindEvidence: () => void
  onJudgment: (value: string) => void
  onEnableCustomJudgment: () => void
  onReviewChange: (field: keyof ReviewDraft, value: string) => void
  onSave: () => void
  onContinue: () => void
}) {
  const actionLabel = savingReview
    ? '正在保存…'
    : reviewDirty
      ? (hasNextUnreviewed ? '保存并继续' : '保存复核')
      : hasNextUnreviewed
        ? '下一未记录项 →'
        : finding.review ? '复核已记录' : '选择复核判断'
  const saveState = savingReview ? '正在保存…' : reviewDirty ? '未保存' : finding.review ? '已保存' : ''

  return (
    <FindingArticle>
      <Inline justify="between" align="center">
        <FindingBadge status={finding.status} prefix="机器检查" data-testid="finding-status" data-status={finding.status} />
        <Text as="span" size="sm" tone="muted">{position} / {total}</Text>
      </Inline>
      <Heading level={3} ref={headingRef} tabIndex={-1} testId="finding-heading">{finding.item}</Heading>
      <Inline gap="md" wrap>
        <Text as="span" size="sm" tone="muted" testId="finding-classification">分类 · {finding.classification || '未记录'}</Text>
        <Text as="span" size="sm" tone="muted" testId="finding-priority">优先级 · {finding.priority || '未记录'}</Text>
      </Inline>
      <Text testId="finding-suggestion">{finding.suggestions}</Text>
      <Evidence testId="finding-evidence">
        <Text>{finding.locator?.locator || <Badge tone="warning" data-testid="locator-gap">定位线索 · 待核实</Badge>}</Text>
        <Button type="button" variant="ghost" size="sm" onClick={onFindEvidence}>
          <Icon icon={Search} size="sm" />在原件中查找
        </Button>
      </Evidence>

      <ReviewForm onSubmit={onContinue}>
        <Inline justify="between" align="center">
          <Inline gap="xs" align="center">
            <Heading level={3}>人工复核</Heading>
            <Help title="复核与自动检查">
              <Text>记录复核不等于问题已解决，也不改变机器结论。未自动检查的规则条目单独列出，不计入机器通过。</Text>
            </Help>
          </Inline>
          {saveState ? <Text as="span" size="sm" tone="muted">{saveState}</Text> : null}
        </Inline>
        <LockedFields disabled={busy}>
          <ToggleRow label="复核判断">
            {reviewJudgments.map(value => (
              <Toggle key={value} pressed={!customJudgment && review.judgment === value} onClick={() => onJudgment(value)}>{value}</Toggle>
            ))}
            <Toggle pressed={customJudgment} onClick={onEnableCustomJudgment}>其他判断</Toggle>
          </ToggleRow>
          {customJudgment && (
            <Field label="判断">
              <Input data-testid="review-judgment" value={review.judgment} placeholder="填写你的复核判断" onChange={event => onReviewChange('judgment', event.target.value)} />
            </Field>
          )}
          <Field label="理由">
            <Textarea data-testid="review-rationale" value={review.rationale} rows={3} onChange={event => onReviewChange('rationale', event.target.value)} />
          </Field>
          <Field label="下一步">
            <Textarea data-testid="review-next" value={review.next_step} rows={2} onChange={event => onReviewChange('next_step', event.target.value)} />
          </Field>
          <Inline justify="between" align="center" wrap>
            <Text as="span" size="sm" tone="muted">{reviewFeedback || (finding.review && !reviewDirty ? '已记录此项' : '')}</Text>
            <Inline gap="sm">
              {reviewDirty && hasNextUnreviewed && <Button type="button" data-testid="save-review" disabled={!review.judgment.trim()} onClick={onSave}>仅保存</Button>}
              <Button
                type="submit"
                variant="primary"
                data-testid={reviewDirty && !hasNextUnreviewed ? 'save-review' : 'save-and-continue'}
                disabled={reviewDirty ? !review.judgment.trim() : !hasNextUnreviewed}
              >
                {actionLabel}
              </Button>
            </Inline>
          </Inline>
        </LockedFields>
      </ReviewForm>
    </FindingArticle>
  )
}
