import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
// Single-role product journey. Synthetic records are limited to explicit preview entrypoints.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, FileQuestion, MoreHorizontal, Plus, UploadCloud} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../components/ui/dropdown-menu'
import {Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle} from '../components/ui/sheet'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import '../index.css'
import {ProgressPrototype} from './progress'
import {ConceptApp, isReviewDraft, savedReviewSummary, type IntakeHandoff} from './concept'
import {draftPrefix, isObject, listDrafts, readDraft, useSavedDraft} from './saved-draft'
import {ProductAccess, AccountMenu, isPreview, useAccess} from './access'
import {dateLabel, executionLabels, request, type Draft, type DraftCreateRequest, type FileVersion, type Material, type Round, type RuleDetail} from '../api'
import {message, useRemote, inputItems} from './remote'
import {loadRuleCollections, parsedBasis, roundInput} from './product'
import {useLiveAudit} from './live-audit'
import type {RuleTypes} from '../api'
import {RoundReviewContext} from './review-record'
import {localBasis} from './requirements-state'
import {TaskWorkbench} from './task-workbench'
import type {WorkItem} from './work-items-table'
import {useWorkbenchData} from './workbench-data'
import {Requirements, type RequirementCatalogRead} from './requirements'
import {basisConfirmed, basisMatchesFixedReview, isRequirementBasis, type RequirementBasis} from './requirements-state'
import {completeSampleEvidence, decisionSummary, unexecutedEvidence, validDraft} from './decision'
import {demoEvidence} from './demo-check'
import {initialLibrary, isRequirementLibrary, type RequirementCollection} from './requirement-library-state'
import {RequirementLibraryPanel} from './requirement-library'
import {isChangeState, savedChangeSummary} from './change-review-state'
import {SourceReaderButton} from './source-reader'
import {demoDocument, type DemoFlavor} from './source-documents'
import {CheckPreparation} from './check-preparation'
import {checkPlan, checkSummary, initialCheck, modeName, recoverCheck, startCheck, validCheck, type CheckPreparationState} from './check-preparation-state'
import {UsageWorkspace} from './usage'
import {validChecks} from './reassessment'
import {fileAccept, optionalLocalFile, readLocalFile, useLocalUploads, type LocalUpload} from './local-files'
import {FileCollection, fileReferences} from './file-collection'
import {optionalUploadReceipt, readDocumentUpload, transferDocuments, type UploadDocument} from './upload-files'

type Step = 'cases' | 'scope' | 'requirements' | 'upload' | 'prepare' | 'confirm' | 'submitted' | 'progress'
type FileKey = string
type Kind = string
type Document = UploadDocument & {
  origin: 'demo' | 'local'
  kind: Kind
  expectedKind: Kind
  declaredRevision: string
  contentRevision: string
  sourceVersion: number
  selected: boolean
  associated: boolean
  readability: 'clear' | 'partial' | 'unverified'
  inheritedBinding?: boolean
}
type Gap = {fileId: FileKey; title: string; detail: string; nature: 'quality' | 'coverage' | 'identity'}
type CaseScope = {customer: string; supplier: string; part: string; revision: string; plant: string; requirements: string}
const scopeKey = ({customer, supplier, part, revision, plant}: CaseScope) => JSON.stringify({customer, supplier, part, revision, plant})
const demoScope: CaseScope = {customer: '北辰汽车', supplier: '明川精密', part: '支架 P-204', revision: 'B', plant: '苏州工厂', requirements: '客户要求 v2'}
const emptyScope: CaseScope = {customer: '', supplier: '', part: '', revision: '', plant: '', requirements: ''}

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
  return {step: 'prepare', scope: demo ? {...demoScope} : {...emptyScope}, scopeMode: demo ? 'demo' : 'new', scopeConfirmed: false, submitted: false, files, sourceMode: demo ? 'demo' : files.length ? 'local' : null, notes: {}, acknowledged: false, clarification: null, reviewView: false, progressScenario: 'running', check: initialCheck()}
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
      && typeof file.selected === 'boolean' && typeof file.associated === 'boolean' && (file.inheritedBinding === undefined || typeof file.inheritedBinding === 'boolean') && optionalLocalFile(file.localFile) && optionalUploadReceipt(file.uploadReceipt, file.localFile))
}
function localDocuments(incoming: LocalUpload[]): Document[] {
  return incoming.map(({file, ref}) => ({id: ref.id, localFile: ref, origin: 'local', name: file.name, kind: '待分类', expectedKind: '待分类', declaredRevision: '未知', contentRevision: '未解析', sourceVersion: 1, selected: true, associated: false, readability: 'unverified'}))
}
function draftMaterials(files: Document[], basis: RequirementBasis | null) {
  return files.filter(file => file.selected).flatMap(file => {
    if (file.inheritedBinding && file.material) return [file.material]
    const target = basis?.server && (file.kind === '尺寸报告' ? '全功能检验报告' : file.kind) === basis.server.body.type
    return file.material ? [{...file.material, role: (target ? 'target' : 'reference') as Material['role'], rule_version_id: target ? basis.server!.ruleId ?? null : null}] : []
  })
}
function removeFromDraft(files: Document[], id: string) {
  return files.flatMap(file => file.id !== id ? [file] : file.uploadReceipt && !file.material ? [{...file, selected: false}] : [])
}

function routeStep(): Step {
  const value = new URLSearchParams(window.location.search).get('step')
  return value === 'scope' || value === 'requirements' || value === 'upload' || value === 'prepare' || value === 'confirm' || value === 'progress' ? value : 'cases'
}

