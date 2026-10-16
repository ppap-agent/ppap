import {isPreview} from './access'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch reference: projects/13261964020862199279/screens/6bf39ffd79574ebd9e29fde1db6eb83c.
// Prototype only: batch snapshots and explicit synthetic file facts, never a real-file parser.
import {useEffect, useState} from 'react'
import {ArrowLeft, ArrowRight, RefreshCw} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {isIntactDemoFile, type Submission} from './amendment'
import {decisionReviewStatus, decisionSummary, type DecisionEvidence, type DecisionInput} from './decision'
import {isKnownDemoClause, type RequirementBasis} from './requirements-state'
import {BasisSummary} from './requirements'
import {isObject, readDraft, useSavedDraft} from './saved-draft'
import {demoDocument, validDemoDocument, type DemoDocument, type DemoFlavor} from './source-documents'
import {validDecisionInput} from './followup'
import {SourceReaderButton} from './source-reader'
import {CheckPreparation} from './check-preparation'
import {applyCheckCoverage, checkPlan, checkSummary, initialCheck, recoverCheck, validCheck, type CheckPreparationState} from './check-preparation-state'
import {usageHref} from './usage-state'
import type {LocalFileRef} from './local-files'

type Original = {receiptId: string; files: DecisionInput['files']; basis: RequirementBasis | null; clarification: string | null}
type FileFact = {name: string; revision: string; sourceVersion: number; receipt: string; known: boolean; process?: string; readable?: boolean; measurements?: number[]; localFile?: LocalFileRef}
type FileKind = '控制计划' | '过程流程图' | '尺寸报告'
type Check = {state: 'prepared' | 'running' | 'failed' | 'done'; input: DecisionInput | null; attempt: number; check?: CheckPreparationState; source?: {original: Original; batches: Submission[]}; fail?: boolean; receiptSource?: {decisionId: string; input: DecisionInput; documents: DemoDocument[]}}
export type Checks = {rounds: Record<string, Check>}
const kinds: FileKind[] = ['控制计划', '过程流程图', '尺寸报告']
const titles = {conflict: '工序描述冲突', scan: '工序号可读性', report: 'Rev B 尺寸'}
const layouts = ['条款优先', '证据对照', '批次历史']

function activeFiles(original: Original, batches: Submission[]) {
  const files = Object.fromEntries(kinds.map((kind, index) => {
    const name = ['控制计划_B.xlsx', '过程流程图_B.pdf', '尺寸报告_A.xlsx'][index]
    const source = original.files.find(file => file.name === name)
    return [kind, {...(source ?? {name, revision: index === 2 ? 'A' : 'B', sourceVersion: 1}), receipt: original.receiptId, known: Boolean(source && !source.localFile), process: index === 0 ? '表面处理' : index === 1 ? '热处理' : undefined, readable: index === 1 ? false : undefined}]
  })) as Record<FileKind, FileFact>
  for (const batch of batches) {
    for (const kind of kinds) {
      const candidates = batch.files.filter(file => file.kind === kind)
      if (!candidates.length) continue
      const file = candidates[0]
      const known = candidates.length === 1 && isIntactDemoFile(file)
      files[kind] = {name: candidates.map(item => item.name).join(' / '), revision: file.revision, receipt: batch.id,
        sourceVersion: files[kind].sourceVersion + 1, known, localFile: candidates.length === 1 ? file.localFile : undefined,
        process: known && kind !== '尺寸报告' ? file.demoKey === 'flow' ? '表面处理' : '热处理' : undefined,
        readable: kind === '过程流程图' ? known : undefined,
        measurements: known && kind === '尺寸报告' ? [10.02, file.demoKey === 'outlier' ? 10.14 : 10.04, 9.98] : undefined,
      }
    }
  }
  return files
}

