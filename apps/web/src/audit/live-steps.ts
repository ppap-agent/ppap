import type {TaskActivity} from './task-events.ts'

/** How many live steps stay on screen; older ones only count. */
export const LIVE_STEP_WINDOW = 10

/** The latest live deep steps seen on this page, and how many arrived in all. */
export type LiveSteps = {items: (TaskActivity & {id: number})[]; count: number}

export const NO_STEPS: LiveSteps = {items: [], count: 0}

/** A new step joins the window; the oldest one leaves it but stays counted. */
export function appendStep(previous: LiveSteps, step: TaskActivity): LiveSteps {
  return {items: [...previous.items, {...step, id: previous.count}].slice(-LIVE_STEP_WINDOW), count: previous.count + 1}
}
