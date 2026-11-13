import type {ActivityAction, TaskActivity} from './task-events.ts'

/** How many step rows stay on screen; older ones only count. */
export const LIVE_STEP_WINDOW = 10

/** Consecutive identical actions on one file share a row ("查看图片 ×6"). */
export type StepRow = {id: number; action: ActivityAction; target?: string; subjects: string[]; times: number}

/**
 * The deep review as seen on this page: recent step rows, how many steps each file took,
 * which file is under review now, and which files it already finished.
 */
export type LiveSteps = {items: StepRow[]; count: number; perTarget: Record<string, number>; current?: string; finished: string[]}

export const NO_STEPS: LiveSteps = {items: [], count: 0, perTarget: {}, finished: []}

const MAX_SUBJECTS = 20

export function appendStep(previous: LiveSteps, step: TaskActivity): LiveSteps {
  const target = step.target ?? previous.current
  const key = target ?? ''
  // Files are reviewed one after another: a step for a new file ends the previous one.
  const finished = previous.current && target && target !== previous.current && !previous.finished.includes(previous.current) ? [...previous.finished, previous.current] : previous.finished
  const last = previous.items.at(-1)
  const items = last && last.action === step.action && last.target === target && step.action !== 'note'
    ? [...previous.items.slice(0, -1), {...last, times: last.times + 1, subjects: step.subject && !last.subjects.includes(step.subject) && last.subjects.length < MAX_SUBJECTS ? [...last.subjects, step.subject] : last.subjects}]
    : [...previous.items, {id: previous.count, action: step.action, target, subjects: step.subject ? [step.subject] : [], times: 1}].slice(-LIVE_STEP_WINDOW)
  return {items, count: previous.count + 1, perTarget: {...previous.perTarget, [key]: (previous.perTarget[key] ?? 0) + 1}, current: target, finished}
}