type CaseTarget = {id: string; kind: 'case' | 'round'}
function routeCase(params = new URLSearchParams(location.search)): CaseTarget | null {
  const round = params.get('round')
  if (round) return {id: round, kind: 'round'}
  const id = params.get('case') || params.get('draft')
  return id ? {id, kind: 'case'} : null
}
function caseHref(target: CaseTarget, owner?: import('./usage-state').CheckOwner) {
  const url = new URL(location.href), id = target.id
  url.search = ''; url.searchParams.set(target.kind, id)
  if (owner) {
    const decisionId = owner.kind === 'reassessment' ? readDraft(`reassessment:${id}`, validChecks)?.value.rounds[owner.roundId]?.receiptSource?.decisionId : undefined
    const changeId = decisionId?.startsWith(`${id}:change:`) ? decisionId.slice(`${id}:change:`.length) : undefined
    url.searchParams.set('resume', owner.kind === 'changes' || changeId ? 'changes' : decisionId ? 'decision' : 'delta')
    url.searchParams.set('checkRound', owner.roundId)
    if (changeId) url.searchParams.set('decisionRound', changeId)
  } else if (target.kind === 'case') {
    if (savedChangeSummary(id)) url.searchParams.set('resume', 'changes')
    else if (decisionSummary(id)) url.searchParams.set('resume', 'decision')
  }
  return `${url.pathname}${url.search}${url.hash}`
}
function restoreIntake(saved: IntakeDraft, initial: IntakeDraft): IntakeDraft {
  const accepted = initial.check?.request?.phase === 'accepted'
  const sameRound = accepted && saved.check?.request?.runId === initial.check?.request?.runId
  const restored = accepted ? {...initial, ...(sameRound ? {clarification: saved.clarification, clarificationDraft: saved.clarificationDraft, progressScenario: saved.progressScenario} : {})} : saved
  const target = routeStep(), query = new URLSearchParams(location.search)
  const review = query.has('clause') || query.has('stage') || query.has('resume')
  return {...restored, step: target === 'cases' ? restored.step : target, reviewView: target !== 'cases' && !review ? false : restored.reviewView}
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

// Reference: Stitch projects/13261964020862199279/screens/0a13f18b48164253b361644d2c02d6a0.
function IntakeApp({id, initial, initialUploadError = '', onHome, onPrepareRound, requirementCollections, catalog}: {id: string; initial: IntakeDraft; initialUploadError?: string; onHome: () => void; onPrepareRound: (round: Round, snapshot: IntakeDraft) => Promise<void>; requirementCollections: RequirementCollection[]; catalog: RequirementCatalogRead}) {
  const {value, field, saveError, update} = useSavedDraft(`intake:${id}`, () => initial, isIntakeDraft, saved => restoreIntake(saved, initial))
  const access = useAccess(), liveMode = Boolean(access?.principal && !access.preview)
  const [serverSaveError, setServerSaveError] = useState(initialUploadError)
  const savingDraft = useRef<Promise<unknown>>(Promise.resolve())
  const latestDraft = useRef(value); latestDraft.current = value
  const mounted = useRef(true), transferBusy = useRef(false)
  const [readingUpload, setReadingUpload] = useState<string | null>(null)
  useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
  const initialRegistration = useRef(JSON.stringify(value))
  const [savedStep, setStep] = field('step')
  const [scope, setScope] = field('scope')
  const [scopeMode] = field('scopeMode')
  const [, setScopeConfirmed] = field('scopeConfirmed')
  const [submitted, setSubmitted] = field('submitted')
  const [files, setFiles] = field('files')
  const [sourceMode] = field('sourceMode')
  const [notes, setNotes] = field('notes')
  const [, setAcknowledged] = field('acknowledged')
  const [clarification, setClarification] = field('clarification')
  const [clarificationDraft, setClarificationDraft] = field('clarificationDraft')
  const [reviewView, setReviewView] = field('reviewView')
  const [progressScenario, setProgressScenario] = field('progressScenario')
  const [savedBasis, setBasis] = field('basis')
  const [savedCheck, setCheck] = field('check')
  const [uploadError, setUploadError] = useState('')
  const [preparing, setPreparing] = useState(false), [prepareError, setPrepareError] = useState('')
  const preparingRound = useRef(false)
  const [infoFile, setInfoFile] = useState<string | null>(null), fileInfoReturn = useRef('')
  const picker = useRef<HTMLInputElement>(null)
  const check = savedCheck ?? initialCheck()
  const basis = savedBasis ?? null
  const accepted = check.request?.phase === 'accepted'
  const wasAccepted = useRef(accepted)
  const frozen = Boolean(check.request && check.request.phase !== 'absent')
  // Older saved positions reopen in the same preparation workspace.
  const step = savedStep === 'requirements' ? 'requirements' : savedStep === 'progress' && accepted ? 'progress' : 'prepare'
  const typeCatalog = useRemote<RuleTypes>(liveMode ? '/rule-types' : null)
  const availableKinds = liveMode ? [...new Set(['待分类', ...(typeCatalog.value?.types ?? kinds.slice(1)), ...files.map(file => file.kind)])] : kinds
  const requirementsReady = accepted || basisConfirmed(basis, scopeKey(scope))
  const scopeMatchesDemo = scopeMode === 'demo' && scopeKey(scope) === scopeKey(demoScope)
  const selectedFiles = files.filter(file => file.selected)
  const confirmedFiles = selectedFiles.map(file => ({...file, associated: true}))
  const scopeFields = [{field: 'customer', label: '客户'}, {field: 'supplier', label: '供应商'}, {field: 'part', label: '零件与编号'}, {field: 'revision', label: '图纸修订'}, {field: 'plant', label: '生产地点'}] as const
  const missingScope = scopeFields.filter(item => !scope[item.field].trim())
  const gaps = files.flatMap(file => file.selected ? gapsFor({...file, associated: true}, scope.part) : [missingGap(file)])
  const receiptId = `${liveMode ? 'PPAP' : 'DEMO'}-${id.slice(0, 8)}`
  const documents = readingDocuments(files, receiptId)
  const knownInput = sourceMode === 'demo' && scopeMatchesDemo && documents.length === files.length && files.every(file => file.kind === file.expectedKind && file.declaredRevision === file.contentRevision)
  const completeSample = sourceMode === 'demo' && scopeMatchesDemo && (matchesSample(confirmedFiles, completeFiles) || matchesSample(confirmedFiles, outlierFiles))
  const sampleOutlier = matchesSample(confirmedFiles, outlierFiles)
  const [previousReview] = useState(() => readDraft(`review:${id}`, isReviewDraft)?.value)
  const preservePreviousStory = Boolean(previousReview && (previousReview.sent || previousReview.submission || previousReview.receivedFiles.length || Object.values(previousReview.review).some(row => row.outcome !== 'pending' || row.reason.trim())))
  const decisionFiles = selectedFiles.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, material: file.material}))
  const decisionScope = [scope.customer, scope.supplier, scope.part, scope.revision && `图纸 Rev ${scope.revision}`, scope.plant].filter(Boolean).join(' · ')
  const plan = basis ? checkPlan(basis, confirmedFiles, receiptId, check.mode, knownInput) : null
  const liveItems = draftMaterials(files, basis)
  // Starting the audit is the explicit confirmation of this scope and selected files.
  const registration = {...value, scopeConfirmed: true, submitted: true, acknowledged: true, step: 'progress' as Step, files: files.map(file => file.selected ? {...file, associated: true} : file)}
  const audit = useLiveAudit({id, registration, items: liveItems, ready: liveItems.length === selectedFiles.length, state: check, plan, onChange: setCheck, beforeStart: () => savingDraft.current.then(() => undefined, () => undefined)})
  const executionItems = audit.round?.inputs ?? liveItems
  const showPurpose = Boolean(audit.round || files.some(file => file.inheritedBinding) || basis && (!liveMode || basis.server))
  function fileUse(file: Document) {
    if (!file.selected) return '未纳入'
    const item = executionItems.find(item => item.file_version_id === file.material?.file_version_id)
    if ((audit.round || file.inheritedBinding) && item) return item.role === 'target' ? '待审' : '参考'
    if (!basis || liveMode && !basis.server) return '待选依据'
    if (file.kind === '待分类') return '待分类'
    if (!liveMode) return '待审'
    return item ? item.role === 'target' ? '待审' : '参考' : '用途待确定'
  }
  const targets = files.filter(file => fileUse(file) === '待审').length, references = files.filter(file => fileUse(file) === '参考').length
  const fileDetails = files.find(file => file.id === infoFile)
  const editingLocked = frozen || Boolean(audit.live?.busy) || transferBusy.current
  useEffect(() => {setCheck(current => current ? recoverCheck(current) : current)}, [])
  useEffect(() => {
    if (!liveMode || frozen || audit.live?.busy || transferBusy.current || saveError || JSON.stringify(value) === initialRegistration.current) return
    const timer = setTimeout(() => {
      savingDraft.current = savingDraft.current.catch(() => undefined).then(() => mounted.current ? request<Draft>(`/drafts/${id}`, 'PUT', {case_context: {registration: JSON.stringify(value)}, items: inputItems(liveItems), analysis_mode: check.mode}) : undefined)
      void savingDraft.current.then(() => {if (mounted.current) setServerSaveError('')}).catch(cause => {if (mounted.current) setServerSaveError(message(cause))})
    }, 600)
    return () => clearTimeout(timer)
  }, [id, value, liveMode, frozen, audit.live?.busy, saveError])
  useEffect(() => {
    const justAccepted = accepted && !wasAccepted.current
    wasAccepted.current = accepted
    if (!justAccepted) return
    setSubmitted(true); setScopeConfirmed(true)
    setStep('progress')
    const url = new URL(location.href); url.searchParams.set('step', 'progress'); history.replaceState(null, '', url)
  }, [accepted])
  useEffect(() => {
    const sync = () => {
      const target = routeStep()
      if (target === 'cases') return
      setStep(target === 'requirements' ? 'requirements' : target === 'progress' && accepted ? 'progress' : 'prepare')
      const query = new URLSearchParams(location.search)
      setReviewView(query.has('clause') || query.has('stage') || query.has('resume'))
    }
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [accepted])
  function go(next: 'prepare' | 'requirements' | 'progress') {
    setStep(next)
    const url = new URL(location.href)
    url.searchParams.set('step', next)
    url.searchParams.delete('variant'); url.searchParams.delete('scenario')
    history.pushState(null, '', url); window.scrollTo({top: 0})
  }
  function openReview() {
    const url = new URL(location.href)
    url.searchParams.set('step', 'progress'); url.searchParams.set('resume', 'decision'); url.searchParams.set('reviewTask', 'review')
    history.pushState(null, '', url); setStep('progress'); setReviewView(true)
  }
  function changeScope(key: keyof CaseScope, next: string) {
    if (scope[key] === next || editingLocked) return
    setScope(current => ({...current, [key]: next})); setScopeConfirmed(false); setAcknowledged(false)
    // Scope affects applicability, but does not invalidate an unchanged transcription.
    if (basis) setBasis({...basis, confirmed: null, clauses: basis.clauses.map(clause => ({...clause, applicability: 'pending', reason: ''}))})
  }
  function updateFile(fileId: string, patch: Partial<Document>) {
    if (editingLocked) return
    setFiles(current => current.map(file => file.id === fileId ? {...file, ...patch} : file))
    setAcknowledged(false)
  }
  function applyFiles(next: Document[]) {
    if (!mounted.current) return
    const snapshot = {...latestDraft.current, files: next, sourceMode: 'local' as const}
    latestDraft.current = snapshot; update(snapshot)
    return snapshot
  }
  function finishTransfer(result: {files: Document[]; error: string}) {
    if (!mounted.current) return
    if (result.error) applyFiles(result.files)
    setUploadError(result.error)
  }
  async function saveFiles(next: Document[]) {
    const snapshot = applyFiles(next)
    if (!snapshot) return
    if (!liveMode) return
    const items = draftMaterials(next, snapshot.basis ?? null)
    savingDraft.current = savingDraft.current.catch(() => undefined).then(() => mounted.current ? request<Draft>(`/drafts/${id}`, 'PUT', {case_context: {registration: JSON.stringify(snapshot)}, items: inputItems(items), analysis_mode: snapshot.check?.mode ?? 'standard'}) : undefined)
    try {await savingDraft.current; if (mounted.current) setServerSaveError('')}
    catch (cause) {if (mounted.current) setServerSaveError(message(cause)); throw cause}
  }
  async function transfer(incoming: LocalUpload[], next = latestDraft.current.files) {
    if (transferBusy.current) return
    transferBusy.current = true
    try {
      if (liveMode) {
        const result = await transferDocuments(next, incoming, saveFiles, () => mounted.current)
        finishTransfer(result)
      } else await saveFiles(next)
    } finally {transferBusy.current = false}
  }
  const upload = useLocalUploads(async incoming => {
    const mapped = localDocuments(incoming)
    setUploadError(''); setAcknowledged(false)
    await transfer(incoming, [...latestDraft.current.files, ...mapped])
  }, id)
  async function recoverUpload(file: Document, send = false) {
    if (editingLocked || transferBusy.current) return
    transferBusy.current = true; setReadingUpload(file.id); setUploadError('')
    try {
      if (send) {
        if (file.uploadReceipt) {
          if (file.uploadReceipt.state === 'unknown' && !file.uploadReceipt.file) {
            const observed = await readDocumentUpload(file)
            if (!mounted.current) return
            const receipt = observed.uploadReceipt!
            if (!(receipt.state === 'unknown' && !receipt.file || receipt.state === 'uploading' && receipt.resume_allowed)) {
              await saveFiles(latestDraft.current.files.map(item => item.id === file.id ? observed : item)); return
            }
          } else if (!(file.uploadReceipt.state === 'uploading' && file.uploadReceipt.resume_allowed)) return
        }
        const blob = file.localFile ? await readLocalFile(file.localFile) : null
        if (!blob || !file.localFile) throw new Error('本机没有此原件。请在原上传浏览器继续；不要把新文件替换到原请求。')
        const result = await transferDocuments(latestDraft.current.files, [{file: new File([blob], file.name, {type: file.localFile.type}), ref: file.localFile}], saveFiles, () => mounted.current)
        finishTransfer(result)
      } else {
        const recovered = await readDocumentUpload(file)
        if (mounted.current) await saveFiles(latestDraft.current.files.map(item => item.id === file.id ? recovered : item))
      }
    } catch (cause) {if (mounted.current) setUploadError(message(cause))}
    finally {transferBusy.current = false; if (mounted.current) setReadingUpload(null)}
  }
  function addFiles(list: FileList | null) {
    if (!list?.length || editingLocked) return
    if (sourceMode === 'demo') {setUploadError('请从工作台上传另一组资料，当前示例仍会保留。'); return}
    setUploadError(''); void upload.upload(Array.from(list))
  }
  const startIssues = [
    ...(!selectedFiles.length ? [{text: '至少纳入一份资料', target: 'intake-files'}] : []),
    ...(selectedFiles.some(file => file.kind === '待分类' && !file.inheritedBinding) ? [{text: '确认资料类型', target: 'intake-files'}] : []),
    ...(missingScope.length ? [{text: `补全${missingScope.map(item => item.label).join('、')}`, target: 'intake-scope'}] : []),
    ...(!requirementsReady ? [{text: basis ? '确认本次要求的适用范围' : '选择审核依据', target: 'intake-basis'}] : []),
    ...(audit.live && !audit.live.ready ? [{text: '部分资料尚未上传成功', target: 'intake-files'}] : []),
    ...(audit.live && requirementsReady && selectedFiles.length && !audit.live.targets.length ? [{text: '至少一份资料类型须对应审核依据', target: 'intake-files'}] : []),
  ]
  function resolveIssue(target: string) {
    if (target === 'intake-basis') {go('requirements'); return}
    if (target === 'intake-files' && !files.length) {picker.current?.click(); return}
    const section = document.getElementById(target)
    section?.scrollIntoView({block: 'center'})
    const control = section?.querySelector<HTMLElement>('[data-missing="true"]:not(:disabled)') ?? section?.querySelector<HTMLElement>('input:not([type="file"]):not(:disabled),select:not(:disabled),button[role="checkbox"]:not(:disabled)')
    control?.focus({preventScroll: true})
  }
  const nextIssue = startIssues[0]
  const nextAction = nextIssue ? {label: nextIssue.target === 'intake-basis' ? '核对审核依据' : nextIssue.target === 'intake-scope' ? '补全审核范围' : '核对审核资料', run: () => resolveIssue(nextIssue.target)} : undefined
  const saveNotice = prepareError || serverSaveError || (step === 'prepare' && plan && requirementsReady ? '' : saveError)
  async function prepareRound() {
    if (preparingRound.current || !audit.round || audit.round.execution !== 'failed' || !audit.round.preparation_allowed) return
    preparingRound.current = true; setPreparing(true); setPrepareError('')
    try {await onPrepareRound(audit.round, value)}
    catch (cause) {if (mounted.current) setPrepareError(message(cause))}
    finally {preparingRound.current = false; if (mounted.current) setPreparing(false)}
  }
  function start() {
    if (startIssues.length || !plan || upload.busy || transferBusy.current) return
    setSubmitted(true); setScopeConfirmed(true); setAcknowledged(true)
    setFiles(current => current.map(file => file.selected ? {...file, associated: true} : file))
    if (audit.live) audit.live.start()
    else setCheck(current => startCheck(current ?? check, plan))
  }
  const readingInput = {receiptId: submitted ? receiptId : '审核准备', scope: decisionScope, files: files.map(file => ({name: file.name, revision: file.origin === 'local' ? file.declaredRevision : file.contentRevision, sourceVersion: file.sourceVersion, localFile: file.localFile, material: file.material, pending: !submitted})), evidence: [], clarification}
  const readingSources = documents.map(doc => ({...doc, pending: !submitted, origin: submitted ? doc.origin : '本次上传'}))
  const progressFiles = audit.round ? audit.round.inputs.map(material => ({name: material.original_filename, revision: '已固定版本', sourceVersion: 1, material, roundId: audit.round!.id})) : readingInput.files
  const progressTitle = scope.part || '本轮审核'
  let handoff: IntakeHandoff | undefined = submitted ? {
    sample: completeSample ? sampleOutlier ? 'outlier' : 'complete' : undefined,
    receiptId, documents, check: accepted ? {id: check.request!.runId, method: modeName(check.request!.plan.mode)} : undefined,
    files: decisionFiles, disclosures: gaps.map(gap => ({fileName: files.find(file => file.id === gap.fileId)!.name, issue: gap.title, note: notes[gap.fileId] || ''})),
    clarification, basis,
    decisionInput: requirementsReady && basis && (!preservePreviousStory || completeSample || !knownInput || !basisMatchesFixedReview(basis)) ? {receiptId, scope: decisionScope, basis, files: decisionFiles, clarification, evidence: completeSample ? completeSampleEvidence(basis, sampleOutlier) : knownInput ? demoEvidence({receiptId, scope: decisionScope, basis, files: decisionFiles, clarification, evidence: []}, documents) : unexecutedEvidence(basis)} : undefined,
    matchesFixedReview: requirementsReady && basisMatchesFixedReview(basis) && scopeMatchesDemo && sourceMode === 'demo' && matchesSample(confirmedFiles, initialFiles),
  } : undefined
  if (audit.round && handoff) handoff = {...handoff, receiptId: audit.round.id, check: {id: audit.round.id, method: modeName(audit.round.analysis_mode)}, files: roundInput(audit.round, basis, decisionScope, clarification).files, decisionInput: roundInput(audit.round, basis, decisionScope, clarification), matchesFixedReview: false}
  if (reviewView && liveMode && accepted && !audit.round) return <main className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6"><p role="status">{audit.roundError || '正在读取本轮审核与复核记录…'}</p><Button variant="outline" onClick={audit.refresh}>重新读取</Button><Button variant="ghost" onClick={onHome}>返回工作台</Button></main>
  if (reviewView) return <RoundReviewContext.Provider value={audit.round ? {round: audit.round, onChange: audit.setRound} : null}><ConceptApp handoff={handoff} draftId={id} onHome={onHome} changeContext={{scope, collections: requirementCollections}}/></RoundReviewContext.Provider>

  return <div className="min-h-dvh bg-background text-foreground">
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b bg-background px-4 sm:px-6">
      <nav className="flex min-w-0 items-center gap-2 text-sm" aria-label="当前位置"><Button variant="ghost" size="sm" asChild><a href="/" onClick={event => {if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); onHome()}}><ArrowLeft/>工作台</a></Button><span className="text-muted-foreground">/</span><span className="truncate font-medium">{step === 'requirements' ? '审核依据' : step === 'progress' ? '审核进度' : '审核准备'}</span></nav>
      <AccountMenu compact/>
    </header>
    <main id="main-content" className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      {saveNotice && <Alert variant="destructive" className="mb-5"><AlertDescription>{saveNotice}{serverSaveError && !frozen && <Button variant="outline" size="sm" disabled={editingLocked} onClick={() => {void saveFiles(latestDraft.current.files).catch(() => undefined)}}>重试保存资料</Button>}</AlertDescription></Alert>}
      {step === 'requirements' ? <Requirements basis={basis} scopeKey={scopeKey(scope)} scope={scope} scopeReady={!missingScope.length} collections={requirementCollections} catalog={catalog} context={decisionScope} readonly={editingLocked} backLabel={accepted ? '返回本轮资料' : '返回审核准备'} onBack={() => go('prepare')} onChange={next => setBasis(next)} onConfirm={next => {setBasis(next); setScope(current => ({...current, requirements: `${next.source.title} ${next.source.version}`})); go('prepare')}}/>
        : step === 'progress' && accepted ? <>
          {audit.enabled && !audit.round ? <div role="status" className="flex flex-wrap items-center gap-3 py-8"><p>{audit.roundError || '正在读取本轮审核…'}</p><Button variant="outline" onClick={audit.refresh}>重新读取</Button></div>
            : (knownInput || Boolean(audit.round)) && requirementsReady ? <ProgressPrototype round={audit.round} remoteError={audit.roundError} onRefresh={audit.refresh} onHome={onHome} onPrepare={() => {void prepareRound()}} preparing={preparing} title={progressTitle} files={<FileCollection files={fileReferences(progressFiles)} context={scope.part}/>} context={decisionScope} checkRequest={check.request ?? undefined} onCheckPlan={() => go('prepare')} snapshot={{fileCount: selectedFiles.length, hasFlow: Boolean(selectedFiles.find(file => file.id === 'flow')), hasControl: Boolean(selectedFiles.find(file => file.id === 'control')), flowUnreadable: Boolean(selectedFiles.find(file => file.id === 'flow')?.readability === 'partial'), dimensionRevision: selectedFiles.find(file => file.id === 'dimension')?.contentRevision ?? null}} completedEvidence={handoff?.decisionInput?.evidence} clarification={clarification} answer={clarificationDraft ?? ''} onAnswerChange={setClarificationDraft} onClarification={setClarification} onOpenReview={openReview} savedScenario={progressScenario} onScenario={setProgressScenario} basis={basis}/>
              : <section className="space-y-4 py-8"><h1 className="text-2xl font-semibold">没有可核对的检查结果</h1><p>适用要求仍未覆盖，可以阅读原件并人工核对。</p>{handoff?.decisionInput && <Button onClick={() => setReviewView(true)}>开始人工复核<ArrowRight/></Button>}</section>}
        </> : <>
          <header className="mb-6 flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-2xl font-semibold tracking-tight">{accepted ? '本轮资料与依据' : '审核准备'}</h1>{frozen && <p className="mt-2 text-sm text-muted-foreground">文件与审核依据已固定。</p>}</div></header>
          <div className="grid min-w-0 items-start gap-8 xl:grid-cols-[minmax(0,2fr)_minmax(19rem,1fr)]">
            <section id="intake-files" aria-label="审核资料" className="min-w-0 scroll-mt-20">
              <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-base font-semibold">资料 <span className="ml-2 font-normal text-muted-foreground">{selectedFiles.length}/{files.length}</span></h2>{!frozen && <Button variant="outline" size="sm" disabled={editingLocked || upload.busy} onClick={() => picker.current?.click()}><Plus/>添加文件</Button>}</div>
              <input ref={picker} type="file" accept={fileAccept} className="sr-only" aria-label="添加审核资料" disabled={editingLocked || upload.busy} multiple onChange={event => {addFiles(event.target.files); event.target.value = ''}}/>
              {!files.length ? <div className="flex flex-col items-center gap-3 border border-dashed py-10 text-center"><UploadCloud className="text-muted-foreground"/><p>添加收到的资料</p><Button variant="outline" disabled={editingLocked || upload.busy} onClick={() => picker.current?.click()}>选择文件</Button></div> : <Table aria-label="审核资料" className="table-fixed">
                <TableHeader className="hidden md:table-header-group"><TableRow><TableHead className="w-10"><span className="sr-only">纳入</span></TableHead><TableHead className="w-[45%]">文件</TableHead><TableHead>类型</TableHead>{showPurpose && <TableHead className="w-24">用途</TableHead>}<TableHead className="w-10"><span className="sr-only">操作</span></TableHead></TableRow></TableHeader>
                <TableBody>{files.map(file => {
                  const warnings = file.selected && file.origin === 'demo' ? gapsFor({...file, associated: true}, scope.part) : []
                  return <TableRow key={file.id} className="grid grid-cols-[2.75rem_minmax(0,1fr)_4.5rem_3.5rem] items-start md:table-row">
                    <TableCell className="align-top"><label className="-m-2 flex min-h-11 items-center justify-center md:m-0 md:min-h-0"><Checkbox aria-label={`纳入 ${file.name}`} disabled={editingLocked} checked={file.selected} onCheckedChange={checked => updateFile(file.id, {selected: checked === true})}/><span className="sr-only">纳入 {file.name}</span></label></TableCell>
                    <TableCell className="col-span-2 col-start-2 row-start-1 min-w-0 whitespace-normal align-top">
                      <SourceReaderButton input={readingInput} documents={readingSources} file={readingInput.files[files.indexOf(file)]} label={file.name} variant="link" className="h-auto w-full justify-start whitespace-normal text-left break-all"/>
                      {file.inheritedBinding && <p className="mt-1 break-words text-xs text-muted-foreground">{file.material?.role === 'target' ? `审核要求：${file.material.rule?.title || '原轮规则'}` : '沿用原轮参考资料用途'}</p>}
                      {liveMode && !file.material && <div className="mt-2 space-y-2 text-sm">
                        <p className="text-muted-foreground">{file.uploadReceipt?.reason || (file.uploadReceipt ? '尚未确认文件已接收。' : '尚未上传。前一份文件需先核对。')}</p>
                        <div className="flex flex-wrap gap-2">
                          {file.uploadReceipt && file.uploadReceipt.state !== 'ready' && <Button variant="outline" size="sm" disabled={editingLocked} onClick={() => {void recoverUpload(file)}}>{readingUpload === file.id ? '正在核对…' : '核对上传结果'}</Button>}
                          {(!file.uploadReceipt || file.uploadReceipt.state === 'uploading' && file.uploadReceipt.resume_allowed || file.uploadReceipt.state === 'unknown' && !file.uploadReceipt.file) && <Button variant="outline" size="sm" disabled={editingLocked} onClick={() => {void recoverUpload(file, true)}}>{!file.uploadReceipt ? '上传此文件' : file.uploadReceipt.state === 'unknown' ? '核对并继续原传输' : '继续原传输'}</Button>}
                        </div>
                      </div>}
                      {warnings.length > 0 ? <p className="mt-1 break-words text-xs text-muted-foreground">{warnings.map(gap => gap.title).join(' · ')}</p> : file.selected && file.readability === 'partial' && <p className="mt-1 text-xs text-muted-foreground">已标记部分不可读</p>}{notes[file.id] && <p className="mt-1 break-words text-xs text-muted-foreground">{notes[file.id]}</p>}
                    </TableCell>
                    <TableCell className={`col-start-2 min-w-0 ${showPurpose ? '' : 'col-span-2'}`}>{frozen || file.inheritedBinding ? <span>{file.kind}</span> : <label><span className="mb-1 block text-xs text-muted-foreground md:sr-only">资料类型</span><NativeSelect className="w-full" aria-label={`${file.name} 资料类型`} data-missing={file.selected && file.kind === '待分类' || undefined} value={file.kind} disabled={editingLocked} onChange={event => updateFile(file.id, {kind: event.target.value})}>{availableKinds.map(kind => <NativeSelectOption key={kind} value={kind}>{kind}</NativeSelectOption>)}</NativeSelect></label>}</TableCell>
                    {showPurpose && <TableCell className="col-start-3 min-w-0 whitespace-normal"><span className="mb-1 block text-xs text-muted-foreground md:sr-only">用途</span><span className="text-sm">{fileUse(file)}</span></TableCell>}
                    <TableCell className="col-start-4 row-start-1"><div className="-m-2 flex min-h-11 items-center justify-center md:m-0 md:min-h-0"><DropdownMenu><DropdownMenuTrigger asChild><Button id={`file-info-${file.id}`} variant="ghost" size="icon-sm" aria-label={`${file.name} 操作`}><MoreHorizontal/></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => {fileInfoReturn.current = `file-info-${file.id}`; setInfoFile(file.id)}}>文件信息</DropdownMenuItem><DropdownMenuItem disabled={editingLocked} onSelect={() => setFiles(current => removeFromDraft(current, file.id))}>移出本次资料</DropdownMenuItem></DropdownMenuContent></DropdownMenu></div></TableCell>
                  </TableRow>
                })}</TableBody>
              </Table>}
              {basis && selectedFiles.length > 0 && <p className="mt-3 text-sm text-muted-foreground">待审文件产生检查结果；参考资料不单独生成结果。</p>}
              {files.some(file => file.inheritedBinding) && <p className="mt-2 text-sm text-muted-foreground">原轮文件保留各自的用途与审核规则；新加入的文件使用本次选择的审核依据。</p>}
              {(uploadError || upload.error) && <Alert variant="destructive" className="mt-3"><AlertDescription>{uploadError || upload.error}</AlertDescription></Alert>}
              {upload.busy && <p role="status" className="mt-3 text-sm">正在上传资料…</p>}
            </section>
            <div className="min-w-0 space-y-7">
              <section id="intake-scope" className="scroll-mt-20" aria-label="审核范围"><h2 className="mb-3 text-base font-semibold">审核范围</h2>{frozen ? <dl className="grid gap-3 text-sm sm:grid-cols-2">{scopeFields.map(({field: key, label}) => <div key={key} className="min-w-0"><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 break-words">{scope[key] || '未记录'}</dd></div>)}</dl> : <div className="grid gap-3 sm:grid-cols-2">{scopeFields.map(({field: key, label}) => <label key={key} className={`grid gap-1.5 text-sm ${key === 'plant' ? 'sm:col-span-2' : ''}`}><span>{label}</span><Input value={scope[key]} data-missing={!scope[key].trim() || undefined} disabled={editingLocked} onChange={event => changeScope(key, event.target.value)} aria-required="true"/></label>)}</div>}</section>
              <section id="intake-basis" className="scroll-mt-20" aria-label="审核依据"><h2 className="mb-3 text-base font-semibold">审核依据</h2>{basis && <div className="mb-3 space-y-1"><p className="break-words text-sm font-medium">{basis.source.title}</p><p className="text-sm text-muted-foreground">{basis.source.version} · {basis.clauses.filter(clause => clause.applicability === 'apply').length}/{basis.clauses.length} 条适用{!requirementsReady && ' · 范围待确认'}</p></div>}<Button variant="outline" size="sm" disabled={Boolean(audit.live?.busy)} onClick={() => go('requirements')}>{frozen ? '查看本轮条款' : basis ? '核对审核要求' : '选择审核要求'}</Button></section>
            </div>
          </div>
          {plan && requirementsReady ? <div className="sticky bottom-0 z-10 mt-8 space-y-3 border-t bg-background py-4"><p className="text-sm">待审 {targets} 份{references > 0 && ` · 参考 ${references} 份`}</p><CheckPreparation compact caseId={id} state={check} plan={plan} onChange={setCheck} onContinue={audit.round?.execution === 'completed' ? openReview : () => go('progress')} continueLabel={audit.round?.execution === 'completed' ? '返回人工复核' : undefined} onHome={onHome} saveError={saveError} live={audit.live} onStart={start} disabledReason={nextIssue?.text || (upload.busy ? '资料上传中' : '')} nextAction={upload.busy ? undefined : nextAction}/></div>
            : <div className="sticky bottom-0 z-10 mt-8 flex flex-wrap items-center justify-between gap-3 border-t bg-background py-4"><p role="status" className="text-sm text-muted-foreground">{upload.busy ? '正在上传资料…' : nextIssue?.text || '请核对本次资料与审核依据。'}</p><Button disabled={upload.busy || !nextAction} onClick={nextAction?.run}>{upload.busy ? '正在上传…' : nextAction?.label || '开始审核'}<ArrowRight/></Button></div>}
          {!audit.enabled && submitted && handoff?.decisionInput && <Button variant="ghost" onClick={() => setReviewView(true)}>转人工核对</Button>}
        </>}
    </main>
    <Sheet open={infoFile !== null} onOpenChange={open => !open && setInfoFile(null)}><SheetContent className="data-[side=right]:w-full data-[side=right]:max-w-full sm:data-[side=right]:max-w-md" onCloseAutoFocus={event => {event.preventDefault(); document.getElementById(fileInfoReturn.current)?.focus()}}><SheetHeader><SheetTitle>文件信息</SheetTitle><SheetDescription className="break-words">{fileDetails?.name}</SheetDescription></SheetHeader>{fileDetails && <div className="min-h-0 space-y-5 overflow-y-auto py-2">
      <p className="text-sm text-muted-foreground">{fileUse(fileDetails)}{fileDetails.material ? ' · 原件已上传' : ''}</p>
      <label className="grid gap-1.5 text-sm"><span>文件声明修订 <span className="text-muted-foreground">（可选）</span></span><Input aria-label={`${fileDetails.name} 修订`} disabled={editingLocked} value={fileDetails.declaredRevision === '未知' ? '' : fileDetails.declaredRevision} placeholder="原件未注明时可留空" onChange={event => updateFile(fileDetails.id, {declaredRevision: event.target.value})}/></label>
      <label className="grid gap-1.5 text-sm"><span>可读性标记</span><NativeSelect aria-label={`${fileDetails.name} 可读性`} disabled={editingLocked} value={fileDetails.readability} onChange={event => updateFile(fileDetails.id, {readability: event.target.value as Document['readability']})}><NativeSelectOption value="unverified">未核对</NativeSelectOption><NativeSelectOption value="clear">人工确认可读</NativeSelectOption><NativeSelectOption value="partial">部分不可读</NativeSelectOption></NativeSelect></label>
      <label className="grid gap-1.5 text-sm"><span>供应商随件说明 <span className="text-muted-foreground">（可选）</span></span><Textarea disabled={editingLocked} value={notes[fileDetails.id] ?? ''} onChange={event => setNotes(current => ({...current, [fileDetails.id]: event.target.value}))} placeholder="记录收到的原话"/></label>
    </div>}<SheetFooter><Button variant="outline" onClick={() => setInfoFile(null)}>完成</Button></SheetFooter></SheetContent></Sheet>
  </div>
}

export function JourneyApp() {
  const access = useAccess(), preview = isPreview()
  const library = useSavedDraft('requirement-library', preview ? initialLibrary : () => ({collections: []}), isRequirementLibrary)
  const [collections, setCollections] = library.field('collections')
  const [search, setSearchState] = useState(() => (new URLSearchParams(location.search).get('q') || '').slice(0, 200))
  function setSearch(value: string) {
    const bounded = value.slice(0, 200)
    setSearchState(bounded)
    const url = new URL(location.href)
    if (bounded) url.searchParams.set('q', bounded)
    else url.searchParams.delete('q')
    history.replaceState(null, '', url)
  }
  useEffect(() => {
    const sync = () => {const params = new URLSearchParams(location.search); if (!params.has('case') && !params.has('draft') && !params.has('round')) setSearchState((params.get('q') || '').slice(0, 200))}
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])
  const [libraryRead, setLibraryRead] = useState({loading: !preview, error: ''})
  const [libraryRevision, setLibraryRevision] = useState(0)
  useEffect(() => {
    if (preview) return
    let active = true
    setLibraryRead({loading: true, error: ''})
    void loadRuleCollections().then(rows => {
      if (!active) return
      setCollections(current => [...current.filter(item => item.draft), ...rows.filter(row => !current.some(item => item.draft && item.versions.some(version => version.id === row.id)))])
      setLibraryRead({loading: false, error: ''})
    }).catch(cause => {if (active) setLibraryRead({loading: false, error: message(cause)})})
    return () => {active = false}
  }, [preview, libraryRevision])
  function openState(target: CaseTarget, initial?: IntakeDraft, uploadError = '') {
    return {id: target.id, target, initial: initial ?? freshDraft(), uploadError, missing: !initial && (!preview || !readDraft(`intake:${target.id}`, isIntakeDraft))}
  }
  const caseGeneration = useRef(0)
  useEffect(() => () => {caseGeneration.current++}, [])
  const workbenchReturn = useRef<string | null>(null)
  const [caseReadError, setCaseReadError] = useState('')
  const [caseRevision, setCaseRevision] = useState(0)
  const [active, setActive] = useState<ReturnType<typeof openState> | null>(() => {
    const query = new URLSearchParams(window.location.search)
    const target = routeCase(query)
    if (target) return openState(target)
    if (preview && query.get('step') === 'progress') return openState({id: 'progress-example', kind: 'case'}, {...freshDraft(initialFiles, true), step: 'progress', submitted: true, scopeConfirmed: true})
    return null
  })
  const workbench = useWorkbenchData(search.trim(), !preview, !active)
  const drafts = listDrafts('intake:', isIntakeDraft)
  const localItems: WorkItem[] = drafts.map(({key, value: draft, updatedAt}) => {
    const change = savedChangeSummary(key.slice('intake:'.length))
    const review = change ?? savedReviewSummary(key.slice('intake:'.length))
    const currentScope = change?.scope ?? draft.scope
    const basisReady = basisConfirmed(draft.basis, scopeKey(draft.scope))
    const demo = basisReady && draft.sourceMode === 'demo' && scopeKey(draft.scope) === scopeKey(demoScope)
    const preparation = draft.submitted ? checkSummary(draft.check) : null
    return {
      id: key.slice('intake:'.length), href: caseHref({id: key.slice('intake:'.length), kind: 'case'}), title: draft.scope.part || draft.files[0]?.name || '待核对资料',
      context: [currentScope.customer, currentScope.supplier, currentScope.revision ? `${change?.pending ? '拟采用' : '图纸'} Rev ${currentScope.revision}` : '修订待确认'].filter(Boolean).join(' · '),
      state: draft.reviewView ? review?.state ?? '待复核' : !draft.submitted ? draft.scopeConfirmed && !basisReady ? '要求待确认' : '待核对' : preparation?.state ?? (!demo ? '内容未检查' : ({running: '检查中', clarification: '待澄清', failure: '局部失败', complete: '待复核'}[draft.progressScenario])),
      next: draft.reviewView ? review?.next ?? '继续复核' : !draft.submitted ? '核对资料' : preparation?.next ?? (!demo ? '查看登记' : '查看进度'),
      detail: draft.reviewView ? review?.detail ?? '继续核对发现与原件证据。' : !draft.submitted ? `${draft.files.length} 份文件 · ${draft.scopeConfirmed ? basisReady ? '要求已确认，继续逐份核对文件' : '归属已记录，审核依据待确认' : '尚未确认资料归属'}` : preparation?.detail ?? (!demo ? '已登记文件名与说明；没有执行内容检查。' : draft.clarification ? '供应商说明已记录，原件仍需核实。' : '继续查看检查结果与尚未覆盖的要求。'),
      received: draft.submitted, updatedAt,
      files: fileReferences(draft.files.map(file => ({name: file.name, revision: file.contentRevision, sourceVersion: file.sourceVersion, pending: !draft.submitted, material: file.material, localFile: file.localFile}))),
    }
  })
  const items: WorkItem[] = preview ? localItems : [
    ...(workbench.drafts?.rows.map(draft => {
      let saved: IntakeDraft | null = null
      try {const parsed: unknown = JSON.parse(draft.case_context?.registration || 'null'); if (isIntakeDraft(parsed)) saved = parsed} catch { /* Filename still identifies older drafts. */ }
      const uncertain = saved?.check?.request && ['requesting', 'unknown'].includes(saved.check.request.phase)
      const files = draft.items.map(material => ({name: material.original_filename, revision: '待核对', sourceVersion: 1, pending: true, material}))
      return {id: draft.id, href: caseHref({id: draft.id, kind: 'case'}), title: saved?.scope.part || files.find(file => file.material.role === 'target')?.name || files[0]?.name || '待核对资料', files: fileReferences(files), context: saved ? [saved.scope.customer, saved.scope.supplier, saved.scope.revision && `图纸 Rev ${saved.scope.revision}`].filter(Boolean).join(' · ') : '', state: uncertain ? '受理待核对' : '审核准备', next: uncertain ? '核对受理结果' : '准备审核', detail: saved?.basis ? `${saved.basis.source.title} · ${saved.basis.source.version}` : '审核依据待选择', received: false, updatedAt: draft.updated_at, task: uncertain ? 'attention' as const : 'prepare' as const}
    }) ?? []),
    ...(workbench.rounds?.rows.map(round => {
      const total = Object.entries(round.result_counts).reduce((sum, [status, count]) => sum + (status === 'NA' ? 0 : count), 0) + (round.uncovered_count ?? 0)
      const reviewed = round.execution === 'completed' && total > 0 && round.reviewed_count >= total
      const files = round.inputs.map(material => ({id: material.file_version_id, name: material.original_filename, role: material.role, roundId: round.id}))
      return {id: round.id, href: caseHref({id: round.id, kind: 'round'}), title: round.case_title || files.find(file => file.role === 'target')?.name || files[0]?.name || '待核对资料', files, context: round.case_scope, mode: modeName(round.analysis_mode), state: reviewed ? '已复核' : round.execution === 'completed' ? '待复核' : executionLabels[round.execution] || round.execution, next: reviewed ? '查看复核' : round.execution === 'completed' ? round.reviewed_count ? '继续复核' : '开始复核' : round.execution === 'failed' ? '查看失败原因' : '查看进度', detail: '', counts: {...round.result_counts, uncovered: round.uncovered_count}, review: round.execution === 'completed' ? {saved: round.reviewed_count, total} : undefined, received: true, updatedAt: round.created_at || '', task: reviewed ? 'completed' as const : round.execution === 'completed' ? 'review' as const : round.execution === 'failed' ? 'attention' as const : 'running' as const}
    }) ?? []),
  ]

  async function openServer(target: CaseTarget, identity: number, signal: AbortSignal) {
    const requestedId = target.id, pathId = encodeURIComponent(requestedId)
    const current = () => !signal.aborted && identity === caseGeneration.current
    try {
      let round: Round | null = null, draft: Draft | null = null
      if (target.kind === 'round') round = await request<Round>(`/rounds/${pathId}`, 'GET', undefined, signal)
      else {
        draft = await request<Draft>(`/drafts/${pathId}`, 'GET', undefined, signal)
        if (draft.status === 'accepted') round = await request<Round | null>(`/drafts/${pathId}/round`, 'GET', undefined, signal)
        if (draft.status === 'accepted' && !round) throw new Error('审核已受理，但轮次尚未能读取。请重新读取，不要再次开始审核。')
      }
      if (!current()) return
      const source = round ?? draft!
      let snapshot = freshDraft()
      const registration = source.case_context?.registration
      if (registration) {const parsed: unknown = JSON.parse(registration); if (isIntakeDraft(parsed)) snapshot = parsed}
      const materials = round?.inputs ?? draft!.items
      const registered = snapshot.files
      const restored = materials.map<Document>(material => {
        const known = snapshot.files.find(file => file.material?.file_version_id === material.file_version_id)
        const binding = {material, inheritedBinding: Boolean(draft?.predecessor_id), selected: true}
        return known ? {...known, ...binding, associated: Boolean(round) || known.associated} : {id: material.file_version_id, ...binding, name: material.original_filename, origin: 'local', kind: material.rule?.body.kind === 'engine' ? material.rule.body.type === '全功能检验报告' ? '尺寸报告' : material.rule.body.type : '待分类', expectedKind: '待分类', declaredRevision: '未知', contentRevision: '未解析', sourceVersion: 1, associated: Boolean(round), readability: 'unverified'}
      })
      snapshot.files = [...registered.map(file => restored.find(item => item.material && item.material.file_version_id === file.material?.file_version_id) ?? (!file.material ? file : {...file, selected: false})), ...restored.filter(file => !registered.some(item => item.material?.file_version_id === file.material?.file_version_id))]
      if (draft) snapshot.check = {...snapshot.check ?? initialCheck(), mode: draft.analysis_mode}
      if (!snapshot.basis) {
        const bound = materials.find(item => item.role === 'target' && item.rule)?.rule
        if (bound?.body.kind === 'engine') {
          const rule = await request<RuleDetail>(`/rules/${encodeURIComponent(bound.id)}`, 'GET', undefined, signal)
          const base = localBasis(rule.title)
          if (rule.source_file_id && rule.source_file_version_id) {
            const source = await request<FileVersion>(`/files/${rule.source_file_id}/versions/${rule.source_file_version_id}`, 'GET', undefined, signal)
            base.source.filename = source.original_filename
            base.server = {body: bound.body, ruleId: bound.id, material: {file_id: rule.source_file_id, file_version_id: rule.source_file_version_id, original_filename: source.original_filename, role: 'reference', rule_version_id: null}}
          }
          const parsed = parsedBasis(base, {title: bound.title, body: bound.body, source_file_version_id: rule.source_file_version_id || ''})
          parsed.source = {...parsed.source, id: bound.id, version: rule.created_at.slice(0, 16).replace('T', ' ')}
          parsed.clauses = parsed.clauses.map(row => ({...row, applicability: 'apply'}))
          snapshot.basis = parsed
        }
      }
      if (round) {
        const current = snapshot.check ?? initialCheck(), plan = snapshot.basis ? checkPlan(snapshot.basis, snapshot.files, round.id, round.analysis_mode, false) : {key: round.id, mode: round.analysis_mode, rows: [], estimate: {low: 0, high: 0, minutes: ''}, receipt: round.id, basis: '本轮冻结规则', files: materials.map(item => item.original_filename)}
        snapshot = {...snapshot, submitted: true, scopeConfirmed: true, step: 'progress', reviewView: round.execution === 'completed', check: {...current, mode: round.analysis_mode, request: {id: current.request?.id || round.id, runId: round.id, plan, attempt: current.request?.attempt || 1, at: current.request?.at || round.created_at || '', phase: 'accepted', remote: 'accepted', queryError: false}}}
      }
      if (!current()) return
      const actualId = round?.source_draft_id || draft?.id || requestedId
      const canonicalTarget: CaseTarget = round ? {id: round.id, kind: 'round'} : {id: actualId, kind: 'case'}
      setActive({id: actualId, target: canonicalTarget, initial: snapshot, uploadError: '', missing: false}); setCaseReadError('')
      const url = new URL(location.href); url.searchParams.set('case', actualId); url.searchParams.delete('draft')
      if (round) url.searchParams.set('round', round.id)
      if (!['step', 'stage', 'clause', 'resume'].some(key => url.searchParams.has(key))) {
        if (savedChangeSummary(actualId)) url.searchParams.set('resume', 'changes')
        else if (round?.execution === 'completed') url.searchParams.set('resume', 'decision')
      }
      history.replaceState(null, '', url)
    } catch (cause) {if (current()) setCaseReadError(message(cause))}
  }
  useEffect(() => {
    if (preview || !active?.missing) return
    const controller = new AbortController()
    setCaseReadError('')
    void openServer(active.target, caseGeneration.current, controller.signal)
    return () => controller.abort()
  }, [preview, active?.target, active?.missing, caseRevision])
  function navigateCase(href: string, initial?: IntakeDraft, uploadError = '') {
    const url = new URL(href, location.href), target = routeCase(url.searchParams)
    if (!target) return
    if (!active) workbenchReturn.current = location.href
    caseGeneration.current++; setCaseReadError('')
    history.pushState(null, '', url)
    setActive(openState(target, initial, uploadError)); window.scrollTo({top: 0})
  }
  function open(id: string, initial?: IntakeDraft, owner?: import('./usage-state').CheckOwner, uploadError = '') {
    navigateCase(caseHref({id, kind: 'case'}, owner), initial, uploadError)
  }
  async function prepareFromRound(round: Round, snapshot: IntakeDraft) {
    if (preview || round.execution !== 'failed' || !round.preparation_allowed) return
    const identity = caseGeneration.current, current = () => identity === caseGeneration.current
    const key = `${draftPrefix}round-preparation:${round.id}`
    const pending = localStorage.getItem(key), requestId = pending ?? crypto.randomUUID()
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(requestId)) throw new Error('新轮准备记录无法读取。请保留本机记录并联系支持。')
    // This is a retry identity, not a draft or an accepted round.
    localStorage.setItem(key, requestId)
    const registration: IntakeDraft = {...snapshot, step: 'prepare', submitted: false, reviewView: false, acknowledged: false, progressScenario: 'running', check: {...initialCheck(), mode: round.analysis_mode}}
    const body: DraftCreateRequest = {request_id: requestId, predecessor_id: round.id, items: inputItems(round.inputs), case_context: {registration: JSON.stringify(registration)}}
    let prepared: Draft | null = null
    if (pending) {
      try {prepared = await request<Draft>(`/drafts/${requestId}`)}
      catch (cause) {if ((cause as {status?: number}).status !== 403) throw cause}
    }
    if (!current()) return
    if (!prepared) {
      try {prepared = await request<Draft>('/drafts', 'POST', body)}
      catch (cause) {
        if (!current()) return
        try {prepared = await request<Draft>(`/drafts/${requestId}`)}
        catch {throw new Error(`新轮准备结果尚未确认，原审核未改变。再次操作会先核对同一请求。${message(cause)}`)}
      }
    }
    if (!current()) return
    if (prepared.id !== requestId || prepared.predecessor_id !== round.id) throw new Error('新轮准备回执与原请求不一致，未切换审核记录。')
    localStorage.removeItem(key)
    open(prepared.id)
  }
  function home() {
    if (!preview) {location.assign('/workbench'); return}
    caseGeneration.current++; setCaseReadError('')
    setActive(null)
    const url = new URL(workbenchReturn.current || location.href)
    if (!workbenchReturn.current) {url.search = ''; if (search) url.searchParams.set('q', search)}
    window.history.pushState(null, '', url)
    window.scrollTo({top: 0})
  }
  useEffect(() => {
    function sync() {
      const target = routeCase()
      caseGeneration.current++; setCaseReadError('')
      setActive(current => target ? current && !current.missing && current.target.id === target.id && current.target.kind === target.kind ? current : openState(target) : null)
    }
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])
  if (active?.missing && !preview) return <div className="flex min-h-dvh flex-col"><header className="flex h-14 items-center justify-between border-b px-4 sm:px-6"><Button variant="ghost" asChild><a href="/" onClick={event => {if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); home()}}><ArrowLeft/>工作台</a></Button><AccountMenu compact/></header><main className="m-auto flex w-full max-w-md flex-col items-start gap-4 p-6" aria-busy={!caseReadError}><h1 className="text-2xl font-semibold tracking-tight">{caseReadError ? '暂时无法打开这条审核记录' : '正在打开审核记录'}</h1><p role={caseReadError ? 'alert' : 'status'} className="text-sm text-muted-foreground">{caseReadError || '正在读取资料与审核记录…'}</p>{caseReadError && <Button variant="outline" onClick={() => {caseGeneration.current++; setCaseReadError(''); setCaseRevision(value => value + 1)}}>重新读取</Button>}</main></div>
  if (active?.missing) return <main className={"flex min-h-dvh bg-background text-foreground text-base items-center justify-center flex-col gap-3.5 py-10 px-6 text-center [&_h1]:text-2xl [&_h1]:font-semibold [&_p]:max-w-105 [&_p]:text-muted-foreground [&_p]:leading-relaxed"}><FileQuestion size={28} aria-hidden="true"/><h1>暂时无法打开这条审核记录</h1><p>此浏览器没有这条记录。请返回工作台选择已有审核记录。</p><Button className={""} onClick={home}>返回工作台<ArrowRight/></Button><AccountMenu compact/></main>
  if (active) return <IntakeApp key={active.id} id={active.id} initial={active.initial} initialUploadError={active.uploadError} onHome={home} onPrepareRound={prepareFromRound} requirementCollections={collections} catalog={{...libraryRead, reload: () => setLibraryRevision(value => value + 1)}}/>
  return <TaskWorkbench search={preview ? undefined : search} onSearch={preview ? undefined : setSearch} loading={workbench.loading} refreshing={workbench.busy === 'refresh'} errors={workbench.errors} onRefresh={preview ? undefined : workbench.refresh} loadingMore={workbench.busy === 'more'} onLoadMore={workbench.more ? workbench.loadMore : undefined} items={items} onOpen={href => navigateCase(href)} usage={<UsageWorkspace onOpen={(id, owner) => open(id, undefined, owner)}/>} requirements={<RequirementLibraryPanel collections={collections} onChange={setCollections} saveError={library.saveError} loading={libraryRead.loading} readError={libraryRead.error} onReload={() => setLibraryRevision(value => value + 1)} uses={drafts.flatMap(({key, value}) => value.basis ? [{id: key.slice('intake:'.length), title: [value.scope.part, value.scope.supplier].filter(Boolean).join(' · '), basis: value.basis, submitted: value.submitted}] : [])} onOpen={id => open(id)}/>} onDemo={(kind = 'issues') => open(crypto.randomUUID(), freshDraft((kind === 'complete' ? completeFiles : kind === 'outlier' ? outlierFiles : initialFiles).map(file => ({...file})), true))} onFiles={async files => {
    const identity = caseGeneration.current
    try {
      const mapped = localDocuments(files)
      const server = preview ? null : await request<Draft>('/drafts', 'POST', {})
      if (identity !== caseGeneration.current) return null
      let initial = freshDraft(mapped), uploadError = ''
      if (server) {
        const result = await transferDocuments(mapped, files, async next => {
          initial = {...initial, files: next}
          await request<Draft>(`/drafts/${server.id}`, 'PUT', {items: inputItems(next.flatMap(file => file.material ? [file.material] : [])), case_context: {registration: JSON.stringify(initial)}})
        }, () => identity === caseGeneration.current)
        initial = {...initial, files: result.files}
        uploadError = result.error
      }
      if (identity === caseGeneration.current) open(server?.id || crypto.randomUUID(), initial, undefined, uploadError)
      return null
    } catch (cause) {return message(cause)}
  }}/>
}

export const recordValidators = {intake: isIntakeDraft, review: isReviewDraft, decision: validDraft, changes: isChangeState, reassessment: validChecks, 'requirement-library': isRequirementLibrary}
export default function ProductJourney() {
  return <ProductAccess recordValidators={recordValidators}><JourneyApp/></ProductAccess>
}
