import type {Ref} from 'react'
import {Search} from 'lucide-react'
import {type Finding} from '../api'
import {reviewJudgments} from '../helpers'
import Help from '../Help'
import {Button} from '../ui/primitives'
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
        : '复核已记录'

  return (
    <article className="finding-detail">
      <div className="section-heading">
        <FindingBadge status={finding.status} prefix="机器检查" data-testid="finding-status" data-status={finding.status} />
        <span className="pc-muted finding-position">{position} / {total}</span>
      </div>
      <h3 ref={headingRef} tabIndex={-1}>{finding.item}</h3>
      <div className="finding-meta">
        <span data-testid="finding-classification">分类 · {finding.classification || '未记录'}</span>
        <span data-testid="finding-priority">优先级 · {finding.priority || '未记录'}</span>
      </div>
      <p className="finding-description" data-testid="finding-suggestion">{finding.suggestions}</p>
      <div className="evidence" data-testid="finding-evidence">
        <div>{finding.locator?.locator || <span data-testid="locator-gap">定位线索 · 待核实</span>}</div>
        <Button type="button" variant="ghost" size="sm" onClick={onFindEvidence}>
          <Search size={16} aria-hidden="true" />在原件中查找
        </Button>
      </div>

      <form className="audit-review" onSubmit={event => { event.preventDefault(); onContinue() }}>
        <div className="section-heading">
          <div className="scope-inline">
            <h3>人工复核</h3>
            <Help title="复核与自动检查">
              <p>记录复核不等于问题已解决，也不改变机器结论。未自动检查的规则条目单独列出，不计入机器通过。</p>
            </Help>
          </div>
          <span className="review-save-state" role="status">{savingReview ? '正在保存…' : reviewDirty ? '未保存' : finding.review ? '已保存' : ''}</span>
        </div>

        <fieldset className="plain-fieldset" disabled={busy}>
          <div className="judgment-options" aria-label="复核判断">
            {reviewJudgments.map(value => (
              <button type="button" key={value} aria-pressed={!customJudgment && review.judgment === value} onClick={() => onJudgment(value)}>{value}</button>
            ))}
            <button type="button" aria-pressed={customJudgment} onClick={onEnableCustomJudgment}>其他判断</button>
          </div>
          {customJudgment && (
            <label>
              判断
              <input data-testid="review-judgment" value={review.judgment} placeholder="填写你的复核判断" onChange={event => onReviewChange('judgment', event.target.value)} />
            </label>
          )}
          <label>
            理由
            <textarea data-testid="review-rationale" value={review.rationale} rows={3} onChange={event => onReviewChange('rationale', event.target.value)} />
          </label>
          <label>
            下一步
            <textarea data-testid="review-next" value={review.next_step} rows={2} onChange={event => onReviewChange('next_step', event.target.value)} />
          </label>
          <div className="review-actions">
            <span className="review-save-state" role="status">{reviewFeedback || (finding.review && !reviewDirty ? '已记录此项' : '')}</span>
            <div className="button-group">
              {reviewDirty && hasNextUnreviewed && <Button type="button" data-testid="save-review" disabled={!review.judgment.trim()} onClick={onSave}>仅保存</Button>}
              <Button
                type="submit"
                variant="primary"
                data-testid={reviewDirty && !hasNextUnreviewed ? 'save-review' : 'save-and-continue'}
                disabled={reviewDirty ? !review.judgment.trim() : !hasNextUnreviewed}
              >
                {actionLabel}
              </Button>
            </div>
          </div>
        </fieldset>
      </form>
    </article>
  )
}
