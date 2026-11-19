import {BookOpen, Check, FileCode2, FileSpreadsheet, FileText, Image, ImageUp, type LucideIcon, MessageSquareText, PencilLine, Search, SquareTerminal} from 'lucide-react'
import {Badge} from '../components/ui/badge'
import {Progress} from '../components/ui/progress'
import type {components} from '../generated/openapi'
import {FileName} from '../journey/file-name'
import {PixelLoader} from './live-progress'
import type {LiveSteps, StepRow} from './live-steps'
import type {ActivityAction} from './task-events'

/* Adapted from the confirmed design board "审核运行状态 · 设计评审". */

type Run = components['schemas']['AuditRun']

/** How many of the file's latest steps stay on screen; they are only a live notice, never kept. */
const SHOWN_STEPS = 5

const ACTIONS: Record<ActivityAction, {label: string; icon: LucideIcon}> = {
  read_file: {label: '读取文件', icon: FileText},
  parse_sheet: {label: '解析表格', icon: FileSpreadsheet},
  view_image: {label: '查看图片', icon: Image},
  convert_image: {label: '转换嵌入图片', icon: ImageUp},
  search: {label: '检索内容', icon: Search},
  read_program: {label: '查阅检查程序', icon: FileCode2},
  read_guide: {label: '查阅审核指引', icon: BookOpen},
  run_check: {label: '运行核对', icon: SquareTerminal},
  record: {label: '记录复核结论', icon: PencilLine},
  note: {label: '说明', icon: MessageSquareText},
}

function StepLine({row, current, age}: {row: StepRow; current: boolean; age: number}) {
  const {label, icon: Icon} = ACTIONS[row.action]
  const subject = row.subjects.length > 1 ? `${row.subjects[0]} 等 ${row.subjects.length} 个` : row.subjects[0]
  // Older steps fade; the newest one is the step under way.
  const fade = current ? '' : ['opacity-90', 'opacity-75', 'opacity-60', 'opacity-40'][Math.min(age - 1, 3)]
  return <li className={`flex h-7 min-w-0 items-center gap-2 motion-safe:animate-fade-up ${fade}`}>
    <Icon aria-hidden="true" className={`size-3.5 shrink-0 ${current ? 'text-primary' : 'text-muted-foreground'}`}/>
    {row.action === 'note'
      ? <span className="min-w-0 truncate text-xs" title={subject}><span className="sr-only">{label}：</span>{subject}</span>
      : <>
        <span className="shrink-0 text-xs font-medium">{label}{row.times > 1 && <span className="ml-1 tabular-nums text-muted-foreground">×{row.times}</span>}</span>
        {subject && <span title={subject} className="inline-flex h-5.5 min-w-0 items-center truncate rounded-md bg-muted px-1.5 font-mono text-xs text-muted-foreground ring-1 ring-border">{subject}</span>}
      </>}
    {current && <PixelLoader/>}
  </li>
}

/** The round finished: a short pop-in check, before the results take its place. */
export function RunFinished() {
  return <div role="status" className="flex items-center justify-center gap-3 rounded-lg border border-success/25 bg-success/10 px-4 py-6">
    <span className="flex size-10 items-center justify-center rounded-full bg-success text-white motion-safe:animate-pop-in"><Check aria-hidden="true" className="size-5" strokeWidth={3}/></span>
    <span className="text-base font-semibold text-success">审核完成</span>
  </div>
}

/**
 * The task under way while a round runs: one line naming the stage, the file and its place in the round,
 * and in a deep audit the latest steps of that file.
 */
export function RunStatus({run, deep, name, steps}: {run: Run & {file_version_id: string; index: number; total: number}; deep: boolean; name: string; steps?: LiveSteps}) {
  const preparing = run.stage === 'prepare'
  const [before, after] = preparing ? ['正在初始化', '审核环境'] : [deep ? '正在深度审核' : '正在审核', '']
  const rows = deep && !preparing ? (steps?.items ?? []).filter(row => row.target === run.file_version_id).slice(-SHOWN_STEPS) : []
  const last = rows.at(-1)?.id
  return <section aria-label="审核进度" className="overflow-hidden rounded-lg border bg-card motion-safe:animate-fade-up">
    <div className="flex min-w-0 items-center justify-between gap-3 px-4 py-3">
      <div className="flex min-w-0 items-center gap-2.5">
        <Badge shape="square" variant={preparing ? 'outline' : 'info'}>{preparing ? '准备' : '审核'}</Badge>
        {preparing && <PixelLoader/>}
        <p role="status" className="flex min-w-0 items-center text-sm"><span className="shrink-0">{before}</span><span className="shrink-0">《</span><FileName truncate className="min-w-0 font-medium" name={name}/><span className="shrink-0">》{after}</span></p>
      </div>
      <p className="shrink-0 text-sm tabular-nums text-muted-foreground">进度 <span className="font-semibold text-foreground">{run.index}</span>/{run.total}</p>
    </div>
    <div className="px-4 pb-3"><Progress aria-label="审核进度" value={Math.min(100, run.index / run.total * 100)}/></div>
    {rows.length > 0 && <ol role="log" aria-label="深度审核步骤" className="flex flex-col border-t bg-muted/40 px-4 py-2">
      {rows.map((row, index) => <StepLine key={row.id} row={row} current={row.id === last} age={rows.length - 1 - index}/>)}
    </ol>}
  </section>
}
