export function meaningChangedWithoutText(impact: {changed: boolean; appendix_changed?: boolean; clauses?: {kind: string}[] | null}): boolean {
  return Boolean(impact.changed && !impact.appendix_changed && impact.clauses?.length && impact.clauses.every(row => row.kind === 'unchanged'))
}
