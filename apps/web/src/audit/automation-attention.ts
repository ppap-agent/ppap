/** How many automatic-review programs need the person; the rules page publishes it and the navigation shows it. */
let count = 0
const listeners = new Set<(value: number) => void>()

export function publishAutomationAttention(value: number) {
  if (value === count) return
  count = value
  for (const listener of listeners) listener(value)
}

export function subscribeAutomationAttention(listener: (value: number) => void): () => void {
  listeners.add(listener)
  listener(count)
  return () => {listeners.delete(listener)}
}