export function reassessmentDocuments(original: Original, batches: Submission[], initial: DemoDocument[]) {
  const result = [...initial]
  batches.forEach((batch, index) => {
    const active = activeFiles(original, batches.slice(0, index + 1))
    for (const file of batch.files) {
      const fact = active[file.kind as FileKind]
      if (!fact?.known || !isIntactDemoFile(file)) continue
      const flavor: DemoFlavor = file.kind === '控制计划' ? 'control-clear' : file.kind === '过程流程图' ? file.demoKey === 'flow' ? 'flow-changed' : 'flow-clear' : file.demoKey === 'outlier' ? 'dimension-outlier' : 'dimension-clear'
      result.push(demoDocument({name: fact.name, revision: fact.revision, sourceVersion: fact.sourceVersion}, flavor, batch.id))
    }
  })
  return result
}

export function reassessInput(original: Original, batches: Submission[]): DecisionInput {
  const files = activeFiles(original, batches)
  const flow = files['过程流程图'], control = files['控制计划'], report = files['尺寸报告']
  const source = (file: FileFact, location: string, excerpt: string) => ({file: file.name, location: `${location} · 原件 v${file.sourceVersion} · ${file.receipt}`, excerpt})
  const processText = (file: FileFact) => file.known ? `16 机加工\n17 ${file.process}\n${file.readable === false ? '另有一处工序号无法辨认' : '18 表面检查'}` : '内容未解析或存在多个候选版本，不能沿用上轮结果。'
  const mismatch = flow.known && control.known && flow.process !== control.process
  const comparable = flow.known && control.known && flow.readable
  const facts: Record<string, Pick<DecisionEvidence, 'result' | 'explanation' | 'sources'>> = {
    R1: {result: mismatch ? '不满足' : comparable ? '满足' : '无法判断',
      explanation: mismatch ? `工序 17：流程图为“${flow.process}”，控制计划为“${control.process}”。` : comparable ? '工序 16、17、18 的名称一致。只核对文件一致性，不证明实际工艺执行。' : '工序信息尚不完整或无法读取，不能确认全部对应名称一致。',
      sources: [source(flow, '第 3 页 · 工序列', processText(flow)), source(control, '控制计划 · A16:B18', processText(control))]},
    R2: {result: flow.known && flow.readable ? '满足' : '无法判断', explanation: flow.known && flow.readable ? '本样例工序号 16、17、18 清晰可辨。' : '当前流程图仍有无法辨认或未解析内容。', sources: [source(flow, '第 3 页 · 工序号列', flow.known && flow.readable ? '16 → 17 → 18' : processText(flow))]},
    R3: {result: !report.known ? '无法判断' : report.revision !== 'B' || !report.measurements ? '未覆盖' : report.measurements.every(value => value >= 9.9 && value <= 10.1) ? '满足' : '不满足',
      explanation: !report.known ? '此报告内容没有解析结果，不能根据文件名或登记修订判断尺寸。' : report.revision !== 'B' || !report.measurements ? '现有报告不覆盖本轮 Rev B 尺寸要求。' : report.measurements.every(value => value >= 9.9 && value <= 10.1) ? 'Rev B 的 3 件孔径均在 9.90–10.10 mm 内。' : '第 2 件孔径 10.14 mm，超过 10.10 mm 上限。',
      sources: [source(report, '尺寸 · B2（修订）、D8:F8（实测）', report.known ? `修订 ${report.revision}\n${report.measurements ? `φ10.00±0.10 mm\n实测 ${report.measurements.map(value => value.toFixed(2)).join('、')} mm` : '无可用于 Rev B 的实测值'}` : '内容未解析。')]},
  }
  return {receiptId: batches.at(-1)?.id ?? original.receiptId, scope: original.basis?.confirmed?.scope ?? '支架 P-204 · 图纸 Rev B（范围尚未确认）', basis: original.basis,
    files: kinds.map(kind => ({name: files[kind].name, revision: files[kind].revision, sourceVersion: files[kind].sourceVersion, localFile: files[kind].localFile})),
    clarification: [original.clarification, ...batches.flatMap(batch => Object.entries(batch.replies).map(([key, reply]) => `${batch.id} · ${batch.source} · ${titles[key as keyof typeof titles]}\n供应商原话：${reply?.note}`))].filter(Boolean).join('\n\n') || null,
    evidence: (original.basis?.clauses ?? []).map(clause => ({id: clause.id, displayId: clause.id, title: clause.title, requirement: clause.text,
      ...(clause.applicability === 'exclude' ? {result: '不适用' as const, explanation: clause.reason, sources: []} : isKnownDemoClause(clause) ? facts[clause.id] : {result: '未覆盖' as const, explanation: '没有此条款对应的示例执行结果。', sources: []})})),
    receipts: [{id: original.receiptId, source: '初次收件', files: original.files, notes: original.clarification ?? ''}, ...batches.map(batch => ({id: batch.id, source: batch.source, files: batch.files.map(file => ({name: file.name, revision: file.revision, localFile: file.localFile, sourceVersion: activeFiles(original, batches.slice(0, batches.indexOf(batch) + 1))[file.kind as FileKind]?.sourceVersion ?? 1})), notes: Object.entries(batch.replies).map(([key, reply]) => `${titles[key as keyof typeof titles]}：${reply?.note}`).join('\n')}))],
  }
}
export function validChecks(value: unknown): value is Checks {
  if (isObject(value) && isObject(value.rounds) && Object.values(value.rounds).some(round => isObject(round) && round.receiptSource !== undefined && (!isObject(round.receiptSource) || typeof round.receiptSource.decisionId !== 'string' || !validDecisionInput(round.receiptSource.input) || !Array.isArray(round.receiptSource.documents) || !round.receiptSource.documents.every(validDemoDocument)))) return false
  return isObject(value) && isObject(value.rounds) && Object.values(value.rounds).every(check => isObject(check) && ['prepared', 'running', 'failed', 'done'].includes(String(check.state)) && typeof check.attempt === 'number' && (check.check === undefined || validCheck(check.check)) && (check.source === undefined || isObject(check.source) && isObject(check.source.original) && Array.isArray(check.source.batches)) && (check.input === null || isObject(check.input) && Array.isArray(check.input.evidence) && Array.isArray(check.input.files)))
}
export function readReassessment(id: string, batchId: string) {return readDraft(`reassessment:${id}`, validChecks)?.value.rounds[batchId]}

