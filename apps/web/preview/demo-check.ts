// Executes only explicit synthetic documents, never uploads identified by name.
import type {DecisionEvidence, DecisionInput} from './decision'
import {isKnownDemoClause} from './requirements-state'
import {checkPlan, type CheckMode} from './check-preparation-state'
import {documentRows, sourceKey, type DemoDocument} from './source-documents'

export function demoCheckPlan(input: DecisionInput, documents: DemoDocument[], mode: CheckMode) {
  const files = input.files.map(file => {
    const matches = documents.filter(doc => !file.localFile && sourceKey(doc) === sourceKey(file))
    const doc = matches.length === 1 ? matches[0] : undefined
    return {name: file.name, kind: doc ? {flow: '过程流程图', control: '控制计划', dimension: '尺寸报告'}[doc.kind] : '待确认', contentRevision: file.revision, sourceVersion: file.sourceVersion, readability: doc?.readable === false ? 'partial' : 'readable', origin: doc?.origin ?? '上传原件', known: Boolean(doc)}
  })
  return input.basis ? checkPlan(input.basis, files, input.receiptId, mode, files.every(file => file.known)) : null
}

export function demoEvidence(input: DecisionInput, documents: DemoDocument[]): DecisionEvidence[] {
  let revision = ''
  try {revision = JSON.parse(input.basis?.confirmed?.scope ?? '{}').revision} catch { /* Unconfirmed scope has no synthetic execution. */ }
  const current = input.files.map(file => {
    const matches = documents.filter(doc => !file.localFile && sourceKey(doc) === sourceKey(file))
    return matches.length === 1 ? matches[0] : undefined
  })
  return (input.basis?.clauses ?? []).map(clause => {
    const row = {id: clause.id, title: clause.title, requirement: clause.text}
    const uncovered = (explanation: string): DecisionEvidence => ({...row, result: '未覆盖', explanation, sources: []})
    if (clause.applicability === 'exclude') return {...row, result: '不适用', explanation: clause.reason, sources: []}
    if (revision !== 'B' || !isKnownDemoClause(clause)) return uncovered('当前范围或条款没有对应的示例执行能力。')
    if (current.some(doc => !doc)) return uncovered('当前有尚未解析的原件，不能只比较旧示例就确认本条满足。可结合原件独立人工审核。')
    const kinds = clause.id === 'R1' ? ['flow', 'control'] : clause.id === 'R2' ? ['flow'] : ['dimension']
    const docs = kinds.map(kind => {
      const matches = current.filter(doc => doc?.kind === kind)
      return matches.length === 1 ? matches[0] : undefined
    })
    if (docs.some(doc => !doc)) return uncovered('所需原件缺失或存在多个当前候选；请先核对资料。')
    const [first, second] = docs as DemoDocument[]
    const sources = (docs as DemoDocument[]).map(doc => ({file: doc.name, location: `${doc.kind === 'flow' ? '第 3 页 · 工序列' : doc.kind === 'control' ? '控制计划 · A16:B18' : '尺寸 · B2、D8:F8'} · 原件 v${doc.sourceVersion} · ${doc.origin}`, excerpt: documentRows(doc).map(line => line.cells.join(' · ')).join('\n')}))
    if (docs.some(doc => doc!.revision !== 'B')) return {...row, result: '未覆盖', explanation: '所需原件不对应本轮 Rev B。', sources}
    if (clause.id === 'R1') return {...row, result: first.process !== second.process ? '不满足' : first.readable && second.readable ? '满足' : '无法判断', explanation: first.process !== second.process ? `工序 17：流程图为“${first.process}”，控制计划为“${second.process}”。` : first.readable && second.readable ? '示例工序 16、17、18 的名称一致；不证明现场工艺执行。' : '可读部分名称一致，但仍有不可读工序，不能确认全部一致。', sources}
    if (clause.id === 'R2') return {...row, result: first.readable ? '满足' : '无法判断', explanation: first.readable ? '示例工序号 16、17、18 清晰可辨。' : '流程图仍有无法辨认的工序号。', sources}
    const values = first.measurements
    return {...row, result: values?.length !== 3 ? '未覆盖' : values.every(value => value >= 9.9 && value <= 10.1) ? '满足' : '不满足', explanation: values?.length !== 3 ? '尚无三件样本的可核对实测值。' : `三件实测为 ${values.join('、')} mm，与 9.90–10.10 mm 逐件比较。`, sources}
  })
}
