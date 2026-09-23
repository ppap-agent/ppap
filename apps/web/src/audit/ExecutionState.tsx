import {analysisModeLabels, type AnalysisMode} from '../api'
import {Button, FeedbackState} from '../ui/primitives'
import {ExecutionBadge} from './StatusBadge'

export default function ExecutionState({
  execution,
  analysisMode,
  fileFindingCount,
  onShowAll,
}: {
  execution: string
  analysisMode?: AnalysisMode
  fileFindingCount: number
  onShowAll: () => void
}) {
  if (execution === 'queued' || execution === 'running') {
    return (
      <div className="audit-task-state" role="status">
        <span className="audit-task-state__indicator" aria-hidden="true"><span /></span>
        <div className="audit-task-state__copy">
          <strong>正在检查资料…</strong>
          <span>
            {analysisMode === 'deep'
              ? analysisModeLabels.deep + '审核正在执行。可以离开页面，稍后从审核结果继续查看。'
              : '可以离开页面，稍后从审核结果继续查看。'}
          </span>
        </div>
        <ExecutionBadge execution={execution} />
      </div>
    )
  }

  if (execution === 'failed') {
    return <FeedbackState compact title="本次检查未完成" detail="执行失败。可以返回审核准备修改资料后重审。" />
  }

  return (
    <FeedbackState compact title="当前筛选下没有检查项">
      {fileFindingCount > 0 ? <Button type="button" size="sm" onClick={onShowAll}>查看全部检查项</Button> : null}
    </FeedbackState>
  )
}
