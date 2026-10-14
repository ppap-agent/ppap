import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Single-role product journey. Synthetic records are limited to explicit preview entrypoints.
import {useEffect, useMemo, useState} from 'react'
import {
  ArrowLeft, ArrowRight, Check, ChevronRight, ClipboardCheck, FileCheck2,
  FileQuestion, FileText, Files, RotateCcw, ShieldAlert, UploadCloud,
} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import '../index.css'
import {ProgressPrototype} from './progress'
import {ConceptApp, isReviewDraft, savedReviewSummary, type IntakeHandoff} from './concept'
import {isObject, listDrafts, readDraft, useSavedDraft} from './saved-draft'
import {ProductAccess, AccountMenu, isPreview, useAccess} from './access'
import {dateLabel, executionLabels, request, upload as uploadProduct, type Draft, type DraftList, type Material, type Round, type RoundList, type RuleDetail} from '../api'
import {message, useRemote, inputItems} from './remote'
import {loadRuleCollections, parsedBasis, roundInput} from './product'
import {useLiveAudit} from './live-audit'
import type {RuleTypes} from '../api'
import {RoundReviewContext} from './review-record'
import {confirmBasis, localBasis} from './requirements-state'
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
type Kind = string
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
  material?: Material
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
    ? [demoDocument({name: file.name, revision: file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, material: file.material}, known[file.name], receipt)] : [])
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
      && ['demo', 'local'].includes(String(file.origin)) && [file.kind, file.expectedKind].every(kind => typeof kind === 'string' && kind.length > 0 && kind.length <= 200)
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
  return <span className={["inline-flex items-center w-fit min-h-6.5 py-1 px-2 rounded-lg text-xs font-semibold whitespace-nowrap",({"ready":"bg-muted text-foreground","quality":"bg-muted text-foreground","coverage":"bg-muted text-primary","identity":"bg-destructive/10 text-destructive"} as Record<string,string>)[String(nature)] ?? "",""].filter(Boolean).join("")}>{children}</span>
}

