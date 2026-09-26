import {analysisModeLabels, bundleUrl, dateLabel, resultLabels, type Round} from '../api'
import {productHref} from '../navigation/route'
import {Download} from 'lucide-react'
import {Alert, AlertDescription} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Progress} from '@/components/ui/progress'
import {Tabs, TabsList, TabsTrigger} from '@/components/ui/tabs'
import {ExecutionBadge} from './StatusBadge'

function machineVariant(status: string): 'default' | 'outline' | 'destructive' | 'secondary' {
  if (status === 'PASS') return 'default'
  if (status === 'NG' || status === 'UNABLE') return 'outline'
  if (status === 'ERR') return 'destructive'
  return 'secondary'
}

export default function RoundOverview({
  round,
  counts,
  reviewed,
  roundView,
  workspacePanel,
  busy,
  onWriteback,
  onRefresh,
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
  onRefresh: () => void
  onPrepareAgain: () => void
  onSwitchView: (view: 'review' | 'original' | 'report') => void
}) {
  const targets = round.inputs.filter(file => file.role === 'target')
  const references = round.inputs.length - targets.length
  const title = targets.map(file => file.original_filename).join('、') || '审核结果'
  const executionLocked = ['queued', 'running'].includes(round.execution)
  const unsupported = (round.coverage?.rule_items || []).filter(item => item.coverage === 'unsupported').length
  const total = Math.max(round.coverage?.rule_items?.length || 0, round.findings.length + unsupported)
  const activeTab = roundView === 'report' ? 'report' : workspacePanel === 'original' ? 'original' : 'review'

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex min-w-0 flex-col gap-2">
          <a href={productHref({page: 'audit-history'})} data-testid="open-history" className="w-fit text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">← 审核结果</a>
          <h1 className="break-words text-2xl font-semibold tracking-tight">{title}</h1>
          <div className="flex flex-wrap items-center gap-2">
            <ExecutionBadge execution={round.execution} />
            {round.analysis_mode === 'deep' && <Badge variant="outline" data-testid="round-analysis-mode">{analysisModeLabels.deep}</Badge>}
            <span className="text-sm text-muted-foreground">{targets.length} 份待审{references ? ` · ${references} 份参考` : ''}</span>
            <span className="text-sm text-muted-foreground">{dateLabel(round.created_at)}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {round.predecessor_id && <Button asChild variant="ghost" size="sm"><a data-testid="previous-round" href={productHref({page: 'audit-round', roundId: round.predecessor_id})}>上一轮</a></Button>}
          <Button asChild variant="ghost" size="icon" data-testid="download-bundle"><a aria-label="打包下载本轮资料" title="打包下载本轮资料" href={bundleUrl(round.id)}><Download className="size-4" aria-hidden="true" /></a></Button>
          {round.writeback?.available && <Button type="button" variant="ghost" size="sm" data-testid="write-feishu" disabled={busy || executionLocked} onClick={onWriteback}>写回飞书文档</Button>}
          {round.execution !== 'failed' && <Button type="button" variant="ghost" size="sm" data-testid="prepare-again" disabled={busy || executionLocked} onClick={onPrepareAgain}>修改后重审</Button>}
        </div>
      </div>

      {round.execution === 'failed' && !!round.findings.length && (
        <Alert variant="destructive">
          <AlertDescription><div className="flex flex-wrap items-center gap-2">
            <span>{round.failure?.message || '本轮执行失败，结果不完整。'}</span>
            <Button type="button" size="sm" data-testid="prepare-again" disabled={busy} onClick={onPrepareAgain}>修改后重审</Button>
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onRefresh}>刷新状态</Button>
          </div></AlertDescription>
        </Alert>
      )}

      {(round.execution === 'completed' || !!round.findings.length) && (!!total || !!round.findings.length) && roundView !== 'report' && (
        <div data-testid="result-summary" className="flex flex-wrap items-center gap-2 border-y py-3" aria-label="本轮条款覆盖与复核">
          <span className="text-sm text-muted-foreground">共 {total} 条</span>
          {counts.filter(count => count.count > 0).map(count => <Badge key={count.status} variant={machineVariant(count.status)}>{resultLabels[count.status as keyof typeof resultLabels]} {count.count}</Badge>)}
          {!!unsupported && <Badge variant="outline">未覆盖 {unsupported}</Badge>}
          {!!round.findings.length && <div className="ml-auto flex min-w-40 items-center gap-2 text-xs text-muted-foreground"><span>复核 {reviewed} / {round.findings.length}</span><Progress value={round.findings.length ? reviewed / round.findings.length * 100 : 0} aria-label={`复核 ${reviewed} / ${round.findings.length}`} /></div>}
        </div>
      )}

      <Tabs value={activeTab} onValueChange={value => onSwitchView(value as 'review' | 'original' | 'report')}>
        <TabsList variant="line" aria-label="审核视图">
          <TabsTrigger value="review">{!round.findings.length && round.execution !== 'completed' ? '审核状态' : '检查与复核'}</TabsTrigger>
          <TabsTrigger value="original">原件</TabsTrigger>
          <TabsTrigger value="report">报告</TabsTrigger>
        </TabsList>
      </Tabs>
    </section>
  )
}
