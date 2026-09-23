import {analysisModeLabels, bundleUrl, dateLabel, resultLabels, type Round} from '../api'
import {productHref} from '../navigation/route'
import {Badge, Button, Heading, Inline, Link, Meter, Stack, Stat, StatRow, Text, ViewTab, ViewTabs} from '../ui'
import {ExecutionBadge} from './StatusBadge'

function machineTone(status: string): 'success' | 'warning' | 'danger' {
  if (status === 'PASS') return 'success'
  if (status === 'NG') return 'warning'
  return 'danger'
}

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
    <Stack gap="md">
      <Inline align="start" justify="between" wrap gap="lg">
        <Stack gap="xs">
          <Link href={productHref({page: 'audit-history'})} data-testid="open-history">← 审核结果</Link>
          <Heading level={1}>{title}</Heading>
          <Inline gap="sm" wrap>
            <ExecutionBadge execution={round.execution} />
            {round.analysis_mode === 'deep' && <Badge tone="info" data-testid="round-analysis-mode">{analysisModeLabels.deep}</Badge>}
            <Text as="span" size="sm" tone="muted">{targets.length} 份资料</Text>
            <Text as="span" size="sm" tone="muted">{dateLabel(round.created_at)}</Text>
          </Inline>
        </Stack>
        <Inline gap="xs" wrap>
          {round.predecessor_id && <Link variant="ghost" size="sm" data-testid="previous-round" href={productHref({page: 'audit-round', roundId: round.predecessor_id})}>上一轮</Link>}
          <Link variant="ghost" size="sm" data-testid="download-bundle" href={bundleUrl(round.id)}>打包下载资料</Link>
          {round.writeback?.available && (
            <Button type="button" variant="ghost" size="sm" data-testid="write-feishu" disabled={busy || executionLocked} onClick={onWriteback}>写回飞书文档</Button>
          )}
          <Button type="button" variant="ghost" size="sm" data-testid="prepare-again" disabled={busy || executionLocked} onClick={onPrepareAgain}>修改后重审</Button>
        </Inline>
      </Inline>

      {!!round.findings.length && roundView !== 'report' && (
        <StatRow label="本轮机器检查结果" testId="result-summary">
          {counts.map(count => (
            <Stat key={count.status} value={count.count} label={resultLabels[count.status]} tone={machineTone(count.status)} muted={!count.count} />
          ))}
          <Meter value={reviewed} max={round.findings.length} label={`复核 ${reviewed} / ${round.findings.length}`} />
        </StatRow>
      )}

      <ViewTabs>
        <ViewTab pressed={roundView === 'review' && workspacePanel === 'checks'} onClick={() => onSwitchView('review')}>检查与复核</ViewTab>
        <ViewTab kind="original" pressed={roundView === 'review' && workspacePanel === 'original'} onClick={() => onSwitchView('original')}>原件</ViewTab>
        <ViewTab pressed={roundView === 'report'} onClick={() => onSwitchView('report')}>审核报告</ViewTab>
      </ViewTabs>
    </Stack>
  )
}
