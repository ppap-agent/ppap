import {analysisModeLabels, type AnalysisMode} from '../api'
import {Button, FeedbackState, TaskState} from '../ui'
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
      <TaskState
        title="正在检查资料…"
        detail={analysisMode === 'deep'
          ? analysisModeLabels.deep + '审核正在执行。可以离开页面，稍后从审核结果继续查看。'
          : '可以离开页面，稍后从审核结果继续查看。'}
        badge={<ExecutionBadge execution={execution} />}
      />
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
