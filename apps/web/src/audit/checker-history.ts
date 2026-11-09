import type {CheckerRevision} from './checker-contract'

/** Describe immutable source differences, without inventing a business explanation. */
export function checkerHistoryChanges(revision: Pick<CheckerRevision, 'diff' | 'changed_paths' | 'files'>, previous?: Pick<CheckerRevision, 'files'>) {
  if (previous) {
    const changed = [...new Set([...Object.keys(previous.files), ...Object.keys(revision.files)])].filter(path => previous.files[path] !== revision.files[path])
    revision = {...revision, changed_paths: changed, diff: changed.map(path => ({path, before: previous.files[path] ?? null, after: revision.files[path] ?? null}))}
  }
  const paths = [...new Set([...revision.changed_paths, ...revision.diff.map(item => item.path)])]
  return paths.map(path => {
    const change = revision.diff.find(item => item.path === path)
    if (!change) return {path, state: '未提供差异内容', before: '', after: ''}
    const before = change.before ?? '', after = change.after ?? ''
    const oldLines = before.split('\n'), newLines = after.split('\n')
    let start = 0
    while (start < oldLines.length && start < newLines.length && oldLines[start] === newLines[start]) start++
    let oldEnd = oldLines.length, newEnd = newLines.length
    while (oldEnd > start && newEnd > start && oldLines[oldEnd - 1] === newLines[newEnd - 1]) {oldEnd--; newEnd--}
    const excerpt = (lines: string[]) => {const text = lines.join('\n').trim(); return text.length > 300 ? text.slice(0, 300) + '…' : text}
    return {path, state: !before && after ? '新增' : before && !after ? '移除' : before === after ? '内容未变' : '修改', before: excerpt(oldLines.slice(start, oldEnd)), after: excerpt(newLines.slice(start, newEnd))}
  })
}
