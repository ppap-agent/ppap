import {analysisModeLabels, bundleUrl, dateLabel, resultLabels, type Round} from '../api'
import {Badge, Button, ButtonLink} from '../ui/primitives'
import {ExecutionBadge} from './StatusBadge'

export default function RoundOverview({
  round,
  counts,
  reviewed,
  roundView,
  workspacePanel,
  busy,
  onWriteback,
  onPrepareAgain,
  onSwitchView,
}: {
  round: Round
  counts: Array<{status: string; count: number}>
  reviewed: number
  roundView: string
  workspacePanel: string
  busy: boolean
  onWriteback: () => void
  onPrepareAgain: () => void
  onSwitchView: (view: 'review' | 'original' | 'report') => void
}) {
  const targets = round.inputs.filter(file => file.role === 'target')
  const title = targets.map(file => file.original_filename).join('、') || '审核结果'
  const executionLocked = ['queued', 'running'].includes(round.execution)

  return (
    <section className="audit-round-overview">
      <header className="audit-round-header">
        <div className="audit-round-copy">
          <a className="back-link" href="/audit-files" data-testid="open-history">← 审核结果</a>
          <h2 title={title}>{title}</h2>
          <div className="audit-round-meta">
            <ExecutionBadge execution={round.execution} />
            {round.analysis_mode === 'deep' && <Badge tone="info" data-testid="round-analysis-mode">{analysisModeLabels.deep}</Badge>}
            <span>{targets.length} 份资料</span>
            <span>{dateLabel(round.created_at)}</span>
          </div>
        </div>
        <div className="audit-round-actions">
          {round.predecessor_id && <ButtonLink variant="ghost" size="sm" data-testid="previous-round" href={'/?round=' + round.predecessor_id}>上一轮</ButtonLink>}
          <ButtonLink variant="ghost" size="sm" data-testid="download-bundle" href={bundleUrl(round.id)}>打包下载资料</ButtonLink>
          {round.writeback?.available && (
            <Button type="button" variant="ghost" size="sm" data-testid="write-feishu" disabled={busy || executionLocked} onClick={onWriteback}>写回飞书文档</Button>
          )}
          <Button type="button" variant="ghost" size="sm" data-testid="prepare-again" disabled={busy || executionLocked} onClick={onPrepareAgain}>修改后重审</Button>
        </div>
      </header>

      {!!round.findings.length && roundView !== 'report' && (
        <div className="audit-round-stats" aria-label="本轮机器检查结果" data-testid="result-summary">
          {counts.map(count => (
            <span key={count.status} className={count.count ? 'audit-stat status-' + count.status : 'audit-stat is-muted'}>
              <strong>{count.count}</strong>
              <span>{resultLabels[count.status]}</span>
            </span>
          ))}
          <span className="audit-review-progress">
            <span>复核 {reviewed} / {round.findings.length}</span>
            <progress value={reviewed} max={round.findings.length} aria-label="已记录人工复核" />
          </span>
        </div>
      )}

      <nav className="view-tabs" aria-label="审核视图">
        <button type="button" aria-pressed={roundView === 'review' && workspacePanel === 'checks'} onClick={() => onSwitchView('review')}>检查与复核</button>
        <button type="button" className="original-tab" aria-pressed={roundView === 'review' && workspacePanel === 'original'} onClick={() => onSwitchView('original')}>原件</button>
        <button type="button" aria-pressed={roundView === 'report'} onClick={() => onSwitchView('report')}>审核报告</button>
      </nav>
    </section>
  )
}
