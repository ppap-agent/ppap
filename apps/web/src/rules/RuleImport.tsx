import {useEffect, useRef, useState, type FormEvent} from 'react'
import {originalUrl, request, upload, type Job, type Material} from '../api'
import Help from '../Help'
import Modal from '../Modal'
import {productHref, type ReturnTarget} from '../navigation/route'
import {Badge, Button} from '../ui/primitives'
import type {RuleCandidate as Candidate, RuleDetail as Detail} from './types'

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
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [source, setSource] = useState<Material | undefined>()
  const [types, setTypes] = useState<string[]>([])
  const [type, setType] = useState(initialType)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [parseId, setParseId] = useState(initialParseId)
  const [pendingLeave, setPendingLeave] = useState(false)

  const disposed = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const candidateRef = useRef(candidate)
  const busyRef = useRef(busy)
  candidateRef.current = candidate
  busyRef.current = busy

  const backHref = productHref({page: 'rule-library', returnTo})

  useEffect(() => {
    disposed.current = false
    function guardLeave(event: BeforeUnloadEvent) {
      if (candidateRef.current) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', guardLeave)
    void request<{types: string[]}>('/rule-types')
      .then(result => { if (!disposed.current) setTypes(result.types) })
      .catch(caught => { if (!disposed.current) setError(caught instanceof Error ? caught.message : '无法读取规则类型') })
    if (initialParseId) void resumeParse(initialParseId)
    return () => {
      disposed.current = true
      clearTimeout(timer.current)
      window.removeEventListener('beforeunload', guardLeave)
    }
  }, [])

  function updateParseUrl(id: string) {
    history.replaceState(null, '', productHref({
      page: 'rule-import',
      parseId: id,
      predecessorId: predecessor || undefined,
      returnTo,
    }))
  }

  function setScope(field: 'customers' | 'models' | 'parts', event: FormEvent<HTMLInputElement>) {
    const values = [...new Set(event.currentTarget.value.split(/[,，、]/).map(value => value.trim()).filter(Boolean))]
    setCandidate(current => current ? {...current, body: {...current.body, [field]: values}} : current)
  }

  function applyParseJob(id: string, job: Job<Candidate>) {
    setStatus(job.status)
    if (job.status === 'succeeded' && job.result) {
      setCandidate(job.result)
      setBusy(false)
      busyRef.current = false
    } else if (['queued', 'leased'].includes(job.status)) {
      timer.current = setTimeout(() => { void poll(id) }, 2000)
    } else {
      setBusy(false)
      busyRef.current = false
      setError(
        job.status === 'unknown'
          ? '解析结果暂不确定，请稍后刷新查看，不要重复提交。原任务可能仍会完成，再次解析可能再次消耗模型费用。'
          : job.result?.error === 'rule_type_required'
            ? '请选择文档类型后再解析。'
            : job.result?.error || '解析失败，请检查原件与文档类型。',
      )
    }
  }

  async function poll(id: string) {
    try {
      const job = await request<Job<Candidate>>(`/rules/parse/${id}`)
      if (disposed.current) return
      applyParseJob(id, job)
    } catch {
      setBusy(false)
      busyRef.current = false
      setError('无法获取解析状态，请刷新重访当前任务。')
    }
  }

  async function parse(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError('')
    setCandidate(null)
    try {
      const uploaded = await upload(file, 'reference')
      setSource(uploaded)
      const job = await request<Job<Candidate>>('/rules/parse', 'POST', {
        request_id: crypto.randomUUID(),
        source_file_id: uploaded.file_id,
        source_file_version_id: uploaded.file_version_id,
        document_type: type,
      })
      setParseId(job.id)
      updateParseUrl(job.id)
      await poll(job.id)
    } catch (caught) {
      setBusy(false)
      busyRef.current = false
      setError(caught instanceof Error ? caught.message : '上传失败')
    }
  }

  async function resumeParse(id = parseId) {
    if (busyRef.current || !id) return
    busyRef.current = true
    setBusy(true)
    setError('')
    try {
      const job = await request<Job<Candidate> & {source_file_id: string; source_file_version_id: string; document_type: string}>(`/rules/parse/${id}`)
      if (disposed.current) return
      setSource({
        file_id: job.source_file_id,
        file_version_id: job.source_file_version_id,
        original_filename: '规则原件',
        role: 'reference',
        rule_version_id: null,
      })
      setType(job.document_type)
      applyParseJob(id, job)
    } catch {
      setBusy(false)
      busyRef.current = false
      setError('无法读取解析任务，请稍后重新读取。')
    }
  }

  async function rerunParse() {
    if (busyRef.current || !source) return
    busyRef.current = true
    setBusy(true)
    setError('')
    setCandidate(null)
    try {
      const job = await request<Job<Candidate>>('/rules/parse', 'POST', {
        request_id: crypto.randomUUID(),
        source_file_id: source.file_id,
        source_file_version_id: source.file_version_id,
        document_type: type,
      })
      setParseId(job.id)
      updateParseUrl(job.id)
      await poll(job.id)
    } catch (caught) {
      setBusy(false)
      busyRef.current = false
      setError(caught instanceof Error ? caught.message : '无法重新解析')
    }
  }

  async function publish(destination?: string) {
    if (!candidateRef.current || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError('')
    try {
      const result = await request<Detail>('/rules', 'POST', {...candidateRef.current, predecessor_id: predecessor || null})
      if (destination) {
        location.href = destination
      } else {
        location.replace(productHref({page: 'rule-detail', ruleId: result.id, saved: true, returnTo}))
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存规则失败')
      setBusy(false)
      busyRef.current = false
    }
  }

  function requestBack() {
    if (busyRef.current) return
    if (candidateRef.current) setPendingLeave(true)
    else location.href = backHref
  }

  const statusLabel = ({queued: '正在排队解析…', leased: '正在解析规则…', succeeded: '解析完成，请核对并保存。'} as Record<string, string>)[status] || status
  const canRerun = !!source && !candidate && (!!error || status === 'unknown' || status === 'failed')
  const body = candidate?.body

  return (
    <section className="page-content rules-page">
      {error && <p className="error" role="alert" id="rule-message" data-testid="rule-message">{error}</p>}
      <button type="button" className="back-link" disabled={busy} onClick={requestBack}>← 规则库</button>
      <div className="section-heading">
        <h2>{candidate ? '核对规则' : predecessor ? '上传新版本' : '上传客户规则'}</h2>
        {candidate && <div className="scope-inline"><Badge>机审清单</Badge><Help title="客户规则用途"><p>保存后的规则条目是审核清单。能映射到内置检查的条目会机器执行；其余条目明确标为未覆盖，需人工核对。</p></Help></div>}
      </div>

      {!candidate && (
        <fieldset className="rule-upload" disabled={busy}>
          <label>文档类型
            <select id="parse-type" data-testid="parse-type" value={type} onChange={event => setType(event.target.value)}>
              <option value="">自动识别</option>
              {types.map(name => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label className="file-button button pc-primary">选择规则文件
            <input id="rule-file" data-testid="rule-file" type="file" aria-label="规则原件" accept=".xlsx,.xls,.xlsm,.docx,.pdf,.md,.json" disabled={busy} onChange={event => void parse(event)} />
          </label>
          <p className="pc-muted">Excel、Word、PDF、Markdown、JSON</p>
        </fieldset>
      )}

      {status && !error && <p role="status" className="notice" id="rule-message" data-testid="rule-message">{statusLabel}</p>}
      {parseId && !candidate && (error || status === 'unknown') && <button type="button" disabled={busy} onClick={() => void resumeParse()}>刷新解析状态</button>}
      {canRerun && (
        <>
          <p className="pc-muted">原任务可能仍会完成，再次解析可能再次消耗模型费用。</p>
          <button type="button" id="rerun-rule" data-testid="rerun-rule" disabled={busy} onClick={() => void rerunParse()}>重新解析</button>
        </>
      )}

      {candidate && body && (
        <>
          <label className="rule-name">规则名称<input id="rule-title" data-testid="rule-title" value={candidate.title} disabled={busy} onChange={event => setCandidate({...candidate, title: event.target.value})} /></label>
          <p id="rule-origin" className="pc-muted">{predecessor ? '基于版本' : '新规则'}</p>
          <dl className="rule-metadata">
            <div>
              <dt>文件类型</dt>
              <dd><select value={body.type} aria-label="核对规则类型" disabled={busy} onChange={event => setCandidate({...candidate, body: {...body, type: event.target.value}})}>{types.map(name => <option key={name}>{name}</option>)}</select></dd>
            </div>
            <div>
              <dt>适用客户</dt>
              <dd><input id="rule-customers" data-testid="rule-customers" defaultValue={body.customers.join('、')} aria-label="适用客户" disabled={busy} onChange={event => setScope('customers', event)} /></dd>
            </div>
            <div>
              <dt>型号</dt>
              <dd><input defaultValue={body.models.join('、')} aria-label="型号" disabled={busy} onChange={event => setScope('models', event)} /></dd>
            </div>
            <div>
              <dt>零件</dt>
              <dd><input defaultValue={body.parts.join('、')} aria-label="零件" disabled={busy} onChange={event => setScope('parts', event)} /></dd>
            </div>
          </dl>
          <div className="toolbar rule-detail-actions">
            {source?.file_id && <a id="rule-source" data-testid="rule-source" href={originalUrl(source)} target="_blank" rel="noopener">原始文件 ↗</a>}
          </div>
          <div className="section-heading"><h3>客户要求</h3><span className="pc-muted">{body.rules.length} 项</span></div>
          {!body.rules.length && <p className="empty-state">未解析出规则条目，请核对原始文件。</p>}
          <div data-testid="rule-items" id="rule-items">
            {body.rules.map((item, index) => (
              <article key={item.id} className="rule-item">
                <h3><span className="rule-number">{String(index + 1).padStart(2, '0')}</span>{item.category} {item.priority && <Badge>{item.priority}</Badge>}</h3>
                <textarea
                  data-field="rule_to_check"
                  value={item.rule_to_check}
                  disabled={busy}
                  rows={3}
                  aria-label={'检查要求 ' + (index + 1)}
                  onChange={event => setCandidate({
                    ...candidate,
                    body: {...body, rules: body.rules.map((row, rowIndex) => rowIndex === index ? {...row, rule_to_check: event.target.value} : row)},
                  })}
                />
              </article>
            ))}
          </div>
          <div className="action-bar sticky-actions">
            <span className="pc-muted">{body.rules.length} 项客户要求</span>
            <button type="button" className="pc-primary" data-testid="publish-rule" id="publish-rule" disabled={busy || !candidate.title.trim()} onClick={() => void publish()}>{busy ? '正在保存…' : '保存规则'}</button>
          </div>
        </>
      )}

      {pendingLeave && (
        <Modal title="保存修改后离开？" busy={busy} onClose={() => setPendingLeave(false)}>
          <p>解析内容尚未保存。你可以保存后继续，也可以留在这里编辑。</p>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="leave-actions">
            <button type="button" disabled={busy} onClick={() => setPendingLeave(false)}>继续编辑</button>
            <button type="button" disabled={busy} onClick={() => { setCandidate(null); setPendingLeave(false); location.href = backHref }}>放弃修改</button>
            <button type="button" className="pc-primary" disabled={busy || !candidate?.title.trim()} onClick={() => void publish(backHref)}>{busy ? '正在保存…' : '保存并离开'}</button>
          </div>
        </Modal>
      )}
    </section>
  )
}
