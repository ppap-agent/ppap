import {useEffect, useRef, useState} from 'react'
import {request, upload, type RuleParseJob, type RuleTypes, type RuleSources} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {isPreview} from '../audit/routes'
import {localBasis, type RequirementBasis} from './requirements-state'
import type {LocalUpload} from './local-files'
import {canonical, parsedBasis} from './product'
import {message, useRemote} from './remote'
import {restoreRequirementSources} from './requirement-sources'

const ruleParseReceiptError = '提取结果与当前原件或资料类型不一致。草稿已保留，请刷新提取进度。'
// The worker reports why an extraction stopped as a code; the person reads what happened and what to do.
const extractionErrors: Record<string, string> = {
  rule_document_type_mismatch: '原件中的资料类型与所选类型不同。请选择与原件一致的类型，再重新提取。',
  file_not_ready: '原件尚未上传完成。请稍后重新提取。',
  rule_table_incomplete: '原件中的规则表格不完整。请核对原件，或按原件补录条款。',
  unknown_rule_type: '暂不支持所选资料类型。请换一个类型，或按原件补录条款。',
  rule_type_required: '请先选择适用资料类型，再提取条款。',
  unsupported_rule_file: '暂不支持这种原件格式。请上传 Word、Excel 或 PDF，或按原件补录条款。',
  invalid_rule_json: '原件内容无法读取。请核对原件，或按原件补录条款。',
  rule_text_extraction_failed: '原件中的文字没能读出。请核对原件是否可读取，或按原件补录条款。',
  empty_rule_document: '原件中没有可提取的条款。请核对原件，或按原件补录条款。',
  rule_document_too_long: '原件内容过长，无法一次提取。请拆分后上传，或按原件补录条款。',
  rule_parse_failed: '条款没能提取出来。可重新提取，或按原件补录条款。',
}
const extractionError = (code: string) => `要求提取失败：${extractionErrors[code] ?? '条款没能提取出来。可重新提取，或按原件补录条款。'}`
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

/** `onChange` receives an update of the draft's latest basis, never a whole basis: a read that lands
 * after the person typed must not put back what the page showed before. */
