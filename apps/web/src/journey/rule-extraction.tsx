import {useEffect, useRef, useState} from 'react'
import {request, upload, type RuleParseJob, type RuleTypes} from '../api'
import {Button} from '../components/ui/button'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {isPreview} from './access'
import {localBasis, type RequirementBasis} from './requirements-state'
import type {LocalUpload} from './local-files'
import {parsedBasis} from './product'
import {message, useRemote} from './remote'

export async function receiveRequirement(entry: LocalUpload, stage?: (basis: RequirementBasis) => void) {
  let basis = localBasis(entry.file.name, entry.ref)
  if (isPreview()) return basis
  const body = {kind: 'engine' as const, type: '', rule_profile: 'v2', customers: [], models: [], parts: [], rules: []}
  const material = await upload(entry.file, 'reference', material => {
    basis = {...basis, server: {material, body}}; stage?.(basis)
  })
  return {...basis, server: {material, body}}
}

export function RuleExtraction({basis, onChange, readonly = false}: {basis: RequirementBasis; onChange: (basis: RequirementBasis) => void; readonly?: boolean}) {
  const types = useRemote<RuleTypes>(basis.server && !readonly ? '/rule-types' : null)
  const job = useRemote<RuleParseJob>(basis.server?.parseId && !['ready', 'manual'].includes(basis.source.state) ? `/rules/parse/${basis.server.parseId}` : null, 2000)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const working = useRef(false), consumed = useRef(''), latest = useRef(basis); latest.current = basis
  useEffect(() => {
    const result = job.value?.result
    if (['ready', 'manual'].includes(latest.current.source.state)) return
    if (job.value?.status === 'failed' && consumed.current !== job.value.id) {
      consumed.current = job.value.id
      setError(result && 'error' in result ? `要求提取失败：${result.error}` : '要求提取失败，可重新提取或按原件补录条款。')
      onChange({...latest.current, source: {...latest.current.source, state: 'failed'}})
      return
    }
    if (!result || consumed.current === job.value!.id) return
    if ('body' in result && job.value?.status === 'succeeded') {
      consumed.current = job.value.id
      onChange(parsedBasis(latest.current, result))
    } else if ('error' in result) {consumed.current = job.value!.id; setError(`要求提取未完成：${result.error}`)}
  }, [job.value, onChange])
  if (!basis.server || readonly) return null
  if (basis.source.state === 'ready' || basis.source.state === 'manual' && basis.server.body.type) return <p className="text-sm text-muted-foreground">适用资料：{basis.server.body.type}</p>
  const pending = Boolean(basis.server.parseId && !['succeeded', 'failed'].includes(job.value?.status || ''))
  async function parse() {
    if (working.current || !basis.server?.body.type) return
    working.current = true; setBusy(true); setError('')
    const id = job.value?.status === 'failed' ? crypto.randomUUID() : basis.server.parseId || crypto.randomUUID()
    const next = {...basis, source: {...basis.source, state: 'unparsed' as const}, server: {...basis.server, parseId: id}}
    onChange(next)
    try {
      const value = await request<RuleParseJob>('/rules/parse', 'POST', {request_id: id, source_file_id: basis.server.material.file_id, source_file_version_id: basis.server.material.file_version_id, document_type: basis.server.body.type})
      job.setValue(value)
    } catch (cause) {setError(message(cause)); job.refresh()}
    finally {working.current = false; setBusy(false)}
  }
  return <section className={"flex flex-col gap-3 py-4"} aria-label="提取审核条款">
    <div className={"flex flex-wrap items-end gap-3"}><label className={"flex flex-col gap-2 text-sm"}>适用资料类型<NativeSelect value={basis.server.body.type} disabled={Boolean(basis.server.parseId && job.value?.status !== 'failed')} onChange={event => onChange({...basis, server: {...basis.server!, body: {...basis.server!.body, type: event.target.value}}})}><NativeSelectOption value="">请选择</NativeSelectOption>{types.value?.types.map(type => <NativeSelectOption key={type} value={type}>{type}</NativeSelectOption>)}</NativeSelect></label>
      <Button variant="outline" disabled={busy || !basis.server.body.type || pending && !job.error} onClick={() => void parse()}>{job.value?.status === 'failed' ? '重新提取条款' : basis.server.parseId ? '核对原提取请求' : '提取条款'}</Button>
      {basis.server.parseId && <Button variant="ghost" onClick={job.refresh}>刷新提取状态</Button>}
    </div>
    {pending && !job.error && <p role="status" className={"text-sm text-muted-foreground"}>正在提取条款，完成后逐条核对原文。</p>}
    {(error || job.error || types.error) && <p role="alert" className={"text-sm text-destructive"}>{error || job.error || types.error}</p>}
  </section>
}
