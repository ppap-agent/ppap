import {useEffect, useMemo, useRef, useState, type FormEvent} from 'react'
import {request, upload, originalUrl, dateLabel, type Rule, type Material, type Job} from './api'
import {isSafeReturnTo, latestRules} from './helpers'
import rulesIcon from './assets/rules.svg'
import Modal from './Modal'

interface RuleItem {id: string; category: string; priority: string | null; rule_to_check: string; type_of_rule: string}
interface RuleBody {kind: string; type: string; rule_profile: string; customers: string[]; models: string[]; parts: string[]; rules: RuleItem[]}
interface Detail extends Rule {body: RuleBody; source_file_id: string; source_file_version_id: string}
interface Candidate {title: string; body: RuleBody; source_file_version_id: string}

export default function RulesView() {
  const [rules, setRules] = useState<Rule[]>([])
  const [selected, setSelected] = useState<Detail | null>(null)
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [source, setSource] = useState<Material | undefined>()
  const [types, setTypes] = useState<string[]>([])
  const [type, setType] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [predecessor, setPredecessor] = useState<string | null>(null)
  const [customer, setCustomer] = useState('')
  const [view, setView] = useState<'list' | 'detail' | 'upload'>('list')
  const [showVersions, setShowVersions] = useState(false)
  const [parseId, setParseId] = useState(new URLSearchParams(location.search).get('parse') || '')
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null)
  const params = useRef(new URLSearchParams(location.search))
  const backTo = params.current.get('return_to') || ''
  const returnTo = isSafeReturnTo(backTo) ? backTo : ''
  const returnLabel = returnTo.startsWith('/?round=') ? '返回审核结果' : '返回审核准备'
  const disposed = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const candidateRef = useRef(candidate)
  candidateRef.current = candidate
  const busyRef = useRef(busy)
  busyRef.current = busy

  const currentRules = useMemo(() => latestRules(rules), [rules])
  const customers = useMemo(() => Array.from(new Set(currentRules.flatMap(rule => rule.customers?.length ? rule.customers : ['未指定客户']))).sort(), [currentRules])
  function belongs(rule: Rule, name: string) {
    return name === '未指定客户' ? !rule.customers?.length : !!rule.customers?.includes(name)
  }
  const visible = (showVersions ? rules : currentRules).filter(rule => (!customer || belongs(rule, customer)) && (rule.title + ' ' + rule.type).toLowerCase().includes(query.toLowerCase()))
  const body = candidate?.body || selected?.body
  const sourceFile = candidate
    ? source
    : selected
      ? {file_id: selected.source_file_id, file_version_id: selected.source_file_version_id, original_filename: selected.title, role: 'reference' as const, rule_version_id: null}
      : undefined

  function guardLeave(event: BeforeUnloadEvent) {
    if (candidateRef.current) { event.preventDefault(); event.returnValue = '' }
  }
  function leaveCandidate(work: () => void) {
    if (busyRef.current) return
    if (candidateRef.current) setPendingLeave(() => work)
    else work()
  }
  function setUrl(ruleId?: string, nextParse?: string | null) {
    const next = new URLSearchParams()
    if (ruleId) next.set('rule', ruleId)
    if (nextParse) next.set('parse', nextParse)
    if (predecessor) next.set('predecessor', predecessor)
    if (returnTo) next.set('return_to', returnTo)
    history.replaceState(null, '', '/rules' + (next.size ? '?' + next : ''))
  }
  function showList(name = customer) {
    leaveCandidate(() => {
      setCustomer(name)
      setCandidate(null)
      setSelected(null)
      setView('list')
      setPredecessor(null)
      setParseId('')
      setStatus('')
      setError('')
      setUrl()
    })
  }
  function newRule() {
    leaveCandidate(() => {
      setCandidate(null)
      setSelected(null)
      setPredecessor(null)
      setParseId('')
      setSource(undefined)
      setType('')
      setStatus('')
      setError('')
      setView('upload')
      setUrl()
    })
  }
  function setScope(field: 'customers' | 'models' | 'parts', event: FormEvent<HTMLInputElement>) {
    const values = [...new Set(event.currentTarget.value.split(/[,，、]/).map(value => value.trim()).filter(Boolean))]
    setCandidate(current => current ? {...current, body: {...current.body, [field]: values}} : current)
  }
  async function run(work: () => Promise<void>) {
    setError('')
    try { await work() }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); throw e }
  }
  async function action(work: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    try { await run(work) }
    catch { /* surfaced in error */ }
    finally { busyRef.current = false; setBusy(false) }
  }
  async function refresh() {
    setRules((await request<{rules: Rule[]}>('/rules')).rules)
  }
  async function select(id: string) {
    leaveCandidate(() => {
      void action(async () => {
        const detail = await request<Detail>(`/rules/${id}`)
        setSelected(detail)
        setCandidate(null)
        setPredecessor(null)
        setParseId('')
        setStatus('')
        setView('detail')
        setUrl(detail.id)
      })
    })
  }
  function applyParseJob(id: string, job: Job<Candidate>) {
    setStatus(job.status)
    if (job.status === 'succeeded' && job.result) {
      setCandidate(job.result)
      setView('detail')
      setBusy(false)
    } else if (['queued', 'leased'].includes(job.status)) {
      timer.current = setTimeout(() => { void poll(id) }, 2000)
    } else {
      setBusy(false)
      setError(job.status === 'unknown' ? '解析结果暂不确定，请稍后刷新查看，不要重复提交。原任务可能仍会完成，再次解析可能再次消耗模型费用。' : job.result?.error === 'rule_type_required' ? '请选择文档类型后再解析。' : job.result?.error || '解析失败，请检查原件与文档类型。')
    }
  }
  async function poll(id: string) {
    try {
      const job = await request<Job<Candidate>>(`/rules/parse/${id}`)
      if (disposed.current) return
      applyParseJob(id, job)
    } catch {
      setBusy(false)
      setError('无法获取解析状态，请刷新重访当前任务。')
    }
  }
  async function parse(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || busyRef.current) return
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
      const url = new URL(location.href)
      url.searchParams.set('parse', job.id)
      if (predecessor) url.searchParams.set('predecessor', predecessor)
      else url.searchParams.delete('predecessor')
      history.replaceState(null, '', url)
      await poll(job.id)
    } catch (e) {
      setBusy(false)
      setError(e instanceof Error ? e.message : '上传失败')
    }
  }
  async function publish() {
    if (!candidateRef.current) return
    const result = await request<Detail>('/rules', 'POST', {...candidateRef.current, predecessor_id: predecessor})
    setCandidate(null)
    setSelected(result)
    setParseId('')
    await refresh()
    setUrl(result.id)
    setStatus('已保存规则')
  }
  async function resumeParse() {
    if (busyRef.current || !parseId) return
    setBusy(true)
    setError('')
    setView('upload')
    try {
      const job = await request<Job<Candidate> & {source_file_id: string; source_file_version_id: string; document_type: string}>(`/rules/parse/${parseId}`)
      if (disposed.current) return
      setSource({file_id: job.source_file_id, file_version_id: job.source_file_version_id, original_filename: '规则原件', role: 'reference', rule_version_id: null})
      setType(job.document_type)
      setPredecessor(new URLSearchParams(location.search).get('predecessor'))
      applyParseJob(parseId, job)
    } catch {
      setBusy(false)
      setError('无法读取解析任务，请稍后重新读取。')
    }
  }
  async function rerunParse() {
    if (busyRef.current || !source) return
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
      const url = new URL(location.href)
      url.searchParams.set('parse', job.id)
      history.replaceState(null, '', url)
      await poll(job.id)
    } catch (e) {
      setBusy(false)
      setError(e instanceof Error ? e.message : '无法重新解析')
    }
  }

  useEffect(() => {
    disposed.current = false
    window.addEventListener('beforeunload', guardLeave)
    void action(async () => {
      await refresh()
      setTypes((await request<{types: string[]}>('/rule-types')).types)
    }).then(async () => {
      if (disposed.current) return
      if (parseId) await resumeParse()
      else if (params.current.get('rule')) await select(params.current.get('rule')!)
      else if (params.current.has('new')) newRule()
    })
    return () => {
      disposed.current = true
      clearTimeout(timer.current)
      window.removeEventListener('beforeunload', guardLeave)
    }
  }, [])

  const statusLabel = ({queued: '正在排队解析…', leased: '正在解析规则…', succeeded: '解析完成，请核对并保存。'} as Record<string, string>)[status] || status
  const canRerun = !!source && !candidate && (error || status === 'unknown' || status === 'failed')

  return (
    <section className="page-content rules-page">
      {error && <p className="error" role="alert" id="rule-message" data-testid="rule-message">{error}</p>}
      {returnTo && !candidate && <a className="back-link" href={returnTo}>← {returnLabel}</a>}
      {view === 'list' ? (
        <>
          <div className="toolbar">
            <input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="搜索规则名称或文件类型" aria-label="搜索规则" />
            <select value={customer} aria-label="客户筛选" onChange={event => setCustomer(event.target.value)}>
              <option value="">全部客户</option>
              {customers.map(name => <option key={name}>{name}</option>)}
            </select>
            <label className="inline-check"><input type="checkbox" checked={showVersions} onChange={event => setShowVersions(event.target.checked)} />历史版本</label>
            <button type="button" className="pc-primary toolbar-end" data-testid="new-rule" id="new-rule" disabled={busy} onClick={newRule}>上传规则</button>
          </div>
          {busy && <p className="loading-state" role="status">正在读取规则…</p>}
          {!busy && !visible.length && (
            <div className="empty-state">
              <img src={rulesIcon} alt="" width={36} height={36} />
              <h2>{query || customer ? '没有匹配的规则' : '还没有客户规则'}</h2>
              {query || customer
                ? <button type="button" onClick={() => { setQuery(''); setCustomer('') }}>清除筛选</button>
                : <button type="button" className="pc-primary" onClick={newRule}>上传规则</button>}
            </div>
          )}
          {!!visible.length && (
            <div className="table-scroll">
              <table className="responsive-table rules-table">
                <thead><tr><th>规则名称</th><th>适用客户</th><th>文件类型</th><th>更新时间</th></tr></thead>
                <tbody data-testid="rule-versions" id="rule-versions">
                  {visible.map(rule => (
                    <tr key={rule.id}>
                      <td className="record-title">
                        <button type="button" className="text-button" disabled={busy} onClick={() => void select(rule.id)}><img src={rulesIcon} alt="" width={18} height={18} />{rule.title}</button>
                        {!currentRules.some(row => row.id === rule.id) && <span className="badge">历史版本</span>}
                      </td>
                      <td data-label="适用客户">{rule.customers?.join('、') || '未指定'}</td>
                      <td data-label="文件类型">{rule.type || '未指定'}</td>
                      <td data-label="更新时间">{dateLabel(rule.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <>
          <button type="button" className="back-link" disabled={busy} onClick={() => showList()}>← 规则库</button>
          <div className="section-heading">
            <h2>{candidate ? '核对规则' : view === 'upload' ? (predecessor ? '上传新版本' : '上传客户规则') : selected?.title}</h2>
            {view === 'detail' && <span className="badge">人工复核参考</span>}
          </div>
          {view === 'upload' && (
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
          {body && view === 'detail' && (
            <>
              {candidate && <label className="rule-name">规则名称<input id="rule-title" data-testid="rule-title" value={candidate.title} disabled={busy} onChange={event => setCandidate({...candidate, title: event.target.value})} /></label>}
              <p id="rule-origin" className="pc-muted">{candidate ? (predecessor ? '基于版本' : '新规则') : '版本 · ' + dateLabel(selected?.created_at)}</p>
              <dl className="rule-metadata">
                <div>
                  <dt>文件类型</dt>
                  <dd>{candidate
                    ? <select value={candidate.body.type} aria-label="核对规则类型" disabled={busy} onChange={event => setCandidate({...candidate, body: {...candidate.body, type: event.target.value}})}>{types.map(name => <option key={name}>{name}</option>)}</select>
                    : body.type}</dd>
                </div>
                <div>
                  <dt>适用客户</dt>
                  <dd>{candidate
                    ? <input id="rule-customers" data-testid="rule-customers" defaultValue={body.customers.join('、')} aria-label="适用客户" disabled={busy} onChange={event => setScope('customers', event)} />
                    : (body.customers?.join('、') || '未指定')}</dd>
                </div>
                <div>
                  <dt>型号</dt>
                  <dd>{candidate
                    ? <input defaultValue={body.models.join('、')} aria-label="型号" disabled={busy} onChange={event => setScope('models', event)} />
                    : (body.models?.join('、') || '未指定')}</dd>
                </div>
                <div>
                  <dt>零件</dt>
                  <dd>{candidate
                    ? <input defaultValue={body.parts.join('、')} aria-label="零件" disabled={busy} onChange={event => setScope('parts', event)} />
                    : (body.parts?.join('、') || '未指定')}</dd>
                </div>
              </dl>
              <div className="toolbar rule-detail-actions">
                {sourceFile?.file_id && <a id="rule-source" data-testid="rule-source" href={originalUrl(sourceFile)} target="_blank" rel="noopener">原始文件 ↗</a>}
                {selected && !candidate && <span className="pc-muted">版本 · {dateLabel(selected.created_at)}</span>}
                {selected?.predecessor_id && !candidate && <button type="button" disabled={busy} onClick={() => void select(selected.predecessor_id!)}>上一版本</button>}
                {selected && !candidate && <button type="button" disabled={busy} onClick={() => { setPredecessor(selected.id); setType(selected.body.type); setView('upload'); setStatus('') }}>上传新版本</button>}
              </div>
              <p className="scope-note">客户要求用于人工复核，不会自动增加检查项。</p>
              <div className="section-heading"><h3>客户要求</h3><span className="pc-muted">{body.rules?.length || 0} 项</span></div>
              {!body.rules?.length && <p className="empty-state">未解析出规则条目，请核对原始文件。</p>}
              <div data-testid="rule-items" id="rule-items">
                {body.rules.map((item, index) => (
                  <article key={item.id} className="rule-item">
                    <h3><span className="rule-number">{String(index + 1).padStart(2, '0')}</span>{item.category} {item.priority && <small className="badge">{item.priority}</small>}</h3>
                    {candidate
                      ? <textarea data-field="rule_to_check" value={item.rule_to_check} disabled={busy} rows={3} aria-label={'检查要求 ' + (index + 1)} onChange={event => setCandidate({
                          ...candidate,
                          body: {...candidate.body, rules: candidate.body.rules.map((row, rowIndex) => rowIndex === index ? {...row, rule_to_check: event.target.value} : row)},
                        })} />
                      : <p>{item.rule_to_check}</p>}
                  </article>
                ))}
              </div>
              {candidate
                ? <div className="action-bar sticky-actions"><span className="pc-muted">{body.rules.length} 项客户要求</span><button type="button" className="pc-primary" data-testid="publish-rule" id="publish-rule" disabled={busy || !candidate.title.trim()} onClick={() => void action(publish)}>保存规则</button></div>
                : returnTo ? <a className="button pc-primary" href={returnTo}>{returnLabel}</a> : null}
            </>
          )}
        </>
      )}
      {pendingLeave && (
        <Modal title="保存修改后离开？" busy={busy} onClose={() => setPendingLeave(null)}>
          <p>解析内容尚未保存。你可以保存后继续，也可以留在这里编辑。</p>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="leave-actions">
            <button type="button" disabled={busy} onClick={() => setPendingLeave(null)}>继续编辑</button>
            <button type="button" disabled={busy} onClick={() => { const work = pendingLeave; setPendingLeave(null); setCandidate(null); work() }}>放弃修改</button>
            <button type="button" className="pc-primary" disabled={busy || !candidate?.title.trim()} onClick={() => void action(async () => { await publish(); const work = pendingLeave; setPendingLeave(null); work() })}>{busy ? '正在保存…' : '保存并离开'}</button>
          </div>
        </Modal>
      )}
    </section>
  )
}
