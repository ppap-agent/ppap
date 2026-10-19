import {useEffect, useRef, useState} from 'react'
import {request, upload, type RuleParseJob, type RuleTypes, type RuleSources} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {isPreview} from './access'
import {localBasis, type RequirementBasis} from './requirements-state'
import type {LocalUpload} from './local-files'
import {canonical, parsedBasis} from './product'
import {message, useRemote} from './remote'
import {restoreRequirementSources} from './requirement-sources'

const ruleParseReceiptError = '提取回执与当前原件或资料类型不一致。草稿已保留，请核对原提取请求。'
function matchesSource(job: RuleParseJob, basis: RequirementBasis) {
  const server = basis.server
  // A failed job has no candidate to apply; the user may correct its type.
  return Boolean(server && job.id === server.parseId && job.source_file_id === server.material.file_id && job.source_file_version_id === server.material.file_version_id && (job.document_type === server.body.type || job.status === 'failed'))
}

export async function receiveRequirement(entry: LocalUpload, stage?: (basis: RequirementBasis) => void) {
  let basis = localBasis(entry.file.name, entry.ref)
  if (isPreview()) return basis
  const body = {kind: 'engine' as const, type: '', rule_profile: 'v3', customers: [], models: [], parts: [], rules: []}
  const material = await upload(entry.file, 'reference', material => {
    basis = {...basis, server: {material, body}}; stage?.(basis)
  })
  return {...basis, server: {material, body}}
}