function IntakeApp({id, initial, onHome, requirementCollections}: {id: string; initial: IntakeDraft; onHome: () => void; requirementCollections: RequirementCollection[]}) {
  const {value, field, saveError} = useSavedDraft(`intake:${id}`, () => initial, isIntakeDraft)
  const access = useAccess(), liveMode = Boolean(access?.principal && !access.preview)
  const [serverSaveError, setServerSaveError] = useState('')
  const [variant, setVariant] = useState(routeVariant)
  const [step, setStep] = field('step')
  const [scope, setScope] = field('scope')
  const [scopeMode] = field('scopeMode')
  const [scopeConfirmed, setScopeConfirmed] = field('scopeConfirmed')
  const [submitted, setSubmitted] = field('submitted')
  const [files, setFiles] = field('files')
  const typeCatalog = useRemote<RuleTypes>(liveMode ? '/rule-types' : null)
  const availableKinds = liveMode ? [...new Set(['待分类', ...(typeCatalog.value?.types ?? kinds.slice(1)), ...files.map(file => file.kind)])] : kinds
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
  const receiptId = `${liveMode ? 'PPAP' : 'DEMO'}-${id.slice(0, 8)}`
  const documents = readingDocuments(files, receiptId)
  const knownInput = sourceMode === 'demo' && scopeMatchesDemo && documents.length === files.length && files.every(file => file.kind === file.expectedKind && file.declaredRevision === file.contentRevision)
  const [previousReview] = useState(() => readDraft(`review:${id}`, isReviewDraft)?.value)
  const preservePreviousStory = Boolean(previousReview && (previousReview.sent || previousReview.submission || previousReview.receivedFiles.length || Object.values(previousReview.review).some(row => row.outcome !== 'pending' || row.reason.trim())))
  const decisionFiles = selectedFiles.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, material: file.material}))
  const decisionScope = `${scope.customer} · ${scope.supplier} · ${scope.part} · 图纸 Rev ${scope.revision} · ${scope.plant}`
  const plan = basis ? checkPlan(basis, selectedFiles, receiptId, check.mode, knownInput) : null
  const documentType = (file: Document) => file.kind === '尺寸报告' ? '全功能检验报告' : file.kind
  const liveItems = selectedFiles.flatMap(file => file.material ? [{...file.material, role: (basis?.server && documentType(file) === basis.server.body.type ? 'target' : 'reference') as Material['role'], rule_version_id: basis?.server && documentType(file) === basis.server.body.type ? basis.server.ruleId ?? null : null}] : [])
  const audit = useLiveAudit({id, registration: value, items: liveItems, ready: liveItems.length === selectedFiles.length, state: check, plan, onChange: setCheck})
  useEffect(() => {
    if (!liveMode || check.request && check.request.phase !== 'absent' || saveError) return
    const timer = setTimeout(() => {
      void request<Draft>(`/drafts/${id}`, 'PUT', {case_context: {registration: JSON.stringify(value)}, items: inputItems(liveItems), analysis_mode: check.mode})
        .then(() => setServerSaveError('')).catch(cause => setServerSaveError(message(cause)))
    }, 600)
    return () => clearTimeout(timer)
  }, [id, value, liveMode, saveError])
  const readingInput = {receiptId: submitted ? receiptId : '待登记资料', scope: scope.part, files: files.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, material: file.material, pending: !submitted})), evidence: [], clarification}
  const readingSources = documents.map(doc => ({...doc, pending: !submitted, origin: submitted ? doc.origin : '本次上传 · 待登记'}))
  let handoff: IntakeHandoff | undefined = submitted ? {
    sample: completeSample ? sampleOutlier ? 'outlier' : 'complete' : undefined,
    receiptId,
    documents,
    check: check.request?.phase === 'accepted' ? {id: check.request.runId, method: modeName(check.request.plan.mode)} : undefined,
    files: selectedFiles.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, material: file.material})),
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
  if (audit.round && handoff) handoff = {...handoff, receiptId: audit.round.id, check: {id: audit.round.id, method: modeName(audit.round.analysis_mode)}, files: roundInput(audit.round, basis, decisionScope, clarification).files, decisionInput: roundInput(audit.round, basis, decisionScope, clarification), matchesFixedReview: false}

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
    const onKey = (event: KeyboardEvent) => {if (!isPreview() || !import.meta.env.DEV) return;
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
  const upload = useLocalUploads(async incoming => {
    const mapped = localDocuments(incoming)
    if (liveMode) for (let index = 0; index < incoming.length; index++) mapped[index].material = await uploadProduct(incoming[index].file, 'target')
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

  if (reviewView && liveMode && check.request?.phase === 'accepted' && !audit.round) return <main className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6"><p role="status">{audit.roundError || '正在读取本轮审核与复核记录…'}</p><Button variant="outline" onClick={audit.refresh}>重新读取</Button><Button variant="ghost" onClick={onHome}>返回工作台</Button></main>
  if (reviewView) return <RoundReviewContext.Provider value={audit.round ? {round: audit.round, onChange: audit.setRound} : null}><ConceptApp handoff={handoff} draftId={id} onHome={onHome} changeContext={{scope, collections: requirementCollections}}/></RoundReviewContext.Provider>

  const fileSection = <section className={"min-w-0"} aria-label="本次文件">
    <div className={"flex items-end justify-between gap-4 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-normal [&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_>_span]:whitespace-nowrap max-sm:items-start"}><div><h2>本次文件</h2><p>确认资料类型、修订、归属和是否纳入本轮。登记不等于审核通过。</p></div><span>{selectedCount} / {files.length} 份</span></div>
    <div className={"border-t"}>{files.map(file => {
      const fileGaps = gapsFor(file, scope.part)
      return <article className={"pt-4.5 pr-0.5 pb-4 pl-0.5 border-b"} key={file.id}>
        <div className={"flex items-center gap-3 min-w-0 [&_>_svg]:shrink-0 [&_>_svg]:text-primary max-sm:items-start max-sm:gap-2.5 max-sm:[&_>_svg]:hidden"}>
          <Checkbox aria-label={`纳入${file.name}`} checked={file.selected} onCheckedChange={value => update(file.id, {selected: value === true})}/>
          <FileText size={18} aria-hidden="true"/>
          <div className={"grid flex-1 gap-1 min-w-0 [&_strong]:break-all [&_strong]:text-sm [&_strong]:font-semibold [&_span]:text-muted-foreground [&_span]:text-xs max-sm:basis-[55%]"}><strong>{file.name}</strong><span>{fileMeta(file)}</span></div>
          {!file.selected ? <Label nature="coverage">未纳入</Label> : fileGaps.length ? <Label nature={fileGaps[0].nature}>{fileGaps[0].title}</Label> : <Label nature="ready">已核对登记信息</Label>}
        </div>
        {file.selected && fileGaps.length > 0 && <p className={"mt-2.5 mr-0 mb-0 ml-13.5 text-muted-foreground text-xs leading-normal max-sm:ml-8"}>{fileGaps.map(gap => gap.title).join(' · ')}</p>}
        <div className={"flex flex-wrap gap-y-3 gap-x-5.5 mt-3 mr-0 mb-0 ml-13.5 max-sm:ml-8 max-sm:gap-y-2.5 max-sm:gap-x-4.5"}>
          <SourceReaderButton input={readingInput} documents={readingSources} file={{name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, material: file.material, pending: !submitted}} label="查看资料"/>
          <Button variant="ghost" type="button" onClick={() => setEditing(editing === file.id ? null : file.id)}>{editing === file.id ? '收起归属' : '核对归属'} <ChevronRight size={15}/></Button>
          {file.origin === 'demo' && (file.id === 'flow' && file.readability === 'partial' || file.id === 'dimension' && file.contentRevision !== 'B') && <Button variant="ghost" type="button" onClick={() => replace(file.id)}><RotateCcw size={14}/> {file.id === 'flow' ? '模拟登记清晰件' : '模拟登记 Rev B'}</Button>}
          {file.origin === 'local' && <Button variant="ghost" type="button" onClick={() => removeLocalFile(file.id)}>移除文件</Button>}
        </div>
        {editing === file.id && <div className={"grid grid-cols-[minmax(0,_1fr)_minmax(0,_1fr)] gap-3.5 mt-4 mr-0 mb-0 ml-13.5 p-4 border rounded-lg bg-background [&_label]:grid [&_label]:gap-1.5 [&_label]:text-muted-foreground [&_label]:text-xs [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-xs [&_p]:leading-normal max-sm:ml-8 max-sm:grid-cols-1"}>
          <label>资料类型<NativeSelect value={file.kind} onChange={event => update(file.id, {kind: event.target.value as Kind})}>{availableKinds.filter(kind => file.origin === 'local' || kind !== '待分类').map(kind => <NativeSelectOption key={kind}>{kind}</NativeSelectOption>)}</NativeSelect></label>
          {file.origin === 'local' ? <label>人工记录修订<Input value={file.declaredRevision === '未知' ? '' : file.declaredRevision} placeholder="如 B、02；留空表示未知" onChange={event => update(file.id, {declaredRevision: event.target.value})}/></label> : <label>声明修订<NativeSelect value={file.declaredRevision} onChange={event => update(file.id, {declaredRevision: event.target.value})}><NativeSelectOption value="A">Rev A</NativeSelectOption><NativeSelectOption value="B">Rev B</NativeSelectOption><NativeSelectOption value="未知">未知</NativeSelectOption></NativeSelect></label>}
          <label className={""}><Checkbox aria-label={`${file.name}属于${scope.part}`} checked={file.associated} onCheckedChange={value => update(file.id, {associated: value === true})}/> 属于{scope.part}</label>
          {file.origin === 'local' ? <label>人工核对可读性<NativeSelect value={file.readability} onChange={event => update(file.id, {readability: event.target.value as Document['readability']})}><NativeSelectOption value="unverified">尚未核对</NativeSelectOption><NativeSelectOption value="clear">可读</NativeSelectOption><NativeSelectOption value="partial">部分不可读</NativeSelectOption></NativeSelect></label> : <p>原件内容修订 Rev {file.contentRevision}。修改声明不会使旧版报告覆盖 Rev B。</p>}
        </div>}
      </article>
    })}</div>
    <p className={"flex items-center gap-1.5 mt-4 mr-0 mb-0 ml-0 text-muted-foreground text-xs"}><UploadCloud size={15}/> {sourceMode === 'demo' ? '当前是模拟资料包，文件内容与缺口均为预设。' : '文件已上传。类型、修订与可读性由本次核对记录。'}</p>
    {!submitted && <label className={"relative inline-flex items-center justify-center min-h-9.5 py-0 px-4 border rounded-lg bg-muted text-foreground text-sm font-semibold cursor-pointer mt-3 bg-background text-foreground"}>继续选择文件<input type="file" accept={fileAccept} disabled={upload.busy} multiple onChange={event => {addLocalFiles(event.target.files); event.target.value = ''}}/></label>}
    {upload.busy && <p role="status">正在保存上传原件…</p>}{(uploadError || upload.error) && <p className={"mt-2 mr-0 mb-0 ml-0 text-destructive text-xs leading-normal"} role="alert">{uploadError || upload.error}</p>}
  </section>

  const actionSection = <section className={"min-w-0"} aria-label="收件时发现的事项">
    <div className={"flex items-end justify-between gap-4 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-normal [&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_>_span]:whitespace-nowrap max-sm:items-start"}><div><h2>收件时发现</h2><p>先核对资料归属；缺口带入审核，不在收件时判定合格。</p></div><span>{gapCount} 项</span></div>
    <div className={"border-t"}>{gaps.length ? gaps.map((gap, index) => <div className={"grid grid-cols-[94px_minmax(0,_1fr)_auto] items-start gap-3.5 py-4 px-0.5 border-b [&_>_div_strong]:break-all [&_>_div_strong]:text-sm [&_>_div_strong]:font-semibold [&_>_div_p]:mt-1.5 [&_>_div_p]:mr-0 [&_>_div_p]:mb-0 [&_>_div_p]:ml-0 [&_>_div_p]:text-muted-foreground [&_>_div_p]:text-xs [&_>_div_p]:leading-normal max-sm:grid-cols-[1fr_auto] max-sm:gap-2 max-sm:[&_>_div]:min-w-0"} key={`${gap.fileId}-${index}`}>
      <Label nature={gap.nature}>{gap.title}</Label><div><strong>{byId[gap.fileId].name}</strong><p>{gap.detail}</p></div>
      <Button variant="ghost" type="button" onClick={() => {setEditing(gap.fileId); document.getElementById('intake-files')?.scrollIntoView({behavior: 'smooth', block: 'start'})}}>核对资料 <ChevronRight size={15}/></Button>
    </div>) : <div className={"flex items-center gap-2 py-5.5 px-0.5 text-foreground text-sm"}><FileCheck2 size={20}/> 当前所选资料没有预检缺口。正式审核仍可能发现内容问题。</div>}</div>
  </section>

  const coverageSection = <section className={"min-w-0"} aria-label="资料与要求覆盖">
    <div className={"flex items-end justify-between gap-4 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-normal [&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_>_span]:whitespace-nowrap max-sm:items-start"}><div><h2>本轮要求与资料</h2><p>预检只核对可用性与版本；不判断工艺、尺寸是否合格。</p></div></div>
    {sourceMode === 'demo' ? <div className={"overflow-x-auto border-t [&_>_div]:grid [&_>_div]:grid-cols-[minmax(160px,_.8fr)_minmax(180px,_1.5fr)_120px] [&_>_div]:items-center [&_>_div]:gap-3.5 [&_>_div]:min-w-132.5 [&_>_div]:py-4.5 [&_>_div]:px-1.5 [&_>_div]:border-b [&_strong]:text-sm [&_strong]:font-semibold"}>
      <div><strong>控制计划</strong><span>{byId.control.selected ? byId.control.name : '未纳入本轮'}</span><Label nature={byId.control.selected ? gapsFor(byId.control, scope.part)[0]?.nature ?? 'ready' : 'coverage'}>{byId.control.selected ? gapsFor(byId.control, scope.part)[0]?.title ?? '可供审核' : '未覆盖'}</Label></div>
      <div><strong>过程流程图</strong><span>{byId.flow.selected ? byId.flow.name : '未纳入本轮'}</span><Label nature={byId.flow.selected ? gapsFor(byId.flow, scope.part)[0]?.nature ?? 'ready' : 'coverage'}>{byId.flow.selected ? gapsFor(byId.flow, scope.part)[0]?.title ?? '可供审核' : '未覆盖'}</Label></div>
      <div><strong>Rev B 尺寸报告</strong><span>{byId.dimension.selected ? byId.dimension.name : '未纳入本轮'}</span><Label nature={byId.dimension.selected ? gapsFor(byId.dimension, scope.part)[0]?.nature ?? 'ready' : 'coverage'}>{byId.dimension.selected ? gapsFor(byId.dimension, scope.part)[0]?.title ?? '可供审核' : '未覆盖'}</Label></div>
    </div> : <div className={"overflow-x-auto border-t [&_>_div]:grid [&_>_div]:grid-cols-[minmax(160px,_.8fr)_minmax(180px,_1.5fr)_120px] [&_>_div]:items-center [&_>_div]:gap-3.5 [&_>_div]:min-w-132.5 [&_>_div]:py-4.5 [&_>_div]:px-1.5 [&_>_div]:border-b [&_strong]:text-sm [&_strong]:font-semibold"}>{(['控制计划', '过程流程图', '尺寸报告'] as Kind[]).map(kind => {
      const candidate = selectedFiles.find(file => file.kind === kind)
      return <div key={kind}><strong>{kind}</strong><span>{candidate?.name ?? '尚无已分类文件'}</span><Label nature="identity">{candidate ? '内容待审核' : '归类待核对'}</Label></div>
    })}</div>}
  </section>

  const readiness = <aside className={"sticky top-7.5 grid gap-5 p-5.5 border rounded-lg bg-background [&_>_p]:m-0 [&_>_p]:text-muted-foreground [&_>_p]:text-xs [&_>_p]:leading-normal max-lg:static"} aria-label="收件核对摘要">
    <div className={"flex items-start gap-3 [&_>_svg]:text-primary [&_>_div]:grid [&_>_div]:gap-1.5 [&_span]:text-muted-foreground [&_span]:text-xs [&_strong]:text-lg [&_strong]:font-bold [&_strong]:leading-tight"}><ClipboardCheck size={20}/><div><span>收件核对</span><strong>{canPrepare ? gapCount ? '可保留缺口完成登记' : '可以完成登记' : '请先确认类型与归属'}</strong></div></div>
    <p>{sourceMode === 'demo' ? '这是资料检查，不是质量批准。模拟审核会继续核对内容与证据。' : '登记完成后，按已确认的审核要求开始检查。'}</p>
    <div className={"grid border-t [&_>_div]:flex [&_>_div]:justify-between [&_>_div]:gap-2 [&_>_div]:py-2.5 [&_>_div]:px-0 [&_>_div]:border-b [&_>_div]:text-xs [&_span]:text-muted-foreground [&_strong]:text-right [&_strong]:font-semibold"}><div><span>本轮基准</span><strong>图纸 Rev {scope.revision} · {requirementsReady && basis ? `${basis.source.title} ${basis.source.version}` : '审核依据待确认'}</strong></div><div><span>纳入原件</span><strong>{selectedCount} 份</strong></div><div><span>资料缺口</span><strong>{gapCount} 项</strong></div></div>
    <Button variant="outline" onClick={() => go('requirements')}>{requirementsReady ? '查看审核依据' : '确认审核依据'}</Button>
    {!canPrepare && <p className={""}><ShieldAlert size={16}/> 至少纳入一份文件，确认其资料类型与事项归属。</p>}
    <Button className={""} disabled={!canPrepare} onClick={() => go('confirm')}>核对收件清单 <ArrowRight size={16}/></Button>
  </aside>

  return <div className={"flex min-h-dvh bg-background text-foreground text-base"}>
    <aside className={"sticky top-0 self-start flex flex-[0_0_224px] flex-col min-h-dvh pt-7 pr-3.5 pb-4.5 pl-3.5 border-r bg-background [&_nav]:grid [&_nav]:gap-1.5 max-xl:basis-47 max-lg:hidden"}>
      <div className={"flex items-center gap-2.5 pt-0 pr-3 pb-14 pl-3 text-lg font-bold tracking-tight [&_small]:ml-auto [&_small]:text-muted-foreground [&_small]:text-xs [&_small]:font-medium [&_small]:tracking-wide"}><div className={"grid place-items-center w-7.5 h-7.5 rounded-lg bg-muted text-foreground"}><ClipboardCheck size={19}/></div><span>PPAP</span><small>{isPreview() ? '交互预览' : '审核工作台'}</small></div>
      <div className={"pt-0 pr-3 pb-3 pl-3 text-muted-foreground text-xs font-semibold tracking-wide"}>审核团队 · {scope.customer || '新事项'}</div>
      <nav aria-label="审核流程"><Button variant="ghost" onClick={onHome}><ArrowLeft size={18}/><span>工作台</span></Button><Button variant="ghost" aria-current={step === 'scope' ? 'page' : undefined} disabled={!files.length || submitted} onClick={() => go('scope')}><Files size={18}/><span>资料归属</span></Button><Button variant="ghost" aria-current={step === 'requirements' ? 'page' : undefined} disabled={!scopeConfirmed} onClick={() => go('requirements')}><FileText size={18}/><span>审核依据</span></Button><Button variant="ghost" aria-current={step === 'upload' ? 'page' : undefined} disabled={submitted} onClick={() => go('upload')}><UploadCloud size={18}/><span>上传资料</span></Button><Button variant="ghost" aria-current={step === 'prepare' || step === 'confirm' ? 'page' : undefined} disabled={!files.length || !scopeConfirmed || submitted} onClick={() => go('prepare')}><ClipboardCheck size={18}/><span>核对收件</span></Button><Button variant="ghost" aria-current={step === 'submitted' || step === 'progress' ? 'page' : undefined} disabled={!submitted} onClick={() => go('progress')}><FileCheck2 size={18}/><span>{checkNavigation}</span></Button></nav>
      <div className={"flex items-center gap-2.5 mt-auto pt-4.5 pr-2.5 pb-1 pl-2.5 border-t"}><AccountMenu/></div>
    </aside>
    <div className={"flex-1 min-w-0"}>
      <header className={"lg:hidden flex items-center justify-between gap-3 h-15 py-0 px-10.5 border-b bg-background text-muted-foreground text-sm [&_>_div]:flex [&_>_div]:items-center [&_>_div]:gap-2.5 [&_>_div]:min-w-0 [&_strong]:overflow-hidden [&_strong]:text-foreground [&_strong]:font-semibold [&_strong]:text-ellipsis [&_strong]:whitespace-nowrap max-xl:py-0 max-xl:px-7 max-lg:h-13.5 max-lg:py-0 max-lg:px-5.5 max-sm:py-0 max-sm:px-4.5"}><div><span className={"w-2 h-2 rounded-full bg-primary"}/><span>量产批准工作台</span><span className={"text-foreground"}>/</span><strong>{scope.part || '待核对资料'}</strong></div><span className={"text-muted-foreground whitespace-nowrap max-sm:hidden"}>审核工作台</span><AccountMenu compact className={"hidden max-lg:inline-flex"}/></header>
      <div className={"hidden max-lg:flex max-lg:gap-1.5 max-lg:overflow-x-auto max-lg:py-2 max-lg:px-4.5 max-lg:border-b max-lg:bg-background"}><Button variant="ghost" onClick={onHome}>工作台</Button><Button variant="ghost" aria-current={step === 'scope' ? 'page' : undefined} disabled={!files.length || submitted} onClick={() => go('scope')}>资料归属</Button><Button variant="ghost" aria-current={step === 'requirements' ? 'page' : undefined} disabled={!scopeConfirmed} onClick={() => go('requirements')}>审核依据</Button><Button variant="ghost" aria-current={step === 'prepare' || step === 'confirm' ? 'page' : undefined} disabled={!files.length || !scopeConfirmed || submitted} onClick={() => go('prepare')}>核对收件</Button><Button variant="ghost" aria-current={step === 'submitted' || step === 'progress' ? 'page' : undefined} disabled={!submitted} onClick={() => go('progress')}>{checkNavigation}</Button></div>
      <main className={"w-full max-w-280 my-0 mx-auto pt-10.5 pr-12 pb-30 pl-12 max-xl:pr-7 max-xl:pl-7 max-lg:pt-8 max-sm:pt-6.5 max-sm:pr-4.5 max-sm:pb-29 max-sm:pl-4.5 max-w-300"}>
        {saveError && <p role="alert" className={"mt-2 mr-0 mb-0 ml-0 text-destructive text-xs leading-normal"}>{saveError}</p>}
        {step === 'scope' && <>
          <Button variant="ghost" className={"inline-flex items-center mr-0 mb-6 ml-0"} onClick={onHome}><ArrowLeft size={16}/> 工作台</Button>
          <header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl max-sm:block"}><div><h1>核对资料归属</h1><p className={"mt-3 mr-0 mb-0 ml-0 text-muted-foreground text-sm [&_span]:my-0 [&_span]:mx-1.5 [&_span]:text-muted-foreground max-sm:text-xs max-sm:leading-relaxed"}>已选择 {files.length} 份文件。请确认本轮零件、修订与审核依据。</p></div></header>
          <Collapsible className={"mb-6"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">本次文件 · {files.length} 份</Button></CollapsibleTrigger><CollapsibleContent><ul>{files.map(file => <li key={file.id}>{file.name}</li>)}</ul></CollapsibleContent></Collapsible>
          <SourceReaderButton input={readingInput} documents={readingSources} label="阅读已上传资料"/>
          <div className={"grid grid-cols-[repeat(2,_minmax(0,_1fr))] gap-4.5 w-full max-w-195 mb-4.5 [&_label]:grid [&_label]:gap-2 [&_label]:text-muted-foreground [&_label]:text-sm max-sm:grid-cols-1"}>{([{field: 'customer', label: '客户'}, {field: 'supplier', label: '供应商'}, {field: 'part', label: '零件与编号'}, {field: 'revision', label: '图纸修订'}, {field: 'plant', label: '生产地点'}] as {field: keyof CaseScope; label: string}[]).map(item => <label key={item.field}>{item.label}<Input value={scope[item.field]} onChange={event => changeScope(item.field, event.target.value)} /></label>)}</div>
          <Button className={""} disabled={!canConfirmScope || !files.length} onClick={() => {setScopeConfirmed(true); go('requirements')}}>确认归属，选择审核依据 <ArrowRight size={16}/></Button>
        </>}
        {step === 'requirements' && <Requirements basis={basis} scopeKey={scopeKey(scope)} scope={scope} collections={requirementCollections} context={`${scope.part} · 图纸 Rev ${scope.revision} · ${scope.customer} / ${scope.supplier}`} readonly={submitted} onBack={() => go(submitted ? 'progress' : 'scope')} onChange={next => {setBasis(next); setAcknowledged(false)}} onConfirm={next => {setBasis(next); setScope(current => ({...current, requirements: `${next.source.title} ${next.source.version}`})); setAcknowledged(false); go('prepare')}}/>}
        {step === 'upload' && <>
          <Button variant="ghost" className={"inline-flex items-center mr-0 mb-6 ml-0"} onClick={() => go('scope')}><ArrowLeft size={16}/> 返回事项范围</Button>
          <header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl max-sm:block"}><div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>02 · 建立资料包</div><h1>上传资料</h1><p className={"mt-3 mr-0 mb-0 ml-0 text-muted-foreground text-sm [&_span]:my-0 [&_span]:mx-1.5 [&_span]:text-muted-foreground max-sm:text-xs max-sm:leading-relaxed"}>{scope.supplier} <span>·</span> {scope.part} <span>·</span> 图纸 Rev {scope.revision}</p></div></header>
          <div className={"grid justify-items-start gap-3.5 w-full max-w-170 p-9 border border-dashed rounded-lg bg-background [&_>_svg]:text-primary [&_h2]:m-0 [&_h2]:text-2xl [&_h2]:leading-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-relaxed [&_small]:text-muted-foreground [&_small]:text-xs [&_small]:leading-normal max-sm:p-6"}><UploadCloud size={30} aria-hidden="true"/><h2>先添加收到的文件</h2><p>供应商从系统外发送资料。审核员选择文件后，再逐份核对类型、修订和归属。</p>
            <label className={""}>选择文件<input type="file" accept={fileAccept} disabled={upload.busy} multiple onChange={event => {addLocalFiles(event.target.files); event.target.value = ''}}/></label>
            <small>支持 Excel、Word、PDF、图片和文本，单份最大 64 MB。原件仅保存在本机。</small>
            {upload.busy && <p role="status">正在保存本机原件…</p>}{(uploadError || upload.error) && <p className={"mt-2 mr-0 mb-0 ml-0 text-destructive text-xs leading-normal"} role="alert">{uploadError || upload.error}</p>}
          </div>
          {scopeMatchesDemo && <div className={"[&_small]:text-muted-foreground [&_small]:text-xs [&_small]:leading-normal flex flex-wrap items-center gap-y-2 gap-x-4 w-full max-w-170 mt-5.5 text-muted-foreground text-sm [&_small]:basis-full"}><span>需要查看完整模拟审核旅程？</span><Button variant="ghost" type="button" onClick={startDemo}>加载示例资料包 <ArrowRight size={15}/></Button><small>仅用于演示，不是第二个真实收件入口。</small></div>}
        </>}
        {step === 'prepare' && <>
          <Button variant="ghost" className={"inline-flex items-center mr-0 mb-6 ml-0"} onClick={() => go('upload')}><ArrowLeft size={16}/> 返回上传</Button>
          <header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl max-sm:block"}><div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>03 · 上传后核对</div><h1>核对收件</h1><p className={"mt-3 mr-0 mb-0 ml-0 text-muted-foreground text-sm [&_span]:my-0 [&_span]:mx-1.5 [&_span]:text-muted-foreground max-sm:text-xs max-sm:leading-relaxed"}>{scope.supplier} <span>·</span> {scope.part} <span>·</span> 图纸 Rev {scope.revision} <span>·</span> {scope.plant}</p></div><span className={"self-center text-muted-foreground text-sm whitespace-nowrap max-sm:mt-1"}>{scope.requirements}</span></header>
          {variant === 'file' && <div className={"grid grid-cols-[minmax(0,_1fr)_296px] items-start gap-8 max-xl:grid-cols-[minmax(0,_1fr)_270px] max-xl:gap-5.5 max-lg:grid-cols-1"}><div id="intake-files">{fileSection}</div>{readiness}</div>}
          {variant === 'action' && <div className={"grid grid-cols-[minmax(0,_1fr)_296px] items-start gap-8 max-xl:grid-cols-[minmax(0,_1fr)_270px] max-xl:gap-5.5 max-lg:grid-cols-1"}><div>{actionSection}<div id="intake-files" className={"mt-10.5"}>{fileSection}</div></div>{readiness}</div>}
          {variant === 'coverage' && <>{coverageSection}<div className={"grid grid-cols-[minmax(0,_1fr)_296px] items-start gap-8 max-xl:grid-cols-[minmax(0,_1fr)_270px] max-xl:gap-5.5 max-lg:grid-cols-1 mt-10.5"}><div id="intake-files">{fileSection}</div>{readiness}</div></>}
        </>}
        {step === 'confirm' && <>
          <Button variant="ghost" className={"inline-flex items-center mr-0 mb-6 ml-0"} onClick={() => go('prepare')}><ArrowLeft size={16}/> 返回文件清单</Button>
          <header className={"flex items-start justify-between gap-6 mb-7.5 [&_h1]:m-0 [&_h1]:text-2xl [&_h1]:leading-tight [&_h1]:font-bold [&_h1]:tracking-tight max-sm:items-start max-sm:gap-3 max-sm:mb-6.5 max-sm:[&_h1]:text-3xl max-sm:block"}><div><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>登记前确认</div><h1>确认登记</h1><p className={"mt-3 mr-0 mb-0 ml-0 text-muted-foreground text-sm [&_span]:my-0 [&_span]:mx-1.5 [&_span]:text-muted-foreground max-sm:text-xs max-sm:leading-relaxed"}>{scope.supplier} · {scope.part} · 图纸 Rev {scope.revision} · {scope.requirements}</p></div></header>
          <BasisSummary basis={basis}/>
          <div className={"grid grid-cols-[minmax(0,_1fr)_minmax(0,_1fr)] gap-9.5 max-lg:grid-cols-1 max-lg:gap-7.5"}><section className={"min-w-0"}><div className={"flex items-end justify-between gap-4 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-normal [&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_>_span]:whitespace-nowrap max-sm:items-start"}><div><h2>纳入本轮的原件</h2><p>核对纳入本轮的文件和版本。</p></div><span>{selectedCount} 份</span></div>
            <div className={"border-t [&_>_div]:flex [&_>_div]:items-start [&_>_div]:gap-2.5 [&_>_div]:py-4.5 [&_>_div]:px-0 [&_>_div]:border-b [&_svg]:shrink-0 [&_svg]:text-primary [&_>_div_>_div]:grid [&_>_div_>_div]:gap-1.5 [&_strong]:break-all [&_strong]:text-sm [&_strong]:font-semibold [&_span]:text-muted-foreground [&_span]:text-xs"}>{selectedFiles.map(file => <div key={file.id}><FileText size={18}/><div><strong>{file.name}</strong><span>{fileMeta(file)}</span></div></div>)}</div>
          </section><section className={"min-w-0"}><div className={"flex items-end justify-between gap-4 mb-4 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1 [&_h2]:ml-0 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:tracking-tight [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-normal [&_>_span]:text-muted-foreground [&_>_span]:text-xs [&_>_span]:whitespace-nowrap max-sm:items-start"}><div><h2>收件时已知的缺口</h2><p>如供应商随件说明，可记录原话；没有说明也可继续检查。</p></div><span>{gapCount} 项</span></div>
            {filesWithGaps.length ? filesWithGaps.map(id => <label key={id} className={"grid gap-1.5 py-3.5 px-0 border-t [&_strong]:break-all [&_strong]:text-sm [&_strong]:font-semibold [&_span]:text-muted-foreground [&_span]:text-xs"}><strong>{byId[id].selected ? byId[id].name : `${byId[id].expectedKind}（未纳入）`}</strong><span>{gaps.filter(gap => gap.fileId === id).map(gap => gap.title).join(' · ')}</span><Textarea aria-label={`${byId[id].name}的随件说明`} placeholder="如有供应商说明，在此记录；请勿代写结论" value={notes[id] ?? ''} onChange={event => {setNotes(current => ({...current, [id]: event.target.value})); setAcknowledged(false)}}/></label>) : <p className={"flex items-center gap-2 py-3.5 px-0 text-foreground text-sm"}><Check size={17}/> 当前资料无登记缺口；正式审核仍可能发现问题。</p>}
          </section></div>
          <section className={"flex items-center justify-between gap-5 mt-8.5 py-5.5 px-0 border-t [&_label]:flex [&_label]:items-center [&_label]:gap-2.5 [&_label]:text-sm [&_label]:leading-normal max-sm:items-stretch max-sm:flex-col"}><label><Checkbox aria-label="确认收件清单与已知缺口" checked={acknowledged} onCheckedChange={value => setAcknowledged(value === true)}/><span>我已核对本次文件清单和已知缺口。登记后确认检查范围与方式。</span></label><Button className={""} disabled={!canSubmit} onClick={() => go('submitted')}>完成收件登记 <ArrowRight size={16}/></Button></section>
        </>}
        {(step === 'submitted' || step === 'progress' && check.request?.phase !== 'accepted') && (plan && requirementsReady ? <><CheckPreparation caseId={id} state={check} plan={plan} onChange={setCheck} onContinue={() => go('progress')} onHome={onHome} saveError={saveError || serverSaveError} live={audit.live}/>{!audit.enabled && !knownInput && handoff?.decisionInput && <section className={"grid justify-items-start w-full max-w-145 mt-15 mr-auto mb-0 ml-auto [&_h1]:m-0 [&_h1]:text-3xl [&_h1]:font-bold [&_h1]:tracking-tight [&_p]:mt-4 [&_p]:mr-0 [&_p]:mb-7 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-relaxed [&_>_div:nth-last-of-type(1)]:flex [&_>_div:nth-last-of-type(1)]:items-start [&_>_div:nth-last-of-type(1)]:gap-2.5 [&_>_div:nth-last-of-type(1)]:w-full [&_>_div:nth-last-of-type(1)]:py-4.5 [&_>_div:nth-last-of-type(1)]:px-0 [&_>_div:nth-last-of-type(1)]:border-t [&_>_div:nth-last-of-type(1)]:border-b [&_>_div:nth-last-of-type(1)]:text-sm [&_>_div:nth-last-of-type(1)_svg]:shrink-0 [&_>_div:nth-last-of-type(1)_svg]:text-primary [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:mt-5.5 [&_a]:text-primary [&_a]:text-sm [&_a]:font-semibold max-sm:mt-7 max-sm:[&_h1]:text-2xl"}><h2>也可以由人工核对</h2><p>阅读本次原件，逐条记录证据和判断，不启动机器检查。</p><Button variant="outline" onClick={() => setReviewView(true)}>开始人工核对<ArrowRight/></Button></section>}</> : <section className={"grid justify-items-start w-full max-w-145 mt-15 mr-auto mb-0 ml-auto [&_h1]:m-0 [&_h1]:text-3xl [&_h1]:font-bold [&_h1]:tracking-tight [&_p]:mt-4 [&_p]:mr-0 [&_p]:mb-7 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-relaxed [&_>_div:nth-last-of-type(1)]:flex [&_>_div:nth-last-of-type(1)]:items-start [&_>_div:nth-last-of-type(1)]:gap-2.5 [&_>_div:nth-last-of-type(1)]:w-full [&_>_div:nth-last-of-type(1)]:py-4.5 [&_>_div:nth-last-of-type(1)]:px-0 [&_>_div:nth-last-of-type(1)]:border-t [&_>_div:nth-last-of-type(1)]:border-b [&_>_div:nth-last-of-type(1)]:text-sm [&_>_div:nth-last-of-type(1)_svg]:shrink-0 [&_>_div:nth-last-of-type(1)_svg]:text-primary [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:mt-5.5 [&_a]:text-primary [&_a]:text-sm [&_a]:font-semibold max-sm:mt-7 max-sm:[&_h1]:text-2xl"}><h1>审核依据尚未确认</h1><p>没有确认的要求快照，不能开始检查。</p><Button onClick={() => go('requirements')}>查看审核依据</Button></section>)}
        {step === 'progress' && check.request?.phase === 'accepted' && (audit.enabled && !audit.round ? <p role="status">正在读取本轮审核…{audit.roundError}<Button variant="ghost" onClick={audit.refresh}>重新读取</Button></p> : (knownInput || Boolean(audit.round)) && requirementsReady ? <ProgressPrototype round={audit.round} remoteError={audit.roundError} onRefresh={audit.refresh} title={scope.part || selectedFiles[0]?.name} context={decisionScope} checkRequest={check.request} onCheckPlan={() => go('submitted')} snapshot={{fileCount: selectedCount, hasFlow: Boolean(byId.flow?.selected), hasControl: Boolean(byId.control?.selected), flowUnreadable: Boolean(byId.flow?.selected && byId.flow.readability === 'partial'), dimensionRevision: byId.dimension?.selected ? byId.dimension.contentRevision : null}} completedEvidence={handoff?.decisionInput?.evidence} clarification={clarification} answer={clarificationDraft ?? ''} onAnswerChange={setClarificationDraft} onClarification={setClarification} onOpenReview={() => setReviewView(true)} sameCase={submitted} savedScenario={progressScenario} onScenario={setProgressScenario} basis={basis} receiptId={handoff?.receiptId ?? `DEMO-${id.slice(0, 8)}`}/> : <div className={"grid justify-items-start w-full max-w-145 mt-15 mr-auto mb-0 ml-auto [&_h1]:m-0 [&_h1]:text-3xl [&_h1]:font-bold [&_h1]:tracking-tight [&_p]:mt-4 [&_p]:mr-0 [&_p]:mb-7 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_p]:leading-relaxed [&_>_div:nth-last-of-type(1)]:flex [&_>_div:nth-last-of-type(1)]:items-start [&_>_div:nth-last-of-type(1)]:gap-2.5 [&_>_div:nth-last-of-type(1)]:w-full [&_>_div:nth-last-of-type(1)]:py-4.5 [&_>_div:nth-last-of-type(1)]:px-0 [&_>_div:nth-last-of-type(1)]:border-t [&_>_div:nth-last-of-type(1)]:border-b [&_>_div:nth-last-of-type(1)]:text-sm [&_>_div:nth-last-of-type(1)_svg]:shrink-0 [&_>_div:nth-last-of-type(1)_svg]:text-primary [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:mt-5.5 [&_a]:text-primary [&_a]:text-sm [&_a]:font-semibold max-sm:mt-7 max-sm:[&_h1]:text-2xl"}><div className={"mb-2 text-primary text-xs font-bold tracking-wide"}>模拟检查状态</div><h1>未取得可验证的检查结果</h1><p>已保存本次请求和资料记录。此组合没有模拟执行结果，所有适用要求仍未覆盖。</p><div><FileQuestion size={18}/><span>原型不解析真实文件，不能把登记信息当作审核证据。</span></div><BasisSummary basis={basis}/>{handoff?.decisionInput && <Button onClick={() => setReviewView(true)}>转人工核对<ArrowRight/></Button>}<Button variant="outline" onClick={() => go('submitted')}>查看本次检查方案</Button></div>)}
      </main>
    </div>
    {isPreview() && import.meta.env.DEV && step === 'prepare' && <aside className={"fixed z-10 bottom-5 left-[50%] flex items-center gap-2.5 p-1.5 border rounded-lg bg-muted shadow-sm text-foreground text-xs whitespace-nowrap max-sm:bottom-3.5"} aria-label="准备页方案切换"><Button variant="ghost" aria-label="上一种布局" onClick={() => selectVariant(variants[(variants.findIndex(item => item.id === variant) + variants.length - 1) % variants.length].id)}><ArrowLeft size={16}/></Button><span>布局方案　{variants.findIndex(item => item.id === variant) + 1} / 3 · {variants.find(item => item.id === variant)?.label}</span><Button variant="ghost" aria-label="下一种布局" onClick={() => selectVariant(variants[(variants.findIndex(item => item.id === variant) + 1) % variants.length].id)}><ArrowRight size={16}/></Button></aside>}
    <span className="sr-only" aria-live="polite">本轮状态：{step} · {selectedCount} 份资料 · {gapCount} 项缺口</span>
  </div>
}

export function JourneyApp() {
  const access = useAccess(), preview = isPreview()
  const library = useSavedDraft('requirement-library', preview ? initialLibrary : () => ({collections: []}), isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const serverDrafts = useRemote<DraftList>(preview ? null : '/drafts?limit=50')
  const serverRounds = useRemote<RoundList>(preview ? null : '/rounds?limit=50')
  const [remoteError, setRemoteError] = useState('')
  useEffect(() => {
    if (preview) return
    void loadRuleCollections().then(rows => setCollections(current => [...current.filter(item => item.draft), ...rows.filter(row => !current.some(item => item.draft && item.versions.some(version => version.id === row.id)))])).catch(cause => setRemoteError(message(cause)))
  }, [preview])
  const [active, setActive] = useState<{id: string; initial: IntakeDraft; missing?: boolean} | null>(() => {
    const query = new URLSearchParams(window.location.search)
    const id = query.get('case') || query.get('draft') || query.get('round')
    if (id) return {id, initial: freshDraft(), missing: !readDraft(`intake:${id}`, isIntakeDraft)}
    if (preview && query.get('step') === 'progress') return {id: 'progress-example', initial: {...freshDraft(initialFiles, true), step: 'progress', submitted: true, scopeConfirmed: true}}
    return null
  })
  const drafts = listDrafts('intake:', isIntakeDraft)
  const localItems: WorkItem[] = drafts.map(({key, value: draft, updatedAt}) => {
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
  const items: WorkItem[] = preview ? localItems : [
    ...(serverDrafts.value?.drafts.map(draft => ({id: draft.id, title: draft.items.map(item => item.original_filename).join('、'), context: `${draft.items.length} 份资料`, state: '待核对', next: '继续核对', detail: '继续确认资料归属与本次审核要求。', received: false, updatedAt: draft.updated_at})) ?? []),
    ...(serverRounds.value?.rounds.map(round => ({id: round.id, title: round.inputs.filter(item => item.role === 'target').map(item => item.original_filename).join('、'), context: `${modeName(round.analysis_mode)} · ${dateLabel(round.created_at)}`, state: executionLabels[round.execution] || round.execution, next: round.execution === 'completed' ? '继续复核' : '查看进度', detail: `人工复核 ${round.reviewed_count} 项 · 机器结论 ${Object.values(round.result_counts).reduce((a, b) => a + b, 0)} 项`, received: true, updatedAt: round.created_at || ''})) ?? []),
  ]
  async function openServer(requestedId: string) {
    try {
      let round: Round | null = null, draft: Draft | null = null
      if (serverRounds.value?.rounds.some(row => row.id === requestedId) || new URLSearchParams(location.search).get('round') === requestedId) round = await request<Round>(`/rounds/${requestedId}`)
      else {
        draft = await request<Draft>(`/drafts/${requestedId}`)
        if (draft.status === 'accepted') round = await request<Round | null>(`/drafts/${requestedId}/round`)
      }
      const source = round ?? draft!
      let snapshot = freshDraft()
      const registration = source.case_context?.registration
      if (registration) {const parsed: unknown = JSON.parse(registration); if (isIntakeDraft(parsed)) snapshot = parsed}
      const materials = round?.inputs ?? draft!.items
      snapshot.files = materials.map(material => snapshot.files.find(file => file.material?.file_version_id === material.file_version_id) ?? {id: material.file_version_id, material, name: material.original_filename, origin: 'local', kind: material.rule?.body.kind === 'engine' ? material.rule.body.type === '全功能检验报告' ? '尺寸报告' : kinds.includes(material.rule.body.type as Kind) ? material.rule.body.type as Kind : '待分类' : '待分类', expectedKind: '待分类', declaredRevision: '未知', contentRevision: '未解析', sourceVersion: 1, selected: true, associated: Boolean(round), readability: 'unverified'})
      if (round) {
        if (!snapshot.basis) {
          const bound = round.inputs.find(item => item.rule)?.rule
          if (bound?.body.kind === 'engine') {
            const rule = await request<RuleDetail>(`/rules/${bound.id}`)
            const base = localBasis(rule.title)
            if (rule.source_file_id && rule.source_file_version_id) base.server = {body: bound.body, ruleId: bound.id, material: {file_id: rule.source_file_id, file_version_id: rule.source_file_version_id, original_filename: rule.title, role: 'reference', rule_version_id: null}}
            const parsed = parsedBasis(base, {title: bound.title, body: bound.body, source_file_version_id: rule.source_file_version_id || ''})
            parsed.clauses = parsed.clauses.map(row => ({...row, checked: true, applicability: 'apply'}))
            snapshot.basis = confirmBasis(parsed, scopeKey(snapshot.scope), '本轮冻结规则')
          }
        }
        const current = snapshot.check ?? initialCheck(), plan = snapshot.basis ? checkPlan(snapshot.basis, snapshot.files, round.id, round.analysis_mode, false) : {key: round.id, mode: round.analysis_mode, rows: [], estimate: {low: 0, high: 0, minutes: ''}, receipt: round.id, basis: '本轮冻结规则', files: materials.map(item => item.original_filename)}
        snapshot = {...snapshot, submitted: true, scopeConfirmed: true, step: 'progress', reviewView: false, check: {...current, mode: round.analysis_mode, request: {id: round.id, runId: round.id, plan, attempt: 1, at: round.created_at || '', phase: 'accepted', remote: 'accepted', queryError: false}}}
      }
      const actualId = round?.source_draft_id || draft?.id || requestedId
      setActive({id: actualId, initial: snapshot, missing: false}); setRemoteError('')
      const url = new URL(location.href); url.search = ''; url.searchParams.set('case', actualId); history.replaceState(null, '', url)
    } catch (cause) {setRemoteError(message(cause))}
  }
  useEffect(() => {if (!preview && active?.missing) void openServer(active.id)}, [preview, active?.id, active?.missing])
  function open(id: string, initial?: IntakeDraft, owner?: import('./usage-state').CheckOwner) {
    if (!preview && !initial) {void openServer(id); return}
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
  if (active?.missing && !preview) return <main className={"flex min-h-dvh flex-col items-center justify-center gap-4 p-6"}><p role="status">{remoteError || '正在读取审核事项…'}</p><Button variant="outline" onClick={() => void openServer(active.id)}>重新读取</Button><Button variant="ghost" onClick={home}>返回工作台</Button></main>
  if (active?.missing) return <main className={"flex min-h-dvh bg-background text-foreground text-base items-center justify-center flex-col gap-3.5 py-10 px-6 text-center [&_h1]:text-2xl [&_h1]:font-semibold [&_p]:max-w-105 [&_p]:text-muted-foreground [&_p]:leading-relaxed"}><FileQuestion size={28} aria-hidden="true"/><h1>暂时无法打开此事项</h1><p>此浏览器没有可读取的记录。请返回工作台选择已有事项。原型记录不跨浏览器同步；打开链接不会新建事项。</p><Button className={""} onClick={home}>返回工作台<ArrowRight/></Button><AccountMenu compact/></main>
  if (active) return <IntakeApp key={active.id} id={active.id} initial={active.initial} onHome={home} requirementCollections={collections}/>
  return <>{(remoteError || serverDrafts.error || serverRounds.error) && <p role="alert" className={"p-4 text-sm text-destructive"}>{remoteError || serverDrafts.error || serverRounds.error}</p>}<TaskWorkbench items={items} onOpen={id => open(id)} usage={<UsageWorkspace onOpen={(id, owner) => open(id, undefined, owner)}/>} requirements={<RequirementLibraryPanel collections={collections} onChange={setCollections} saveError={library.saveError} uses={drafts.flatMap(({key, value}) => value.basis ? [{id: key.slice('intake:'.length), title: [value.scope.part, value.scope.supplier].filter(Boolean).join(' · '), basis: value.basis, submitted: value.submitted}] : [])} onOpen={id => open(id)}/>} onDemo={(kind = 'issues') => open(crypto.randomUUID(), freshDraft((kind === 'complete' ? completeFiles : kind === 'outlier' ? outlierFiles : initialFiles).map(file => ({...file})), true))} onFiles={async files => {
    try {
      const mapped = localDocuments(files)
      const server = preview ? null : await request<Draft>('/drafts', 'POST', {})
      if (server) for (let index = 0; index < files.length; index++) mapped[index].material = await uploadProduct(files[index].file, 'target')
      const initial = freshDraft(mapped)
      if (server) await request<Draft>(`/drafts/${server.id}`, 'PUT', {items: inputItems(mapped.map(file => file.material!)), case_context: {registration: JSON.stringify(initial)}})
      open(server?.id || crypto.randomUUID(), initial)
      return null
    } catch (cause) {return message(cause)}
  }}/></>
}

export const recordValidators = {intake: isIntakeDraft, review: isReviewDraft, decision: validDraft, changes: isChangeState, reassessment: validChecks, 'requirement-library': isRequirementLibrary}
export default function ProductJourney() {
  return <ProductAccess recordValidators={recordValidators}><JourneyApp/></ProductAccess>
}
