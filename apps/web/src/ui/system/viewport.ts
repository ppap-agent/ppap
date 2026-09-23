/** Compact workspace query. Matches the table/workspace breakpoint in system.css. */
export const compactViewport = '(max-width: 1023px)'

export function isCompactViewport() {
  return typeof matchMedia === 'function' && matchMedia(compactViewport).matches
}
