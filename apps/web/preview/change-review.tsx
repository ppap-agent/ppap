// Stitch: projects/13261964020862199279/screens/4d76224ee4f244f5a9bd6b8ef0032880.
// On the same case, changeLayout=compare|tasks|history explores three structures.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, GitCompareArrows, Plus, UploadCloud} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Checkbox} from '../src/components/ui/checkbox'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../src/components/ui/dialog'
import {DecisionWorkspace, decisionContinuation, decisionSummary, unexecutedEvidence, type DecisionInput} from './decision'
import {BasisSummary, Requirements} from './requirements'
import {type RequirementCollection} from './requirement-library-state'
import {draftEvent, useSavedDraft} from './saved-draft'
import {changeCheckPlan, changeDecisionId, changeIssues, changeScopeKey, changeScopeLabel, clauseDifferences, executeChange, freshChange, inputIdentity, isChangeState, newChangeInput, type ChangeDraft, type ChangeRound, type ChangeScope, type ChangeState} from './change-review-state'
import {SourceReaderButton} from './source-reader'
import {mergeDocuments, sourceKey, type DemoDocument} from './source-documents'
import './change-review.css'
import {CheckPreparation} from './check-preparation'
import {checkSummary, initialCheck, recoverCheck} from './check-preparation-state'
import {usageHref} from './usage-state'
import {fileAccept, useLocalUploads} from './local-files'

