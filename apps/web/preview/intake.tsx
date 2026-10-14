// Single-role prototype. Records and uploaded originals stay in this browser.
import {useEffect, useMemo, useState} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {
  ArrowLeft, ArrowRight, Check, ChevronRight, ClipboardCheck, FileCheck2,
  FileQuestion, FileText, Files, RotateCcw, ShieldAlert, UploadCloud,
} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Checkbox} from '../src/components/ui/checkbox'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import '../src/index.css'
import './concept.css'
import './intake.css'
import {ProgressPrototype} from './progress'
import {ConceptApp, isReviewDraft, savedReviewSummary, type IntakeHandoff} from './concept'
import {isObject, listDrafts, readDraft, useSavedDraft} from './saved-draft'
import {PrototypeAccess, PrototypeAccount} from './access'
import {TaskWorkbench, type WorkItem} from './task-workbench'
import {BasisSummary, Requirements} from './requirements'
import {basisConfirmed, basisMatchesFixedReview, isRequirementBasis, type RequirementBasis} from './requirements-state'
import {completeSampleEvidence, decisionSummary, unexecutedEvidence, validDraft} from './decision'
import {demoEvidence} from './demo-check'
import {initialLibrary, isRequirementLibrary, type RequirementCollection} from './requirement-library-state'
import {RequirementLibraryPanel} from './requirement-library'
import {isChangeState, savedChangeSummary} from './change-review-state'
import {SourceReaderButton} from './source-reader'
import {demoDocument, type DemoFlavor} from './source-documents'
import {CheckPreparation} from './check-preparation'
import {checkPlan, checkSummary, initialCheck, modeName, recoverCheck, validCheck, type CheckPreparationState} from './check-preparation-state'
import {UsageWorkspace} from './usage'
import {validChecks} from './reassessment'
import {fileAccept, optionalLocalFile, useLocalUploads, type LocalFileRef, type LocalUpload} from './local-files'

type Variant = 'file' | 'action' | 'coverage'
type Step = 'cases' | 'scope' | 'requirements' | 'upload' | 'prepare' | 'confirm' | 'submitted' | 'progress'
type FileKey = string
type Kind = '待分类' | '控制计划' | '过程流程图' | '尺寸报告'
type Document = {
  id: FileKey
  origin: 'demo' | 'local'
  name: string
  localFile?: LocalFileRef
  kind: Kind
  expectedKind: Kind
  declaredRevision: string
  contentRevision: string
  sourceVersion: number
  selected: boolean
  associated: boolean
  readability: 'clear' | 'partial' | 'unverified'
}
type Gap = {fileId: FileKey; title: string; detail: string; nature: 'quality' | 'coverage' | 'identity'}
type CaseScope = {customer: string; supplier: string; part: string; revision: string; plant: string; requirements: string}
const scopeKey = ({customer, supplier, part, revision, plant}: CaseScope) => JSON.stringify({customer, supplier, part, revision, plant})
const demoScope: CaseScope = {customer: '北辰汽车', supplier: '明川精密', part: '支架 P-204', revision: 'B', plant: '苏州工厂', requirements: '客户要求 v2'}
const emptyScope: CaseScope = {customer: '', supplier: '', part: '', revision: '', plant: '', requirements: ''}

const variants: {id: Variant; label: string}[] = [
  {id: 'file', label: '资料优先'},
  {id: 'action', label: '待办优先'},
  {id: 'coverage', label: '要求优先'},
]
const initialFiles: Document[] = [
  {id: 'control', origin: 'demo', name: '控制计划_B.xlsx', kind: '控制计划', expectedKind: '控制计划', declaredRevision: 'B', contentRevision: 'B', sourceVersion: 1, selected: true, associated: true, readability: 'clear'},
  {id: 'flow', origin: 'demo', name: '过程流程图_B.pdf', kind: '过程流程图', expectedKind: '过程流程图', declaredRevision: 'B', contentRevision: 'B', sourceVersion: 1, selected: true, associated: true, readability: 'partial'},
  {id: 'dimension', origin: 'demo', name: '尺寸报告_A.xlsx', kind: '尺寸报告', expectedKind: '尺寸报告', declaredRevision: 'A', contentRevision: 'A', sourceVersion: 1, selected: true, associated: true, readability: 'clear'},
]
const completeFiles: Document[] = initialFiles.map(file => ({...file, name: ({control: '控制计划_B_一致.xlsx', flow: '过程流程图_B_完整.pdf', dimension: '尺寸报告_B_完整.xlsx'} as Record<string, string>)[file.id], declaredRevision: 'B', contentRevision: 'B', readability: 'clear'}))
const outlierFiles: Document[] = completeFiles.map(file => file.id === 'dimension' ? {...file, name: '尺寸报告_B_超差.xlsx'} : {...file})
function readingDocuments(files: Document[], receipt: string) {
  const known: Record<string, DemoFlavor> = {
    '控制计划_B.xlsx': 'control-conflict', '控制计划_B_一致.xlsx': 'control-clear',
    '过程流程图_B.pdf': 'flow-blurred', '过程流程图_B_完整.pdf': 'flow-clear', '过程流程图_B_清晰版.pdf': 'flow-clear',
    '尺寸报告_A.xlsx': 'dimension-old', '尺寸报告_B.xlsx': 'dimension-clear', '尺寸报告_B_完整.xlsx': 'dimension-clear', '尺寸报告_B_超差.xlsx': 'dimension-outlier',
  }
  return files.flatMap(file => file.origin === 'demo' && known[file.name]
    ? [demoDocument({name: file.name, revision: file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile}, known[file.name], receipt)] : [])
}
function matchesSample(files: Document[], sample: Document[]) {
  return files.length === sample.length && sample.every(expected => files.some(actual => actual.id === expected.id && actual.origin === 'demo' && actual.selected && actual.associated && actual.name === expected.name && actual.kind === expected.kind && actual.declaredRevision === expected.declaredRevision && actual.contentRevision === expected.contentRevision && actual.sourceVersion === expected.sourceVersion && actual.readability === expected.readability))
}
const kinds: Kind[] = ['待分类', '控制计划', '过程流程图', '尺寸报告']
type IntakeDraft = {
  step: Step; scope: CaseScope; scopeMode: 'demo' | 'new'; scopeConfirmed: boolean
  submitted: boolean; files: Document[]; sourceMode: 'demo' | 'local' | null
  notes: Record<FileKey, string>; acknowledged: boolean; clarification: string | null
  clarificationDraft?: string
  reviewView: boolean; progressScenario: 'running' | 'clarification' | 'failure' | 'complete'
  basis?: RequirementBasis | null
  check?: CheckPreparationState
}
function freshDraft(files: Document[] = [], demo = false): IntakeDraft {
  return {step: files.length ? 'scope' : 'upload', scope: demo ? {...demoScope} : {...emptyScope}, scopeMode: demo ? 'demo' : 'new', scopeConfirmed: false, submitted: false, files, sourceMode: demo ? 'demo' : files.length ? 'local' : null, notes: {}, acknowledged: false, clarification: null, reviewView: false, progressScenario: 'running', check: initialCheck()}
}
function isIntakeDraft(value: unknown): value is IntakeDraft {
  if (!isObject(value) || !isObject(value.scope) || !Object.keys(emptyScope).every(key => typeof (value.scope as Record<string, unknown>)[key] === 'string')) return false
  return ['scope', 'requirements', 'upload', 'prepare', 'confirm', 'submitted', 'progress'].includes(String(value.step))
    && (value.basis === undefined || value.basis === null || isRequirementBasis(value.basis))
    && (value.check === undefined || validCheck(value.check))
    && ['demo', 'new'].includes(String(value.scopeMode)) && [null, 'demo', 'local'].includes(value.sourceMode as null | string)
    && ['scopeConfirmed', 'submitted', 'acknowledged', 'reviewView'].every(key => typeof value[key] === 'boolean')
    && (value.clarification === null || typeof value.clarification === 'string')
    && (value.clarificationDraft === undefined || typeof value.clarificationDraft === 'string')
    && ['running', 'clarification', 'failure', 'complete'].includes(String(value.progressScenario))
    && isObject(value.notes) && Object.values(value.notes).every(note => typeof note === 'string')
    && Array.isArray(value.files) && value.files.every(file => isObject(file)
      && ['id', 'name', 'declaredRevision', 'contentRevision'].every(key => typeof file[key] === 'string')
      && ['demo', 'local'].includes(String(file.origin)) && kinds.includes(file.kind as Kind) && kinds.includes(file.expectedKind as Kind)
      && ['clear', 'partial', 'unverified'].includes(String(file.readability)) && typeof file.sourceVersion === 'number'
      && typeof file.selected === 'boolean' && typeof file.associated === 'boolean' && optionalLocalFile(file.localFile))
}
function localDocuments(incoming: LocalUpload[]): Document[] {
  return incoming.map(({file, ref}) => ({id: ref.id, localFile: ref, origin: 'local', name: file.name, kind: '待分类', expectedKind: '待分类', declaredRevision: '未知', contentRevision: '未解析', sourceVersion: 1, selected: true, associated: false, readability: 'unverified'}))
}