export function RuleExtraction({basis, onChange, readonly = false}: {basis: RequirementBasis; onChange: (basis: RequirementBasis) => void; readonly?: boolean}) {
  const types = useRemote<RuleTypes>(basis.server && !readonly ? '/rule-types' : null)
  const job = useRemote<RuleParseJob>(basis.server?.parseId && !['ready', 'manual'].includes(basis.source.state) ? `/rules/parse/${basis.server.parseId}` : null, 2000)
  const sources = useRemote<RuleSources>(basis.server && !readonly && basis.clauses.length ? `/files/${basis.server.material.file_id}/versions/${basis.server.material.file_version_id}/rule-sources` : null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [rerun, setRerun] = useState(false)
  const working = useRef(false), consumed = useRef(''), latest = useRef(basis); latest.current = basis
  const observed = useRef<RuleParseJob | null>(null)
  if (job.value && matchesSource(job.value, basis)) observed.current = job.value
  useEffect(() => {
    const current = latest.current, value = sources.value
    if (readonly || !value || !current.server || value.file_version_id !== current.server.material.file_version_id || canonical(current.server.sources) === canonical(value)) return
    const restored = restoreRequirementSources(current, value)
    onChange({...restored, server: {...current.server, sources: value}})
  }, [sources.value, onChange, readonly])
  useEffect(() => {
    const result = job.value?.result
    if (readonly || !job.value || ['ready', 'manual'].includes(latest.current.source.state)) return
    if (!matchesSource(job.value, latest.current)) {setError(ruleParseReceiptError); return}
    if (job.value?.status === 'failed' && consumed.current !== job.value.id) {
      consumed.current = job.value.id
      setError(result && 'error' in result ? result.error === 'rule_document_type_mismatch' ? '原件中的资料类型与所选类型不同。请选择与原件一致的类型，再重新提取。' : `要求提取失败：${result.error}` : '要求提取失败，可重新提取或按原件补录条款。')
      onChange({...latest.current, source: {...latest.current.source, state: 'failed'}})
      return
    }
    if (!result) {setError(''); return}
    if (consumed.current === job.value!.id) return
    if ('body' in result && job.value?.status === 'succeeded') {
      const current = latest.current.server!
      if (result.source_file_version_id !== current.material.file_version_id || result.body.type !== current.body.type) {setError(ruleParseReceiptError); return}
      consumed.current = job.value.id
      setError('')
      onChange(parsedBasis(latest.current, result))
    } else if ('error' in result) {consumed.current = job.value!.id; setError(`要求提取未完成：${result.error}`)}
  }, [job.value, onChange, readonly])
  if (!basis.server || readonly) return null
  if (basis.source.state === 'ready' || basis.source.state === 'manual' && basis.server.body.type) return <div className="space-y-2"><p className="text-sm text-muted-foreground">适用资料：{basis.server.body.type}</p>{sources.error && <p role="alert" className="text-sm text-destructive">无法读取原文出处。<Button variant="outline" size="sm" onClick={sources.refresh}>重新读取出处</Button></p>}</div>
  // A read in flight does not erase the last observation of this exact job.
  const status = job.value && !matchesSource(job.value, basis) ? null : observed.current?.id === basis.server.parseId ? observed.current?.status : null
  const pending = status === 'queued' || status === 'leased'
  const unknown = status === 'unknown'
  async function parse(restart = false) {
    if (working.current || !basis.server?.body.type) return
    if (basis.server.parseId && !restart && status !== 'failed') {job.refresh(); return}
    if (restart && !unknown) return
    working.current = true; setBusy(true); setError('')
    setRerun(false)
    const id = crypto.randomUUID()
    const next = {...basis, source: {...basis.source, state: 'unparsed' as const}, server: {...basis.server, parseId: id}}
    onChange(next)
    try {
      const value = await request<RuleParseJob>('/rules/parse', 'POST', {request_id: id, source_file_id: basis.server.material.file_id, source_file_version_id: basis.server.material.file_version_id, document_type: basis.server.body.type})
      if (latest.current.server?.parseId === id && !['ready', 'manual'].includes(latest.current.source.state)) job.setValue(value)
    } catch (cause) {if (latest.current.server?.parseId === id && !['ready', 'manual'].includes(latest.current.source.state)) {setError(message(cause)); job.refresh()}}
    finally {working.current = false; setBusy(false)}
  }
  return <Dialog open={rerun} onOpenChange={setRerun}><section className={"flex flex-col gap-3 py-4"} aria-label="提取审核条款">
    <div className={"flex flex-wrap items-end gap-3"}><label className={"flex flex-col gap-2 text-sm"}>适用资料类型<NativeSelect value={basis.server.body.type} disabled={Boolean(basis.server.parseId && status !== 'failed')} onChange={event => onChange({...basis, server: {...basis.server!, body: {...basis.server!.body, type: event.target.value}}})}><NativeSelectOption value="">请选择</NativeSelectOption>{types.value?.types.map(type => <NativeSelectOption key={type} value={type}>{type}</NativeSelectOption>)}</NativeSelect></label>
      <Button variant="outline" disabled={busy || !basis.server.body.type || pending && !job.error} onClick={() => void parse()}>{status === 'failed' ? '重新提取条款' : basis.server.parseId ? '核对原提取请求' : '提取条款'}</Button>
      {basis.server.parseId && <Button variant="ghost" onClick={job.refresh}>刷新提取状态</Button>}
      {unknown && <DialogTrigger asChild><Button variant="outline" disabled={busy}>重新提取条款</Button></DialogTrigger>}
    </div>
    {pending && !job.error && <p role="status" className={"text-sm text-muted-foreground"}>正在提取条款，完成后逐条核对原文。</p>}
    {unknown && <Alert><AlertTitle>提取结果暂不确定</AlertTitle><AlertDescription>原任务可能仍会完成。请先核对原提取请求；重新提取可能再次消耗模型费用。</AlertDescription></Alert>}
    {(error || job.error || types.error) && <p role="alert" className={"text-sm text-destructive"}>{error || job.error || types.error}</p>}
  </section><DialogContent><DialogHeader><DialogTitle>重新提取条款？</DialogTitle><DialogDescription>原任务可能仍会完成。重新提取会创建一个新请求，可能再次消耗模型费用；不会取消原任务。</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button variant="outline">取消</Button></DialogClose><Button disabled={busy || !unknown} onClick={() => void parse(true)}>确认重新提取</Button></DialogFooter></DialogContent></Dialog>
}
