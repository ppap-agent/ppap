import {useCallback, useEffect, useId, useState} from 'react'
import {request} from '../api'
import {Button} from '../components/ui/button'
import {Label} from '../components/ui/label'
import type {components} from '../generated/openapi'
import {CheckerRelease} from './checker-release'
import {auditAddressChanged, writeAuditAddress} from './routes'

const selectedSuite = () => new URL(location.href).searchParams.get('checkerSuite') || ''

/** The rule library can adopt a first Checker without an existing audit round. */
export function CheckerCatalog({active, revision}: {active: boolean; revision: number}) {
  const selectId = useId()
  const [suites, setSuites] = useState<components['schemas']['SuiteActive'][]>([])
  const [unavailable, setUnavailable] = useState<components['schemas']['SuiteUnavailable'][]>([])
  const [selected, setSelected] = useState(selectedSuite)
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const reload = useCallback(() => setRefresh(value => value + 1), [])
  useEffect(() => {
    const restore = () => setSelected(selectedSuite())
    window.addEventListener('popstate', restore); window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setLoading(true); setError('')
    void request<components['schemas']['SuiteCatalog']>('/suites', 'GET', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) {setSuites(value.suites); setUnavailable(value.unavailable || []); setLoading(false)}
    }).catch(cause => {if (!controller.signal.aborted) {setError(cause instanceof Error ? cause.message : '检查版本暂不可读。'); setLoading(false)}})
    return () => controller.abort()
  }, [active, revision, refresh])
  function choose(key: string) {
    const url = new URL(location.href)
    if (key) url.searchParams.set('checkerSuite', key)
    else url.searchParams.delete('checkerSuite')
    writeAuditAddress(url, 'push')
  }
  const suite = suites.find(value => value.suite_key === selected)
  const blocked = unavailable.some(value => value.suite_key === selected)
  const revisionId = suite?.current_revision_id || suite?.candidate_revision_id
  return <section aria-label="企业检查版本" className="space-y-3 rounded-md border p-4">
    <h2 className="font-semibold">检查版本</h2>
    <p className="text-sm text-muted-foreground">开始首次审核前，可在这里核对并采用检查版本。未验证的候选需要明确确认，只对本企业之后的新审核生效。</p>
    <Label htmlFor={selectId}>选择检查</Label>
    <select id={selectId} className="w-full rounded-md border bg-background p-2 text-sm" value={selected} disabled={busy} onChange={event => choose(event.target.value)}>
      <option value="">选择要核对的检查版本</option>
      {selected && !suite && !blocked && <option value={selected}>{selected}</option>}
      {unavailable.map(value => <option key={value.suite_key} value={value.suite_key}>{value.suite_key} · 暂不可用</option>)}
      {suites.map(value => <option key={value.suite_key} value={value.suite_key}>{value.title} · {value.suite_key} · {value.current_revision_id ? '已采用' : '尚未采用'}</option>)}
    </select>
    {loading && <p role="status" className="text-sm">正在读取检查版本…</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}{suites.length > 0 && ' 已读取的版本仍保留，请刷新核对当前状态。'}</p>}
    {unavailable.length > 0 && <p role="alert" className="text-sm text-destructive">以下检查暂不可用：{unavailable.map(value => value.suite_key).join('、')}。需要核对权限、关联检查或版本内容；其他检查仍可选择。</p>}
    {!loading && !error && !suites.length && !unavailable.length && <p className="text-sm">还没有可访问的检查候选。</p>}
    {!loading && !error && selected && !suite && !blocked && <p role="alert" className="text-sm">所选检查当前不可访问，请重新选择。</p>}
    <Button variant="outline" size="sm" disabled={loading || busy} onClick={reload}>刷新检查列表</Button>
    {active && suite && revisionId && <CheckerRelease key={suite.suite_key} scope={`catalog:${suite.suite_key}`} suiteKey={suite.suite_key} revisionId={revisionId} onBusyChange={setBusy} onPublished={reload} onDependency={choose}/>}
  </section>
}
