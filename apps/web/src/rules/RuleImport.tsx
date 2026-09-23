import {useEffect, useReducer, useRef, useState, type ChangeEvent} from 'react'
import {originalUrl, request, upload, type Job, type Material} from '../api'
import Help from '../Help'
import Modal from '../Modal'
import {productHref, type ReturnTarget} from '../navigation/route'
import {ActionBar, Badge, Button, Callout, FeedbackState, Field, FileInput, Heading, Inline, Input, Link, LockedFields, Page, ReportBlock, Select, Stack, Text, Textarea} from '../ui'
import {importBusy, ruleImportReducer, type RuleImportEvent, type RuleImportState} from '../workflow/ruleImport'
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
  const candidate = state.kind === 'reviewing' || state.kind === 'publishing' ? state.candidate : null
  const source = sourceMaterial(state.kind === 'idle' ? undefined : state.source)
  const error = state.kind === 'failed' || state.kind === 'unknown' || state.kind === 'reviewing' ? state.message : ''
  const jobId = 'jobId' in state ? state.jobId : ''
  const leavePrompt = state.kind === 'reviewing' && state.leavePrompt

  useEffect(() => {
    disposed.current = false
    function guardLeave(event: BeforeUnloadEvent) {
      const current = stateRef.current
      if (current.kind === 'reviewing' || current.kind === 'publishing') {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', guardLeave)
    void request<{types: string[]}>('/rule-types')
      .then(result => { if (!disposed.current) setTypes(result.types) })
      .catch(caught => {
        if (!disposed.current) send({type: 'catalog-failed', message: caught instanceof Error ? caught.message : '无法读取规则类型'})
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

  function jobError(job: Job<Candidate>) {
    const result = job.result
    if (result && typeof result === 'object' && 'error' in result && result.error) return String(result.error)
    return undefined
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

  function observe(id: string, job: Job<Candidate>) {
    const next = send({
      type: 'observed',
      job: {id, status: job.status, result: job.result, error: jobError(job)},
    })
    if (next.kind === 'parsing') timer.current = setTimeout(() => { void poll(id) }, 2000)
  }

  async function poll(id: string) {
    try {
      const job = await request<Job<Candidate>>(`/rules/parse/${id}`)
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
      const job = await request<Job<Candidate>>('/rules/parse', 'POST', {
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
      const job = await request<Job<Candidate> & {source_file_id: string; source_file_version_id: string; document_type: string}>(`/rules/parse/${id}`)
      if (disposed.current) return
      const source = job.source_file_id && job.source_file_version_id
        ? {file_id: job.source_file_id, file_version_id: job.source_file_version_id, original_filename: '规则原件'}
        : undefined
      send({type: 'record-source', source, documentType: job.document_type})
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
      const job = await request<Job<Candidate>>('/rules/parse', 'POST', {
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
      const result = await request<Detail>('/rules', 'POST', {...publishing.candidate, predecessor_id: predecessor || null})
      if (destination) location.href = destination
      else location.replace(productHref({page: 'rule-detail', ruleId: result.id, saved: true, returnTo}))
    } catch (caught) {
      send({type: 'publish-failed', message: caught instanceof Error ? caught.message : '保存规则失败'})
    }
  }

  function requestBack() {
    if (busy) return
    if (stateRef.current.kind === 'reviewing') send({type: 'prompt-leave'})
    else location.href = backHref
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
      : ''
  const canRerun = (state.kind === 'unknown' || state.kind === 'failed') && !!state.source
  const body = candidate?.body

  return (
    <Page>
      {error && <Callout tone="error" id="rule-message" testId="rule-message">{error}</Callout>}
      <Button type="button" variant="ghost" data-testid="rule-back" disabled={busy} onClick={requestBack}>← 规则库</Button>
      <Inline justify="between" align="center" wrap>
        <Heading level={2}>{candidate ? '核对规则' : predecessor ? '上传新版本' : '上传客户规则'}</Heading>
        {candidate && <Inline gap="xs" align="center"><Badge>机审清单</Badge><Help title="客户规则用途"><Text>保存后的规则条目是审核清单。能映射到内置检查的条目会机器执行；其余条目明确标为未覆盖，需人工核对。</Text></Help></Inline>}
      </Inline>

      {state.kind !== 'reviewing' && state.kind !== 'publishing' && (
        <LockedFields disabled={busy}>
          <Stack gap="sm">
            <Field label="文档类型">
              <Select id="parse-type" data-testid="parse-type" value={state.documentType} onChange={event => send({type: 'set-type', documentType: event.target.value})}>
                <option value="">自动识别</option>
                {types.map(name => <option key={name}>{name}</option>)}
              </Select>
            </Field>
            <FileInput id="rule-file" data-testid="rule-file" label="选择规则文件" aria-label="规则原件" accept=".xlsx,.xls,.xlsm,.docx,.pdf,.md,.json" disabled={busy} onChange={event => void parse(event)} />
            <Text size="sm" tone="muted">Excel、Word、PDF、Markdown、JSON</Text>
          </Stack>
        </LockedFields>
      )}

      {statusLabel && !error && <Callout id="rule-message" testId="rule-message">{statusLabel}</Callout>}
      {!!jobId && !candidate && (state.kind === 'unknown' || state.kind === 'failed') && <Button type="button" disabled={busy} onClick={() => void resumeParse()}>刷新解析状态</Button>}
      {canRerun && (
        <Stack gap="sm">
          <Text size="sm" tone="muted">原任务可能仍会完成，再次解析可能再次消耗模型费用。</Text>
          <Button type="button" id="rerun-rule" data-testid="rerun-rule" disabled={busy} onClick={() => void rerunParse()}>重新解析</Button>
        </Stack>
      )}

      {candidate && body && (
        <Stack gap="md">
          <Field label="规则名称">
            <Input id="rule-title" data-testid="rule-title" value={candidate.title} disabled={busy} onChange={event => edit({...candidate, title: event.target.value})} />
          </Field>
          <Text id="rule-origin" tone="muted">{predecessor ? '基于版本' : '新规则'}</Text>
          <Stack id="rule-metadata" gap="sm">
            <Field label="文件类型">
              <Select value={body.type} aria-label="核对规则类型" disabled={busy} onChange={event => edit({...candidate, body: {...body, type: event.target.value}})}>{types.map(name => <option key={name}>{name}</option>)}</Select>
            </Field>
            <Field label="适用客户">
              <Input id="rule-customers" data-testid="rule-customers" defaultValue={body.customers.join('、')} aria-label="适用客户" disabled={busy} onChange={event => setScope('customers', event)} />
            </Field>
            <Field label="型号">
              <Input defaultValue={body.models.join('、')} aria-label="型号" disabled={busy} onChange={event => setScope('models', event)} />
            </Field>
            <Field label="零件">
              <Input defaultValue={body.parts.join('、')} aria-label="零件" disabled={busy} onChange={event => setScope('parts', event)} />
            </Field>
          </Stack>
          {source?.file_id && <Link id="rule-source" data-testid="rule-source" href={originalUrl(source)} target="_blank" rel="noopener">原始文件 ↗</Link>}
          <Inline justify="between"><Heading level={3}>客户要求</Heading><Text tone="muted">{body.rules.length} 项</Text></Inline>
          {!body.rules.length && <FeedbackState title="未解析出规则条目" detail="请核对原始文件。" />}
          <Stack id="rule-items" testId="rule-items" gap="md">
            {body.rules.map((item, index) => (
              <ReportBlock key={item.id}>
                <Inline gap="sm" align="center">
                  <Text as="span" tone="muted">{String(index + 1).padStart(2, '0')}</Text>
                  <Heading level={3}>{item.category}</Heading>
                  {item.priority && <Badge>{item.priority}</Badge>}
                </Inline>
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
          </Stack>
          <ActionBar>
            <Text tone="muted">{body.rules.length} 项客户要求</Text>
            <Button type="button" variant="primary" data-testid="publish-rule" id="publish-rule" disabled={busy || !candidate.title.trim()} onClick={() => void publish()}>{busy ? '正在保存…' : '保存规则'}</Button>
          </ActionBar>
        </Stack>
      )}

      {leavePrompt && candidate && (
        <Modal title="保存修改后离开？" busy={busy} onClose={() => send({type: 'dismiss-leave'})}>
          <Stack gap="md">
            <Text>解析内容尚未保存。你可以保存后继续，也可以留在这里编辑。</Text>
            {error && <Callout tone="error">{error}</Callout>}
            <Inline gap="sm" wrap>
              <Button type="button" disabled={busy} onClick={() => send({type: 'dismiss-leave'})}>继续编辑</Button>
              <Button type="button" disabled={busy} onClick={() => { location.href = backHref }}>放弃修改</Button>
              <Button type="button" variant="primary" disabled={busy || !candidate.title.trim()} onClick={() => void publish(backHref)}>{busy ? '正在保存…' : '保存并离开'}</Button>
            </Inline>
          </Stack>
        </Modal>
      )}
    </Page>
  )
}
