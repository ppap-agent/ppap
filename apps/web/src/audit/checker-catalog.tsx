import {useCallback, useEffect, useId, useState} from 'react'
import {request} from '../api'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Label} from '../components/ui/label'
import type {components} from '../generated/openapi'
import {CheckerRelease} from './checker-release'
import {canOpenPredefined, groupPredefined, predefinedPath, predefinedScope, readPredefinedPath, sourceIdentity, type PredefinedEntry} from './predefined-catalog'
import {auditAddressChanged, writeAuditAddress} from './routes'

const selectedSuite = () => new URL(location.href).searchParams.get('checkerSuite') || ''
const selectedPath = () => readPredefinedPath(new URL(location.href))
const unavailableCopy: Record<string, string> = {unseeded: '尚未安装官方修订', unavailable: '官方修订不可读', invalid: '来源路径无效'}

/** The rule library can adopt a first Checker without an existing audit round. */
export function CheckerCatalog({active, revision}: {active: boolean; revision: number}) {
  const selectId = useId()
  const [suites, setSuites] = useState<components['schemas']['SuiteActive'][]>([])
  const [unavailable, setUnavailable] = useState<components['schemas']['SuiteUnavailable'][]>([])
  const [predefined, setPredefined] = useState<PredefinedEntry[]>([])
  const [predefinedUnavailable, setPredefinedUnavailable] = useState<components['schemas']['SuitePredefinedUnavailable'][]>([])
  const [predefinedError, setPredefinedError] = useState('')
  const [selected, setSelected] = useState(selectedSuite)
  const [path, setPath] = useState(selectedPath)
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const reload = useCallback(() => setRefresh(value => value + 1), [])
  useEffect(() => {
    const restore = () => {setSelected(selectedSuite()); setPath(selectedPath())}
    window.addEventListener('popstate', restore); window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setLoading(true); setError('')
    void request<components['schemas']['SuiteCatalog']>('/suites', 'GET', undefined, controller.signal).then(value => {
      if (controller.signal.aborted) return
      setSuites(value.suites)
      setUnavailable(value.unavailable || [])
      setPredefined(value.predefined_error ? [] : value.predefined || [])
      setPredefinedUnavailable(value.predefined_error ? [] : value.predefined_unavailable || [])
      setPredefinedError(value.predefined_error || '')
      setLoading(false)
    }).catch(cause => {if (!controller.signal.aborted) {setError(cause instanceof Error ? cause.message : '检查版本暂不可读。'); setLoading(false)}})
    return () => controller.abort()
  }, [active, revision, refresh])
  function choose(key: string) {
    const url = new URL(location.href)
    if (key) url.searchParams.set('checkerSuite', key)
    else url.searchParams.delete('checkerSuite')
    writeAuditAddress(url, 'push')
  }
  function choosePath(next: string) {
    const url = new URL(location.href)
    if (next) url.searchParams.set('checkerPath', next)
    else url.searchParams.delete('checkerPath')
    writeAuditAddress(url, 'push')
  }
  const suite = suites.find(value => value.suite_key === selected)
  const blocked = unavailable.some(value => value.suite_key === selected)
  const revisionId = suite?.current_revision_id || suite?.candidate_revision_id
  const {groups, rejected} = groupPredefined(predefined)
  const selectedEntry = predefined.find(value => value.path === path)
  const openEntry = selectedEntry && canOpenPredefined(selectedEntry) ? selectedEntry : undefined
  const adoptedRevisionId = openEntry ? suites.find(value => value.suite_key === openEntry.suite_key)?.current_revision_id : null
  const kept = suites.length > 0 || predefined.length > 0 || predefinedUnavailable.length > 0
  return <>
    <section aria-label="企业检查版本" className="space-y-3 rounded-md border p-4">
      <h2 className="font-semibold">检查版本</h2>
      <p className="text-sm text-muted-foreground">检查版本是把审核规则落实为自动检查的程序。选择一项检查即可核对并采用；采用后只影响本企业之后的新审核，已有结果不变。</p>
      <Label htmlFor={selectId}>选择检查</Label>
      <select id={selectId} className="w-full rounded-md border bg-background p-2 text-sm" value={selected} disabled={busy} onChange={event => choose(event.target.value)}>
        <option value="">选择要核对的检查</option>
        {selected && !suite && !blocked && <option value={selected}>{selected}</option>}
        {unavailable.map((value, index) => <option key={value.suite_key} value={value.suite_key}>检查 {index + 1} · 暂不可用</option>)}
        {suites.map(value => <option key={value.suite_key} value={value.suite_key}>{value.title} · {value.current_revision_id ? '已采用' : '尚未采用'}</option>)}
      </select>
      {loading && <ReadSkeleton label="正在读取检查版本…" kind="inline"/>}
      {error && <p role="alert" className="text-sm text-destructive">{error}{kept && ' 已读取的版本仍保留，请刷新核对当前状态。'}</p>}
      {unavailable.length > 0 && <p role="alert" className="text-sm text-destructive">{unavailable.length} 项检查暂不可用。需要核对权限、关联检查或版本内容；其他检查仍可选择。</p>}
      {!loading && !error && !suites.length && !unavailable.length && <p className="text-sm">还没有可访问的检查候选。</p>}
      {!loading && !error && selected && !suite && !blocked && <p role="alert" className="text-sm">所选检查当前不可访问，请重新选择。</p>}
      <Button variant="outline" size="sm" disabled={loading || busy} onClick={reload}>刷新检查列表</Button>
      {active && suite && revisionId && <CheckerRelease key={suite.suite_key} scope={`catalog:${suite.suite_key}`} suiteKey={suite.suite_key} revisionId={revisionId} onBusyChange={setBusy} onPublished={reload} onDependency={choose}/>}
    </section>
    <section aria-label="预定义检查目录" className="space-y-3 rounded-md border p-4">
      <h2 className="font-semibold">预定义检查</h2>
      <p className="text-sm text-muted-foreground">平台提供的官方检查，按版本和客户分组。打开某项只查看它的官方修订，不会改变本企业当前采用的版本。</p>
      {loading && <ReadSkeleton label="正在读取预定义检查…" kind="inline"/>}
      {predefinedError && <p role="alert" className="text-sm text-destructive">预定义检查目录不可用。未列出任何来源路径。</p>}
      {!loading && !error && !predefinedError && !predefined.length && !predefinedUnavailable.length && <p className="text-sm">还没有已安装的预定义检查。</p>}
      {groups.map(group => <section key={group.version} aria-label={`版本 ${group.version}`} className="space-y-3">
        <h3 className="text-sm font-medium">{group.version}</h3>
        {group.customers.map(customer => <div key={customer.customer} className="space-y-2">
          <h4 className="text-sm text-muted-foreground">{customer.customer}</h4>
          <ul className="space-y-2">{customer.entries.map(entry => {
            const parts = predefinedPath(entry.path)
            return <li key={entry.path}><button type="button" aria-pressed={path === entry.path} className="flex min-h-11 w-full flex-col items-start gap-0.5 break-all rounded-md border bg-background px-3 py-2 text-left text-sm hover:bg-muted/50 aria-pressed:border-foreground aria-pressed:bg-muted/50 focus-visible:outline focus-visible:outline-2" onClick={() => choosePath(entry.path)}><span className="font-medium">{entry.title || parts?.leaf}</span><span className="text-xs text-muted-foreground">{parts?.leaf} · {entry.path}</span></button></li>
          })}</ul>
        </div>)}
      </section>)}
      {(predefinedUnavailable.length > 0 || rejected.length > 0) && <ul aria-label="不可用的预定义来源" className="space-y-2 text-sm">{predefinedUnavailable.map(value => <li key={value.path} className="break-all" role="status">{value.path} · {unavailableCopy[value.error] || '来源不可用'}</li>)}{rejected.map(value => <li key={value.path} className="break-all" role="status">{value.path || '未提供路径'} · 来源路径无效</li>)}</ul>}
      {!loading && !error && !predefinedError && path && !openEntry && <p role="alert" className="text-sm">所选来源当前不可访问，请重新选择。</p>}
      {openEntry && <div className="space-y-1 text-sm"><p className="break-all">来源路径 {openEntry.path}</p><p className="break-all">官方修订 {openEntry.revision_id}</p><p>{sourceIdentity(openEntry, adoptedRevisionId)}</p>{openEntry.evidence_reader_clause_ids.length === 0 && <p role="status">此检查尚不能自动完成审核。采用前请核对规则和样本。</p>}</div>}
      {path && <Button variant="outline" size="sm" disabled={busy} onClick={() => choosePath('')}>关闭此来源</Button>}
      <Button variant="outline" size="sm" disabled={loading || busy} onClick={reload}>刷新预定义目录</Button>
      {active && openEntry && <CheckerRelease key={`${openEntry.path}:${openEntry.revision_id}`} scope={predefinedScope(openEntry.path)} suiteKey={openEntry.suite_key} revisionId={openEntry.revision_id} onBusyChange={setBusy} onPublished={reload} onDependency={choose}/>}
    </section>
  </>
}
