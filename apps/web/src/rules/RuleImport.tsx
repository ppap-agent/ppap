import {useEffect, useReducer, useRef, useState, type ChangeEvent} from 'react'
import {originalUrl, request, upload, type Material, type RuleParseJob, type RuleParseRequest, type RulePublishRequest, type RuleTypes} from '../api'
import {Modal, LockedFields, ReportBlock} from '../ui/domain'
import {productHref, type ReturnTarget} from '../navigation/route'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Input} from '@/components/ui/input'
import {Label} from '@/components/ui/label'
import {NativeSelect} from '@/components/ui/native-select'
import {Textarea} from '@/components/ui/textarea'
import {PageHeader, PageLayout} from '../ui/PageLayout'
import {importBusy, importGuarded, ruleImportReducer, type RuleImportEvent, type RuleImportState} from '../workflow/ruleImport'
import type {RuleCandidate as Candidate, RuleDetail as Detail} from './types'

function sourceMaterial(source: {file_id: string; file_version_id: string; original_filename: string} | undefined): Material | undefined {
  if (!source) return undefined
  return {...source, role: 'reference', rule_version_id: null}
}

export default function RuleImport({
  initialParseId,
  predecessor,
  initialType,
  returnTo,
}: {
  initialParseId: string
  predecessor: string
  initialType: string
  returnTo?: ReturnTarget
}) {
  const [state, dispatch] = useReducer(
    ruleImportReducer<Candidate>,
    initialParseId
      ? {kind: 'parsing' as const, jobId: initialParseId, documentType: initialType, progress: 'waiting' as const}
      : {kind: 'idle' as const, documentType: initialType},
  )
  const [types, setTypes] = useState<string[]>([])
  const stateRef = useRef<RuleImportState<Candidate>>(state)
  const disposed = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const backHref = productHref({page: 'rule-library', returnTo})
  const busy = importBusy(state)
  const candidate = state.kind === 'reviewing' || state.kind === 'publishing' || state.kind === 'saved' ? state.candidate : null
  const source = sourceMaterial('source' in state ? state.source : undefined)
  const error = state.kind === 'failed' || state.kind === 'unknown' || state.kind === 'reviewing' ? state.message : ''
  const jobId = 'jobId' in state ? state.jobId : ''
  const leavePrompt = state.kind === 'reviewing' && state.leavePrompt

  useEffect(() => {
    disposed.current = false
    function guardLeave(event: BeforeUnloadEvent) {
      const current = stateRef.current
      if (importGuarded(current)) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', guardLeave)
    void request<RuleTypes>('/rule-types')
      .then(result => { if (!disposed.current) setTypes(result.types) })
      .catch(caught => {
        if (!disposed.current) send({type: 'catalog-failed', message: caught instanceof TypeError ? '无法读取规则类型，请刷新重试。' : caught instanceof Error ? caught.message : '无法读取规则类型'})
      })
    if (initialParseId) void resumeParse(initialParseId)
    return () => {
      disposed.current = true
      clearTimeout(timer.current)
      window.removeEventListener('beforeunload', guardLeave)
    }
  }, [])

  function send(event: RuleImportEvent<Candidate>) {
    const previous = stateRef.current
    const next = ruleImportReducer(previous, event)
    if (next === previous) return next
    stateRef.current = next
    dispatch(event)
    return next
  }

  function jobError(job: RuleParseJob) {
    const result = job.result
    if (result && typeof result === 'object' && 'error' in result && result.error) return String(result.error)
    return undefined
  }

  function candidateResult(job: RuleParseJob): Candidate | undefined {
    const result = job.result
    return result && !('error' in result) ? result : undefined
  }

  function updateParseUrl(id: string) {
    history.replaceState(null, '', productHref({
      page: 'rule-import',
      parseId: id,
      predecessorId: predecessor || undefined,
      returnTo,
    }))
  }

  function edit(candidateNext: Candidate) {
    send({type: 'edit', candidate: candidateNext})
  }

  function observe(id: string, job: RuleParseJob) {
    const next = send({
      type: 'observed',
      job: {id, status: job.status, result: candidateResult(job), error: jobError(job)},
    })
    if (next.kind === 'parsing') timer.current = setTimeout(() => { void poll(id) }, 2000)
  }

  async function poll(id: string) {
    try {
      const job = await request<RuleParseJob>(`/rules/parse/${id}`)
      if (disposed.current) return
      observe(id, job)
    } catch {
      send({type: 'observed', job: {id, status: 'failed', error: '无法获取解析状态，请刷新重访当前任务。'}})
    }
  }

  async function parse(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const previous = stateRef.current
    const started = send({type: 'begin-upload'})
    if (started === previous || started.kind !== 'uploading') return
    try {
      const uploaded = await upload(file, 'reference')
      const job = await request<RuleParseJob, RuleParseRequest>('/rules/parse', 'POST', {
        request_id: crypto.randomUUID(),
        source_file_id: uploaded.file_id,
        source_file_version_id: uploaded.file_version_id,
        document_type: started.documentType,
      })
      send({
        type: 'begin-parse',
        jobId: job.id,
        source: {file_id: uploaded.file_id, file_version_id: uploaded.file_version_id, original_filename: uploaded.original_filename},
      })
      updateParseUrl(job.id)
      await poll(job.id)
    } catch (caught) {
      send({type: 'upload-failed', message: caught instanceof Error ? caught.message : '上传失败'})
    }
  }

  async function resumeParse(id = jobId) {
    const current = stateRef.current
    const reading = current.kind === 'parsing' && current.jobId === id ? current : send({type: 'read-back'})
    if (reading.kind !== 'parsing' || reading.jobId !== id) return
    try {
      const job = await request<RuleParseJob>(`/rules/parse/${id}`)
      if (disposed.current) return
      const source = job.source_file_id && job.source_file_version_id
        ? {file_id: job.source_file_id, file_version_id: job.source_file_version_id, original_filename: '规则原件'}
        : undefined
      send({type: 'record-source', jobId: id, source, documentType: job.document_type})
      observe(id, job)
    } catch {
      send({type: 'observed', job: {id, status: 'failed', error: '无法读取解析任务，请稍后重新读取。'}})
    }
  }

  async function rerunParse() {
    const previous = stateRef.current
    const started = send({type: 'begin-rerun'})
    if (started === previous || started.kind !== 'uploading' || !started.source) return
    try {
      const job = await request<RuleParseJob, RuleParseRequest>('/rules/parse', 'POST', {
        request_id: crypto.randomUUID(),
        source_file_id: started.source.file_id,
        source_file_version_id: started.source.file_version_id,
        document_type: started.documentType,
      })
      send({type: 'begin-parse', jobId: job.id, source: started.source})
      updateParseUrl(job.id)
      await poll(job.id)
    } catch (caught) {
      send({type: 'upload-failed', message: caught instanceof Error ? caught.message : '无法重新解析'})
    }
  }

  async function publish(destination?: string) {
    const previous = stateRef.current
    const publishing = send({type: 'begin-publish', leaveAfter: !!destination})
    if (publishing === previous || publishing.kind !== 'publishing') return
    try {
      const result = await request<Detail, RulePublishRequest>('/rules', 'POST', {...publishing.candidate, predecessor_id: predecessor || null})
      const href = destination || productHref({page: 'rule-detail', ruleId: result.id, saved: true, returnTo})
      const saved = send({type: 'publish-succeeded', ruleId: result.id, destination: href})
      if (saved.kind !== 'saved') return
      location.href = saved.destination || href
    } catch (caught) {
      send({type: 'publish-failed', message: caught instanceof Error ? caught.message : '保存规则失败'})
    }
  }

  function requestBack() {
    if (busy) return
    if (stateRef.current.kind === 'reviewing') send({type: 'prompt-leave'})
    else location.href = backHref
  }

  function confirmAbandon() {
    const next = send({type: 'abandon'})
    if (next.kind !== 'abandoned') return
    location.href = backHref
  }

  function openSaved() {
    const current = stateRef.current
    if (current.kind !== 'saved') return
    location.href = current.destination || productHref({page: 'rule-detail', ruleId: current.ruleId, saved: true, returnTo})
  }

  function setScope(field: 'customers' | 'models' | 'parts', event: ChangeEvent<HTMLInputElement>) {
    if (!candidate) return
    const values = [...new Set(event.currentTarget.value.split(/[,，、]/).map(value => value.trim()).filter(Boolean))]
    edit({...candidate, body: {...candidate.body, [field]: values}})
  }

  const statusLabel = state.kind === 'parsing'
    ? state.progress === 'queued' ? '正在排队解析…' : '正在解析规则…'
    : state.kind === 'reviewing' || state.kind === 'publishing'
      ? '解析完成，请核对并保存。'
      : state.kind === 'saved'
        ? '已保存规则'
        : ''
  const canRerun = (state.kind === 'unknown' || state.kind === 'failed') && !!state.source
  const body = candidate?.body

  return (
    <PageLayout>
      <PageHeader
        title={candidate ? '核对规则' : predecessor ? '上传新版本' : '上传客户规则'}
        back={<Button type="button" variant="ghost" data-testid="rule-back" disabled={busy} onClick={requestBack}>← 规则库</Button>}
        action={candidate && source?.file_id ? <Button asChild variant="outline" size="sm"><a id="rule-source" data-testid="rule-source" href={originalUrl(source)} target="_blank" rel="noopener">查看原始文件 ↗</a></Button> : undefined}
      />
      {error && <Alert variant="destructive" id="rule-message" data-testid="rule-message"><AlertDescription>{error}</AlertDescription></Alert>}

      {state.kind !== 'reviewing' && state.kind !== 'publishing' && state.kind !== 'saved' && state.kind !== 'abandoned' && (
        <LockedFields disabled={busy}>
          <div className="grid gap-4 border-y py-5 sm:grid-cols-[minmax(0,18rem)_minmax(0,1fr)] sm:items-end">
            <div className="grid gap-1.5 text-sm font-medium"><Label htmlFor="parse-type">规则文件类型</Label><NativeSelect id="parse-type" data-testid="parse-type" value={state.documentType} onChange={event => send({type: 'set-type', documentType: event.target.value})}>
              <option value="">自动识别</option>
              {types.map(name => <option key={name}>{name}</option>)}
            </NativeSelect></div>
            <div className="flex flex-wrap items-end gap-3">
              <Button asChild type="button" variant="outline"><label><span>选择规则文件</span><input id="rule-file" data-testid="rule-file" className="sr-only" type="file" aria-label="规则原件" accept=".xlsx,.xls,.xlsm,.docx,.pdf,.md,.json" disabled={busy} onChange={event => void parse(event)} /></label></Button>
              <span className="text-sm text-muted-foreground">支持 Excel、Word、PDF、Markdown、JSON</span>
            </div>
          </div>
        </LockedFields>
      )}

      {statusLabel && !error && <Alert id="rule-message" data-testid="rule-message"><AlertDescription>{statusLabel}</AlertDescription></Alert>}
      {!!jobId && !candidate && (state.kind === 'unknown' || state.kind === 'failed') && <Button type="button" disabled={busy} onClick={() => void resumeParse()}>刷新解析状态</Button>}
      {canRerun && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">原任务可能仍会完成，再次解析可能再次消耗模型费用。</p>
          <Button type="button" id="rerun-rule" data-testid="rerun-rule" disabled={busy} onClick={() => void rerunParse()}>重新解析</Button>
        </div>
      )}

      {candidate && body && (
        <div className="flex flex-col gap-4">
          <label className="grid gap-1.5 text-sm font-medium">规则名称<Input id="rule-title" data-testid="rule-title" value={candidate.title} disabled={busy || state.kind === 'saved'} onChange={event => edit({...candidate, title: event.target.value})} /></label>
          <span id="rule-origin" className="text-sm text-muted-foreground">{predecessor ? '基于上一版本' : '新规则'}</span>
          <div id="rule-metadata" className="grid min-w-0 gap-3 border-y py-4 sm:grid-cols-2 xl:grid-cols-4">
            <label className="grid min-w-0 gap-1.5 text-sm font-medium">文件类型<NativeSelect className="w-full" value={body.type} aria-label="核对规则类型" disabled={busy} onChange={event => edit({...candidate, body: {...body, type: event.target.value}})}>{types.map(name => <option key={name}>{name}</option>)}</NativeSelect></label>
            <label className="grid min-w-0 gap-1.5 text-sm font-medium">适用客户<Input id="rule-customers" data-testid="rule-customers" defaultValue={body.customers.join('、')} aria-label="适用客户" disabled={busy} onChange={event => setScope('customers', event)} /></label>
            <label className="grid min-w-0 gap-1.5 text-sm font-medium">型号<Input defaultValue={body.models.join('、')} aria-label="型号" disabled={busy} onChange={event => setScope('models', event)} /></label>
            <label className="grid min-w-0 gap-1.5 text-sm font-medium">零件<Input defaultValue={body.parts.join('、')} aria-label="零件" disabled={busy} onChange={event => setScope('parts', event)} /></label>
          </div>
          <div className="flex items-baseline justify-between"><h2 className="text-base font-semibold">客户要求</h2><span className="text-sm text-muted-foreground">{body.rules.length} 条</span></div>
          {!body.rules.length && <Alert><AlertTitle>未解析出规则条目</AlertTitle><AlertDescription>请核对原始文件。</AlertDescription></Alert>}
          <div id="rule-items" data-testid="rule-items" className="flex flex-col gap-4">
            {body.rules.map((item, index) => (
              <ReportBlock key={item.id}>
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">{String(index + 1).padStart(2, '0')}</span>
                  <h3 className="text-lg font-semibold tracking-tight">{item.category}</h3>
                  {item.priority && <Badge>{item.priority}</Badge>}
                </div>
                <Textarea
                  data-field="rule_to_check"
                  value={item.rule_to_check}
                  disabled={busy}
                  rows={3}
                  aria-label={'检查要求 ' + (index + 1)}
                  onChange={event => edit({
                    ...candidate,
                    body: {...body, rules: body.rules.map((row, rowIndex) => rowIndex === index ? {...row, rule_to_check: event.target.value} : row)},
                  })}
                />
              </ReportBlock>
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 py-4">
            {state.kind === 'saved'
              ? <Button type="button" data-testid="publish-rule" id="publish-rule" onClick={openSaved}>打开已保存规则</Button>
              : <Button type="button" data-testid="publish-rule" id="publish-rule" disabled={busy || !candidate.title.trim()} onClick={() => void publish()}>{busy ? '正在保存…' : '保存规则'}</Button>}
          </div>
        </div>
      )}

      {leavePrompt && candidate && (
        <Modal title="保存修改后离开？" busy={busy} onClose={() => send({type: 'dismiss-leave'})}>
          <div className="flex flex-col gap-4">
            <p>解析内容尚未保存。你可以保存后继续，也可以留在这里编辑。</p>
            {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
            <div className="flex flex-wrap gap-2">
              <Button type="button" disabled={busy} onClick={() => send({type: 'dismiss-leave'})}>继续编辑</Button>
              <Button type="button" disabled={busy} onClick={confirmAbandon}>放弃修改</Button>
              <Button type="button" disabled={busy || !candidate.title.trim()} onClick={() => void publish(backHref)}>{busy ? '正在保存…' : '保存并离开'}</Button>
            </div>
          </div>
        </Modal>
      )}
    </PageLayout>
  )
}
