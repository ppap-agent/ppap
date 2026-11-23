// Which steps of a rule-clarification plan the person confirms (#351). The selection is always closed under the
// steps' dependencies: checking a step checks what it needs, unchecking one unchecks what needs it.
export type PlanStep = {id: string; external: boolean; depends_on: string[]}

function needs(steps: PlanStep[], id: string, seen = new Set<string>()): Set<string> {
  for (const dependency of steps.find(step => step.id === id)?.depends_on ?? []) {
    if (!seen.has(dependency)) {seen.add(dependency); needs(steps, dependency, seen)}
  }
  return seen
}

/** Internal steps start checked; a step the customer sees, and everything after it, waits for the person's own check. */
export function initialSelection(steps: PlanStep[]): Set<string> {
  const chosen = new Set<string>()
  for (const step of steps) if (!step.external && step.depends_on.every(id => chosen.has(id))) chosen.add(step.id)
  return chosen
}

export function toggle(steps: PlanStep[], selected: Set<string>, id: string, on: boolean): Set<string> {
  const next = new Set(selected)
  if (on) for (const step of [id, ...needs(steps, id)]) next.add(step)
  else for (const step of steps) if (step.id === id || needs(steps, step.id).has(id)) next.delete(step.id)
  return next
}

/** The one call that confirms the checked steps, in plan order; the rest, and why, go with it for the record. */
export function planRequest<S extends PlanStep & {body: unknown}>(steps: S[], selected: Set<string>, reason: string) {
  const unselected = steps.filter(step => !selected.has(step.id)).map(step => step.id)
  return {
    steps: steps.filter(step => selected.has(step.id)).map(step => ({step: step.id, body: step.body})),
    unselected,
    ...(unselected.length && reason.trim() ? {reason: reason.trim()} : {}),
  }
}