export function RuleExtraction({basis, onChange, readonly = false}: {basis: RequirementBasis; onChange: (update: (basis: RequirementBasis) => RequirementBasis) => void; readonly?: boolean}) {
  const types = useRemote<RuleTypes>(basis.server && !readonly ? '/rule-types' : null)
  const job = useRemote<RuleParseJob>(basis.server?.parseId && !['ready', 'manual'].includes(basis.source.state) ? `/rules/parse/${basis.server.parseId}` : null, 2000)
  const sources = useRemote<RuleSources>(basis.server && !readonly && basis.clauses.length ? `/files/${basis.server.material.file_id}/versions/${basis.server.material.file_version_id}/rule-sources` : null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [rerun, setRerun] = useState(false)
  const working = useRef(false), consumed = useRef(''), latest = useRef(basis); latest.current = basis
  const observed = useRef<RuleParseJob | null>(null)
  if (job.value && matchesSource(job.value, basis)) observed.current = job.value
  useEffect(() => {
    const value = sources.value
    const stale = (current: RequirementBasis) => !value || !current.server || value.file_version_id !== current.server.material.file_version_id || canonical(current.server.sources) === canonical(value)
    if (readonly || stale(latest.current)) return
    onChange(current => stale(current) ? current : {...restoreRequirementSources(current, value!), server: {...current.server!, sources: value!}})
  }, [sources.value, onChange, readonly])
  useEffect(() => {
    const result = job.value?.result
    if (readonly || !job.value || ['ready', 'manual'].includes(latest.current.source.state)) return
    if (!matchesSource(job.value, latest.current)) {setError(ruleParseReceiptError); return}
    if (job.value?.status === 'failed' && consumed.current !== job.value.id) {
      consumed.current = job.value.id
      setError(result && 'error' in result ? result.error === 'rule_document_type_mismatch' ? extractionErrors.rule_document_type_mismatch : extractionError(result.error) : '要求提取失败，可重新提取或按原件补录条款。')
      onChange(current => ({...current, source: {...current.source, state: 'failed'}}))
      return
    }
    if (!result) {setError(''); return}
    if (consumed.current === job.value!.id) return
    if ('body' in result && job.value?.status === 'succeeded') {
      const current = latest.current.server!
      if (result.source_file_version_id !== current.material.file_version_id || result.body.type !== current.body.type) {setError(ruleParseReceiptError); return}
      consumed.current = job.value.id
      setError('')
      const parsed = job.value
      onChange(current => matchesSource(parsed, current) && !['ready', 'manual'].includes(current.source.state) ? parsedBasis(current, result) : current)
    } else if ('error' in result) {consumed.current = job.value!.id; setError(extractionError(result.error))}
  }, [job.value, onChange, readonly])
  if (!basis.server || readonly) return null
  if (basis.source.state === 'ready' || basis.source.state === 'manual' && basis.server.body.type) return <div className="space-y-2"><p className="text-sm text-muted-foreground">适用资料：{basis.server.body.type}</p>{sources.error && <Alert variant="destructive"><AlertTitle>无法查看原文出处</AlertTitle><AlertDescription><Button variant="outline" size="sm" onClick={sources.refresh}>重新读取出处</Button></AlertDescription></Alert>}</div>
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
    onChange(current => current.server ? {...current, source: {...current.source, state: 'unparsed' as const}, server: {...current.server, parseId: id}} : current)
    try {
      // GET owns job state: a delayed acceptance receipt may predate its terminal result.
      await request<RuleParseJob>('/rules/parse', 'POST', {request_id: id, source_file_id: basis.server.material.file_id, source_file_version_id: basis.server.material.file_version_id, document_type: basis.server.body.type})
    } catch (cause) {if (latest.current.server?.parseId === id && !['ready', 'manual'].includes(latest.current.source.state)) {setError(message(cause)); job.refresh()}}
    finally {working.current = false; setBusy(false)}
  }
  return <Dialog open={rerun} onOpenChange={setRerun}><section className={"flex flex-col gap-3 py-4"} aria-label="提取审核条款">
    <div className={"flex flex-wrap items-end gap-3"}><label className={"flex flex-col gap-2 text-sm"}>适用资料类型<Select value={basis.server.body.type || undefined} disabled={Boolean(basis.server.parseId && status !== 'failed')} onValueChange={value => onChange(current => current.server ? {...current, server: {...current.server, body: {...current.server.body, type: value}}} : current)}><SelectTrigger className="w-56"><SelectValue placeholder="请选择"/></SelectTrigger><SelectContent>{types.value?.types.map(type => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select></label>
      {(!basis.server.parseId || status === 'failed') && <Button disabled={busy || !basis.server.body.type} onClick={() => void parse()}>{status === 'failed' ? '重新提取条款' : '提取条款'}</Button>}
      {basis.server.parseId && <Button variant="outline" onClick={job.refresh}>刷新提取进度</Button>}
      {unknown && <DialogTrigger asChild><Button variant="outline" disabled={busy}>重新提取条款</Button></DialogTrigger>}
    </div>
    {pending && !job.error && <ReadSkeleton label="正在提取条款，完成后逐条核对原文…" kind="inline"/>}
    {unknown && <Alert><AlertTitle>提取结果暂不确定</AlertTitle><AlertDescription>原任务可能仍会完成。请先刷新提取进度，再决定是否重新提取。</AlertDescription></Alert>}
    {(error || job.error || types.error) && <Alert variant="destructive"><AlertTitle>{error || job.error || types.error}</AlertTitle></Alert>}
  </section><DialogContent><DialogHeader><DialogTitle>重新提取条款？</DialogTitle><DialogDescription>原任务可能仍会完成。重新提取会重新开始，不会取消原任务。</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button variant="outline">取消</Button></DialogClose><Button disabled={busy || !unknown} onClick={() => void parse(true)}>确认重新提取</Button></DialogFooter></DialogContent></Dialog>
}