export function ReassessmentWorkspace({id, original, batches, onReceive, onDecision, readonly = false, documents = []}: {
  id: string; original: Original; batches: Submission[]; readonly?: boolean; documents?: DemoDocument[]; onReceive: () => void; onDecision: (batchId: string, input: DecisionInput) => void
}) {
  const {field, saveError} = useSavedDraft<Checks>(`reassessment:${id}`, () => ({rounds: {}}), validChecks)
  const [rounds, setRounds] = field('rounds')
  const [selected, setSelected] = useState(new URLSearchParams(location.search).get('checkRound') ?? batches.at(-1)?.id ?? '')
  const [preparing, setPreparing] = useState(new URLSearchParams(location.search).has('checkRound'))
  const [fail, setFail] = useState(false)
  const [layout, setLayout] = useState(() => Math.max(0, ['requirements', 'comparison', 'history'].indexOf(new URLSearchParams(location.search).get('recheckLayout') ?? 'requirements')))
  const index = Math.max(0, batches.findIndex(batch => batch.id === selected))
  const batch = batches[index]
  const check = batch ? rounds[batch.id] : undefined
  const current = check?.state === 'prepared' ? null : check?.input ?? null
  const previous = index > 0 ? rounds[batches[index - 1].id]?.input : reassessInput(original, [])
  const decisionId = `${id}:${batch?.id}`
  const human = decisionReviewStatus(decisionId)
  const record = decisionSummary(decisionId, id)
  const relevant = current?.evidence.filter(row => row.result !== '不适用') ?? []
  const reviewed = relevant.length > 0 && relevant.every(row => human?.reviewed.includes(row.id))
  const reviewStatus = reviewed
    ? relevant.every(row => human?.resolved.includes(row.id)) ? '复核完成，可以作出决定' : '复核已记录，仍有未满足要求'
    : '检查完成，等待人工复核'
  const running = Object.values(rounds).some(round => round.state === 'running')
  const pending = Object.values(rounds).some(round => round.state === 'prepared' && round.check?.request && round.check.request.phase !== 'absent')
  useEffect(() => {
    setRounds(current => Object.fromEntries(Object.entries(current).map(([key, round]) => [key, round.check ? {...round, check: recoverCheck(round.check)} : round])))
    const url = new URL(location.href); url.searchParams.delete('checkRound'); history.replaceState(null, '', url)
  }, [])
  useEffect(() => {
    const accepted = Object.entries(rounds).find(([, round]) => round.state === 'prepared' && round.check?.request?.phase === 'accepted')
    if (accepted && !saveError) {
      const [key] = accepted
      setRounds(current => current[key]?.state === 'prepared' && current[key].check?.request?.phase === 'accepted' ? {...current, [key]: {...current[key], state: 'running', attempt: 1}} : current)
      return
    }
    const active = Object.entries(rounds).find(([, round]) => round.state === 'running')
    if (!active) return
    const [batchId, round] = active
    const batchIndex = batches.findIndex(item => item.id === batchId)
    if (batchIndex < 0) return
    const timer = window.setTimeout(() => {
      const input = reassessInput(round.source?.original ?? original, round.source?.batches ?? batches.slice(0, batchIndex + 1))
      input.evidence = applyCheckCoverage(input.evidence, round.check?.request)
      const failed = round.fail ?? fail
      if (failed) input.evidence = input.evidence.map(row => row.id === 'R1' && !['未覆盖', '不适用'].includes(row.result) ? {...row, result: '执行失败', explanation: '模拟跨文件比对中断，不能给出工序一致性结论。', sources: []} : row)
      setRounds(state => state[batchId]?.state === 'running' && state[batchId].attempt === round.attempt ? {...state, [batchId]: {...state[batchId], state: failed ? 'failed' : 'done', input}} : state)
    }, 700)
    return () => window.clearTimeout(timer)
  }, [rounds, batches, original, fail, saveError])
  function selectLayout(next: number) {
    setLayout(next); const url = new URL(location.href); url.searchParams.set('recheckLayout', ['requirements', 'comparison', 'history'][next]); history.replaceState(null, '', url)
  }
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {if (!isPreview() || !import.meta.env.DEV) return;if (!preparing && ['ArrowLeft', 'ArrowRight'].includes(event.key) && !(event.target as HTMLElement)?.closest('input,textarea,select,button,summary,[role="dialog"],[contenteditable]')) selectLayout((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)}
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  }, [layout, preparing])
  if (!batch) return <section className={"min-w-0 pb-18 text-sm leading-relaxed [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:mt-6 [&_h2]:mr-0 [&_h2]:mb-3 [&_h2]:ml-0 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:m-0 [&_h4]:text-sm [&_h4]:font-semibold [&_h4]:mt-0 [&_h4]:mr-0 [&_h4]:mb-2 [&_h4]:ml-0 [&_p]:my-2 [&_p]:mx-0 [&_p]:break-all [&_small]:block [&_small]:text-muted-foreground [&_small]:text-sm [&_small]:break-all [&_pre]:whitespace-pre-wrap [&_pre]:break-all [&_pre]:font-sans [&_pre]:text-sm [&_pre]:p-3 [&_pre]:bg-background [&_pre]:rounded-lg [&_pre]:mt-2 [&_pre]:mr-0 [&_pre]:mb-4 [&_pre]:ml-0"}><h1>尚未登记补件</h1><Button onClick={onReceive}>登记回件</Button></section>
  function prepare() {
    if (readonly || running || saveError || !original.basis?.confirmed) return
    if (check?.state === 'failed') {setRounds(current => current[batch.id]?.state === 'failed' ? {...current, [batch.id]: {...current[batch.id], state: 'running', attempt: current[batch.id].attempt + 1, fail}} : current); return}
    if (!check) setRounds(current => ({...current, [batch.id]: {state: 'prepared', input: null, attempt: 0, check: initialCheck(), source: structuredClone({original, batches: batches.slice(0, index + 1)}), fail}}))
    setPreparing(true)
  }
  if (preparing && check?.check && original.basis) {
    const source = check.source ?? {original, batches: batches.slice(0, index + 1)}
    const files = Object.entries(activeFiles(source.original, source.batches)).map(([kind, file]) => ({name: file.name, kind, contentRevision: file.revision, sourceVersion: file.sourceVersion, readability: file.readable === false ? 'partial' : 'readable', origin: file.receipt, known: file.known}))
    const plan = checkPlan(source.original.basis!, files, batch.id, check.check.mode, true)
    return <CheckPreparation caseId={id} state={check.check} plan={plan} saveError={saveError} recheck={{owner: {kind: 'reassessment', roundId: batch.id}, description: `第 ${index + 1} 批补件`, note: `${batch.source}\n${Object.values(batch.replies).map(reply => reply?.note).filter(Boolean).join('\n')}\n来源说明不等于已核实事实。`}} onHome={() => setPreparing(false)} onContinue={() => setPreparing(false)} onChange={next => setRounds(current => {
      const target = current[batch.id]; if (!target?.check) return current
      const value = typeof next === 'function' ? next(target.check) : next
      return value ? {...current, [batch.id]: {...target, check: value}} : current
    })}/>
  }
  const evidence = <section aria-label="本批条款变化" className={"min-w-0 [&_>_article]:py-4.5 [&_>_article]:px-0 [&_>_article]:border-b [&_header]:flex [&_header]:justify-between [&_header]:items-center [&_header]:gap-3 [&_header]:flex-wrap"}><h2>要求与变化</h2>{(current?.evidence ?? original.basis?.clauses.map(clause => ({id: clause.id, displayId: clause.id, title: clause.title, requirement: clause.text, result: '未覆盖', explanation: '', sources: []})) ?? []).map(row => {
    const before = previous?.evidence.find(item => item.id === row.id)
    const resolved = human?.resolved.includes(row.id)
    return <article key={row.id}><header><h3>{row.displayId || row.id} · {row.title}</h3><span className={["inline-flex items-center justify-center w-fit min-h-6.5 py-0.5 px-2 rounded-lg text-xs font-semibold whitespace-nowrap",({"bad":"bg-destructive/10 text-destructive","wait":"bg-muted text-foreground","missing":"bg-muted text-primary","good":"bg-muted text-foreground"} as Record<string,string>)[String(!current ? "wait" : row.result === "满足" ? "good" : row.result === "不满足" ? "bad" : "wait")] ?? "",""].filter(Boolean).join("")}>{!current || check?.state === 'running' ? '待重查' : row.result}</span></header><p>{row.requirement}</p><div className={"text-muted-foreground flex justify-between items-center gap-3 flex-wrap"}><span>此前检查：{before?.result ?? '未检查'}</span><span>本批人工复核：{resolved ? '确认满足' : human?.reviewed.includes(row.id) ? '已记录，尚未确认满足' : '未记录'}</span></div>
      <Collapsible defaultOpen={layout === 1}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">查看新旧依据 · {row.id}</Button></CollapsibleTrigger><CollapsibleContent><div className={"grid grid-cols-[repeat(2,minmax(0,1fr))] gap-6 py-3.5 px-0 [&_>_div+div]:border-l [&_>_div+div]:pl-6 [&_strong]:block [&_strong]:break-all [&_strong]:font-medium max-sm:grid-cols-1 max-sm:gap-3 max-sm:[&>div+div]:border-0 max-sm:[&>div+div]:pl-0"}><div><h4>此前依据</h4><p>{before?.explanation ?? '上一批尚未完成检查。'}</p>{before?.sources.map((source, i) => <div key={i}><strong>{source.file}</strong><small>{source.location}</small><pre>{source.excerpt}</pre></div>)}</div><div><h4>本批依据</h4><p>{current ? row.explanation : '已收到文件，但没有本批检查结果。'}</p>{current && row.sources.map((source, i) => <div key={i}><strong>{source.file}</strong><small>{source.location}</small><pre>{source.excerpt}</pre></div>)}</div></div><div className={"[&_blockquote]:border-l [&_blockquote]:pl-3.5 [&_blockquote]:my-2.5 [&_blockquote]:mx-0"}><h4>供应商说明</h4>{batches.slice(0, index + 1).flatMap(item => Object.entries(item.replies).filter(([key]) => ({conflict: 'R1', scan: 'R2', report: 'R3'}[key] === row.id)).map(([key, reply]) => <blockquote key={`${item.id}-${key}`}><p>{reply?.note}</p><small>{item.id} · {item.source} · 原话，不作为已核实事实</small></blockquote>))}</div></CollapsibleContent></Collapsible>
      <div className={"text-muted-foreground flex justify-between items-center gap-3 flex-wrap"}>{previous && <SourceReaderButton input={previous} documents={documents} clauseId={row.id} label={`此前原件 · ${row.id}`}/>}<SourceReaderButton input={current ?? {...reassessInput(original, batches.slice(0, index + 1)), evidence: []}} documents={documents} clauseId={row.id} label={`本批原件 · ${row.id}`}/></div>
    </article>
  })}</section>
  const historyPanel = <section className={"[&_article]:py-3 [&_article]:px-0 [&_article]:border-b [&_article]:min-w-0"} aria-label="回件记录"><h2>回件记录</h2><p>初次收件 {original.receiptId} · {original.files.length} 份</p>{batches.map((item, i) => <article key={item.id}><Button variant={item.id === batch.id ? 'secondary' : 'ghost'} aria-current={item.id === batch.id ? 'true' : undefined} onClick={() => setSelected(item.id)}>第 {i + 1} 批 · {item.files.length} 份文件 · {Object.keys(item.replies).length} 项答复</Button><p>{item.source}</p><Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">文件与供应商原话</Button></CollapsibleTrigger><CollapsibleContent>{item.files.map(file => <p key={file.id}>{file.name} · Rev {file.revision}</p>)}{Object.entries(item.replies).map(([key, reply]) => <p key={key}>{titles[key as keyof typeof titles]}：{reply?.note}</p>)}</CollapsibleContent></Collapsible><small>{rounds[item.id]?.state === 'done' ? '检查完成' : rounds[item.id]?.state === 'failed' ? '检查部分失败' : '尚未完成检查'} · {decisionSummary(`${id}:${item.id}`, id)?.state ?? '尚无决定'}</small></article>)}</section>
  return <div className={"min-w-0 pb-18 text-sm leading-relaxed [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:mt-6 [&_h2]:mr-0 [&_h2]:mb-3 [&_h2]:ml-0 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:m-0 [&_h4]:text-sm [&_h4]:font-semibold [&_h4]:mt-0 [&_h4]:mr-0 [&_h4]:mb-2 [&_h4]:ml-0 [&_p]:my-2 [&_p]:mx-0 [&_p]:break-all [&_small]:block [&_small]:text-muted-foreground [&_small]:text-sm [&_small]:break-all [&_pre]:whitespace-pre-wrap [&_pre]:break-all [&_pre]:font-sans [&_pre]:text-sm [&_pre]:p-3 [&_pre]:bg-background [&_pre]:rounded-lg [&_pre]:mt-2 [&_pre]:mr-0 [&_pre]:mb-4 [&_pre]:ml-0"}><header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl"}><div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>支架 P-204 · 图纸 Rev B</div><h1>补件复审</h1><p>第 {index + 1} 批 · {batch.files.length} 份文件 · {Object.keys(batch.replies).length} 项答复</p></div><Button variant="outline" disabled={running || pending || readonly} onClick={onReceive}>继续登记补件<ArrowRight/></Button></header>
    {index < batches.length - 1 && <div className={"flex items-center flex-wrap gap-3 py-3 px-0 text-foreground"} role="status"><span>正在查看第 {index + 1} 批历史；最新资料为第 {batches.length} 批。</span><Button variant="outline" onClick={() => setSelected(batches.at(-1)!.id)}>回到最新批次</Button></div>}
    {saveError && <p role="alert">{saveError}</p>}<BasisSummary basis={original.basis}/>{!original.basis?.confirmed && <p role="alert">请返回工作台确认审核依据，当前不能批准。</p>}
    <div className={"flex gap-5 justify-between items-center py-5.5 px-0 border-b [&_strong]:text-lg [&_p]:text-muted-foreground [&_>_div:last-child]:flex [&_>_div:last-child]:flex-wrap [&_>_div:last-child]:gap-2 [&_>_div:last-child]:shrink-0 max-xl:items-start max-xl:flex-col max-sm:[&>div:last-child]:w-full"}><div role="status"><strong>{check?.state === 'done' ? record?.recorded ? record.state : reviewStatus : check?.state === 'running' ? '正在重新检查本批资料…' : check?.state === 'failed' ? '部分检查失败，已完成结果保留' : checkSummary(check?.check)?.state ?? '已收到，尚未重新检查'}</strong><p>{record?.recorded ? '本批决定已固定；新增资料须形成新的检查与决定。' : `${human?.resolved.length ?? 0} / ${current?.evidence.filter(row => row.result !== '不适用').length ?? original.basis?.clauses.length ?? 0} 项已由人工确认满足；批准仍需单独决定。`}</p></div><div>{check?.state !== 'done' && <Button disabled={readonly || running || Boolean(saveError) || !original.basis?.confirmed} onClick={prepare}><RefreshCw/>{check?.state === 'failed' ? '重试本次检查' : checkSummary(check?.check)?.next ?? '准备复查'}</Button>}{check?.check && <Button variant="ghost" onClick={() => setPreparing(true)}>检查方案</Button>}{check?.state === 'done' && current && <Button onClick={() => onDecision(batch.id, current)}>{record?.recorded ? '查看本批决定与报告' : '复核并决定'}<ArrowRight/></Button>}</div></div>
    {check?.check?.request && <p className={"text-muted-foreground"}>{check.check.request.runId} · 第 {check.attempt || 1} 次检查尝试 <a href={usageHref({kind: 'reassessment', roundId: batch.id})}>查看本次用量</a></p>}
    <p className={"text-muted-foreground"}>按本批资料重查适用要求；没有新证据的要求保留原缺口。多份同类文件未明确当前版本时，保持无法判断。</p>
    {layout === 2 ? <div className={"grid grid-cols-[minmax(230px,.7fr)_minmax(0,1.5fr)] gap-7.5 items-start max-xl:grid-cols-1"}>{historyPanel}{evidence}</div> : <>{evidence}<Collapsible className={"my-5 mx-0"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">查看全部回件 · {batches.length} 批</Button></CollapsibleTrigger><CollapsibleContent>{historyPanel}</CollapsibleContent></Collapsible></>}
    {isPreview() && import.meta.env.DEV && check?.state !== 'done' && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">演示检查失败</Button></CollapsibleTrigger><CollapsibleContent><label className={"flex items-center gap-2 my-2.5 mx-0"}><Checkbox checked={fail} disabled={running} onCheckedChange={state => setFail(state === true)}/>模拟工序比对中断（关闭后可重试）</label></CollapsibleContent></Collapsible>}
    {isPreview() && import.meta.env.DEV && <div className={"fixed z-10 bottom-5 left-[50%] flex items-center gap-2.5 p-1.5 border rounded-lg bg-muted shadow-sm text-foreground text-xs whitespace-nowrap max-sm:bottom-3.5"} role="toolbar" aria-label="补件复审布局"><Button variant="ghost" aria-label="上一种复审布局" onClick={() => selectLayout((layout + 2) % 3)}><ArrowLeft size={16}/></Button><span>{layout + 1} / 3 · {layouts[layout]}</span><Button variant="ghost" aria-label="下一种复审布局" onClick={() => selectLayout((layout + 1) % 3)}><ArrowRight size={16}/></Button></div>}
  </div>
}