function routeVariant(): Variant {
  const value = new URLSearchParams(window.location.search).get('variant')
  return variants.some(item => item.id === value) ? value as Variant : 'file'
}

function routeStep(): Step {
  const value = new URLSearchParams(window.location.search).get('step')
  return value === 'scope' || value === 'requirements' || value === 'upload' || value === 'prepare' || value === 'confirm' || value === 'progress' ? value : 'cases'
}

function gapsFor(file: Document, part: string): Gap[] {
  const result: Gap[] = []
  if (!file.associated) result.push({fileId: file.id, nature: 'identity', title: '归属待确认', detail: `请确认这份资料属于${part}。`})
  if (file.origin === 'local') {
    if (file.kind === '待分类') result.push({fileId: file.id, nature: 'identity', title: '类型待确认', detail: '请选择资料类型；文件名不能证明文件内容。'})
    if (!file.declaredRevision.trim() || file.declaredRevision === '未知') result.push({fileId: file.id, nature: 'identity', title: '修订待确认', detail: '请打开原件，人工核对修订。'})
    if (file.readability === 'unverified') result.push({fileId: file.id, nature: 'quality', title: '可读性待确认', detail: '请打开原件，人工确认是否可读。'})
    if (file.readability === 'partial') result.push({fileId: file.id, nature: 'quality', title: '人工标记部分不可读', detail: '请在正式审核中定位并复查不可读部分。'})
    return result
  }
  if (file.kind !== file.expectedKind) result.push({fileId: file.id, nature: 'identity', title: '资料类型待确认', detail: '人工分类与文件内容线索不同；不能仅凭分类判断其覆盖范围。'})
  if (file.declaredRevision !== file.contentRevision) result.push({fileId: file.id, nature: 'identity', title: '声明修订待确认', detail: '手填的修订与模拟原件内容不一致；改标签不会改变原件。'})
  if (file.readability === 'partial') result.push({fileId: file.id, nature: 'quality', title: '局部不可读', detail: '扫描件第 3 页有一处工序号无法可靠辨认；其他内容仍可继续处理。'})
  if (file.contentRevision !== 'B') result.push({fileId: file.id, nature: 'coverage', title: '未覆盖 Rev B', detail: '这份原件基于 Rev A，不能证明本轮 Rev B 的尺寸要求；这不是测量超差结论。'})
  return result
}

function missingGap(file: Document): Gap {
  if (file.origin === 'local') return {fileId: file.id, nature: 'coverage', title: '未纳入本轮', detail: '已选择此文件，但未纳入本轮审核资料。'}
  return {fileId: file.id, nature: 'coverage', title: `未收到${file.expectedKind}`, detail: `本轮没有选入${file.expectedKind}原件；这一要求仍未覆盖。`}
}

function fileMeta(file: Document): string {
  return file.origin === 'demo'
    ? `${file.kind} · 原件 v${file.sourceVersion} · 内容修订 Rev ${file.contentRevision}`
    : `${file.kind} · ${!file.declaredRevision.trim() || file.declaredRevision === '未知' ? '修订待确认' : `人工登记修订 ${file.declaredRevision}`} · 内容未解析`
}

function Label({nature, children}: {nature: Gap['nature'] | 'ready'; children: React.ReactNode}) {
  return <span className={`gf-intake-label gf-intake-label--${nature}`}>{children}</span>
}