const layouts = ['compare', 'tasks', 'history'] as const
const labels = ['新旧对照', '当前任务', '轮次历史']
export function ChangeWorkspace({id, original, sourceDecisionId, onSourceReview, scope, collections, onBack, documents: initialDocuments = []}: {documents?: DemoDocument[]; id: string; original: DecisionInput; sourceDecisionId?: string; onSourceReview?: () => void; scope: ChangeScope; collections: RequirementCollection[]; onBack: () => void}) {
  const {field, saveError} = useSavedDraft<ChangeState>(`changes:${id}`, () => ({draft: null, rounds: [], selected: '', editingBasis: false, decision: null}), isChangeState)
  const [draft, setDraft] = field('draft'), [rounds, setRounds] = field('rounds'), [selected, setSelected] = field('selected')
  const [editingBasis, setEditingBasis] = field('editingBasis'), [decision, setDecision] = field('decision')
  const [discard, setDiscard] = useState(false), [error, setError] = useState(''), [fail, setFail] = useState(false)
  const [preparing, setPreparing] = useState(new URLSearchParams(location.search).has('checkRound'))
  const [layout, setLayout] = useState(() => Math.max(0, layouts.indexOf(new URLSearchParams(location.search).get('changeLayout') as typeof layouts[number])))
  const picker = useRef<HTMLInputElement>(null)
  const [, refresh] = useState(0)
  useEffect(() => {
    const changed = () => refresh(value => value + 1)
    window.addEventListener(draftEvent, changed); window.addEventListener('storage', changed)
    return () => {window.removeEventListener(draftEvent, changed); window.removeEventListener('storage', changed)}
  }, [])
  const rootKey = inputIdentity(original), latest = rounds.at(-1)
  const latestSharesRoot = latest?.draft.rootKey === rootKey
  const continuation = latestSharesRoot ? decisionContinuation(changeDecisionId(id, latest.id), latest.input, id) : sourceDecisionId ? decisionContinuation(sourceDecisionId, original, id) : {input: original, pendingReply: false, pendingCheck: false, error: ''}
  const base = continuation.input
  const documents = mergeDocuments(initialDocuments, ...rounds.map(item => item.documents ?? []), ...rounds.map(item => decisionContinuation(changeDecisionId(id, item.id), item.input, id).documents), 'documents' in continuation ? continuation.documents : [])
  const baseScope = latestSharesRoot ? latest.draft.scope : scope
  const running = rounds.find(round => round.state === 'running')
  const pending = rounds.some(round => round.state === 'prepared') || continuation.pendingCheck
  const round = rounds.find(item => item.id === selected) ?? latest
  const roundContinuation = round ? decisionContinuation(changeDecisionId(id, round.id), round.input, id) : null
  const currentInput = roundContinuation?.input
  const resumeReply = () => {if (latestSharesRoot) {setSelected(latest.id); setDecision(latest.id)} else onSourceReview?.()}
  const preparedRound = rounds.find(item => item.state === 'prepared')
  const resumeLabel = preparedRound ? checkSummary(preparedRound.check)?.next ?? '继续准备复查' : continuation.pendingCheck ? '继续本批检查与复核' : '此前收件、决定与报告'
  function resumePrevious() {
    if (preparedRound) {setSelected(preparedRound.id); setPreparing(true)}
    else if (continuation.pendingCheck) resumeReply()
    else onBack()
  }
  const issues = draft ? changeIssues(draft, base, collections) : []
  if (draft && draft.rootKey !== rootKey) issues.push('此前收件已更新，请从最新资料重新评估。')
  const differences = draft ? clauseDifferences(draft.base.basis, draft.basis) : []
  const update = (next: ChangeDraft) => setDraft({...next, acknowledged: false})
  useEffect(() => {
    const decisionRound = new URLSearchParams(location.search).get('decisionRound')
    if (decisionRound && rounds.some(round => round.id === decisionRound)) {
      setSelected(decisionRound); setDecision(decisionRound); setPreparing(false)
      const url = new URL(location.href); url.searchParams.delete('decisionRound'); history.replaceState(null, '', url)
    }
    const target = new URLSearchParams(location.search).get('checkRound')
    if (target && rounds.some(round => round.id === target)) {
      setSelected(target); setDecision(null)
      const url = new URL(location.href); url.searchParams.delete('checkRound'); history.replaceState(null, '', url)
    }
    setRounds(current => current.map(round => round.check ? {...round, check: recoverCheck(round.check)} : round))
  }, [])
  useEffect(() => {
    const accepted = rounds.find(item => item.state === 'prepared' && item.check?.request?.phase === 'accepted')
    if (accepted && !saveError) {setRounds(current => current.map(item => item.id === accepted.id && item.state === 'prepared' && item.check?.request?.phase === 'accepted' ? {...item, state: 'running', attempt: 1} : item)); return}
    if (!running) return
    const timer = window.setTimeout(() => {
      const result = executeChange(running)
      setRounds(current => current.map(item => item.id === running.id && item.state === 'running' && item.attempt === running.attempt ? {...item, state: item.fail ? 'failed' : 'done', input: result} : item))
    }, 800)
    return () => window.clearTimeout(timer)
  }, [running, rounds, saveError])
  function changeLayout(next: number) {
    setLayout(next); const url = new URL(location.href); url.searchParams.set('changeLayout', layouts[next]); history.replaceState(null, '', url)
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (preparing || editingBasis || decision || !['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement)?.closest('input,textarea,select,button,[role="dialog"],[contenteditable]')) return
      changeLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)
    }
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key)
  }, [layout, preparing, editingBasis, decision])
  function begin() {if (draft || running || pending || saveError || continuation.pendingReply || continuation.error) return; setDraft(freshChange(base, baseScope, rootKey)); setSelected(''); setDecision(null)}
  function start(manual = false) {
    if (!draft || issues.length || !draft.acknowledged || running || continuation.pendingCheck || saveError || upload.busy || continuation.pendingReply || continuation.error) return
    const snapshot = structuredClone(draft)
    const input = newChangeInput(snapshot)
    // Keep only known, unchanged source versions; uploaded candidates cannot borrow a fixture by name.
    const sourceDocuments = documents.filter(doc => input.files.some(file => sourceKey(file) === sourceKey(doc)) && !snapshot.files.some(file => file.name === doc.name))
    if (manual && input.basis) input.evidence = unexecutedEvidence(input.basis)
    const next: ChangeRound = {id: snapshot.id, draft: snapshot, input, documents: structuredClone(sourceDocuments), at: new Date().toISOString(), state: manual ? 'manual' : 'prepared', attempt: 0, fail: manual ? false : fail, ...(manual ? {} : {check: initialCheck()})}
    setRounds(current => current.some(item => item.id === next.id) ? current : [...current, next])
    setDraft(null); setSelected(next.id); setEditingBasis(false); setPreparing(!manual); setDecision(manual ? next.id : null)
  }
  function editPreparation() {
    if (!round || round !== latest || round.state !== 'prepared' || round.check?.request || draft || running || saveError) return
    setDraft(structuredClone(round.draft)); setRounds(items => items.filter(item => item.id !== round.id))
    setSelected(''); setPreparing(false)
    const url = new URL(location.href); url.searchParams.delete('checkRound'); history.replaceState(null, '', url)
  }
  const upload = useLocalUploads(files => {
    if (!draft) return
    setError(''); update({...draft, files: [...draft.files, ...files.map(({file, ref}) => ({id: ref.id, localFile: ref, name: file.name, kind: '', revision: '', replaces: '', checked: false, note: ''}))]})
  }, draft?.id ?? '')
  if (preparing && round?.check && round.input.basis) return <CheckPreparation caseId={id} state={round.check} plan={changeCheckPlan(round)} saveError={saveError} recheck={{owner: {kind: 'changes', roundId: round.id}, description: `变更 ${rounds.indexOf(round) + 1} · 图纸 Rev ${round.draft.scope.revision}`, note: [round.draft.reason, ...Object.entries(round.draft.impacts).map(([id, note]) => `${id}：${note}`)].join('\n')}} onHome={() => setPreparing(false)} onContinue={() => setPreparing(false)} onChange={next => setRounds(current => current.map(item => {
    if (item.id !== round.id || !item.check) return item
    const value = typeof next === 'function' ? next(item.check) : next
    return value ? {...item, check: value} : item
  }))}/>
  if (decision) {
    const target = rounds.find(item => item.id === decision)
    if (target && ['done', 'manual'].includes(target.state)) return <DecisionWorkspace allowFollowup caseId={id} documents={documents} key={target.id} id={changeDecisionId(id, target.id)} input={target.input} backLabel="返回变化与重审" pendingChange={Boolean(draft || target.draft.rootKey !== rootKey)} latestReceiptId={latest?.input.receiptId} onBack={() => setDecision(null)}/>
  }
  if (editingBasis && draft) return <Requirements basis={draft.basis} scopeKey={changeScopeKey(draft.scope)} scope={draft.scope} collections={collections} context={changeScopeLabel(draft.scope)} onChange={basis => update({...draft, basis, impacts: {}})} onConfirm={basis => {update({...draft, basis}); setEditingBasis(false)}} onBack={() => setEditingBasis(false)} backLabel="返回变化评估"/>
  const historyPanel = <section className="gf-change-history" aria-label="变更轮次"><h2>变更轮次</h2><Button variant={pending ? 'outline' : 'ghost'} onClick={resumePrevious}>{resumeLabel}<ArrowRight/></Button>{rounds.map((item, index) => <div key={item.id}><Button variant={round?.id === item.id && !draft ? 'secondary' : 'ghost'} onClick={() => {setSelected(item.id); setDecision(null)}}>变更 {index + 1} · {item.draft.scope.revision} · {item.input.basis?.source.version}</Button><small>{item.state === 'prepared' ? checkSummary(item.check)?.state ?? '待开始复查' : item.state === 'running' ? '检查中' : item.state === 'failed' ? '检查部分失败' : decisionSummary(`${id}:change:${item.id}`, id)?.state ?? '待人工复核'}</small><p>{item.draft.reason}</p></div>)}</section>
  const intake = draft && <section className="gf-change-section" aria-label="新输入"><header><h2>本次输入</h2><Button variant="outline" onClick={() => picker.current?.click()}><UploadCloud/>上传新资料</Button></header><input type="file" multiple ref={picker} className="sr-only" aria-label="上传变更资料" accept={fileAccept} disabled={upload.busy} onChange={event => {void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    <div className="gf-change-basis"><div><strong>{draft.basis?.source.title ?? '选择要求'} · {draft.basis?.source.version}</strong><span>{draft.basis?.confirmed ? '本轮已确认适用性' : '本轮适用性待确认'}</span></div><Button variant="outline" onClick={() => setEditingBasis(true)}>选择并核对要求</Button></div>
    <label className="gf-change-revision">本次图纸修订<Input value={draft.scope.revision} disabled={!draft.files.length} onChange={event => update({...draft, scope: {...draft.scope, revision: event.target.value}, basis: draft.basis && {...draft.basis, confirmed: null}})}/></label>
    <details className="gf-change-base"><summary>本轮起点 · {draft.base.files.length} 份已登记资料</summary>{draft.base.files.map(file => <p key={sourceKey(file)}>{file.name} · Rev {file.revision} · v{file.sourceVersion}</p>)}<SourceReaderButton input={draft.base} documents={documents} label="阅读已登记资料"/>{(draft.base.receipts?.at(-1)?.notes || draft.base.clarification) && <p className="gf-change-reply">{draft.base.receipts?.at(-1)?.notes || draft.base.clarification}</p>}</details>
    {!draft.files.length && <p className="gf-change-muted">没有新资料，保留此前文件版本。改变图纸修订时请先上传图纸。</p>}
    {draft.files.map((file, index) => <article className="gf-change-file" key={file.id}><header><strong>{file.name}</strong><SourceReaderButton input={{...draft.base, receiptId: '变化草稿', files: draft.files.map(item => ({name: item.name, revision: item.revision || '待核对', sourceVersion: 0, pending: true, localFile: item.localFile})), evidence: []}} file={{name: file.name, revision: file.revision || '待核对', sourceVersion: 0, pending: true, localFile: file.localFile}} label="查看资料"/><Button variant="ghost" onClick={() => update({...draft, files: draft.files.filter(item => item.id !== file.id)})}>移除</Button></header><div className="gf-change-fields">
      <label>文件类型<select aria-label={`文件 ${index + 1} 类型`} value={file.kind} onChange={event => update({...draft, files: draft.files.map(item => item.id === file.id ? {...item, kind: event.target.value, checked: false} : item)})}><option value="">待核对</option>{['图纸', '控制计划', '过程流程图', '尺寸报告', '其他资料'].map(kind => <option key={kind}>{kind}</option>)}</select></label>
      <label>人工登记修订<Input aria-label={`文件 ${index + 1} 修订`} value={file.revision} onChange={event => update({...draft, files: draft.files.map(item => item.id === file.id ? {...item, revision: event.target.value, checked: false} : item)})}/></label>
      <label>本轮用途<select aria-label={`文件 ${index + 1} 用途`} value={file.replaces} onChange={event => update({...draft, files: draft.files.map(item => item.id === file.id ? {...item, replaces: event.target.value, checked: false} : item)})}><option value="">待核对</option><option value="additional">补充依据，不替代旧文件</option>{draft.base.files.map((old, i) => <option key={i} value={String(i)}>替代：{old.name} · v{old.sourceVersion}</option>)}</select></label>
      <label>归属与变化说明<Textarea aria-label={`文件 ${index + 1} 说明`} value={file.note} onChange={event => update({...draft, files: draft.files.map(item => item.id === file.id ? {...item, note: event.target.value, checked: false} : item)})}/></label>
    </div><label className="gf-change-check"><Checkbox checked={file.checked} aria-label={`文件 ${index + 1} 已核对`} onCheckedChange={value => update({...draft, files: draft.files.map(item => item.id === file.id ? {...item, checked: value === true} : item)})}/>已核对原件归属、修订和上述关联；这不代表内容通过检查</label></article>)}
    {upload.busy && <p role="status">正在保存本机原件…</p>}{upload.error && <p role="alert">{upload.error}</p>}{error && <p role="alert">{error}</p>}
  </section>
  const comparison = draft && <section className="gf-change-section" aria-label="条款变化与影响"><h2>条款变化与影响</h2><p className="gf-change-muted">这里比较文本与出处，不自动判断含义等价。所有适用条款会重新检查。</p>{differences.map(row => <article className="gf-change-clause" key={row.id}><header><h3>{row.id} · {row.next?.title ?? row.old?.title}</h3><span>{row.kind}</span></header><details open={row.kind !== '内容未变' || layout === 0}><summary>新旧要求与出处</summary><div className="gf-change-compare">{[row.old, row.next].map((clause, index) => <div key={index}><strong>{index === 0 ? '此前依据' : '本轮候选'}</strong><p>{clause?.text ?? (index === 0 ? '此前没有此条款。' : '本轮移除此条款。')}</p>{clause && <><small>{clause.source} · {clause.location}</small><small>适用性：{clause.applicability === 'apply' ? '适用' : clause.applicability === 'exclude' ? '不适用' : '待确认'}{clause.reason && ` · ${clause.reason}`}</small></>}</div>)}</div></details>{row.needsImpact && <label>影响与理由<Textarea aria-label={`${row.id} 变化影响`} value={draft.impacts[row.id] ?? ''} onChange={event => update({...draft, impacts: {...draft.impacts, [row.id]: event.target.value}})} placeholder="说明这项变化影响哪些判断，以及重查时要核对什么"/></label>}</article>)}</section>
  const cannotStart = Boolean(issues.length || !draft?.acknowledged || running || continuation.pendingCheck || saveError || upload.busy || continuation.pendingReply || continuation.error)
  const actions = draft && <section className="gf-change-section gf-change-actions">
    <h2>确认本次变化</h2><label>变化来源与重审原因<Textarea aria-label="变化来源与重审原因" value={draft.reason} onChange={event => update({...draft, reason: event.target.value})}/></label>
    {issues.length > 0 && <details open><summary>还有 {issues.length} 项待处理</summary><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></details>}
    <label className="gf-change-check"><Checkbox checked={draft.acknowledged} onCheckedChange={value => setDraft({...draft, acknowledged: value === true})}/>按本次要求和文件复核；历史判断不带入本轮</label>
    <div><Button variant="ghost" onClick={() => setDiscard(true)}>放弃草稿</Button><Button variant="outline" onClick={onBack}>留待处理</Button><Button variant="outline" disabled={cannotStart} onClick={() => start(true)}>确认变化，直接人工复核</Button><Button className="gf-primary" disabled={cannotStart} onClick={() => start()}>确认变化，准备检查<ArrowRight/></Button></div>
  </section>
  const manualUncovered = round?.state === 'manual' && Boolean(currentInput?.evidence.length) && currentInput!.evidence.every(row => row.result === '未覆盖')
  const reviewAction = round && ['done', 'manual'].includes(round.state) && <Button className="gf-primary" disabled={Boolean(roundContinuation?.error)} onClick={() => setDecision(round.id)}>{round !== latest || draft ? '查看历史复核' : roundContinuation?.pendingReply ? '继续核对回件' : '继续复核'}<ArrowRight/></Button>
  const current = round && currentInput && <section className="gf-change-section">
    <header><div><h2>变更 {rounds.indexOf(round) + 1} · 图纸 {round.draft.scope.revision}{round !== latest && ' · 历史轮次'}</h2><p>{round.draft.reason}</p></div><div className="gf-change-next"><span role="status">{round.state === 'prepared' ? checkSummary(round.check)?.state ?? '待开始复查' : round.state === 'running' ? '检查中' : round.state === 'failed' ? '部分检查失败' : decisionSummary(changeDecisionId(id, round.id), id)?.state ?? (round.state === 'manual' ? '待人工复核 · 未执行机器检查' : '检查完成，待复核')}</span>{reviewAction}</div></header>
    {roundContinuation?.error && <p role="alert">{roundContinuation.error}</p>}
    <BasisSummary basis={currentInput.basis}/>
    <div className="gf-change-evidence">
      <section aria-label="本轮当前资料"><header><h3>已登记资料 · {currentInput.files.length} 份</h3><SourceReaderButton input={currentInput} documents={documents} label="阅读本轮资料"/></header>{currentInput.files.map(file => <p key={sourceKey(file)}>{file.name}<small>Rev {file.revision} · 文件 v{file.sourceVersion}</small></p>)}</section>
      <section aria-label="最新登记说明"><h3>最新登记说明</h3><p className="gf-change-reply">{currentInput.receipts?.at(-1)?.notes || currentInput.clarification || '无补充说明'}</p><details><summary>收件与影响记录</summary>{currentInput.receipts?.map(receipt => <div key={receipt.id}><strong>{receipt.source}</strong><p className="gf-change-reply">{receipt.notes || '无补充说明'}</p>{receipt.files.map(file => <small key={sourceKey(file)}>{file.name} · v{file.sourceVersion}</small>)}</div>)}{Object.entries(round.draft.impacts).map(([key, note]) => <p key={key}>{key}：{note}</p>)}</details></section>
    </div>
    {manualUncovered && <p className="gf-change-muted">以下条款尚无机器检查结果，需核对原件并记录人工判断。</p>}
    {currentInput.evidence.map(row => <div className="gf-change-finding" key={row.id}><strong>{row.id} · {row.title}</strong><span>机器：{row.result}</span>{!manualUncovered && <p>{row.explanation}</p>}</div>)}
    {currentInput.receiptId !== round.input.receiptId && <details><summary>补件前的机器状态</summary>{round.input.evidence.map(row => <p key={row.id}>{row.id} · {row.result} · {row.explanation}</p>)}</details>}
    {round.check && <div className="gf-change-buttons"><Button variant={round.state === 'prepared' ? 'default' : 'outline'} onClick={() => setPreparing(true)}>{round.state === 'prepared' ? checkSummary(round.check)?.next ?? '准备复查' : '检查方案'}</Button>{round.check.request && <Button variant="link" asChild><a href={usageHref({kind: 'changes', roundId: round.id})}>查看本次用量</a></Button>}{round.state === 'failed' && round === latest && !draft && <Button disabled={Boolean(running || saveError)} onClick={() => setRounds(items => items.map(item => item.id === round.id && item.state === 'failed' ? {...item, state: 'running', attempt: item.attempt + 1, fail} : item))}>重试本轮检查</Button>}</div>}
  </section>
  return <section className="gf-change"><Button variant="ghost" onClick={onBack}><ArrowLeft/>返回此前事项</Button><header className="gf-page-heading"><div><h1>变化评估与重审</h1><p>{changeScopeLabel(draft?.scope ?? round?.draft.scope ?? scope)}</p></div>{!draft && (round === latest && round?.state === 'prepared' && !round.check?.request ? <Button variant="outline" disabled={Boolean(saveError)} onClick={editPreparation}>修改本次变化</Button> : <Button variant="outline" disabled={Boolean(running || pending || saveError || continuation.pendingReply || continuation.error)} onClick={begin}><Plus/>登记新的变化</Button>)}</header><p className="gf-change-history-note">历史决定只适用于其固定依据。本次新要求或文件尚未因此获得批准。</p>{saveError && <p role="alert">{saveError}</p>}{continuation.error && <p role="alert">{continuation.error}</p>}
    {continuation.pendingReply && <div className="gf-change-banner" role="status"><span>有回件尚未核对。登记或放弃本批回件后，才能开始新的变化。</span><Button variant="outline" onClick={resumeReply}>继续核对回件<ArrowRight/></Button></div>}
    {continuation.pendingCheck && <div className="gf-change-banner" role="status"><span>本批检查或结果复核尚未完成。先处理本次结果，再登记新的变化。</span><Button variant="outline" onClick={resumeReply}>返回本批检查<ArrowRight/></Button></div>}
    {!draft && !round && <div className="gf-change-empty"><GitCompareArrows size={28}/><h2>收到新的要求或资料了吗？</h2><p>保留此前记录，核对变化后开始新一轮检查。</p><Button variant="outline" disabled={Boolean(saveError || pending || continuation.pendingReply || continuation.error)} onClick={begin}>开始评估变化</Button></div>}
    <div className={`gf-change-layout gf-change-layout--${layouts[layout]}`}>
      {layout === 2 && historyPanel}<div>{draft ? <>{layout === 1 ? <>{intake}{actions}{comparison}</> : <>{intake}{comparison}{actions}</>}{round && <details><summary>查看选中的历史变更</summary>{current}</details>}</> : current}</div>
      {layout !== 2 && <details className="gf-change-history-details"><summary>变更历史 · {rounds.length} 轮</summary>{historyPanel}</details>}
    </div>
    {import.meta.env.DEV && <details className="gf-change-simulation"><summary>原型检查设置</summary><label className="gf-change-check"><Checkbox checked={fail} disabled={Boolean(running)} onCheckedChange={state => setFail(state === true)}/>模拟一项检查失败（关闭后可重试）</label><p>上传原件保存在本机；没有对应示例执行结果的条款保持未覆盖，可另行人工核对。</p></details>}
    {import.meta.env.DEV && <div className="gf-switcher" role="toolbar" aria-label="变化页布局"><button aria-label="上一种变化布局" onClick={() => changeLayout((layout + 2) % 3)}><ArrowLeft size={16}/></button><span>{labels[layout]}</span><button aria-label="下一种变化布局" onClick={() => changeLayout((layout + 1) % 3)}><ArrowRight size={16}/></button></div>}
    <Dialog open={discard} onOpenChange={setDiscard}><DialogContent><DialogHeader><DialogTitle>放弃本次变更草稿？</DialogTitle><DialogDescription>只移除尚未开始检查的候选与影响说明。此前文件、检查、决定和报告保持不变。</DialogDescription></DialogHeader><div className="gf-change-buttons"><Button variant="outline" onClick={() => setDiscard(false)}>保留草稿</Button><Button onClick={() => {setDraft(null); setEditingBasis(false); setDiscard(false)}}>确认放弃草稿</Button></div></DialogContent></Dialog>
  </section>
}
