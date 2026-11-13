import {BookOpen, Check, ChevronDown, FileCode2, FileSpreadsheet, FileText, Image, ImageUp, type LucideIcon, MessageSquareText, PencilLine, Search, SquareTerminal} from 'lucide-react'
import {type ReactNode, useState} from 'react'
import {PixelLoader} from './live-progress'
import type {LiveSteps, StepRow} from './live-steps'
import type {ActivityAction} from './task-events'

/* Adapted from Beautiful UI "Task Rows" (stage capsules) and "Tool Chips" (step rows). */

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

type StageStatus = 'done' | 'running' | 'pending'
type Stage = {key: string; label: string; amount?: string; status: StageStatus; steps?: StepRow[]}

/** One file under deep review. */
export type DeepTarget = {id: string; name: string}

const RING = 24, STROKE = 2, RADIUS = (RING - STROKE) / 2, CIRCLE = 2 * Math.PI * RADIUS

function StageBadge({status, index}: {status: StageStatus; index: number}) {
  if (status === 'done') return <span className="flex size-5.5 items-center justify-center rounded-full bg-success text-white motion-safe:animate-pop-in"><Check aria-hidden="true" className="size-3.5" strokeWidth={3.5}/></span>
  return <span className="relative inline-flex size-6 items-center justify-center">
    <svg aria-hidden="true" width={RING} height={RING} className={`absolute inset-0 ${status === 'running' ? 'motion-safe:animate-spin' : ''}`}>
      <circle cx={RING / 2} cy={RING / 2} r={RADIUS} fill="none" stroke="currentColor" strokeWidth={STROKE} className="text-border"/>
      {status === 'running' && <circle cx={RING / 2} cy={RING / 2} r={RADIUS} fill="none" stroke="currentColor" strokeWidth={STROKE} strokeLinecap="round" strokeDasharray={`${CIRCLE * 0.28} ${CIRCLE * 0.72}`} className="text-muted-foreground"/>}
    </svg>
    <span className="relative text-xs font-semibold tabular-nums">{index}</span>
  </span>
}

function StepLine({row, current}: {row: StepRow; current: boolean}) {
  const {label, icon: Icon} = ACTIONS[row.action]
  const subject = row.subjects.length > 1 ? `${row.subjects[0]} 等 ${row.subjects.length} 个` : row.subjects[0]
  return <li className={`flex h-7 min-w-0 items-center gap-2 motion-safe:animate-fade-up ${current ? '' : 'opacity-70'}`}>
    <Icon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground"/>
    {row.action === 'note'
      ? <span className="min-w-0 truncate text-xs" title={subject}><span className="sr-only">{label}：</span>{subject}</span>
      : <>
        <span className="shrink-0 text-xs font-medium">{label}{row.times > 1 && <span className="ml-1 tabular-nums text-muted-foreground">×{row.times}</span>}</span>
        {subject && <span title={subject} className="inline-flex h-5.5 min-w-0 items-center truncate rounded-md bg-muted px-1.5 font-mono text-xs text-muted-foreground ring-1 ring-border">{subject}</span>}
      </>}
    {current && <PixelLoader/>}
  </li>
}

function StageRow({stage, index, open, onToggle, children}: {stage: Stage; index: number; open: boolean; onToggle: () => void; children?: ReactNode}) {
  return <div className={`overflow-hidden bg-card shadow-sm ring-1 ring-border transition-all duration-300 motion-safe:animate-fade-up ${open ? 'rounded-xl' : 'rounded-3xl'}`}>
    <button type="button" aria-expanded={open} disabled={!children} onClick={onToggle} className="flex h-11 w-full items-center gap-2.5 px-2.5 text-left">
      <span className="flex size-6 shrink-0 items-center justify-center"><StageBadge status={stage.status} index={index}/></span>
      <span className={`min-w-0 flex-1 truncate text-sm font-medium ${stage.status === 'pending' ? 'text-muted-foreground' : ''}`}>{stage.label}</span>
      {stage.amount && <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{stage.amount}</span>}
      {stage.status === 'done' && <span className="inline-flex h-5.5 shrink-0 items-center rounded-full bg-success/10 px-2 text-xs font-medium text-success">已完成</span>}
      {children && <ChevronDown aria-hidden="true" className={`size-4 shrink-0 text-muted-foreground transition-transform duration-300 ${open ? 'rotate-180' : ''}`}/>}
    </button>
    {open && children && <div className="mb-2.5 ml-5.5 border-l pl-3.5">{children}</div>}
  </div>
}

/**
 * A deep review in stages: the program checks, each file's review with its latest steps,
 * then the summary. Steps are a live broadcast, so a page opened mid-run starts with the next one.
 */
export function DeepTaskRows({targets, items, steps}: {targets: DeepTarget[]; items: number; steps: LiveSteps}) {
  const [manualOpen, setManualOpen] = useState<Record<string, boolean>>({})
  const started = steps.count > 0
  // A step from an unknown file still belongs to the review under way.
  const current = targets.some(target => target.id === steps.current) ? steps.current : started ? targets[0]?.id : undefined
  const stages: Stage[] = [
    {key: 'program', label: '程序化检查', amount: `${items} 项`, status: started ? 'done' : 'running'},
    ...targets.map((target): Stage => ({
      key: target.id,
      label: `深度复核 · ${target.name}`,
      amount: steps.perTarget[target.id] ? `${steps.perTarget[target.id]} 步` : undefined,
      status: steps.finished.includes(target.id) ? 'done' : target.id === current ? 'running' : 'pending',
      steps: steps.items.filter(row => (targets.some(item => item.id === row.target) ? row.target : current) === target.id),
    })),
    {key: 'summary', label: '汇总结论', status: 'pending'},
  ]
  const last = steps.items.at(-1)?.id
  return <div role="log" aria-label="深度复核过程" className="flex flex-col gap-2 border-t pt-3">
    {stages.map((stage, index) => {
      const open = manualOpen[stage.key] ?? stage.status === 'running'
      const detail = stage.steps && (stage.status !== 'pending' || stage.steps.length > 0)
        ? <ol className="flex flex-col py-0.5">
          {stage.steps.length === 0 ? <li className="flex h-7 items-center text-xs text-muted-foreground">等待下一步…</li> : stage.steps.map(row => <StepLine key={row.id} row={row} current={stage.status === 'running' && row.id === last}/>)}
        </ol>
        : undefined
      return <StageRow key={stage.key} stage={stage} index={index + 1} open={open} onToggle={() => setManualOpen(state => ({...state, [stage.key]: !open}))}>{detail}</StageRow>
    })}
  </div>
}