function IntakeApp({id, initial, onHome, requirementCollections}: {id: string; initial: IntakeDraft; onHome: () => void; requirementCollections: RequirementCollection[]}) {
  const {field, saveError} = useSavedDraft(`intake:${id}`, () => initial, isIntakeDraft)
  const [variant, setVariant] = useState(routeVariant)
  const [step, setStep] = field('step')
  const [scope, setScope] = field('scope')
  const [scopeMode] = field('scopeMode')
  const [scopeConfirmed, setScopeConfirmed] = field('scopeConfirmed')
  const [submitted, setSubmitted] = field('submitted')
  const [files, setFiles] = field('files')
  const [sourceMode, setSourceMode] = field('sourceMode')
  const [uploadError, setUploadError] = useState('')
  const [editing, setEditing] = useState<FileKey | null>(null)
  const [notes, setNotes] = field('notes')
  const [acknowledged, setAcknowledged] = field('acknowledged')
  const [clarification, setClarification] = field('clarification')
  const [clarificationDraft, setClarificationDraft] = field('clarificationDraft')
  const [reviewView, setReviewView] = field('reviewView')
  const [progressScenario, setProgressScenario] = field('progressScenario')
  const [savedBasis, setBasis] = field('basis')
  const [savedCheck, setCheck] = field('check')
  const check = savedCheck ?? initialCheck()
  const checkNavigation = check.request?.phase === 'accepted' ? '检查进度' : check.request && check.request.phase !== 'absent' ? '核对受理' : '检查准备'
  useEffect(() => {setCheck(current => current ? recoverCheck(current) : current)}, [])
  const basis = savedBasis ?? null
  const requirementsReady = basisConfirmed(basis, scopeKey(scope))
  const selectedFiles = files.filter(file => file.selected)
  const gaps = files.flatMap(file => file.selected ? gapsFor(file, scope.part) : [missingGap(file)])
  const canConfirmScope = [scope.customer, scope.supplier, scope.part, scope.revision, scope.plant].every(value => value.trim())
  const scopeMatchesDemo = scopeMode === 'demo' && scopeKey(scope) === scopeKey(demoScope)
  const canPrepare = scopeConfirmed && selectedFiles.length > 0 && selectedFiles.every(file => file.associated && file.kind !== '待分类')
  const filesWithGaps = [...new Set(gaps.map(gap => gap.fileId))]
  const canSubmit = canPrepare && requirementsReady && acknowledged
  const selectedCount = selectedFiles.length
  const gapCount = gaps.length
  const byId = useMemo(() => Object.fromEntries(files.map(file => [file.id, file])) as Record<FileKey, Document>, [files])
  const completeSample = sourceMode === 'demo' && scopeMatchesDemo && (matchesSample(files, completeFiles) || matchesSample(files, outlierFiles))
  const sampleOutlier = matchesSample(files, outlierFiles)
  const receiptId = `DEMO-${id.slice(0, 8)}`
  const documents = readingDocuments(files, receiptId)
  const knownInput = sourceMode === 'demo' && scopeMatchesDemo && documents.length === files.length && files.every(file => file.kind === file.expectedKind && file.declaredRevision === file.contentRevision)
  const [previousReview] = useState(() => readDraft(`review:${id}`, isReviewDraft)?.value)
  const preservePreviousStory = Boolean(previousReview && (previousReview.sent || previousReview.submission || previousReview.receivedFiles.length || Object.values(previousReview.review).some(row => row.outcome !== 'pending' || row.reason.trim())))
  const decisionFiles = selectedFiles.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile}))
  const decisionScope = `${scope.customer} · ${scope.supplier} · ${scope.part} · 图纸 Rev ${scope.revision} · ${scope.plant}`
  const plan = basis ? checkPlan(basis, selectedFiles, receiptId, check.mode, knownInput) : null
  const readingInput = {receiptId: submitted ? receiptId : '待登记资料', scope: scope.part, files: files.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, pending: !submitted})), evidence: [], clarification}
  const readingSources = documents.map(doc => ({...doc, pending: !submitted, origin: submitted ? doc.origin : '本次上传 · 待登记'}))
  const handoff: IntakeHandoff | undefined = submitted ? {
    sample: completeSample ? sampleOutlier ? 'outlier' : 'complete' : undefined,
    receiptId: `DEMO-${id.slice(0, 8)}`,
    documents,
    check: check.request?.phase === 'accepted' ? {id: check.request.runId, method: modeName(check.request.plan.mode)} : undefined,
    files: selectedFiles.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile})),
    disclosures: gaps.map(gap => ({fileName: byId[gap.fileId].name, issue: gap.title, note: notes[gap.fileId] || '收件时未附说明'})),
    clarification,
    basis,
    decisionInput: requirementsReady && basis && (!preservePreviousStory || completeSample || !knownInput || !basisMatchesFixedReview(basis)) ? {
      receiptId, scope: decisionScope,
      basis, files: decisionFiles, clarification,
      evidence: completeSample ? completeSampleEvidence(basis, sampleOutlier) : knownInput ? demoEvidence({receiptId, scope: decisionScope, basis, files: decisionFiles, clarification, evidence: []}, documents) : unexecutedEvidence(basis),
    } : undefined,
    matchesFixedReview: requirementsReady && basisMatchesFixedReview(basis) && scopeMatchesDemo && sourceMode === 'demo' && files.length === initialFiles.length && initialFiles.every(expected => {
      const actual = byId[expected.id]
      return actual && actual.selected && actual.associated && actual.name === expected.name && actual.kind === expected.kind && actual.declaredRevision === expected.declaredRevision && actual.contentRevision === expected.contentRevision && actual.sourceVersion === expected.sourceVersion && actual.readability === expected.readability
    }),
  } : undefined

  useEffect(() => {
    const sync = () => {
      setVariant(routeVariant())
      const target = routeStep()
      if (target === 'cases') return
      setStep(submitted ? target === 'progress' && check.request?.phase === 'accepted' ? 'progress' : 'submitted' : !scopeConfirmed && target !== 'upload' ? 'scope' : files.length || target === 'upload' ? target : 'upload')
    }
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [submitted, files.length, scopeConfirmed, check.request?.phase])
  const selectVariant = (next: Variant) => {
    setVariant(next)
    const url = new URL(window.location.href)
    url.searchParams.set('variant', next)
    window.history.pushState(null, '', url)
  }
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (step !== 'prepare' || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      if ((event.target as HTMLElement)?.closest('input, textarea, select, button, [role="dialog"], [contenteditable="true"]')) return
      const index = variants.findIndex(item => item.id === variant)
      selectVariant(variants[(index + (event.key === 'ArrowRight' ? 1 : variants.length - 1)) % variants.length].id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  const update = (id: FileKey, change: Partial<Document>) => {
    setFiles(current => current.map(file => file.id === id ? {...file, ...change} : file))
    setAcknowledged(false)
  }
  const replace = (id: FileKey) => {
    if (id === 'flow') update(id, {name: '过程流程图_B_清晰版.pdf', readability: 'clear', sourceVersion: 2})
    if (id === 'dimension') update(id, {name: '尺寸报告_B.xlsx', declaredRevision: 'B', contentRevision: 'B', sourceVersion: 2})
    setNotes(current => ({...current, [id]: ''}))
  }
  const removeLocalFile = (id: FileKey) => {
    setFiles(current => current.filter(file => file.id !== id))
    setAcknowledged(false)
    if (files.length === 1) go('upload')
  }
  const startDemo = () => {
    if (!scopeMatchesDemo) return
    upload.cancel()
    setFiles(initialFiles)
    setSourceMode('demo')
    setUploadError('')
    setNotes({})
    setAcknowledged(false)
    go('prepare')
  }
  const addLocalFiles = (list: FileList | null) => {
    if (!list?.length) return
    if (sourceMode === 'demo' && files.length) {
      setUploadError('示例包与真实文件分开登记。请返回工作台上传，当前示例记录会保留。')
      return
    }
    setUploadError('')
    void upload.upload(Array.from(list))
  }
  const upload = useLocalUploads(incoming => {
    const mapped = localDocuments(incoming)
    setFiles(current => sourceMode === 'local' ? [...current, ...mapped] : mapped)
    setSourceMode('local')
    setUploadError('')
    setAcknowledged(false)
    go(scopeConfirmed ? 'prepare' : 'scope')
  }, id)
  const go = (next: Step) => {
    if (next === 'cases') {onHome(); return}
    if (submitted && (next === 'scope' || next === 'upload' || next === 'prepare' || next === 'confirm')) return
    if (next === 'submitted' && !canSubmit) return
    if (next === 'progress' && check.request?.phase !== 'accepted') next = 'submitted'
    if (!submitted && next === 'confirm' && !requirementsReady) next = 'requirements'
    if (next === 'submitted') setSubmitted(true)
    setStep(next)
    const url = new URL(window.location.href)
    if (next !== 'submitted') url.searchParams.set('step', next)
    else url.searchParams.delete('step')
    if (next === 'progress') {
      url.searchParams.set('variant', 'action')
      url.searchParams.set('scenario', progressScenario)
    } else {
      url.searchParams.delete('scenario')
    }
    window.history.pushState(null, '', url)
    window.scrollTo({top: 0, behavior: 'smooth'})
  }
  const changeScope = (field: keyof CaseScope, value: string) => {
    setScope(current => ({...current, [field]: value}))
    setScopeConfirmed(false)
    // A changed association needs confirmation, not deletion of the received file list.
    setFiles(current => current.map(file => ({...file, associated: false})))
    setAcknowledged(false)
    if (basis) setBasis({...basis, confirmed: null, clauses: basis.clauses.map(clause => ({...clause, checked: false, applicability: 'pending'}))})
  }

  if (reviewView) return <ConceptApp handoff={handoff} draftId={id} onHome={onHome} changeContext={{scope, collections: requirementCollections}}/>

  const fileSection = <section className="gf-intake-section" aria-label="本次文件">
    <div className="gf-intake-section-heading"><div><h2>本次文件</h2><p>确认资料类型、修订、归属和是否纳入本轮。登记不等于审核通过。</p></div><span>{selectedCount} / {files.length} 份</span></div>
    <div className="gf-intake-file-list">{files.map(file => {
      const fileGaps = gapsFor(file, scope.part)
      return <article className="gf-intake-file" key={file.id}>
        <div className="gf-intake-file-head">
          <Checkbox aria-label={`纳入${file.name}`} checked={file.selected} onCheckedChange={value => update(file.id, {selected: value === true})}/>
          <FileText size={18} aria-hidden="true"/>
          <div className="gf-intake-file-name"><strong>{file.name}</strong><span>{fileMeta(file)}</span></div>
          {!file.selected ? <Label nature="coverage">未纳入</Label> : fileGaps.length ? <Label nature={fileGaps[0].nature}>{fileGaps[0].title}</Label> : <Label nature="ready">已核对登记信息</Label>}
        </div>
        {file.selected && fileGaps.length > 0 && <p className="gf-intake-file-issue">{fileGaps.map(gap => gap.title).join(' · ')}</p>}
        <div className="gf-intake-file-actions">
          <SourceReaderButton input={readingInput} documents={readingSources} file={{name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, pending: !submitted}} label="查看资料"/>
          <button type="button" onClick={() => setEditing(editing === file.id ? null : file.id)}>{editing === file.id ? '收起归属' : '核对归属'} <ChevronRight size={15}/></button>
          {file.origin === 'demo' && (file.id === 'flow' && file.readability === 'partial' || file.id === 'dimension' && file.contentRevision !== 'B') && <button type="button" onClick={() => replace(file.id)}><RotateCcw size={14}/> {file.id === 'flow' ? '模拟登记清晰件' : '模拟登记 Rev B'}</button>}
          {file.origin === 'local' && <button type="button" onClick={() => removeLocalFile(file.id)}>移除文件</button>}
        </div>
        {editing === file.id && <div className="gf-intake-editor">
          <label>资料类型<select value={file.kind} onChange={event => update(file.id, {kind: event.target.value as Kind})}>{kinds.filter(kind => file.origin === 'local' || kind !== '待分类').map(kind => <option key={kind}>{kind}</option>)}</select></label>
          {file.origin === 'local' ? <label>人工记录修订<Input value={file.declaredRevision === '未知' ? '' : file.declaredRevision} placeholder="如 B、02；留空表示未知" onChange={event => update(file.id, {declaredRevision: event.target.value})}/></label> : <label>声明修订<select value={file.declaredRevision} onChange={event => update(file.id, {declaredRevision: event.target.value})}><option value="A">Rev A</option><option value="B">Rev B</option><option value="未知">未知</option></select></label>}
          <label className="gf-intake-association"><Checkbox aria-label={`${file.name}属于${scope.part}`} checked={file.associated} onCheckedChange={value => update(file.id, {associated: value === true})}/> 属于{scope.part}</label>
          {file.origin === 'local' ? <label>人工核对可读性<select value={file.readability} onChange={event => update(file.id, {readability: event.target.value as Document['readability']})}><option value="unverified">尚未核对</option><option value="clear">可读</option><option value="partial">部分不可读</option></select></label> : <p>原件内容修订 Rev {file.contentRevision}。修改声明不会使旧版报告覆盖 Rev B。</p>}
        </div>}
      </article>
    })}</div>
    <p className="gf-intake-mock"><UploadCloud size={15}/> {sourceMode === 'demo' ? '当前是模拟资料包，文件内容与缺口均为预设。' : '文件与登记信息仅保存在此浏览器；没有自动识别文件内容。'}</p>
    {!submitted && <label className="gf-intake-add">继续选择文件<input type="file" accept={fileAccept} disabled={upload.busy} multiple onChange={event => {addLocalFiles(event.target.files); event.target.value = ''}}/></label>}
    {upload.busy && <p role="status">正在保存本机原件…</p>}{(uploadError || upload.error) && <p className="gf-intake-upload-error" role="alert">{uploadError || upload.error}</p>}
  </section>

  const actionSection = <section className="gf-intake-section" aria-label="收件时发现的事项">
    <div className="gf-intake-section-heading"><div><h2>收件时发现</h2><p>先核对资料归属；缺口带入审核，不在收件时判定合格。</p></div><span>{gapCount} 项</span></div>
    <div className="gf-intake-actions">{gaps.length ? gaps.map((gap, index) => <div className="gf-intake-action" key={`${gap.fileId}-${index}`}>
      <Label nature={gap.nature}>{gap.title}</Label><div><strong>{byId[gap.fileId].name}</strong><p>{gap.detail}</p></div>
      <button type="button" onClick={() => {setEditing(gap.fileId); document.getElementById('intake-files')?.scrollIntoView({behavior: 'smooth', block: 'start'})}}>核对资料 <ChevronRight size={15}/></button>
    </div>) : <div className="gf-intake-empty"><FileCheck2 size={20}/> 当前所选资料没有预检缺口。正式审核仍可能发现内容问题。</div>}</div>
  </section>

  const coverageSection = <section className="gf-intake-section" aria-label="资料与要求覆盖">
    <div className="gf-intake-section-heading"><div><h2>本轮要求与资料</h2><p>预检只核对可用性与版本；不判断工艺、尺寸是否合格。</p></div></div>
    {sourceMode === 'demo' ? <div className="gf-intake-coverage">
      <div><strong>控制计划</strong><span>{byId.control.selected ? byId.control.name : '未纳入本轮'}</span><Label nature={byId.control.selected ? gapsFor(byId.control, scope.part)[0]?.nature ?? 'ready' : 'coverage'}>{byId.control.selected ? gapsFor(byId.control, scope.part)[0]?.title ?? '可供审核' : '未覆盖'}</Label></div>
      <div><strong>过程流程图</strong><span>{byId.flow.selected ? byId.flow.name : '未纳入本轮'}</span><Label nature={byId.flow.selected ? gapsFor(byId.flow, scope.part)[0]?.nature ?? 'ready' : 'coverage'}>{byId.flow.selected ? gapsFor(byId.flow, scope.part)[0]?.title ?? '可供审核' : '未覆盖'}</Label></div>
      <div><strong>Rev B 尺寸报告</strong><span>{byId.dimension.selected ? byId.dimension.name : '未纳入本轮'}</span><Label nature={byId.dimension.selected ? gapsFor(byId.dimension, scope.part)[0]?.nature ?? 'ready' : 'coverage'}>{byId.dimension.selected ? gapsFor(byId.dimension, scope.part)[0]?.title ?? '可供审核' : '未覆盖'}</Label></div>
    </div> : <div className="gf-intake-coverage">{(['控制计划', '过程流程图', '尺寸报告'] as Kind[]).map(kind => {
      const candidate = selectedFiles.find(file => file.kind === kind)
      return <div key={kind}><strong>{kind}</strong><span>{candidate?.name ?? '尚无已分类文件'}</span><Label nature="identity">{candidate ? '内容待审核' : '归类待核对'}</Label></div>
    })}</div>}
  </section>

  const readiness = <aside className="gf-intake-readiness" aria-label="收件核对摘要">
    <div className="gf-intake-readiness-top"><ClipboardCheck size={20}/><div><span>收件核对</span><strong>{canPrepare ? gapCount ? '可保留缺口完成登记' : '可以完成登记' : '请先确认类型与归属'}</strong></div></div>
    <p>{sourceMode === 'demo' ? '这是资料检查，不是质量批准。模拟审核会继续核对内容与证据。' : '真实文件内容未解析。本原型只能演示上传后核对，不能给出审核结论。'}</p>
    <div className="gf-intake-facts"><div><span>本轮基准</span><strong>图纸 Rev {scope.revision} · {requirementsReady && basis ? `${basis.source.title} ${basis.source.version}` : '审核依据待确认'}</strong></div><div><span>纳入原件</span><strong>{selectedCount} 份</strong></div><div><span>资料缺口</span><strong>{gapCount} 项</strong></div></div>
    <Button variant="outline" onClick={() => go('requirements')}>{requirementsReady ? '查看审核依据' : '确认审核依据'}</Button>
    {!canPrepare && <p className="gf-intake-block"><ShieldAlert size={16}/> 至少纳入一份文件，确认其资料类型与事项归属。</p>}
    <Button className="gf-primary gf-intake-next" disabled={!canPrepare} onClick={() => go('confirm')}>核对收件清单 <ArrowRight size={16}/></Button>
  </aside>

  return <div className="gf-app gf-intake-app">
    <aside className="gf-sidebar gf-intake-sidebar">
      <div className="gf-brand"><div className="gf-brand-mark"><ClipboardCheck size={19}/></div><span>PPAP</span><small>概念原型</small></div>
      <div className="gf-sidebar-label">审核团队 · {scope.customer || '新事项'}</div>
      <nav aria-label="统一审核原型流程"><button onClick={onHome}><ArrowLeft size={18}/><span>工作台</span></button><button aria-current={step === 'scope' ? 'page' : undefined} disabled={!files.length || submitted} onClick={() => go('scope')}><Files size={18}/><span>资料归属</span></button><button aria-current={step === 'requirements' ? 'page' : undefined} disabled={!scopeConfirmed} onClick={() => go('requirements')}><FileText size={18}/><span>审核依据</span></button><button aria-current={step === 'upload' ? 'page' : undefined} disabled={submitted} onClick={() => go('upload')}><UploadCloud size={18}/><span>上传资料</span></button><button aria-current={step === 'prepare' || step === 'confirm' ? 'page' : undefined} disabled={!files.length || !scopeConfirmed || submitted} onClick={() => go('prepare')}><ClipboardCheck size={18}/><span>核对收件</span></button><button aria-current={step === 'submitted' || step === 'progress' ? 'page' : undefined} disabled={!submitted} onClick={() => go('progress')}><FileCheck2 size={18}/><span>{checkNavigation}</span></button></nav>
      <div className="gf-sidebar-foot"><PrototypeAccount/></div>
    </aside>
    <div className="gf-workspace">
      <header className="gf-topline"><div><span className="gf-topline-dot"/><span>量产批准工作台</span><span className="gf-topline-sep">/</span><strong>{scope.part || '待核对资料'}</strong></div><span className="gf-demo-label">交互原型 · 仅本机记录</span><PrototypeAccount compact className="gf-account-mobile"/></header>
      <div className="gf-mobile-nav"><button onClick={onHome}>工作台</button><button aria-current={step === 'scope' ? 'page' : undefined} disabled={!files.length || submitted} onClick={() => go('scope')}>资料归属</button><button aria-current={step === 'requirements' ? 'page' : undefined} disabled={!scopeConfirmed} onClick={() => go('requirements')}>审核依据</button><button aria-current={step === 'prepare' || step === 'confirm' ? 'page' : undefined} disabled={!files.length || !scopeConfirmed || submitted} onClick={() => go('prepare')}>核对收件</button><button aria-current={step === 'submitted' || step === 'progress' ? 'page' : undefined} disabled={!submitted} onClick={() => go('progress')}>{checkNavigation}</button></div>
      <main className="gf-main gf-intake-main">
        {saveError && <p role="alert" className="gf-intake-upload-error">{saveError}</p>}
        {step === 'scope' && <>
          <button className="gf-back" onClick={onHome}><ArrowLeft size={16}/> 工作台</button>
          <header className="gf-page-heading gf-intake-heading"><div><h1>核对资料归属</h1><p className="gf-case-meta">已选择 {files.length} 份文件。请确认本轮零件、修订与审核依据。</p></div></header>
          <details className="gf-intake-received-list"><summary>本次文件 · {files.length} 份</summary><ul>{files.map(file => <li key={file.id}>{file.name}</li>)}</ul></details>
          <SourceReaderButton input={readingInput} documents={readingSources} label="阅读已上传资料"/>
          <div className="gf-intake-scope-grid">{([{field: 'customer', label: '客户'}, {field: 'supplier', label: '供应商'}, {field: 'part', label: '零件与编号'}, {field: 'revision', label: '图纸修订'}, {field: 'plant', label: '生产地点'}] as {field: keyof CaseScope; label: string}[]).map(item => <label key={item.field}>{item.label}<Input value={scope[item.field]} onChange={event => changeScope(item.field, event.target.value)} /></label>)}</div>
          <Button className="gf-primary" disabled={!canConfirmScope || !files.length} onClick={() => {setScopeConfirmed(true); go('requirements')}}>确认归属，选择审核依据 <ArrowRight size={16}/></Button>
        </>}
        {step === 'requirements' && <Requirements basis={basis} scopeKey={scopeKey(scope)} scope={scope} collections={requirementCollections} context={`${scope.part} · 图纸 Rev ${scope.revision} · ${scope.customer} / ${scope.supplier}`} readonly={submitted} onBack={() => go(submitted ? 'progress' : 'scope')} onChange={next => {setBasis(next); setAcknowledged(false)}} onConfirm={next => {setBasis(next); setScope(current => ({...current, requirements: `${next.source.title} ${next.source.version}`})); setAcknowledged(false); go('prepare')}}/>}
        {step === 'upload' && <>
          <button className="gf-back" onClick={() => go('scope')}><ArrowLeft size={16}/> 返回事项范围</button>
          <header className="gf-page-heading gf-intake-heading"><div><div className="gf-eyebrow">02 · 建立资料包</div><h1>上传资料</h1><p className="gf-case-meta">{scope.supplier} <span>·</span> {scope.part} <span>·</span> 图纸 Rev {scope.revision}</p></div></header>
          <div className="gf-intake-upload"><UploadCloud size={30} aria-hidden="true"/><h2>先添加收到的文件</h2><p>供应商从系统外发送资料。审核员选择文件后，再逐份核对类型、修订和归属。</p>
            <label className="gf-intake-upload-button">选择文件<input type="file" accept={fileAccept} disabled={upload.busy} multiple onChange={event => {addLocalFiles(event.target.files); event.target.value = ''}}/></label>
            <small>支持 Excel、Word、PDF、图片和文本，单份最大 64 MB。原件仅保存在本机。</small>
            {upload.busy && <p role="status">正在保存本机原件…</p>}{(uploadError || upload.error) && <p className="gf-intake-upload-error" role="alert">{uploadError || upload.error}</p>}
          </div>
          {scopeMatchesDemo && <div className="gf-intake-demo-entry"><span>需要查看完整模拟审核旅程？</span><button type="button" onClick={startDemo}>加载示例资料包 <ArrowRight size={15}/></button><small>仅用于演示，不是第二个真实收件入口。</small></div>}
        </>}
        {step === 'prepare' && <>
          <button className="gf-back" onClick={() => go('upload')}><ArrowLeft size={16}/> 返回上传</button>
          <header className="gf-page-heading gf-intake-heading"><div><div className="gf-eyebrow">03 · 上传后核对</div><h1>核对收件</h1><p className="gf-case-meta">{scope.supplier} <span>·</span> {scope.part} <span>·</span> 图纸 Rev {scope.revision} <span>·</span> {scope.plant}</p></div><span className="gf-round">{scope.requirements}</span></header>
          {variant === 'file' && <div className="gf-intake-grid"><div id="intake-files">{fileSection}</div>{readiness}</div>}
          {variant === 'action' && <div className="gf-intake-grid gf-intake-grid--action"><div>{actionSection}<div id="intake-files" className="gf-intake-below">{fileSection}</div></div>{readiness}</div>}
          {variant === 'coverage' && <>{coverageSection}<div className="gf-intake-grid gf-intake-below"><div id="intake-files">{fileSection}</div>{readiness}</div></>}
        </>}
        {step === 'confirm' && <>
          <button className="gf-back" onClick={() => go('prepare')}><ArrowLeft size={16}/> 返回文件清单</button>
          <header className="gf-page-heading gf-intake-heading"><div><div className="gf-eyebrow">登记前确认</div><h1>确认登记</h1><p className="gf-case-meta">{scope.supplier} · {scope.part} · 图纸 Rev {scope.revision} · {scope.requirements}</p></div></header>
          <BasisSummary basis={basis}/>
          <div className="gf-intake-confirm-layout"><section className="gf-intake-section"><div className="gf-intake-section-heading"><div><h2>纳入本轮的原件</h2><p>登记后，后续来件形成新版本；此处仅展示模拟快照。</p></div><span>{selectedCount} 份</span></div>
            <div className="gf-intake-freeze">{selectedFiles.map(file => <div key={file.id}><FileText size={18}/><div><strong>{file.name}</strong><span>{fileMeta(file)}</span></div></div>)}</div>
          </section><section className="gf-intake-section gf-intake-disclosures"><div className="gf-intake-section-heading"><div><h2>收件时已知的缺口</h2><p>如供应商随件说明，可记录原话；没有说明也可继续检查。</p></div><span>{gapCount} 项</span></div>
            {filesWithGaps.length ? filesWithGaps.map(id => <label key={id} className="gf-intake-note"><strong>{byId[id].selected ? byId[id].name : `${byId[id].expectedKind}（未纳入）`}</strong><span>{gaps.filter(gap => gap.fileId === id).map(gap => gap.title).join(' · ')}</span><Textarea aria-label={`${byId[id].name}的随件说明`} placeholder="如有供应商说明，在此记录；请勿代写结论" value={notes[id] ?? ''} onChange={event => {setNotes(current => ({...current, [id]: event.target.value})); setAcknowledged(false)}}/></label>) : <p className="gf-intake-no-gaps"><Check size={17}/> 当前资料无登记缺口；正式审核仍可能发现问题。</p>}
          </section></div>
          <section className="gf-intake-final"><label><Checkbox aria-label="确认收件清单与已知缺口" checked={acknowledged} onCheckedChange={value => setAcknowledged(value === true)}/><span>我已核对本次文件清单和已知缺口。登记后确认检查范围与方式。</span></label><Button className="gf-primary" disabled={!canSubmit} onClick={() => go('submitted')}>完成收件登记 <ArrowRight size={16}/></Button></section>
        </>}
        {(step === 'submitted' || step === 'progress' && check.request?.phase !== 'accepted') && (plan && requirementsReady ? <><CheckPreparation caseId={id} state={check} plan={plan} onChange={setCheck} onContinue={() => go('progress')} onHome={onHome} saveError={saveError}/>{!knownInput && handoff?.decisionInput && <section className="gf-intake-success"><h2>也可以由人工核对</h2><p>阅读本次原件，逐条记录证据和判断，不启动机器检查。</p><Button variant="outline" onClick={() => setReviewView(true)}>开始人工核对<ArrowRight/></Button></section>}</> : <section className="gf-intake-success"><h1>审核依据尚未确认</h1><p>没有确认的要求快照，不能开始检查。</p><Button onClick={() => go('requirements')}>查看审核依据</Button></section>)}
        {step === 'progress' && check.request?.phase === 'accepted' && (knownInput && requirementsReady ? <ProgressPrototype checkRequest={check.request} onCheckPlan={() => go('submitted')} snapshot={{fileCount: selectedCount, hasFlow: Boolean(byId.flow?.selected), hasControl: Boolean(byId.control?.selected), flowUnreadable: Boolean(byId.flow?.selected && byId.flow.readability === 'partial'), dimensionRevision: byId.dimension?.selected ? byId.dimension.contentRevision : null}} completedEvidence={handoff?.decisionInput?.evidence} clarification={clarification} answer={clarificationDraft ?? ''} onAnswerChange={setClarificationDraft} onClarification={setClarification} onOpenReview={() => setReviewView(true)} sameCase={submitted} savedScenario={progressScenario} onScenario={setProgressScenario} basis={basis} receiptId={handoff?.receiptId ?? `DEMO-${id.slice(0, 8)}`}/> : <div className="gf-intake-success"><div className="gf-eyebrow">模拟检查状态</div><h1>未取得可验证的检查结果</h1><p>已保存本次请求和资料记录。此组合没有模拟执行结果，所有适用要求仍未覆盖。</p><div><FileQuestion size={18}/><span>原型不解析真实文件，不能把登记信息当作审核证据。</span></div><BasisSummary basis={basis}/>{handoff?.decisionInput && <Button onClick={() => setReviewView(true)}>转人工核对<ArrowRight/></Button>}<Button variant="outline" onClick={() => go('submitted')}>查看本次检查方案</Button></div>)}
      </main>
    </div>
    {import.meta.env.DEV && step === 'prepare' && <aside className="gf-switcher" aria-label="准备页方案切换"><button aria-label="上一种布局" onClick={() => selectVariant(variants[(variants.findIndex(item => item.id === variant) + variants.length - 1) % variants.length].id)}><ArrowLeft size={16}/></button><span>布局方案　{variants.findIndex(item => item.id === variant) + 1} / 3 · {variants.find(item => item.id === variant)?.label}</span><button aria-label="下一种布局" onClick={() => selectVariant(variants[(variants.findIndex(item => item.id === variant) + 1) % variants.length].id)}><ArrowRight size={16}/></button></aside>}
    <span className="gf-state-readout" aria-live="polite">模拟状态：{step} · {selectedCount} 份资料 · {gapCount} 项缺口</span>
  </div>
}

function JourneyApp() {
  const library = useSavedDraft('requirement-library', initialLibrary, isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const [active, setActive] = useState<{id: string; initial: IntakeDraft; missing?: boolean} | null>(() => {
    const query = new URLSearchParams(window.location.search)
    const id = query.get('case')
    if (id) return {id, initial: freshDraft(), missing: !readDraft(`intake:${id}`, isIntakeDraft)}
    if (query.get('step') === 'progress') return {id: 'progress-example', initial: {...freshDraft(initialFiles, true), step: 'progress', submitted: true, scopeConfirmed: true}}
    return null
  })
  const drafts = listDrafts('intake:', isIntakeDraft)
  const items: WorkItem[] = drafts.map(({key, value: draft, updatedAt}) => {
    const change = savedChangeSummary(key.slice('intake:'.length))
    const review = change ?? savedReviewSummary(key.slice('intake:'.length))
    const currentScope = change?.scope ?? draft.scope
    const basisReady = basisConfirmed(draft.basis, scopeKey(draft.scope))
    const demo = basisReady && draft.sourceMode === 'demo' && scopeKey(draft.scope) === scopeKey(demoScope)
    const preparation = draft.submitted ? checkSummary(draft.check) : null
    return {
      id: key.slice('intake:'.length), title: draft.scope.part || `${draft.files.length} 份待核对资料`,
      context: [currentScope.customer, currentScope.supplier, currentScope.revision ? `${change?.pending ? '拟采用' : '图纸'} Rev ${currentScope.revision}` : '修订待确认'].filter(Boolean).join(' · '),
      state: draft.reviewView ? review?.state ?? '待复核' : !draft.submitted ? draft.scopeConfirmed && !basisReady ? '要求待确认' : '待核对' : preparation?.state ?? (!demo ? '内容未检查' : ({running: '检查中', clarification: '待澄清', failure: '局部失败', complete: '待复核'}[draft.progressScenario])),
      next: draft.reviewView ? review?.next ?? '继续复核' : !draft.submitted ? '核对资料' : preparation?.next ?? (!demo ? '查看登记' : '查看进度'),
      detail: draft.reviewView ? review?.detail ?? '继续核对发现与原件证据。' : !draft.submitted ? `${draft.files.length} 份文件 · ${draft.scopeConfirmed ? basisReady ? '要求已确认，继续逐份核对文件' : '归属已记录，审核依据待确认' : '尚未确认资料归属'}` : preparation?.detail ?? (!demo ? '已登记文件名与说明；没有执行内容检查。' : draft.clarification ? '供应商说明已记录，原件仍需核实。' : '继续查看检查结果与尚未覆盖的要求。'),
      received: draft.submitted, updatedAt,
    }
  })
  function open(id: string, initial?: IntakeDraft, owner?: import('./usage-state').CheckOwner) {
    setActive({id, initial: initial ?? freshDraft(), missing: !initial && !readDraft(`intake:${id}`, isIntakeDraft)})
    const url = new URL(window.location.href)
    url.search = ''
    url.searchParams.set('case', id)
    if (owner) {
      const decisionId = owner.kind === 'reassessment' ? readDraft(`reassessment:${id}`, validChecks)?.value.rounds[owner.roundId]?.receiptSource?.decisionId : undefined
      const changeId = decisionId?.startsWith(`${id}:change:`) ? decisionId.slice(`${id}:change:`.length) : undefined
      url.searchParams.set('resume', owner.kind === 'changes' || changeId ? 'changes' : decisionId ? 'decision' : 'delta')
      url.searchParams.set('checkRound', owner.roundId)
      if (changeId) url.searchParams.set('decisionRound', changeId)
    }
    else if (savedChangeSummary(id)) url.searchParams.set('resume', 'changes')
    else if (decisionSummary(id)) url.searchParams.set('resume', 'decision')
    window.history.pushState(null, '', url)
    window.scrollTo({top: 0})
  }
  function home() {
    setActive(null)
    window.history.pushState(null, '', window.location.pathname)
    window.scrollTo({top: 0})
  }
  useEffect(() => {
    function sync() {
      const id = new URLSearchParams(window.location.search).get('case')
      setActive(current => id ? current?.id === id ? current : {id, initial: freshDraft(), missing: !readDraft(`intake:${id}`, isIntakeDraft)} : null)
    }
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])
  if (active?.missing) return <main className="gf-app gf-access-missing"><FileQuestion size={28} aria-hidden="true"/><h1>暂时无法打开此事项</h1><p>此浏览器没有可读取的记录。请返回工作台选择已有事项。原型记录不跨浏览器同步；打开链接不会新建事项。</p><Button className="gf-primary" onClick={home}>返回工作台<ArrowRight/></Button><PrototypeAccount compact/></main>
  if (active) return <IntakeApp key={active.id} id={active.id} initial={active.initial} onHome={home} requirementCollections={collections}/>
  return <TaskWorkbench items={items} onOpen={id => open(id)} usage={<UsageWorkspace onOpen={(id, owner) => open(id, undefined, owner)}/>} requirements={<RequirementLibraryPanel collections={collections} onChange={setCollections} saveError={library.saveError} uses={drafts.flatMap(({key, value}) => value.basis ? [{id: key.slice('intake:'.length), title: [value.scope.part, value.scope.supplier].filter(Boolean).join(' · '), basis: value.basis, submitted: value.submitted}] : [])} onOpen={id => open(id)}/>} onDemo={(kind = 'issues') => open(crypto.randomUUID(), freshDraft((kind === 'complete' ? completeFiles : kind === 'outlier' ? outlierFiles : initialFiles).map(file => ({...file})), true))} onFiles={files => {
    open(crypto.randomUUID(), freshDraft(localDocuments(files)))
    return null
  }}/>
}

const container = document.getElementById('app')!
const root = (import.meta.hot?.data.root as Root | undefined) ?? createRoot(container)
const recordValidators = {intake: isIntakeDraft, review: isReviewDraft, decision: validDraft, changes: isChangeState, reassessment: validChecks, 'requirement-library': isRequirementLibrary}
root.render(<PrototypeAccess recordValidators={recordValidators}><JourneyApp/></PrototypeAccess>)
if (import.meta.hot) import.meta.hot.data.root = root
