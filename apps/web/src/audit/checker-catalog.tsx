import {useCallback, useEffect, useId, useRef, useState, type KeyboardEvent} from 'react'
import type {SuitesRead} from './rules'
import {ArrowLeft, ChevronRight, FileCode, Folder} from 'lucide-react'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Label} from '../components/ui/label'
import {CheckerRelease} from './checker-release'
import {canOpenPredefined, checkerLabel, predefinedTree, predefinedScope, readPredefinedPath, sourceIdentity, type PredefinedEntry, type PredefinedNode} from './predefined-catalog'
import {auditAddressChanged, writeAuditAddress} from './routes'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'

const selectedSuite = () => new URL(location.href).searchParams.get('checkerSuite') || ''
const selectedPath = () => readPredefinedPath(new URL(location.href))
const stateLabel = {installed: '', unseeded: '待安装', unavailable: '不可用', historical: '只能查看'}
const rowClass = 'flex min-h-11 w-full min-w-0 items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-muted/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-60'

/** Source navigation never adopts a revision or calls a model. */
export function CheckerCatalog({active, suites: read, onReload, mode = 'predefined', onBusyChange}: {active: boolean; suites: SuitesRead; onReload: () => void; mode?: 'predefined' | 'enterprise'; onBusyChange?: (value: boolean) => void}) {
  const selectId = useId(), groupId = useId(), tree = useRef<HTMLElement>(null), lastPath = useRef(selectedPath())
  const suites = read.value?.suites ?? [], unavailable = read.value?.unavailable ?? []
  const predefinedError = read.value?.predefined_error ?? ''
  const predefined: PredefinedEntry[] = read.value && !predefinedError ? read.value.predefined ?? [] : []
  const loading = read.loading, error = read.error
  const [selected, setSelected] = useState(selectedSuite), [path, setPath] = useState(selectedPath)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const reload = onReload
  const changeBusy = useCallback((value: boolean) => {setBusy(value); onBusyChange?.(value)}, [onBusyChange])
  useEffect(() => {
    const restore = () => {setSelected(selectedSuite()); setPath(selectedPath())}
    window.addEventListener('popstate', restore); window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [])
  useEffect(() => {
    if (path) {
      lastPath.current = path
      const parts = path.split('/')
      setExpanded(previous => ({...previous, ...Object.fromEntries(parts.slice(0, -1).map((_, index) => [parts.slice(0, index + 1).join('/'), true]))}))
    } else if (lastPath.current) {
      tree.current?.querySelectorAll<HTMLButtonElement>('button[data-tree-path]').forEach(button => {if (button.dataset.treePath === lastPath.current) button.focus()})
    }
  }, [path])
  function choose(key: string) {
    if (busy) return
    const url = new URL(location.href)
    url.searchParams.set('checkerSuite', key); url.searchParams.delete('checkerPath'); url.searchParams.delete('rulesArea')
    writeAuditAddress(url, 'push')
  }
  function choosePath(next: string) {
    if (busy) return
    const url = new URL(location.href)
    url.searchParams.delete('checkerSuite')
    // Back to the directory stays on this tab, which is no longer the default one.
    if (next) {url.searchParams.set('checkerPath', next); url.searchParams.delete('rulesArea')}
    else {url.searchParams.delete('checkerPath'); url.searchParams.set('rulesArea', 'predefined')}
    writeAuditAddress(url, 'push')
  }
  function moveInTree(event: KeyboardEvent<HTMLElement>) {
    const target = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-tree-path]')
    if (!target || !tree.current) return
    const buttons = [...tree.current.querySelectorAll<HTMLButtonElement>('button[data-tree-path]:not([disabled])')]
    const index = buttons.indexOf(target)
    let next: HTMLButtonElement | undefined
    if (event.key === 'ArrowDown') next = buttons[index + 1]
    else if (event.key === 'ArrowUp') next = buttons[index - 1]
    else if (event.key === 'Home') next = buttons[0]
    else if (event.key === 'End') next = buttons.at(-1)
    else if (event.key === 'ArrowRight') {
      if (target.getAttribute('aria-expanded') === 'false') target.click()
      else if (buttons[index + 1]?.dataset.treePath?.startsWith(target.dataset.treePath + '/')) next = buttons[index + 1]
    } else if (event.key === 'ArrowLeft') {
      if (target.getAttribute('aria-expanded') === 'true') target.click()
      else next = buttons.find(button => button.dataset.treePath === target.dataset.treePath?.split('/').slice(0, -1).join('/'))
    } else return
    event.preventDefault(); next?.focus()
  }
  const suite = suites.find(value => value.suite_key === selected), blocked = unavailable.some(value => value.suite_key === selected)
  const revisionId = suite?.current_revision_id || suite?.candidate_revision_id
  const {nodes, rejected} = predefinedTree(predefined)
  const selectedEntry = rejected.some(value => value.path === path) ? undefined : predefined.find(value => value.path === path)
  const openEntry = selectedEntry && canOpenPredefined(selectedEntry) ? selectedEntry : undefined
  const adoptedRevisionId = openEntry ? suites.find(value => value.suite_key === openEntry.suite_key)?.current_revision_id : null
  function renderNodes(items: PredefinedNode[], level = 1) {
    return items.map((node, index) => {
      const branch = node.children.length > 0
      const defaultOpen = node.entry?.availability !== 'historical' && (path.startsWith(node.path + '/') || level <= 2 && index === 0)
      const open = expanded[node.path] ?? defaultOpen
      const id = groupId + encodeURIComponent(node.path)
      return <li key={node.path} role="none">
        {branch ? <>
          <button type="button" role="treeitem" aria-label={node.name + (node.entry?.availability === 'historical' ? ' 历史目录，只读' : '')} aria-level={level} aria-expanded={open} aria-controls={id} aria-owns={open ? id : undefined} data-tree-path={node.path} className={rowClass + ' font-medium'} onClick={() => setExpanded(previous => ({...previous, [node.path]: !open}))}>
            <ChevronRight aria-hidden="true" className={'size-3.5 shrink-0 transition-transform ' + (open ? 'rotate-90' : '')}/><Folder aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/><span className="min-w-0 break-words">{node.name}</span>{node.entry?.availability === 'historical' && <span className="ml-auto text-xs text-muted-foreground">只读</span>}
          </button>
          {open && <ul role="group" id={id} className="ml-3 border-l pl-2">{renderNodes(node.children, level + 1)}</ul>}
        </> : node.entry && <button type="button" role="treeitem" aria-level={level} aria-selected={path === node.path} aria-label={node.path + ' ' + (node.entry.availability === 'historical' ? '历史目录，只读' : checkerLabel(node.entry))} data-tree-path={node.path} disabled={busy} className={rowClass + ' aria-selected:bg-muted aria-selected:font-medium'} onClick={() => choosePath(node.path)}>
          {node.entry.availability === 'historical' ? <Folder aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/> : <FileCode aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/>}<span className="min-w-0 flex-1 break-words">{checkerLabel(node.entry)}</span>{stateLabel[node.entry.availability] && <span className="shrink-0 text-xs text-muted-foreground">{stateLabel[node.entry.availability]}</span>}
        </button>}
      </li>
    })
  }
  if (mode === 'enterprise') return <section aria-label="企业检查版本" className="min-w-0 space-y-4">
    <h2 className="text-lg font-semibold">企业检查版本</h2>
    <p className="text-sm text-muted-foreground">本企业每项检查正在用的版本。在审核结果里点某条“这条判错了”，打开的也是这里的同一项检查。发布只影响之后的新审核。</p>
    <Label htmlFor={selectId}>选择检查</Label>
    <Select value={selected || undefined} disabled={busy} onValueChange={choose}><SelectTrigger id={selectId} className="w-full max-w-xl"><SelectValue placeholder="选择要核对的检查"/></SelectTrigger><SelectContent>
      {selected && !suite && !blocked && <SelectItem value={selected}>所选检查</SelectItem>}
      {unavailable.map((value, index) => <SelectItem key={value.suite_key} value={value.suite_key}>检查 {index + 1} · 暂不可用</SelectItem>)}
      {suites.map(value => <SelectItem key={value.suite_key} value={value.suite_key}>{value.title} · {value.current_revision_id ? '已采用' : '尚未采用'}</SelectItem>)}
    </SelectContent></Select>
    {loading && <ReadSkeleton label="正在读取检查版本…" kind="inline"/>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {unavailable.length > 0 && <p role="alert" className="text-sm text-destructive">{unavailable.length} 项检查暂不可用。请核对权限、关联检查或版本内容。</p>}
    {!loading && !error && selected && !suite && <p role="alert">所选检查当前不可访问，请重新选择。</p>}
    <Button variant="outline" size="sm" disabled={loading || busy} onClick={reload}>刷新检查列表</Button>
    {active && suite && revisionId && <CheckerRelease key={'enterprise:' + suite.suite_key} scope={`catalog:${suite.suite_key}`} suiteKey={suite.suite_key} revisionId={revisionId} onBusyChange={changeBusy} onPublished={reload} onDependency={choose}/>}
  </section>
  return <section aria-label="预定义检查目录" className="min-w-0 space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">预定义检查</h2><p className="mt-1 text-sm text-muted-foreground">按版本与客户选择检查。打开来源只供核对，不会采用版本。</p></div><Button variant="outline" size="sm" disabled={loading || busy} onClick={reload}>刷新预定义目录</Button></header>
    {loading && <ReadSkeleton label="正在读取预定义检查…" kind="inline"/>}
    {error && <p role="alert" className="text-sm text-destructive">{error} 请重新读取目录。</p>}
    {predefinedError && <p role="alert" className="text-sm text-destructive">预定义检查目录不可用。未列出任何来源路径。</p>}
    {!loading && !error && !predefinedError && !predefined.length && <p className="text-sm">暂无预定义检查。</p>}
    <div className="grid min-w-0 gap-6 lg:grid-cols-[19rem_minmax(0,1fr)]">
      <nav ref={tree} aria-label="检查目录" onKeyDown={moveInTree} className={'min-w-0 lg:max-h-[calc(100dvh-14rem)] lg:overflow-y-auto lg:border-r lg:pr-4 ' + (path ? 'max-lg:hidden' : '')}>
        <ul role="tree" aria-label="按版本和客户浏览" className="space-y-1">{renderNodes(nodes)}</ul>
        {rejected.length > 0 && <p role="alert" className="mt-3 text-sm text-destructive">另有 {rejected.length} 项检查信息不完整，暂不显示。</p>}
      </nav>
      <section aria-label="检查详情" className={'min-w-0 space-y-4 ' + (!path ? 'max-lg:hidden' : '')}>
        {path && <Button variant="ghost" size="sm" disabled={busy} onClick={() => choosePath('')}><ArrowLeft aria-hidden="true"/>返回目录</Button>}
        {!path && !loading && <p className="py-8 text-sm text-muted-foreground">从左侧选择一项检查，查看它的规则和版本。</p>}
        {!loading && !predefinedError && path && !selectedEntry && <p role="alert" className="text-sm text-destructive">所选来源当前不可访问，请返回目录重新选择。</p>}
        {selectedEntry && <header className="space-y-2 border-b pb-4"><h3 className="text-lg font-semibold" title={'来源 ' + selectedEntry.path}>{selectedEntry.title}</h3>
          {!openEntry && <p role="status" className="text-sm">{selectedEntry.availability === 'historical' ? '历史目录，只能查看，不能采用。' : selectedEntry.availability === 'unseeded' ? '这项检查尚未安装，暂时不能打开或采用。' : '官方版本暂时无法读取，请稍后刷新。'}</p>}
        </header>}
        {openEntry && <div className="space-y-2 text-sm"><p title={'官方修订 ' + openEntry.revision_id}>{sourceIdentity(openEntry, adoptedRevisionId)}</p><p className="text-muted-foreground">可自动检查 {openEntry.evidence_reader_clause_ids?.length ?? 0}/{openEntry.clause_count ?? 0} 条{(openEntry.evidence_reader_clause_ids?.length ?? 0) < (openEntry.clause_count ?? 1) && '；其余条款会显示为“无法判断”，需要人工核对'}。</p></div>}
        {active && openEntry && <CheckerRelease key={`${openEntry.path}:${openEntry.revision_id}`} scope={predefinedScope(openEntry.path)} suiteKey={openEntry.suite_key} revisionId={openEntry.revision_id} refreshKey={String(read.revision)} onBusyChange={changeBusy} onPublished={reload} onDependency={choose}/>}
      </section>
    </div>
  </section>
}
