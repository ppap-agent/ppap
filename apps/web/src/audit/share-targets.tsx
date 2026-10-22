import {useEffect, useRef, useState} from 'react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Input} from '../components/ui/input'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {ReadSkeleton} from '../components/read-skeleton'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {targetKey, type AuditSharingAdapter, type ShareTarget} from './sharing-contract'

/** Organization targets are not Drive files. Eligibility comes from the service. */
export function ShareTargets({adapter, value, onChange, onReadyChange}: {adapter: Pick<AuditSharingAdapter, 'listTargets'>; value: ShareTarget[]; onChange: (targets: ShareTarget[]) => void; onReadyChange: (ready: boolean) => void}) {
  const [kind, setKind] = useState<ShareTarget['kind']>('user'), [query, setQuery] = useState('')
  const [targets, setTargets] = useState<ShareTarget[]>([]), [nextPage, setNextPage] = useState('')
  const [organization, setOrganization] = useState<{id: string; name: string} | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const epoch = useRef(0), selected = useRef(value), organizationId = useRef('')
  const started = useRef(false)
  const committed = useRef({query: '', kind})
  const retry = useRef<{pageToken?: string; lookup: {query: string; kind: ShareTarget['kind']}} | null>(null)
  selected.current = value
  async function load(pageToken?: string, lookup = {query, kind}) {
    const run = ++epoch.current
    onReadyChange(false); setLoading(true); setError(''); setNotice('')
    if (!pageToken) {setTargets([]); setNextPage('')}
    try {
      const search = pageToken ? committed.current : lookup
      retry.current = {pageToken, lookup: search}
      const page = await adapter.listTargets(search.query, search.kind, pageToken)
      if (epoch.current !== run) return
      if (!page.organization_id || organizationId.current && organizationId.current !== page.organization_id) throw new Error('organization_mismatch')
      organizationId.current = page.organization_id
      committed.current = search
      setOrganization({id: page.organization_id, name: page.organization_name})
      const safe = page.targets.map(target => target.organization_id === page.organization_id ? target : {...target, eligibility: 'blocked' as const, reason: '目标不属于当前组织，不能选择。'})
      setTargets(previous => [...new Map([...(pageToken ? previous : []), ...safe].map(target => [targetKey(target), target])).values()])
      if (selected.current.some(target => target.organization_id !== page.organization_id)) {
        selected.current = []; onChange([]); setNotice('当前组织已变更，请重新选择接收人。')
      } else {
        const next = selected.current.map(target => safe.find(next => targetKey(next) === targetKey(target)) || target)
        selected.current = next; onChange(next)
      }
      setNextPage(page.next_page_token || '')
      retry.current = null
      onReadyChange(true)
    } catch (failure) {if (epoch.current === run) setError(failure instanceof Error && (failure.name === 'TimeoutError' || 'code' in failure && ['share_directory_permission_missing', 'share_directory_timeout'].includes(String(failure.code))) ? failure.message : '无法读取人员或群组，请重试。')}
    finally {if (epoch.current === run) setLoading(false)}
  }
  useEffect(() => {
    if (!started.current) {
      started.current = true
      selected.current = selected.current.map(target => ({...target, eligibility: 'unknown', reason: '接收资格待核对。'}))
      onChange(selected.current)
    }
    void load()
    return () => {epoch.current += 1; onReadyChange(false)}
  }, [kind]) // Adapter is fixed for this picker lifetime.
  function toggle(target: ShareTarget, checked: boolean) {
    const next = checked ? [...selected.current, target] : selected.current.filter(row => targetKey(row) !== targetKey(target))
    selected.current = next; onChange(next)
  }
  function verify(target: ShareTarget) {
    setQuery(target.display_name)
    if (kind !== target.kind) setKind(target.kind)
    else void load(undefined, {query: target.display_name, kind: target.kind})
  }
  return <section className="space-y-3" aria-label="组织内接收目标">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">接收人</h3>{organization && <p className="text-xs text-muted-foreground">{organization.name}</p>}</div>
    {notice && <p role="status">{notice}</p>}
    {value.length > 0 && <ItemGroup aria-label="已选接收人">{value.map(target => <Item role="listitem" size="xs" key={targetKey(target)}><ItemContent><span>{target.display_name}</span>{target.reason && <p className="text-xs text-muted-foreground">{target.reason}</p>}</ItemContent>{target.eligibility !== 'eligible' && <Button variant="outline" size="sm" disabled={loading} aria-label={`核对 ${target.display_name}`} onClick={() => verify(target)}>核对</Button>}<Button variant="ghost" size="sm" aria-label={`移除 ${target.display_name}`} onClick={() => toggle(target, false)}>移除</Button></Item>)}</ItemGroup>}
    <Tabs value={kind} onValueChange={value => setKind(value as ShareTarget['kind'])}><TabsList><TabsTrigger value="user">人员</TabsTrigger><TabsTrigger value="group">群组</TabsTrigger></TabsList><TabsContent value={kind}><div className="space-y-3">
    <form role="search" aria-label="搜索分享目标" className="flex gap-2" onSubmit={event => {event.preventDefault(); void load()}}><Input aria-label="搜索人员或群组" placeholder="搜索姓名或群组名称" value={query} onChange={event => setQuery(event.target.value)}/><Button type="submit" variant="outline" disabled={loading}>搜索</Button></form>
    {error && <Alert variant="destructive"><AlertTitle>目录暂不可用</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => {const last = retry.current; if (last) void load(last.pageToken, last.lookup)}}>重新读取目标</Button></AlertDescription></Alert>}
    {loading && <ReadSkeleton label="正在读取组织目录…" kind={targets.length ? 'inline' : 'list'} rows={3}/>}
    <ItemGroup>{targets.map(target => {
      const checked = value.some(row => targetKey(row) === targetKey(target))
      return <Item role="listitem" size="xs" key={targetKey(target)} className="items-start"><Checkbox id={`share-${targetKey(target)}`} aria-label={`选择${target.kind === 'user' ? '人员' : '群组'} ${target.display_name}`} disabled={loading || target.eligibility !== 'eligible' && !checked} checked={checked} onCheckedChange={checked => toggle(target, checked === true)}/><ItemContent><Label htmlFor={`share-${targetKey(target)}`}>{target.display_name}</Label>{target.reason && <p className="text-xs text-muted-foreground">{target.reason}</p>}</ItemContent></Item>
    })}</ItemGroup>
    {!loading && !targets.length && !error && <p>没有符合条件的组织内目标。</p>}
    {nextPage && <Button variant="outline" disabled={loading} onClick={() => void load(nextPage)}>加载更多目标</Button>}
    </div></TabsContent></Tabs>
    {value.length > 0 && <p className="text-xs text-muted-foreground">已选 {value.length} 个接收人或群组</p>}
  </section>
}
