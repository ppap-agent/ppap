import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/5bc532c5a9a142a8a378434ddfe73fd5
import {useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {createPortal} from 'react-dom'
import {requestTrace} from '../api'
import {analysisNotice} from '../helpers'
import {MatchingFeedback, SourceMismatchNotice, isMatchingReason} from './matching-feedback'
import {iconAnglesRight, iconArrowLeft, iconArrowRight, iconArrowsRotate, iconBookOpen, iconChartPie, iconCheck, iconChevronDown, iconCircleInfo, iconCircleQuestion, iconCloud, iconFileShield, iconFolderOpen, iconLayerGroup, iconSliders, iconWandMagicSparkles, iconXmark} from '../components/icons'
import {Icon} from '../components/ui/icon'
import {Segmented} from '../components/segmented'
import {PageLayout} from '../components/page-layout'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Label} from '../components/ui/label'
import {Switch} from '../components/ui/switch'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from '../components/ui/tooltip'
import {FileIcon, FileName} from '../journey/file-name'
import {FileTypeBadge} from './file-type-badge'
import {AccountMenu, useAccess} from '../journey/access'
import {openedInFeishu} from '../hostLogin'
import {pickFeishuDocuments} from '../feishu-native'
import {FeishuPicker, FeishuFolderPicker, type DrivePickerSource} from './feishu-picker'
import {checkerSuiteUnavailable, confirmationScope, submittedCount, suiteAdmissionBlocks, useTaskAcceptance, zeroTargetNote, type AuditAcceptanceAdapter} from './task-acceptance'
import {ruleChoicesFor, ruleCustomer, useTaskRecognition, type AuditRecognitionAdapter} from './task-recognition'
import {AuditExcludedFiles, AuditWorkspace} from './workspace'
import {useMinWidth, type OriginalRenderer} from './review-content'
import {clauseLabel, sameFrozenInputs, sameSnapshot, progressRegressed, reconcileTaskNotification} from './result-projection'
import type {AuditNotificationAdapter} from './completion-notification'
import {canObserveTask, useTaskObservation} from './task-observation'
import type {TaskEvents} from './task-events'
import {useUploads, type ContentOwner, type UploadAdapter, type UploadRow} from './uploads'
import {SkippedItem, TransferItem, useTransferAttach} from './transfer-list'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {AuditArchive} from './archive'
import type {AuditArchiveAdapter} from './archive-contract'
import {AuditSupport, SupportEntryContext, type SupportSnapshot, type AuditSupportAdapter} from './support'
import {TEMPLATE_GUIDE_URL} from './template-links'
import {documentTypeLabel, executionLabel} from './status-labels'
import {AuditUsage} from './usage'
import type {AuditUsageAdapter} from './usage-contract'
import {reconcileTaskDisplay, type DisplayInfo, type DisplayReference} from './display-info-contract'
import {useDisplayEditor, type AuditDisplayAdapter} from './display-info-editor'
import {ReviewEditorContext} from './review-editor'
import {reconcileTaskSummary, subjectLabel, summarizeTask, taskIsBehindSummary, type TaskSummary} from './task-summary'
import {auditAddressChanged, readAuditRoute, ruleAddressChanged, rulesAddressKeys, surfaceAddress, taskAddress as taskHref, writeAuditAddress} from './routes'
import {templateName} from './rule-templates'
import type {ReadRound} from './workspace'
import {TaskRecovery, type TaskRecoveryState} from './task-recovery'
import {TaskRail, taskStateTone} from './task-switcher'
import {deepModeControl, deepModeDescription, deepModeTitle, pointsBasis} from './deep-mode'
import {TemplateLibraryPage, type TemplateLibraryAdapter} from './template-library'
import {PrecheckNotes, PrecheckReport, usePrecheck} from './precheck'
import {taskListStatus} from './task-list-status'
import {createUploadPreparation, describeUploads, preparationFiles, restoreUploads, type PreparationAdapter, type UploadPreparation} from './upload-preparation'

import {CustomerMode, acceptedUploads, type CustomerDefaultAdapter} from './customer-mode'
import {customerCoverage, customerFileSlots, customerTypeSetMissing, type CoverageSource} from './customer-coverage'
import {customerFullName, customerShortName} from './customer-register'
import {AuditDurationEstimate} from './duration-estimate'
import {listedTypes, matchStatus, recognitionHold, sourceMismatches, uploadFileStatus, type UploadFileStatus} from './upload-board'
import {newerRuleOf, ruleName, ruleVersionChoice, withRuleVersion, type RuleVersionChoice} from './reaudit-intent'

export type AuditTask = components['schemas']['AuditTask']
export type AuditTaskFile = components['schemas']['AuditTaskFile']
export type AuditIntakeAdapter = AuditAcceptanceAdapter & AuditRecognitionAdapter & UploadAdapter & {
  preparation?: PreparationAdapter
  /** Live task change notices; without them the page reads on a timer. */
  events?: TaskEvents
  samples?: () => AuditTaskFile[]
  drive?: DrivePickerSource
  customerDefaults: CustomerDefaultAdapter
  customers?: {value: string; label: string}[]
  /** Rules and standard types for each customer's coverage; optional, a failed read shows no numbers. */
  coverage?: (signal?: AbortSignal) => Promise<CoverageSource>
  notifications?: AuditNotificationAdapter
  /** Saves the task's deep-mode choice before acceptance; the server freezes it on the Round. */
  deepMode?: (task: AuditTask, deepMode: boolean) => Promise<AuditTask>
  /** Free file-level precheck, outside the checker flow; it never changes what is accepted. */
  precheck?: {read: (taskId: string, signal?: AbortSignal) => Promise<components['schemas']['AuditTaskPrecheck']>; report: (taskId: string) => Promise<Blob>}
  autoValidate?: boolean
  /** The newest version of every rule, for choosing one by hand when recognition picked the wrong one. */
  /** Newest rules a person may pick; `type` and `template` let one template choice set every file's rule. */
  ruleChoices?: (signal?: AbortSignal) => Promise<RuleChoice[]>
  readValidation: (file: AuditTaskFile, scope?: components['schemas']['FileValidationScope'] | null) => Promise<{validation: AuditTaskFile['validation']; validation_id?: string; validation_scope?: components['schemas']['FileValidationScope']}>
  endTask: (task: AuditTask) => Promise<AuditTask>
  listTasks: (query: {q: string; state: AuditTask['state'] | null; offset: number; signal?: AbortSignal}) => Promise<{tasks: TaskSummary[]; next_offset: number | null}>
  authenticate: () => Promise<void>
  prepareReaudit?: (roundId: string) => Promise<components['schemas']['AuditReauditInputs']>
  readRound?: ReadRound
}
type RuleChoice = {id: string; title: string; type?: string; template?: string; customers?: string[]; customer_scope?: string}
const lastTemplateKey = 'ppap.rule-template'
const lastTemplate = () => {try {return localStorage.getItem(lastTemplateKey) ?? ''} catch {return ''}}
const taskLabels = {matching: '识别中', pending_confirm: '待确认', reviewing: executionLabel.running, completed: executionLabel.completed}
// The icon rail shows each page's short name under its icon; the full name is its accessible name and tooltip.
const productLinks = [
  {view: 'audit', label: 'PPAP Audit', short: '审核', icon: iconFileShield},
  {view: 'archive', label: '审核文件', short: '文件', icon: iconFolderOpen},
  {view: 'templates', label: '模板库', short: '模板库', icon: iconLayerGroup},
  {view: 'requirements', label: '审核规则库', short: '规则库', icon: iconSliders},
  {view: 'usage', label: '用量记录', short: '用量', icon: iconChartPie},
] as const
type ProductSurface = typeof productLinks[number]['view']
type PageKind = 'upload' | 'match' | 'results' | 'library'
const pageFrames: Record<PageKind, {taskList: 'open' | 'folded' | 'none'; flow: boolean; heading: 'frame' | 'page'}> = {
  upload: {taskList: 'folded', flow: true, heading: 'frame'},
  match: {taskList: 'folded', flow: true, heading: 'frame'},
  results: {taskList: 'open', flow: false, heading: 'page'},
  library: {taskList: 'none', flow: false, heading: 'frame'},
}
type IntakeDraft = {files: AuditTaskFile[]; uploads: UploadRow[]; customer: string; step: 'upload' | 'match'; predecessorId?: string}
const replacementRemovalReason = '本次使用补件替换；原件此前已选中'
function reconcileReplacements(files: AuditTaskFile[]): AuditTaskFile[] {
  const replaced = new Set(files.filter(file => file.selection === 'selected' && file.replacement_of_file_version_id).map(file => file.replacement_of_file_version_id))
  return files.map(file => {
    if (replaced.has(file.file.id)) return file.selection === 'selected' ? {...file, selection: 'removed', removal_reason: replacementRemovalReason} : file
    return file.selection === 'removed' && file.removal_reason === replacementRemovalReason ? {...file, selection: 'selected', removal_reason: null} : file
  })
}
const sameRuleIntent = (a: AuditTaskFile, b: AuditTaskFile) => a.preferred_rule_version_id === b.preferred_rule_version_id && Boolean(a.reference_only) === Boolean(b.reference_only) && a.replacement_of_file_version_id === b.replacement_of_file_version_id
const sameSelections = (before: AuditTaskFile[], after: AuditTaskFile[]) => before.length === after.length && before.every(file => after.some(row => sameSnapshot(row.file, file.file) && sameRuleIntent(row, file) && row.selection === file.selection && row.removal_reason === file.removal_reason && row.validation_id === file.validation_id))
const onlyUnmatchedRemovals = (before: AuditTaskFile[], after: AuditTaskFile[]) => before.length === after.length && before.every(file => after.some(row => sameSnapshot(row.file, file.file) && sameRuleIntent(row, file) && row.validation_id === file.validation_id && (row.selection === file.selection && row.removal_reason === file.removal_reason || file.selection === 'selected' && file.match?.state === 'unmatched' && row.selection === 'removed' && Boolean(row.removal_reason?.trim()))))
const onlyEndExclusions = (before: AuditTaskFile[], after: AuditTaskFile[]) => onlyUnmatchedRemovals(before, after.map(row => {
  const original = before.find(file => file.file.id === row.file.id)
  return original?.selection === 'selected' && row.selection === 'unselected' && row.removal_reason === null ? {...row, selection: 'selected' as const} : row
}))
function mergeFileIntent(local: AuditTaskFile[], current: AuditTaskFile[]): AuditTaskFile[] {
  const kept = local.map(file => {
    const next = current.find(row => row.file.id === file.file.id)
    return next ? {...next, selection: file.selection, removal_reason: file.removal_reason, preferred_rule_version_id: file.preferred_rule_version_id, reference_only: file.reference_only, rule_options: next.rule_options || file.rule_options, replacement_of_file_version_id: file.replacement_of_file_version_id} : file
  })
  const added = current.filter(file => !local.some(row => row.file.id === file.file.id)).map(file => file.selection === 'removed' ? file : {...file, selection: 'unselected' as const, removal_reason: null})
  return [...kept, ...added]
}
function taskRegressed(previous: AuditTask | null | undefined, next: AuditTask): boolean {
  if (!previous || previous.id !== next.id) return false
  if ((previous.input_revision ?? 0) > (next.input_revision ?? 0)) return true
  if ((previous.input_revision ?? 0) === (next.input_revision ?? 0) && Object.keys(taskLabels).indexOf(next.state) < Object.keys(taskLabels).indexOf(previous.state)) return true
  return Boolean(previous.round_id && (!sameFrozenInputs(previous, next) || progressRegressed(previous, next)))
}
function mergeTaskSnapshot(previous: AuditTask | null | undefined, next: AuditTask): AuditTask {
  const preference = reconcileTaskNotification(previous, next)
  const combined = taskRegressed(previous, next) ? previous! : reconcileTaskDisplay(previous?.id === next.id ? previous : undefined, next)
  return {...combined, notification_enabled: preference.notification_enabled, notification_revision: preference.notification_revision}
}

/** Controlled data comes through the adapter; this view never calls business APIs. */
export function AuditIntake({adapter, rules, onRefreshCustomers, autoObserve = true, renderOriginal, reportAdapter, sharingAdapter, archiveAdapter, supportAdapter, usageAdapter, displayAdapter, templateAdapter}: {templateAdapter?: TemplateLibraryAdapter; adapter: AuditIntakeAdapter; rules?: (active: boolean, headerActions?: HTMLElement | null) => ReactNode; onRefreshCustomers?: () => void; autoObserve?: boolean; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; archiveAdapter?: AuditArchiveAdapter; supportAdapter?: AuditSupportAdapter; usageAdapter?: AuditUsageAdapter; displayAdapter?: AuditDisplayAdapter}) {
  const access = useAccess()
  const reviewEditor = useContext(ReviewEditorContext), reviewRevision = useRef(reviewEditor?.revision)
  const readSurface = (): ProductSurface => {
    const {surface} = readAuditRoute(new URL(location.href))
    return surface === 'templates' && !templateAdapter ? 'audit' : surface === 'requirements' && !rules ? 'audit' : surface
  }
  const [surface, setSurface] = useState<ProductSurface>(readSurface)
  // Fixed for the page's life: the frame never switches shells while someone works.
  const [inFeishu] = useState(() => openedInFeishu())
  const [archiveOpened, setArchiveOpened] = useState(surface === 'archive')
  const [rulesOpened, setRulesOpened] = useState(surface === 'requirements')
  const [usageOpened, setUsageOpened] = useState(surface === 'usage')
  const [templatesOpened, setTemplatesOpened] = useState(surface === 'templates')
  const ruleDetail = () => {const params = new URL(location.href).searchParams; return params.has('systemRule')}
  const [ruleDetailOpen, setRuleDetailOpen] = useState(ruleDetail)
  const [pageActions, setPageActions] = useState<HTMLDivElement | null>(null)
  const [pageTitle, setPageTitle] = useState<HTMLElement | null>(null)
  const [supportSnapshot, setSupportSnapshot] = useState<SupportSnapshot | null>(null)
  const supportReturnTargets = useRef<(HTMLElement | null)[]>([])
  function openSupport(snapshot: SupportSnapshot, returnTo?: HTMLElement | null) {
    const trigger = returnTo ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    supportReturnTargets.current = [trigger, trigger?.closest<HTMLElement>('[role="dialog"]') || null]
    setSupportSnapshot(structuredClone(snapshot))
  }
  const [supportPageReference] = useState(() => `client-${crypto.randomUUID()}`)
  const surfaceRef = useRef(surface)
  const auditReturn = useRef<{url: string; state: unknown} | null>(null)
  function retainAuditAddress() {auditReturn.current = {url: location.href, state: history.state}}
  const [files, setFiles] = useState<AuditTaskFile[]>([])
  const [step, setStepState] = useState<'upload' | 'match'>('upload')
  const stepRef = useRef(step)
  function setStep(next: typeof step) {stepRef.current = next; setStepState(next)}
  const [customer, setCustomerState] = useState('auto')
  const currentCustomer = useRef(customer); currentCustomer.current = customer
  const customerInitialized = useRef(Boolean(readAuditRoute(new URL(location.href)).taskId || new URL(location.href).searchParams.has('uploadDraft')))
  const [customerReady, setCustomerReady] = useState(customerInitialized.current)
  function setCustomer(value: string) {customerInitialized.current = true; setCustomerReady(true); setCustomerState(value)}
  function initializeCustomer(value: string) {if (!customerInitialized.current) {customerInitialized.current = true; setCustomerReady(true); setCustomerState(value)}}
  const [predecessorId, setPredecessorId] = useState<string>()
  function chooseReplacement(fileId: string, originalId: string) {
    setFiles(rows => {
      const original = rows.find(row => row.file.id === originalId && !row.replacement_of_file_version_id)
      return reconcileReplacements(rows.map(row => row.file.id === fileId ? {...row, replacement_of_file_version_id: original?.file.id, preferred_rule_version_id: original?.preferred_rule_version_id, reference_only: original?.reference_only, rule_options: original?.rule_options} : row))
    })
  }
  const [preparingReaudit, setPreparingReaudit] = useState(false)
  const [task, setTask] = useState<AuditTask | null>(null)
  const [tasks, setTasks] = useState<AuditTask[]>([])
  const [summaries, setSummaries] = useState<TaskSummary[]>([])
  const latestSummaries = useRef(summaries); latestSummaries.current = summaries
  const latestTasks = useRef(tasks); latestTasks.current = tasks
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all')
  // The task list beside PPAP Audit (pageFrames decides per page). The person may fold or open it.
  const listEvents = adapter.events?.tasks
  const [railChoice, setRailChoice] = useState<'auto' | 'open' | 'folded'>('auto')
  // While a file or report is read its table keeps about 1000px: the task list stays open only if the rail, the list (280px),
  // the file list (320px) and the table all fit.
  const [fileOpen, setFileOpen] = useState(false), wideRail = useMinWidth(1664)
  // The sheet closes as support opens, so its own button is gone by the time focus returns.
  const [reportView, setReportView] = useState(() => new URL(location.href).searchParams.has('report'))
  const [error, setError] = useState(''), [dragging, setDragging] = useState(false)
  const [checking, setChecking] = useState<string[]>([])
  const [driveOpen, setDriveOpen] = useState(false)
  // The customer's file types are listed up to a few as a hint; the rest open on request.
  const [typesOpen, setTypesOpen] = useState(false)
  const [localFiles, setLocalFiles] = useState<File[]>([])
  const [readStatus, setReadStatus] = useState<Record<string, {busy: boolean; error: string; traceId?: string}>>({})
  const [endStatus, setEndStatus] = useState<Record<string, 'sending' | 'unknown' | 'rejected'>>({})
  const endingTasks = useRef(new Set<string>())
  const [taskList, setTaskList] = useState<{next: number | null; busy: boolean; error: string; errorScope: string | null; readScope: string | null; ids: string[]; pages: number}>({next: null, busy: false, error: '', errorScope: null, readScope: null, ids: [], pages: 0})
  const listEpoch = useRef(0)
  const latestAdapter = useRef(adapter); latestAdapter.current = adapter
  // Every current rule, read once the confirm step opens, so a wrongly recognized file can be pointed at another rule.
  const [ruleChoices, setRuleChoices] = useState<RuleChoice[]>([])
  // Read once per page; the numbers beside each customer are a hint and never gate the upload.
  const [coverageSource, setCoverageSource] = useState<CoverageSource | null>(null)
  useEffect(() => {
    if (!adapter.coverage) return
    const controller = new AbortController()
    adapter.coverage(controller.signal).then(value => {if (!controller.signal.aborted) setCoverageSource(value)}).catch(() => {})
    return () => controller.abort()
  }, [adapter.coverage])
  const coverageOf = (value: string) => customerCoverage(coverageSource, value)
  const [templateChoice, setTemplateChoice] = useState(''), [templateApply, setTemplateApply] = useState(0)
  const [ruleChanging, setRuleChanging] = useState<string[]>([])
  const [ruleSource, setRuleSource] = useState<{rule: components['schemas']['BoundRule']; clause: components['schemas']['RuleItem'] | null; filename: string} | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const ruleTrigger = useRef<HTMLButtonElement | null>(null)
  const driveTrigger = useRef<HTMLButtonElement | null>(null)
  const drafts = useRef(new Map<string, IntakeDraft>())
  // History stores an identity; the server retains intent, this page alone keeps local bytes.
  const uploadDrafts = useRef(new Map<string, IntakeDraft | {taskId: string}>())
  const activeUploadDraftId = useRef<string | null>(null)
  const draftEpoch = useRef(0)
  const validationReads = useRef(new Set<string>())
  const activeTaskId = useRef<string | null>(null)
  const taskReadEpoch = useRef(new Map<string, number>())
  const taskReads = useRef(new Set<string>())
  const restoreEpoch = useRef(0)
  const recognitionTargets = useRef(new Map<string, {epoch: number; uploadId: string | null}>())
  const recognitionReceiptRead = useRef('')
  const [recovery, setRecovery] = useState<TaskRecoveryState | null>(() => {
    const id = readAuditRoute(new URL(location.href)).taskId
    return id ? {id, state: 'loading', message: ''} : null
  })
  const preparation = useRef<ReturnType<typeof createUploadPreparation> | null>(null)
  if (!preparation.current && adapter.preparation) preparation.current = createUploadPreparation({read: id => latestAdapter.current.preparation!.read(id), save: (...args) => latestAdapter.current.preparation!.save(...args), readFile: (...args) => latestAdapter.current.preparation!.readFile(...args)})
  const [preparationStatus, setPreparationStatus] = useState<{id: string | null; phase: 'idle' | 'reading' | 'saving' | 'ready' | 'error'; message: string; conflict?: boolean}>({id: null, phase: 'idle', message: ''})
  const preparingMatch = useRef<number | null>(null)
  const [preparingMatchEpoch, setPreparingMatchEpoch] = useState<number | null>(null)
  const preparationInput = useRef({files, customer, predecessorId})
  preparationInput.current = {files, customer, predecessorId}
  function preparationValue(rows: UploadRow[]): UploadPreparation {
    const input = preparationInput.current
    return JSON.parse(JSON.stringify({version: 1, customer_preference: input.customer === 'auto' ? null : input.customer, predecessor_id: input.predecessorId ?? null, uploads: describeUploads(rows), files: preparationFiles(input.files)}))
  }
  async function persistPreparation(rows: UploadRow[]) {
    const id = activeUploadDraftId.current, session = preparation.current, epoch = draftEpoch.current
    if (!id || !session) return null
    const value = preparationValue(rows)
    const address = new URL(location.href)
    if (!activeTaskId.current && readAuditRoute(address).surface === 'audit') {address.searchParams.set('uploadDraft', id); writeAuditAddress(address); retainAuditAddress()}
    setPreparationStatus({id, phase: 'saving', message: ''})
    try {
      const saved = await session.save(id, value)
      if (activeUploadDraftId.current === id && draftEpoch.current === epoch) setPreparationStatus({id, phase: 'ready', message: ''})
      return saved
    } catch (cause) {
      if (activeUploadDraftId.current === id && draftEpoch.current === epoch) setPreparationStatus({id, phase: 'error', message: cause instanceof Error ? cause.message : '上传准备保存结果待确认，请查看原准备记录。'})
      throw cause
    }
  }
  const contentOwners = useRef<ContentOwner[]>([])
  const uploads = useUploads(adapter, (file, removed) => setFiles(rows => rows.some(row => row.file.id === file.id || row.selection !== 'removed' && row.file.file_id === file.file_id) ? rows : [...rows, {file, selection: removed ? 'removed' : 'selected', removal_reason: removed ? '传输期间排除，不纳入识别' : null, validation: {state: 'pending', reasons: ['文件待检查。']}, match: null}]), async rows => {await persistPreparation(rows)}, () => contentOwners.current)
  const v4Mode = ['UAES', '理想', '蔚来'].includes(customer)
  const active = files.filter(row => row.selection !== 'removed')
  contentOwners.current = active.map(row => ({id: row.file.id, sha256: row.file.sha256, name: row.file.original_filename}))
  const valid = active.filter(row => row.validation.state === 'valid')
  const waiting = active.filter(row => row.validation.state === 'pending' || row.validation.state === 'checking')
  const exceptions = active.filter(row => row.validation.state === 'invalid' || row.validation.state === 'error')
  const selected = valid.filter(row => row.selection === 'selected')
  // A failed or unconfirmed transfer needs the person; one still uploading only needs time.
  const transferBlocked = uploads.rows.some(row => !row.removed && (row.receipt.state === 'failed' || row.receipt.state === 'unknown'))
  const uploadingCount = uploads.rows.filter(row => !row.removed && row.receipt.state === 'uploading').length
  const blocked = recognitionHold({recognizing: false, saving: false, preparationPending: false, needsCurrent: false, transferBlocked, uploading: uploadingCount, checking: waiting.length, exceptions: exceptions.length, selected: selected.length})
  const matchingFiles = task ? files.filter(row => row.selection === 'selected') : []
  const matchFields = (row: typeof matchingFiles[number]) => [['文件类型', row.match?.document_type ? documentTypeLabel(row.match.document_type) : undefined], ['客户名称', row.match?.metadata.customer ? customerShortName(coverageSource?.customers, row.match.metadata.customer) : undefined], ['车型', row.match?.metadata.model], ['零件号', row.match?.metadata.part_number], ['零件名称', row.match?.metadata.part]] as const
  const matched = matchingFiles.filter(row => row.match?.state === 'matched' && row.match.rule)
  const references = matchingFiles.filter(row => row.match?.state === 'reference')
  const matchPending = (row: typeof matchingFiles[number]) => !row.match || row.match.state === 'matching' || row.match.state === 'pending'
  const needsHandling = matchingFiles.filter(row => !matchPending(row) && !(row.match?.state === 'matched' && row.match.rule) && row.match?.state !== 'reference')
  // Results settle file by file, so rows keep their place until recognition has finished.
  const matchGroupTone: Record<string, [string, string]> = {需要处理: ['text-warning', 'bg-warning/10 text-warning'], 已匹配: ['text-success', 'bg-success/10 text-success'], 比对资料: ['text-primary', 'bg-primary/10 text-primary'], 等待识别: ['text-muted-foreground', 'bg-muted text-muted-foreground']}
  const matchGroups = task?.state === 'matching' ? [{title: '', rows: matchingFiles}] : [{title: '需要处理', rows: needsHandling}, {title: '已匹配', rows: matched}, {title: '比对资料', rows: references}, {title: '等待识别', rows: matchingFiles.filter(matchPending)}].filter(group => group.rows.length)
  const noMatches = Boolean(task?.state === 'pending_confirm' && !matched.length && files.every(row => row.selection !== 'selected' || row.match?.state === 'unmatched' || row.match?.state === 'reference'))
  const recognitionDirty = Boolean(task && !task.round_id && (task.customer_preference !== (customer === 'auto' ? null : customer) || !onlyUnmatchedRemovals(task.files, files)))
  const endInputsDirty = Boolean(task && (task.customer_preference !== (customer === 'auto' ? null : customer) || !onlyEndExclusions(task.files, files)))
  const canEnd = noMatches && !endInputsDirty
  const workbench = task?.state === 'reviewing' || task?.state === 'completed' && task.completion_reason === 'processed'
  function saveTask(next: AuditTask, {activate = false, updateActive = true} = {}) {
    const listed = mergeTaskSnapshot(latestTasks.current.find(row => row.id === next.id), next)
    const combined = mergeTaskSnapshot(task?.id === next.id ? task : undefined, listed)
    latestTasks.current = latestTasks.current.some(row => row.id === next.id) ? latestTasks.current.map(row => row.id === next.id ? combined : row) : [...latestTasks.current, combined]
    if (activate) activeTaskId.current = next.id
    setTask(current => activate || updateActive && current?.id === next.id ? mergeTaskSnapshot(current?.id === next.id ? current : undefined, combined) : current)
    setTasks(rows => rows.some(row => row.id === next.id) ? rows.map(row => row.id === next.id ? mergeTaskSnapshot(row, combined) : row) : [...rows, combined])
    setSummaries(rows => rows.some(row => row.id === next.id) ? rows.map(row => row.id === next.id ? reconcileTaskSummary(row, summarizeTask(combined)) : row) : [summarizeTask(combined), ...rows])
    return combined
  }
  function changeDisplay(reference: DisplayReference, info: DisplayInfo) {
    const update = (current: AuditTask): AuditTask => current.id !== reference.task_id || current.round_id !== reference.round_id ? current : reconcileTaskDisplay(current, {...current, files: current.files.map(file => file.file.id === reference.file_version_id ? {...file, display_info: info} : file)})
    taskReadEpoch.current.set(reference.task_id, (taskReadEpoch.current.get(reference.task_id) || 0) + 1)
    taskReads.current.delete(reference.task_id)
    setReadStatus(rows => ({...rows, [reference.task_id]: {busy: false, error: ''}}))
    setTask(current => current ? update(current) : current)
    setTasks(rows => rows.map(update))
  }
  const displayEditor = useDisplayEditor(displayAdapter, task ? [task, ...tasks.filter(row => row.id !== task.id)] : tasks, `${surface}:${task?.id || ''}:${reportView}`, changeDisplay)
  const acceptance = useTaskAcceptance(adapter, next => {
    drafts.current.delete(next.id)
    const current = saveTask(next)
    if (activeTaskId.current === current.id) {setFiles(structuredClone(current.files)); setStep('match')}
  })
  const recognition = useTaskRecognition(adapter, (next, key) => {
    const target = recognitionTargets.current.get(key)
    if (target?.uploadId) uploadDrafts.current.set(target.uploadId, {taskId: next.id})
    if (taskRegressed(task, next) || taskRegressed(latestTasks.current.find(row => row.id === next.id), next)) return
    const activate = Boolean(target && target.epoch === draftEpoch.current && (activeTaskId.current === key || activeUploadDraftId.current === key))
    taskReadEpoch.current.set(next.id, (taskReadEpoch.current.get(next.id) || 0) + 1); taskReads.current.delete(next.id)
    setReadStatus(rows => ({...rows, [next.id]: {busy: false, error: ''}}))
    const current = saveTask(next, {activate, updateActive: activate})
    if (activate) {
      endingTasks.current.delete(next.id)
      setEndStatus(rows => {const nextRows = {...rows}; delete nextRows[next.id]; return nextRows})
      drafts.current.delete(next.id); activeUploadDraftId.current = null
      setFiles(structuredClone(current.files)); setStep('match'); setError(''); taskAddress(current.id, surfaceRef.current !== 'audit')
    }
  })
  const recognitionKey = task?.id || activeUploadDraftId.current || ''
  const recognitionState = recognition.observations[recognitionKey]
  const recognitionLocked = Boolean(recognitionState && !['accepted', 'rejected', 'conflict'].includes(recognitionState.phase))
  const recognitionNeedsCurrent = recognitionState?.phase === 'conflict' && ((task?.input_revision ?? -1) < (recognitionState.currentRevision ?? Infinity) || task?.state !== recognitionState.currentState)
  // While recognition runs, files, purposes and the customer stay as they were sent.
  const recognizing = recognitionLocked || task?.state === 'matching'
  // Everyone in the enterprise opens a task; only its creator confirms rules or starts the audit.
  const foreign = Boolean(task && !task.creator.mine)
  const inputLocked = recognitionLocked || preparingMatchEpoch === draftEpoch.current || !task && preparationStatus.id === activeUploadDraftId.current && ['reading', 'error'].includes(preparationStatus.phase) || Boolean(task && ['sending', 'unknown'].includes(endStatus[task.id]))
  useEffect(() => {
    if (!preparation.current || task || surface !== 'audit' || !activeUploadDraftId.current || ['reading', 'error'].includes(preparationStatus.phase) || preparingMatch.current === draftEpoch.current) return
    if (!files.length && !uploads.rows.length || uploads.rows.some(row => row.busy && row.input.kind === 'local' && !row.input.sha256)) return
    void persistPreparation(uploads.snapshot()).catch(() => {})
  }, [files, customer, predecessorId, uploads.rows, task, surface])
  function readRecognition() {
    recognitionTargets.current.set(recognitionKey, {epoch: draftEpoch.current, uploadId: activeUploadDraftId.current})
    void recognition.read(recognitionKey)
  }
  function readCurrentRecognition() {
    if (!recognitionState) return
    const id = recognitionState.taskId, uploadId = activeUploadDraftId.current
    const snapshot: IntakeDraft = {files: structuredClone(files), uploads: uploads.snapshot(), customer, step: 'upload', predecessorId}
    taskAddress(id); void recover(id)
    drafts.current.set(id, snapshot)
    if (uploadId) uploadDrafts.current.set(uploadId, {taskId: id})
  }
  const confirmation = task ? acceptance.observations[task.id] : undefined
  const confirmationLocked = Boolean(confirmation && confirmation.phase !== 'rejected' || task?.acceptance?.state === 'pending' && confirmation?.phase !== 'rejected')
  const scope = task ? confirmationScope(task) : null
  const basisNeedsRefresh = Boolean(task && acceptance.needsBasisRefresh(task))
  const suiteBlocks = task ? suiteAdmissionBlocks(task) : []
  const canConfirm = suiteBlocks.length === 0 && Boolean(scope?.target_file_version_ids.length) && !basisNeedsRefresh && Boolean(task?.confirmation_basis_digest) && task?.state === 'pending_confirm' && matched.length > 0 && matchingFiles.every(row => row.match?.state === 'unmatched' || row.match?.state === 'reference' || row.match?.state === 'matched' && row.match.rule) && !confirmationLocked && !recognitionLocked && !recognitionDirty && recognitionState?.phase !== 'conflict'
  const submitted = task ? submittedCount(task, recognitionDirty) : null
  // The precheck is read on the confirm page, and again when a task with nothing to submit has completed there (its report stays available).
  const precheck = usePrecheck(adapter.precheck, task, (step === 'match' && task?.state === 'pending_confirm') || (task?.state === 'completed' && !task.round_id))
  // A file-level block (unreadable, several versions) on a file that would enter the audit holds the start; content-level findings are
  // reported for the formal audit and never hold it, and other files are already excluded.
  const precheckHolds = precheck.result?.blocks.filter(item => item.scope === 'file' && scope?.target_file_version_ids.includes(item.file_version_id)) ?? []
  // Recognized files by precheck level, as on the precheck page; without a precheck the levels come from matching alone.
  // Cards stay where recognition put them, so a file taken out of the audit keeps its unchecked box until it is recognized again.
  const shownMatchRows = task ? task.files.filter(row => row.selection === 'selected').flatMap(row => {const local = files.find(file => file.file.id === row.file.id); return local?.selection === 'removed' ? [] : [local ?? row]}) : []
  const isMatchedRow = (row: AuditTaskFile) => row.match?.state === 'matched' && Boolean(row.match.rule)
  const personAction = ['matching_customer_required', 'matching_customer_conflict', 'matching_document_type_required', 'ambiguous_available_rules', 'matching_rules_customer_unassigned']
  function sectionOf(row: AuditTaskFile) {
    if (matchPending(row)) return 'pending'
    if (row.match?.state === 'reference') return 'reference'
    const level = precheck.result?.files.find(item => item.file_version_id === row.file.id)?.level
    if (level === 'block') return 'block'
    if (!isMatchedRow(row)) return row.match?.state === 'unmatched' && !row.match.candidates?.length && !row.match.gaps.some(gap => personAction.includes(gap)) ? precheck.result?.statuses.some(status => status.code === 'S01' && status.file_version_id === row.file.id) ? 'outside' : 'unmatched' : 'handle'
    return level === 'warn' ? 'warn' : 'ok'
  }
  const sectionSpec = [
    {key: 'ok', title: precheck.result ? '合规文件' : '已匹配', note: '可送审', selectable: true, card: 'ok', tone: 'text-success'},
    {key: 'warn', title: '警告级', note: '标黄，仍可勾选送审', selectable: true, card: 'warn', tone: 'text-warning'},
    {key: 'block', title: '阻断级', note: '文件级阻断移除后才能开始审核', selectable: false, card: 'block', tone: 'text-destructive'},
    {key: 'handle', title: '需要处理', note: '可选择规则、手动指定或移除', selectable: false, card: 'soft', tone: 'text-warning'},
    {key: 'outside', title: '范围外', note: '不在本客户的审核范围内，不送审、不计费', selectable: false, card: 'soft', tone: 'text-muted-foreground'},
    {key: 'unmatched', title: '未匹配：无适配规则', note: '开始审核时不送审、不计费', selectable: false, card: 'soft', tone: 'text-muted-foreground'},
    {key: 'reference', title: '比对资料', note: '不单独审核或收费', selectable: false, card: 'soft', tone: 'text-primary'},
    {key: 'pending', title: '等待识别', note: '', selectable: false, card: 'soft', tone: 'text-muted-foreground'},
  ] as const
  // Only a file-level block keeps a file out of the audit; content-level blocks are checked by the audit itself.
  const fileBlocked = (row: AuditTaskFile) => Boolean(precheck.result?.blocks.some(item => item.file_version_id === row.file.id && item.scope === 'file'))
  const matchSections = sectionSpec
    .map(section => {
      const rows = shownMatchRows.filter(row => sectionOf(row) === section.key)
      return {...section, rows, note: section.key === 'block' && !rows.some(fileBlocked) ? '内容级阻断不阻止开始审核，正式审核将核对' : section.note}
    })
    .filter(section => section.rows.length)
  const [deepSaving, setDeepSaving] = useState<string | null>(null), [deepError, setDeepError] = useState<{taskId: string; message: string} | null>(null)
  async function changeDeepMode(current: AuditTask, value: boolean) {
    if (!adapter.deepMode) return
    setDeepSaving(current.id); setDeepError(null)
    // A read already in flight must not put the previous choice back.
    taskReadEpoch.current.set(current.id, (taskReadEpoch.current.get(current.id) || 0) + 1); taskReads.current.delete(current.id)
    try {saveTask(await adapter.deepMode(current, value))}
    catch (failure) {setDeepError({taskId: current.id, message: failure instanceof Error && failure.message ? failure.message : '深度模式没有保存，请重试。'})}
    finally {setDeepSaving(null)}
  }
  function confirm(current: AuditTask) {
    taskReadEpoch.current.set(current.id, (taskReadEpoch.current.get(current.id) || 0) + 1)
    taskReads.current.delete(current.id)
    void acceptance.confirm(current)
  }
  function changeReport(next: AuditTask) {
    taskReadEpoch.current.set(next.id, (taskReadEpoch.current.get(next.id) || 0) + 1); taskReads.current.delete(next.id)
    setReadStatus(rows => ({...rows, [next.id]: {busy: false, error: ''}})); saveTask(next)
  }
  function retainDraft() {
    if (workbench || recovery) return
    const snapshot = {files: structuredClone(files), uploads: uploads.snapshot(), customer, step, predecessorId}
    if (task) drafts.current.set(task.id, snapshot)
    else if (activeUploadDraftId.current) uploadDrafts.current.set(activeUploadDraftId.current, snapshot)
  }
  function clearReads() {draftEpoch.current += 1; validationReads.current.clear(); photoScopes.current.clear(); setChecking([]); uploads.reset()}
  async function readPreparation(id: string, replace = false) {
    const session = preparation.current, source = latestAdapter.current.preparation
    if (!session || !source) return
    const operation = ++restoreEpoch.current
    setPreparationStatus({id, phase: 'reading', message: ''})
    const current = () => operation === restoreEpoch.current && activeUploadDraftId.current === id && surfaceRef.current === 'audit'
    try {
      const saved = await session.read(id)
      if (!current()) return
      if (saved.task_id) {taskAddress(saved.task_id); void recover(saved.task_id); return}
      const value = saved.upload_preparation!
      if (!replace && (files.length || uploads.rows.length) && !sameSnapshot(value, preparationValue(uploads.snapshot()))) {
        setPreparationStatus({id, phase: 'error', conflict: true, message: '已读到不同的准备资料，本页文件和选择仍保留，未覆盖已保存内容。载入已保存资料会放弃本页未保存的修改；已接收的原件不会删除。'})
        return
      }
      const [versions, inherited] = await Promise.all([
        Promise.all(saved.items.map(row => source.readFile(row.file_id, row.file_version_id))),
        value.predecessor_id && adapter.prepareReaudit ? adapter.prepareReaudit(value.predecessor_id) : null,
      ])
      if (!current()) return
      const restored = value.files.map(intent => {
        const material = saved.items.find(row => row.file_version_id === intent.file_version_id)
        const file = versions.find(row => row.id === intent.file_version_id && row.file_id === material?.file_id && row.status === 'ready')
        if (!file) throw new Error('准备记录中的原文件暂不可读取，请核对原准备记录。')
        const original = inherited?.files.find(row => row.file.id === file.id)
        return {...original, file, selection: intent.selection, removal_reason: intent.removal_reason, validation_id: intent.validation_id ?? undefined,
          preferred_rule_version_id: intent.preferred_rule_version_id ?? undefined, reference_only: intent.reference_only ?? undefined,
          replacement_of_file_version_id: intent.replacement_of_file_version_id ?? undefined, validation: {state: 'pending' as const, reasons: ['正在核对原文件检查结果。']}, match: null}
      })
      const transfers = restoreUploads(value.uploads)
      const memory = uploadDrafts.current.get(id)
      if (memory && !('taskId' in memory)) for (const row of transfers) {
        const prior = memory.uploads.find(item => item.receipt.request_id === row.receipt.request_id)
        if (prior?.input.kind === 'local' && row.input.kind === 'local' && prior.input.file instanceof File && prior.input.sha256 === row.input.sha256) row.input = prior.input
      }
      setTask(null); setFiles(restored); setCustomer(value.customer_preference || 'auto'); setPredecessorId(value.predecessor_id || undefined)
      setStep('upload'); setError(''); uploads.restore(transfers)
      setPreparationStatus({id, phase: 'ready', message: ''})
      for (const row of transfers) uploads.read(row.id)
    } catch (cause) {
      if (current()) setPreparationStatus({id, phase: 'error', message: cause instanceof Error ? cause.message : '上传准备暂不可读取。请重新查看原准备记录。'})
    }
  }
  function taskAddress(id: string | null, keepSurface = false) {
    if (!keepSurface) {surfaceRef.current = 'audit'; setSurface('audit'); setReportView(false)}
    const url = new URL(location.href)
    if (id) url.searchParams.set('task', id); else url.searchParams.delete('task')
    if (!keepSurface) {
      url.pathname = taskHref(id); url.searchParams.delete('task')
      url.searchParams.delete('report'); url.searchParams.delete('view'); url.searchParams.delete('packageRequest')
      url.searchParams.delete('uploadDraft')
      for (const key of ['archiveDoc', 'auditRef', 'archiveReport', 'fromRound', 'file', 'clause', 'focus', 'recognitionRequest', 'acceptRequest', 'checkFilter', 'round']) url.searchParams.delete(key)
    }
    const state = {...history.state, auditUploadDraft: id ? null : activeUploadDraftId.current}
    writeAuditAddress(url, keepSurface ? 'replace' : 'push', state)
    if (!keepSurface) retainAuditAddress()
    if (window.parent !== window && !keepSurface) window.parent.postMessage({type: 'ppap-audit-task', task: id, report: null}, location.origin)
  }
  function newTask() {restoreEpoch.current += 1; setRecovery(null); retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = crypto.randomUUID(); setPreparationStatus({id: null, phase: 'idle', message: ''}); setFiles([]); setTask(null); setLocalFiles([]); setStep('upload'); customerInitialized.current = false; setCustomerReady(false); setCustomerState('auto'); setPredecessorId(undefined); setError(''); taskAddress(null)}
  function taskResultsAddress() {
    const url = new URL(location.href)
    url.searchParams.delete('report'); url.searchParams.delete('packageRequest')
    return url
  }
  function showTaskResults() {
    setReportView(false)
    writeAuditAddress(taskResultsAddress(), 'push')
    if (window.parent !== window && task) window.parent.postMessage({type: 'ppap-audit-task', task: task.id, report: null, packageRequest: null}, location.origin)
  }
  async function prepareReaudit(roundId: string) {
    if (!adapter.prepareReaudit || preparingReaudit) return
    newTask(); setPreparingReaudit(true)
    const epoch = draftEpoch.current
    const address = new URL(location.href); address.searchParams.set('fromRound', roundId); writeAuditAddress(address)
    try {
      const prepared = await adapter.prepareReaudit(roundId)
      if (epoch !== draftEpoch.current) return
      if (prepared.predecessor_id !== roundId) throw new Error('重审来源不一致。')
      // A rule with a newer version is audited with it unless the person keeps the original's (P3 reminder).
      setFiles(prepared.files.map(file => withRuleVersion(file, 'newer'))); setCustomer(prepared.customer_preference || 'auto'); setPredecessorId(roundId); setStep('upload')
    } catch {if (epoch === draftEpoch.current) setError('未能查看原审核的固定输入。原任务没有改变，请返回原任务后重试。')}
    finally {setPreparingReaudit(false)}
  }
  async function recover(id: string, login = false) {
    const epoch = ++restoreEpoch.current
    acceptance.suspend(id)
    taskReadEpoch.current.set(id, (taskReadEpoch.current.get(id) || 0) + 1); taskReads.current.delete(id); setReadStatus(rows => ({...rows, [id]: {busy: false, error: ''}}))
    retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = null; setTask(null); setFiles([]); setRecovery({id, state: 'loading', message: ''})
    try {
      if (login) await adapter.authenticate()
      const params = new URL(location.href).searchParams
      const recognitionRequest = params.get('recognitionRequest')
      if (recognitionRequest) {
        const receipt = await adapter.readRecognition(id, recognitionRequest)
        if (receipt.request_id !== recognitionRequest || receipt.task_id !== id || receipt.state !== 'accepted') throw new Error('recognition_receipt_unknown')
      }
      let next = await adapter.readTask(id, params.get('acceptRequest') || undefined)
      if (restoreEpoch.current !== epoch) return
      const behind = () => taskIsBehindSummary(next, latestSummaries.current.find(row => row.id === id))
      if (behind()) next = await adapter.readTask(id, params.get('acceptRequest') || undefined)
      if (restoreEpoch.current !== epoch) return
      if (next.id !== id) throw new Error('task_identity_mismatch')
      if (behind()) throw new Error('task_snapshot_changed')
      if (!acceptance.restore(next)) throw new Error('acceptance_snapshot_mismatch')
      if (recognition.get(id)) recognitionTargets.current.set(id, {epoch: draftEpoch.current, uploadId: null})
      setEndStatus(rows => {if (rows[id] !== 'rejected') return rows; const nextRows = {...rows}; delete nextRows[id]; return nextRows})
      const current = saveTask(next, {activate: true})
      setPredecessorId(current.predecessor_id)
      const saved = !current.round_id && current.acceptance?.state !== 'pending' ? drafts.current.get(current.id) : undefined
      if (saved) uploads.restore(saved.uploads)
      setFiles(structuredClone(saved ? mergeFileIntent(saved.files, current.files) : current.files)); setCustomer(saved?.customer ?? current.customer_preference ?? 'auto'); setStep(saved?.step ?? 'match'); setRecovery(null)
    } catch (failure) {
      if (restoreEpoch.current !== epoch) return
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      const state = status === 401 ? 'unauthenticated' : status === 403 ? 'forbidden' : status === 404 ? 'not_found' : 'error'
      setRecovery({id, state, traceId: requestTrace(failure), message: state === 'unauthenticated' ? '登录已失效，请重新登录。' : state === 'forbidden' ? '当前账号无权访问此任务。' : state === 'not_found' ? '未找到此任务，请核对链接。' : '暂时无法读取任务，请重试。'})
    }
  }
  /** A Round link (Feishu write-back, review hints, a re-audit's original) opens the task the Round belongs to. */
  async function openRoundTask(roundId: string) {
    const epoch = ++restoreEpoch.current
    setRecovery({id: roundId, state: 'loading', message: ''})
    try {
      const round = await adapter.readRound!(roundId, new AbortController().signal)
      if (epoch !== restoreEpoch.current) return
      const id = round.id === roundId ? round.source_draft_id : null
      if (!id) throw new Error('round_without_task')
      const url = new URL(location.href); url.searchParams.delete('round')
      url.pathname = taskHref(id)
      writeAuditAddress(url)
      void recover(id)
    } catch {if (epoch === restoreEpoch.current) setRecovery({id: roundId, state: 'not_found', message: '未找到这次审核对应的任务，请核对链接。'})}
  }
  function restoreAddress() {
    setLocalFiles([])
    setSupportSnapshot(null)
    const params = new URL(location.href).searchParams, view = readSurface()
    if (view !== 'audit') {
      surfaceRef.current = view; setSurface(view)
      if (view === 'archive') setArchiveOpened(true); else if (view === 'requirements') setRulesOpened(true); else if (view === 'usage') setUsageOpened(true)
      return
    }
    const fromOtherSurface = surfaceRef.current !== 'audit'
    surfaceRef.current = 'audit'
    setSurface('audit')
    setReportView(new URL(location.href).searchParams.has('report'))
    const id = readAuditRoute(new URL(location.href)).taskId
    // checkFilter belongs to a task's review, not to a new audit.
    if (!id && params.has('checkFilter')) {const url = new URL(location.href); url.searchParams.delete('checkFilter'); writeAuditAddress(url)}
    if (!id && params.get('round') && adapter.readRound) {void openRoundTask(params.get('round')!); return}
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: id, report: new URL(location.href).searchParams.get('report'), packageRequest: new URL(location.href).searchParams.get('packageRequest')}, location.origin)
    const localId = params.get('uploadDraft') || (typeof history.state?.auditUploadDraft === 'string' ? history.state.auditUploadDraft : null)
    retainAuditAddress()
    const unresolvedAcceptance = !task?.round_id && confirmationLocked
    if (id ? fromOtherSurface && id === activeTaskId.current && !unresolvedAcceptance : localId && localId === activeUploadDraftId.current) return
    if (id) void recover(id)
    else {
      const draftId = localId || crypto.randomUUID()
      history.replaceState({...history.state, auditUploadDraft: draftId}, '')
      retainAuditAddress()
      const saved = uploadDrafts.current.get(draftId)
      if (!saved && params.get('fromRound') && adapter.prepareReaudit) {void prepareReaudit(params.get('fromRound')!); return}
      if (saved && 'taskId' in saved) {
        taskAddress(saved.taskId)
        if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: saved.taskId, report: null}, location.origin)
        void recover(saved.taskId)
        return
      }
      retainDraft(); restoreEpoch.current += 1; clearReads(); setRecovery(null); activeTaskId.current = null; activeUploadDraftId.current = draftId
      setPreparationStatus({id: null, phase: 'idle', message: ''})
      if (params.has('uploadDraft') && preparation.current) {setTask(null); setFiles([]); setStep('upload'); setError(''); void readPreparation(draftId, true); return}
      if (recognition.get(draftId)) recognitionTargets.current.set(draftId, {epoch: draftEpoch.current, uploadId: draftId})
      if (saved) uploads.restore(saved.uploads)
      setTask(null); setFiles(structuredClone(saved?.files ?? [])); if (saved) setCustomer(saved.customer); else {customerInitialized.current = false; setCustomerReady(false); setCustomerState('auto')} setStep(saved?.step ?? 'upload'); setError('')
      setPredecessorId(saved?.predecessorId)
    }
  }
  const addressCurrent = useRef(restoreAddress)
  addressCurrent.current = restoreAddress
  useEffect(() => {
    const changed = () => {addressCurrent.current(); setRuleDetailOpen(ruleDetail())}
    const remember = () => {setRuleDetailOpen(ruleDetail()); if (readAuditRoute(new URL(location.href)).surface === 'audit') retainAuditAddress()}
    const followRule = () => setRuleDetailOpen(ruleDetail())
    changed(); window.addEventListener('popstate', changed); window.addEventListener(auditAddressChanged, remember); window.addEventListener(ruleAddressChanged, followRule)
    return () => {restoreEpoch.current += 1; window.removeEventListener('popstate', changed); window.removeEventListener(auditAddressChanged, remember); window.removeEventListener(ruleAddressChanged, followRule)}
  }, [])
  function receive(picked: File[]) {
    if (!picked.length || !customerReady || confirmationLocked || inputLocked || recognizing || workbench) return
    if (adapter.drive?.rootFolder) {setLocalFiles(picked); return}
    uploads.add(picked.map(file => ({kind: 'local', file}))); setError('')
  }
  function receiveInFolder(destination: {token: string; name: string}) {
    if (!customerReady || confirmationLocked || inputLocked || recognizing || workbench) return
    uploads.add(localFiles.map(file => ({kind: 'local', file, destination}))); setLocalFiles([]); setError('')
  }
  function receiveDrive(picked: components['schemas']['DriveFile'][]) {
    if (!customerReady || confirmationLocked || inputLocked || recognizing || workbench) return
    uploads.add(picked.map(file => ({kind: 'feishu', file}))); setDriveOpen(false); setError('')
  }
  // Inside Feishu, Feishu's own cloud document picker; the web picker when it is not available here.
  async function chooseDrive(trigger: HTMLButtonElement) {
    driveTrigger.current = trigger
    const picked = inFeishu ? await pickFeishuDocuments() : null
    if (!picked) {setDriveOpen(true); return}
    if (picked.files.length) receiveDrive(picked.files)
    if (picked.skipped.length) setError(`${picked.skipped.join('、')} 不能审核：仅支持飞书文档、表格、多维表格，以及 Excel、Word、PDF 文件。`)
  }
  const driveButton = adapter.drive && <Button variant="outline" disabled={inputLocked || recognizing || !customerReady} onClick={event => void chooseDrive(event.currentTarget)}><Icon icon={iconCloud} aria-hidden="true"/>从飞书云文档选择</Button>
  function remove(id: string, reason?: string) {
    if (confirmationLocked || inputLocked || recognizing || workbench) return
    uploads.rows.filter(row => row.receipt.file?.id === id).forEach(row => uploads.remove(row.id))
    setFiles(rows => reconcileReplacements(rows.map(row => row.file.id === id ? {...row, selection: 'removed', removal_reason: reason || row.validation.reasons[0] || (step === 'match' ? '未匹配到规则' : '识别前移除')} : row)))
  }
  // Transfers that have not produced a file yet; the upload page lists them among its files.
  const transferRows = uploads.rows.filter(row => !row.removed && !files.some(file => file.file.id === row.receipt.file?.id))
  function clearDraft() {
    if (confirmationLocked || inputLocked || recognizing || workbench) return
    for (const row of active) remove(row.file.id)
    for (const row of transferRows) uploads.remove(row.id)
  }
  function toggle(id: string, checked: boolean) {if (!confirmationLocked && !inputLocked && !recognizing && !workbench) setFiles(rows => reconcileReplacements(rows.map(row => row.file.id === id ? {...row, selection: checked ? 'selected' : 'unselected', removal_reason: null} : row)))}
  const photoScopes = useRef(new Set<string>())
  async function usePhotoPurpose(row: AuditTaskFile) {
    if (customer !== 'UAES' || inputLocked || recognizing || validationReads.current.has(row.file.id)) return
    photoScopes.current.add(row.file.id)
    const next = {...row, validation_id: undefined, validation_scope: undefined, validation: {state: 'checking' as const, reasons: ['正在按生产件样品照片检查原件…']}}
    setFiles(rows => rows.map(file => file.file.id === row.file.id ? next : file))
    await readValidation(next)
  }
  function changeCustomer(value: string) {
    if (recognizing) return
    currentCustomer.current = value
    setCustomer(value)
    const scoped = new Set(photoScopes.current)
    photoScopes.current.clear()
    setFiles(rows => rows.map(row => ({...row, preferred_rule_version_id: undefined, ...((row.validation_scope || scoped.has(row.file.id)) && value !== 'UAES' ? {validation_id: undefined, validation_scope: undefined, validation: {state: 'pending' as const, reasons: ['审核客户已变更，需按当前用途重新检查。']}} : {})})))
  }
  async function readValidation(row: AuditTaskFile) {
    if (inputLocked || validationReads.current.has(row.file.id)) return
    const epoch = draftEpoch.current
    validationReads.current.add(row.file.id); setChecking(ids => [...ids, row.file.id])
    const wantsPhoto = customer === 'UAES' && (photoScopes.current.has(row.file.id) || Boolean(row.validation_scope))
    try {
      const result = await adapter.readValidation(row, wantsPhoto ? {customer_preference: 'UAES', preset_key: 'U13'} : customer !== 'UAES' && row.validation_scope ? null : undefined)
      if (draftEpoch.current !== epoch) return
      if (result.validation_scope && currentCustomer.current !== 'UAES') {
        // A restored receipt can reveal an earlier photo purpose. Preserve the
        // file, then check its current ordinary purpose with a separate identity.
        setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, validation_id: undefined, validation_scope: undefined, validation: {state: 'pending', reasons: ['审核客户已变更，需按当前用途重新检查。']}} : file))
        return
      }
      setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, validation_scope: result.validation_scope, ...result} : file))
    } catch (failure) {
      if (draftEpoch.current !== epoch || wantsPhoto && currentCustomer.current !== 'UAES') return
      const requestId = failure instanceof Error && 'validationRequestId' in failure && typeof failure.validationRequestId === 'string' ? failure.validationRequestId : undefined
      setFiles(rows => rows.map(file => file.file.id === row.file.id && file.selection !== 'removed' ? {...file, ...(requestId ? {validation_id: requestId} : {}), validation: {state: 'error', reasons: ['检查结果读取失败。已有文件仍保留；重新读取不会重启审核。']}} : file))
    } finally {
      if (draftEpoch.current === epoch) {validationReads.current.delete(row.file.id); setChecking(ids => ids.filter(id => id !== row.file.id))}
    }
  }
  useEffect(() => {
    if (!adapter.autoValidate || step !== 'upload' || inputLocked) return
    const pending = files.filter(row => row.selection !== 'removed' && ['pending', 'checking'].includes(row.validation.state))
    if (!pending.length) return
    const read = () => {if (!document.hidden) for (const row of pending) void readValidation(row)}
    const timer = window.setTimeout(read, 1500)
    document.addEventListener('visibilitychange', read)
    return () => {window.clearTimeout(timer); document.removeEventListener('visibilitychange', read)}
  }, [adapter, files, step, inputLocked])
  async function startMatching() {
    if (blocked || confirmationLocked || inputLocked || recognitionNeedsCurrent || workbench || preparingMatch.current === draftEpoch.current) return
    const key = task?.id || activeUploadDraftId.current || crypto.randomUUID()
    if (!task && !activeUploadDraftId.current) activeUploadDraftId.current = key
    const epoch = draftEpoch.current, input = {files: structuredClone(files), customer: customer === 'auto' ? null : customer, previous: task, predecessorId}
    preparingMatch.current = epoch
    setPreparingMatchEpoch(epoch)
    let saved: components['schemas']['Draft'] | null = null
    try {
      saved = !task ? await persistPreparation(uploads.snapshot()) : null
    } catch {
      // The preparation error retains this input and offers readback.
      return
    }
    finally {
      if (preparingMatch.current === epoch) preparingMatch.current = null
      setPreparingMatchEpoch(current => current === epoch ? null : current)
    }
    if (epoch !== draftEpoch.current) return
    // Recognition now owns the request lock; preparation no longer owns the page.
    recognitionTargets.current.set(key, {epoch, uploadId: activeUploadDraftId.current})
    if (task) {taskReadEpoch.current.set(task.id, (taskReadEpoch.current.get(task.id) || 0) + 1); taskReads.current.delete(task.id)}
    await recognition.start(key, {...input, ...(saved ? {preparation: {id: saved.id, revision: saved.preparation_revision!}} : {})})
  }
  async function endTask(current: AuditTask) {
    if (endInputsDirty || recognitionState?.phase === 'conflict') {setError('文件选择或输入版本已有变更。请读取当前任务并重新识别，再开始审核。'); return}
    if (endingTasks.current.has(current.id) || recognitionLocked) return
    endingTasks.current.add(current.id); setEndStatus(rows => ({...rows, [current.id]: 'sending'}))
    const input = {...current, files: structuredClone(files)}
    try {
      const next = await adapter.endTask(input)
      if (next.id !== current.id || next.input_revision !== current.input_revision || next.state !== 'completed' || next.completion_reason !== 'no_matching_rules' || !sameSelections(input.files, next.files)) throw new Error('end_task_receipt_mismatch')
      saveTask(next)
    } catch (failure) {
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      if (status === 400 || status === 409) endingTasks.current.delete(current.id)
      setEndStatus(rows => ({...rows, [current.id]: status === 400 || status === 409 ? 'rejected' : 'unknown'}))
    }
  }
  async function readMatching(current: AuditTask) {
    if (taskReads.current.has(current.id)) return null
    const epoch = taskReadEpoch.current.get(current.id)
    taskReads.current.add(current.id)
    setReadStatus(rows => ({...rows, [current.id]: {...rows[current.id], busy: true, error: rows[current.id]?.error || ''}}))
    try {
      const next = await adapter.readTask(current.id)
      if (taskReadEpoch.current.get(current.id) !== epoch) return null
      if (next.id !== current.id) throw new Error('task_identity_mismatch')
      if (!current.round_id && current.input_revision !== next.input_revision) throw new Error('task_input_revision_mismatch')
      if (current.round_id && !sameFrozenInputs(current, next)) throw new Error('frozen_input_mismatch')
      if (progressRegressed(current, next)) throw new Error('regressive_progress')
      const chosen = saveTask(next)
      const saved = drafts.current.get(chosen.id)
      if (saved?.step === 'match') saved.files = mergeFileIntent(saved.files, structuredClone(chosen.files))
      if (activeTaskId.current === chosen.id && (stepRef.current === 'match' || stepRef.current === 'upload')) setFiles(rows => mergeFileIntent(rows, structuredClone(chosen.files)))
      setReadStatus(rows => ({...rows, [current.id]: {busy: true, error: ''}}))
      return true
    } catch (failure) {
      if (taskReadEpoch.current.get(current.id) !== epoch) return null
      setReadStatus(rows => ({...rows, [current.id]: {busy: false, traceId: requestTrace(failure), error: failure instanceof Error && failure.message === 'regressive_progress' ? '收到较早或不完整的审核进度，已保留当前结果。请稍后重新读取。' : '任务结果读取失败，任务与文件仍保留。重新读取不会重新创建任务或开始审核。'}}))
      return false
    } finally {
      if (taskReadEpoch.current.get(current.id) === epoch) {taskReads.current.delete(current.id); setReadStatus(rows => ({...rows, [current.id]: {...rows[current.id], error: rows[current.id]?.error || '', busy: false}}))}
    }
  }
  useEffect(() => {
    if (reviewRevision.current === reviewEditor?.revision) return
    reviewRevision.current = reviewEditor?.revision
    if (task?.round_id) {
      taskReadEpoch.current.set(task.id, (taskReadEpoch.current.get(task.id) || 0) + 1)
      taskReads.current.delete(task.id)
      void readMatching(task)
    }
  }, [reviewEditor?.revision])
  // The report center observes the same task until its PDF reaches a terminal state.
  const observedTaskEpoch = task ? taskReadEpoch.current.get(task.id) : undefined
  const observation = useTaskObservation(task?.id, autoObserve && surface === 'audit' && !recovery && canObserveTask(task) && (step === 'match' || step === 'upload' && task?.state === 'matching'), () => task && activeTaskId.current === task.id && surfaceRef.current === 'audit' && (stepRef.current === 'match' || stepRef.current === 'upload') && taskReadEpoch.current.get(task.id) === observedTaskEpoch ? readMatching(task) : Promise.resolve(null), adapter.events?.task)
  // An unclear recognition receipt is read once from the original request. Do not start another recognition.
  useEffect(() => {
    if (step !== 'upload' || recognitionState?.phase !== 'unknown') return
    if (recognitionReceiptRead.current === recognitionState.requestId) return
    recognitionReceiptRead.current = recognitionState.requestId
    readRecognition()
  }, [step, recognitionState])
  // Even an empty successful read belongs to its search and state filter.
  const taskListState = filter === 'all' ? null : filter as AuditTask['state']
  const taskListScope = JSON.stringify([query, taskListState])
  const currentListScope = useRef(taskListScope); currentListScope.current = taskListScope
  const listStatus = taskListStatus(taskList, taskListScope)
  const taskListNext = taskList.readScope === taskListScope ? taskList.next : null
  async function loadTasks(offset = 0, signal?: AbortSignal, silent = false) {
    if (offset !== 0 && (taskList.readScope !== taskListScope || offset !== taskList.next)) return
    const epoch = ++listEpoch.current
    const isCurrent = () => listEpoch.current === epoch && currentListScope.current === taskListScope && !signal?.aborted
    if (!silent) setTaskList(current => ({...current, busy: true, error: '', errorScope: null}))
    try {
      // Refresh only this search's opened pages, then publish them together.
      const count = offset === 0 && taskList.readScope === taskListScope ? Math.max(taskList.pages, 1) : 1
      const incoming: TaskSummary[] = []
      let next: number | null = offset, pages = offset === 0 ? 0 : taskList.pages
      for (let index = 0; index < count && next !== null; index++) {
        const at: number = next
        const page = await latestAdapter.current.listTasks({q: query, state: taskListState, offset: at, signal})
        if (!isCurrent()) return
        if (page.next_offset !== null && page.next_offset <= at) throw new Error('task_list_cursor_invalid')
        incoming.push(...page.tasks); pages++; next = page.next_offset
      }
      const read = [...new Map(incoming.map(row => [row.id, row])).values()]
      setSummaries(rows => [...rows.map(row => {
        const next = read.find(candidate => candidate.id === row.id)
        return next ? reconcileTaskSummary(row, next) : row
      }), ...read.filter(next => !rows.some(row => row.id === next.id))])
      setTaskList({next, pages, busy: false, error: '', errorScope: null, readScope: taskListScope, ids: offset === 0 ? read.map(row => row.id) : [...new Set([...taskList.ids, ...read.map(row => row.id)])]})
    } catch {if (isCurrent()) setTaskList(current => ({...current, busy: false, error: '任务列表读取失败。当前文件与任务仍保留。', errorScope: taskListScope}))}
  }
  const loadTasksCurrent = useRef(loadTasks); loadTasksCurrent.current = loadTasks
  useEffect(() => {
    if (surface !== 'audit') return
    // A slow read is never aborted by tab focus or a notice; a change during a read reads once more.
    let controller: AbortController | undefined, reading = false, again = false, alive = true
    let poll: number | undefined, settle: number | undefined
    const read = (silent = true, notice = false) => {
      if (!alive || document.hidden) return
      // Timers and focus never queue a read; only a change notice during a read reads once more.
      if (reading) {if (notice) again = true; return}
      controller = new AbortController(); reading = true
      void loadTasksCurrent.current(0, controller.signal, silent).finally(() => {reading = false; if (again) {again = false; read(true, true)}})
    }
    const timer = window.setTimeout(() => read(false), query ? 200 : 0)
    // Without live notices the list is read every 15 seconds.
    const startPolling = () => {if (alive && poll === undefined) poll = window.setInterval(read, 15000)}
    const unsubscribe = listEvents ? listEvents({
      onReady: () => read(true, true),
      onChange: () => {window.clearTimeout(settle); settle = window.setTimeout(() => read(true, true), 300)},
      onUnavailable: startPolling,
    }) : (startPolling(), undefined)
    const visible = () => read()
    document.addEventListener('visibilitychange', visible)
    return () => {alive = false; window.clearTimeout(timer); window.clearTimeout(settle); window.clearInterval(poll); unsubscribe?.(); document.removeEventListener('visibilitychange', visible); controller?.abort(); listEpoch.current += 1}
  }, [query, filter, surface, listEvents])
  const shownTasks = summaries.filter(row => (filter === 'all' || row.state === filter) && `${row.number} ${subjectLabel(row.subject)} ${row.files.map(file => file.file.original_filename).join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  function showSurface(value: ProductSurface) {
    if (surfaceRef.current === 'audit' && value !== 'audit') retainAuditAddress()
    if (value === 'audit' && auditReturn.current) {
      history.pushState(auditReturn.current.state, '', auditReturn.current.url)
      restoreAddress()
      return
    }
    if (value === 'archive') setArchiveOpened(true)
    if (value === 'requirements') setRulesOpened(true)
    if (value === 'usage') setUsageOpened(true)
    if (value === 'templates') setTemplatesOpened(true)
    const url = new URL(location.href)
    for (const key of ['archiveDoc', 'auditRef', 'archiveReport', 'packageRequest', 'round']) url.searchParams.delete(key)
    url.pathname = value === 'audit' ? taskHref(activeTaskId.current) : surfaceAddress(value)
    url.searchParams.delete('view'); url.searchParams.delete('task'); url.searchParams.delete('report')
    // One page's own address state (rule tabs, chosen rule or checker) must not follow the person to another page.
    if (value !== 'requirements') for (const key of rulesAddressKeys) url.searchParams.delete(key)
    history.pushState(history.state, '', url)
    if (value === 'audit') {restoreAddress(); return}
    surfaceRef.current = value
    setSurface(value)
    // A repeated top-level entry leaves its detail without changing the active surface.
    if (value === surface || value === 'requirements') window.dispatchEvent(new PopStateEvent('popstate'))
  }
  /** The rules page from its template list, or one uploaded rule's program. Platform programs have no page here: administrators update them. */
  function openTemplates(submit: boolean) {
    showSurface('templates')
    if (submit) {const url = new URL(location.href); url.searchParams.set('templateRequest', '1'); writeAuditAddress(url)}
  }
  function openRules() {
    showSurface('requirements')
    const url = new URL(location.href)
    for (const key of rulesAddressKeys) url.searchParams.delete(key)
    writeAuditAddress(url)
  }
  // One 64px light icon rail (browser shell): each item is its icon over its short name; the current page is highlighted.
  const railItem = 'flex size-11 flex-col items-center justify-center gap-1 rounded-lg text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground aria-[current=page]:bg-sidebar-primary aria-[current=page]:text-sidebar-primary-foreground'
  // Inside Feishu the same entries sit in a top bar as pills (design.md「Page Frame」: two shells, one page).
  const pillItem = 'flex h-8 shrink-0 items-center gap-1.5 rounded-md px-3 text-sm text-foreground transition-colors hover:bg-muted aria-[current=page]:bg-secondary-hover aria-[current=page]:font-medium'
  const topNavigation = <nav aria-label="产品导航" className="flex min-w-0 items-center gap-1 overflow-x-auto">
    {productLinks.filter(link => link.view !== 'requirements' || rules).map(({view, label, short, icon}) => {
      const content = <><Icon icon={icon} aria-hidden="true" className="size-3.5 text-muted-foreground"/><span aria-hidden="true">{short}</span></>
      return view === 'templates' && !templateAdapter
        ? <a key={view} className={pillItem} href={TEMPLATE_GUIDE_URL} target="_blank" rel="noopener noreferrer" aria-label="模板库（在飞书新窗口打开）">{content}</a>
        : <button key={view} type="button" className={pillItem} aria-label={label} aria-current={surface === view ? 'page' : undefined} onClick={() => view === 'requirements' ? openRules() : showSurface(view)}>{content}</button>
    })}
  </nav>
  const navigation = <nav aria-label="产品导航" className="grid justify-items-center gap-2">
    {productLinks.filter(link => link.view !== 'requirements' || rules).map(({view, label, short, icon}) => {
      const active = surface === view
      const content = <><Icon icon={icon} aria-hidden="true" className="size-4.5"/><span aria-hidden="true" className="text-2xs leading-none">{short}</span></>
      return <Tooltip key={view}><TooltipTrigger asChild>{view === 'templates' && !templateAdapter
        ? <a className={railItem} href={TEMPLATE_GUIDE_URL} target="_blank" rel="noopener noreferrer" aria-label="模板库（在飞书新窗口打开）">{content}</a>
        : <button type="button" className={railItem} aria-label={label} aria-current={active ? 'page' : undefined} onClick={() => view === 'requirements' ? openRules() : showSurface(view)}>{content}</button>}</TooltipTrigger><TooltipContent side="right">{label}</TooltipContent></Tooltip>
    })}
  </nav>
  // Files stopped for the same reason need the same action, so the advice appears once above them.
  useEffect(() => {
    if (step !== 'match' || !adapter.ruleChoices || ruleChoices.length) return
    const controller = new AbortController()
    // Without the list, recognition's own candidates remain the only choices.
    void adapter.ruleChoices(controller.signal).then(setRuleChoices).catch(() => undefined)
    return () => controller.abort()
  }, [step, adapter, ruleChoices.length])
  // Recognition reads the files as they are after the template set each file's rule.
  useEffect(() => {if (templateApply) void startMatching()}, [templateApply])
  const templates = [...new Set(ruleChoices.flatMap(rule => rule.template ? [rule.template] : []))].sort((a, b) => (b === lastTemplate() ? 1 : 0) - (a === lastTemplate() ? 1 : 0) || templateName(a).localeCompare(templateName(b), 'zh-CN'))
  function chooseTemplate(key: string) {
    setTemplateChoice(key)
    try {localStorage.setItem(lastTemplateKey, key)} catch { /* The choice still applies; it is only not remembered. */ }
    const byType = new Map(ruleChoices.filter(rule => rule.template === key && rule.type).map(rule => [rule.type!, rule.id]))
    // A customer's rules make that customer the task's customer, so the page names it and counts its required files.
    // The template key names the customer its rules belong to (the same rules the customer catalog is read from).
    const owner = /^(?:system|customer):(.+)$/.exec(key)?.[1]
    if (owner) setCustomer(owner)
    setFiles(rows => rows.map(row => {
      const id = row.match?.document_type ? byType.get(row.match.document_type) : undefined
      return id ? {...row, preferred_rule_version_id: id} : row
    }))
    setTemplateApply(value => value + 1)
  }
  // Recognition picks a rule; a person can still point the file at another one and recognize it again.
  const ruleOptions = (row: AuditTaskFile) => {
    const match = row.match
    if (!match || matchPending(row) || match.state === 'reference') return []
    const candidates = match.candidates ?? []
    const options = [...candidates.map(rule => ({id: rule.id, title: rule.title})), ...ruleChoicesFor(ruleChoices, task?.customer_preference).filter(rule => !candidates.some(item => item.id === rule.id))]
    return options.length < (match.state === 'matched' ? 2 : 1) ? [] : options
  }
  const ruleChoiceOpen = (row: AuditTaskFile) => (row.match?.candidates?.length ?? 0) > 1 || ruleChanging.includes(row.file.id)
  const ruleChoiceButton = (row: AuditTaskFile) => ruleOptions(row).length > 0 && !ruleChoiceOpen(row) && <Button variant="ghost" size="sm" onClick={() => setRuleChanging(ids => [...ids, row.file.id])}>{row.match?.state === 'matched' ? '更换规则' : '手动选择规则'}</Button>
  const ruleChoice = (row: AuditTaskFile) => {
    const options = ruleOptions(row)
    if (!options.length || !ruleChoiceOpen(row)) return null
    return <div className="space-y-2"><Label>{row.match?.state === 'matched' ? '更换规则后重新识别' : '选择适用规则后重新识别'}</Label><p className="text-xs text-muted-foreground">重新识别会按当前选择重新识别本批全部文件。</p><Select value={row.preferred_rule_version_id || ''} onValueChange={value => setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, preferred_rule_version_id: value} : file))}><SelectTrigger aria-label={`${row.file.original_filename} 的适用规则`}><SelectValue placeholder="请选择规则，不会自动选择"/></SelectTrigger><SelectContent>{options.map(rule => <SelectItem key={rule.id} value={rule.id}>{rule.title}</SelectItem>)}</SelectContent></Select>{row.preferred_rule_version_id && row.preferred_rule_version_id !== row.match?.rule?.id && <Button variant="outline" size="sm" disabled={recognitionLocked || Boolean(blocked)} onClick={() => void startMatching()}>按所选规则重新识别</Button>}</div>
  }
  const sameReasonBatches = (rows: typeof matchingFiles) => {
    const batches = new Map<string, typeof matchingFiles>()
    for (const row of rows) {
      const reason = row.match?.state === 'unmatched' && !(row.match.candidates && row.match.candidates.length > 0) && !row.match.gaps.some(gap => !isMatchingReason(gap) && analysisNotice(gap)) ? row.match.gaps.find(isMatchingReason) ?? 'none' : row.file.id
      batches.set(reason, [...(batches.get(reason) ?? []), row])
    }
    return [...batches.values()]
  }
  const cardTone = {ok: 'bg-card', warn: 'border border-warning/30 bg-card', block: 'border border-destructive/30 bg-card', soft: 'bg-card/60'} as const
  const matchBadge = (row: AuditTaskFile): [UploadFileStatus['tone'], string] => {const status = matchStatus(row.match, task?.state === 'matching'); return [status.tone, status.label]}
  // Inside a file card a notice is plain text: what happened, then what to do.
  const cardNotice = (title: string, body: string, action?: ReactNode) => <div className="space-y-1.5"><p className="text-sm font-medium">{title}</p><p className="text-xs text-muted-foreground">{body}</p>{action}</div>
  // One card per file: format, name and where it came from; the recognized facts; then the rule or what to do next.
  const matchRow = (row: typeof matchingFiles[number], grouped = false, tone: keyof typeof cardTone = 'ok', selectable = false) => {
    const [badgeVariant, badgeLabel] = matchBadge(row)
    const newer = row.match?.rule && row.match.rule.id === newerRuleOf(row)?.id
    return <section key={row.file.id} className={`flex gap-3 rounded-lg p-3.5 ${cardTone[tone]}`}>
            {selectable && <Checkbox className="mt-0.5" aria-label={`送审 ${row.file.original_filename}`} checked={row.selection === 'selected'} disabled={confirmationLocked} onCheckedChange={value => toggle(row.file.id, value === true)}/>}
            <div className="min-w-0 flex-1 space-y-2.5">
            <div className="flex flex-wrap items-start justify-between gap-3"><div className="flex min-w-0 flex-1 items-start gap-3"><span aria-hidden="true" className="flex size-9 shrink-0 items-center justify-center rounded-md border bg-card"><FileIcon name={row.file.original_filename}/></span><div className="min-w-0 flex-1 space-y-0.5"><div className="flex flex-wrap items-center gap-2"><h3 className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere]">{row.file.original_filename}</h3><Badge variant="outline">{row.file.origin?.available && row.file.origin.provider === 'feishu' ? '飞书云盘' : '本地上传'}</Badge></div>
            {/* Recognized facts appear once recognition has produced them, never as placeholders. */}
            {!matchPending(row) && <p className="text-xs leading-relaxed text-muted-foreground">{matchFields(row).map(([label, value], index) => <span key={label}>{index > 0 && <span aria-hidden="true" className="mx-1.5 text-muted-foreground/60">·</span>}{label}：{value || '—'}</span>)}</p>}</div></div>{/* A group heading already names matched, unmatched, reference and waiting files. */}{!(grouped && ['已匹配', '未匹配', '比对资料', '等待识别'].includes(badgeLabel)) && <Badge variant={badgeVariant}>{badgeLabel}</Badge>}</div>
            <PrecheckNotes result={precheck.result} fileVersionId={row.file.id}/>
            {sourceMismatches(precheck.result?.statuses, row.file.id).map(fact => {
              // S07 only: the counterpart is associated but names another part. A missing counterpart (S04) never comes here.
              const found = fact.found_file_version_id
              const choices = files.filter(file => file.selection === 'selected' && file.file.id !== found && file.file.id !== row.file.id && file.match?.document_type === fact.document_type).map(file => ({id: file.file.id, name: file.file.original_filename}))
              const removeHeld = scope?.target_file_version_ids.includes(found) ? '该比对文件本身也在送审，请用【更改归属】处理。' : ''
              return <SourceMismatchNotice key={found} fact={fact} choices={choices} removeHeld={removeHeld} disabled={confirmationLocked || inputLocked || recognizing}
                onReplace={() => remove(found, `已更换为本批其他《${fact.document}》`)} onReassign={() => setStep('upload')} onRemove={() => remove(found, `与被审文件${fact.identity}不一致，已移除比对引用`)}/>
            })}
            {row.match?.state === 'matched' && row.match.rule ? <Collapsible data-rule-version={row.match.rule.id}><div className="flex flex-wrap items-center justify-between gap-2"><p className="flex flex-wrap items-center gap-1.5 text-xs"><span className="font-medium">{ruleCustomer(row.match.rule) && <>{ruleCustomer(row.match.rule)} · </>}{row.match.rule.title}</span>{newer && <Badge variant="info">新版本</Badge>}</p><div className="flex flex-wrap items-center gap-1">{ruleChoiceButton(row)}<CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="group" aria-label={`查看 ${row.file.original_filename} 的条款`}>{row.match.rule.body.kind === 'engine' ? `${row.match.rule.body.rules.length} 条条款` : '文件存在性检查'}<Icon icon={iconChevronDown} aria-hidden="true" className="transition-transform group-data-[state=open]:rotate-180"/></Button></CollapsibleTrigger></div></div><CollapsibleContent><ul aria-label={`${row.file.original_filename} · 匹配条款`} className="grid gap-1 pt-2">{row.match.rule.body.kind === 'engine' ? row.match.rule.body.rules.map(clause => <li key={clause.id}><Button variant="ghost" size="sm" className="h-auto max-w-full justify-start whitespace-normal text-left" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource({rule: row.match!.rule!, clause, filename: row.file.original_filename})}}><Icon icon={iconBookOpen} aria-hidden="true"/>{clauseLabel(row.match!.rule!.body.kind === 'engine' ? row.match!.rule!.body.rules : [], clause.id)}</Button></li>) : <li><Button variant="ghost" size="sm" className="justify-start" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource({rule: row.match!.rule!, clause: null, filename: row.file.original_filename})}}>文件存在性检查</Button></li>}</ul></CollapsibleContent></Collapsible>
              : row.match?.state === 'reference' ? null
              : row.match?.state === 'matching' || !row.match || row.match.state === 'pending' ? <ReadSkeleton label="等待识别结果…" kind="inline"/>
              : sectionOf(row) === 'outside' ? <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-muted-foreground">{precheck.result?.statuses.find(status => status.code === 'S01' && status.file_version_id === row.file.id)?.note}</p><Button variant="ghost" size="sm" onClick={() => remove(row.file.id)}>移除</Button></div>
              : row.match.state === 'unmatched' || row.match.gaps.some(isMatchingReason) ? <MatchingFeedback gaps={row.match.gaps} candidates={row.match.candidates} onCheck={() => setStep('upload')} onRules={rules ? () => showSurface('requirements') : undefined} onTemplates={templateAdapter ? openTemplates : undefined} onRemove={() => remove(row.file.id)}/>
              : checkerSuiteUnavailable(row.match) ? cardNotice('这类文件还没有可用的检查程序', '文件本身没有问题。检查程序由平台离线准备；如需准备或更新，请联系支持。准备好后回到这里读取最新结果。', <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '准备检查程序', facts: [{label: '文件', value: row.file.original_filename}, ...(row.match?.rule ? [{label: '规则', value: row.match.rule.title}] : [])]})}>联系支持</Button>)
              : cardNotice('暂时无法确认匹配结果', '请返回检查文件后重新识别。')}
            {row.match?.gaps.filter(gap => !isMatchingReason(gap)).map(gap => analysisNotice(gap)).filter((notice): notice is string => Boolean(notice)).map(notice => <p key={notice} className="text-xs text-muted-foreground">{notice}</p>)}
            {row.match?.state !== 'matched' && ruleChoiceButton(row)}{ruleChoice(row)}
          </div></section>
  }
  // Which frame regions each page uses (design.md「布局」). Single-action pages (upload, precheck and rule matching) fold the task list and
  // keep what is decided at the top and the action at the bottom; result pages keep the list open and carry their own heading.
  const pageKind: PageKind = surface !== 'audit' ? 'library' : recovery || workbench || task?.state === 'completed' ? 'results' : step
  const frame = pageFrames[pageKind]
  const railCollapsed = railChoice === 'folded' || railChoice === 'auto' && (frame.taskList === 'folded' || !wideRail && (reportView || fileOpen))
  const taskRail = frame.taskList !== 'none' && (railCollapsed
    ? <aside aria-label="审核任务" className="flex w-12 shrink-0 flex-col items-center border-r bg-card py-3"><Button variant="quiet" size="icon" aria-label="展开审核任务" onClick={() => setRailChoice('open')}><Icon icon={iconAnglesRight} aria-hidden="true"/></Button></aside>
    : <aside aria-label="审核任务" className="flex min-h-0 w-70 shrink-0 flex-col self-stretch overflow-hidden border-r bg-card"><TaskRail customerName={value => workbench ? customerFullName(coverageSource?.customers, value) : customerShortName(coverageSource?.customers, value)} currentId={task?.id} tasks={shownTasks} labels={taskLabels} query={query} onQuery={setQuery} filter={filter} onFilter={setFilter} busy={listStatus.busy} ready={listStatus.ready} error={listStatus.error} hasMore={taskListNext !== null} hasTasks={summaries.length > 0} onMore={() => void loadTasks(listStatus.error ? 0 : taskListNext!)} onNew={newTask} onOpen={id => {taskAddress(id); void recover(id)}} onCollapse={() => setRailChoice('folded')}/></aside>)
  const supportScenario = surface === 'archive' ? '审核文件' : surface === 'requirements' ? '审核规则' : surface === 'usage' ? '用量记录' : surface === 'templates' ? '模板库' : workbench ? '审核结果' : '上传与文件核对'
  function contactReadSupport(current: AuditTask) {openSupport({scenario: '读取审核任务', traceId: readStatus[current.id]?.traceId, facts: [{label: '任务标识', value: current.id}, ...(current.round_id ? [{label: '审核轮次', value: current.round_id}] : []), {label: '服务说明', value: readStatus[current.id]?.error || ''}]})}
  const recognitionSupport = recognitionState && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '识别文件与匹配规则', traceId: recognitionState.traceId, facts: [{label: '任务标识', value: recognitionState.taskId}, {label: '识别请求', value: recognitionState.requestId}, {label: '文件', value: files.map(row => row.file.original_filename).join('\n')}, {label: '服务说明', value: recognitionState.message}]})}>联系支持</Button>
  // Library pages at their top level, and a new audit before it has a task, are named by the current pill and the window title;
  // inside Feishu they get no header row of their own.
  const libraryTop = surface === 'archive' || surface === 'templates' || surface === 'usage' || (surface === 'requirements' && !ruleDetailOpen)
  const quietHeader = libraryTop || (surface === 'audit' && !task && !recovery && step === 'upload')
  const pageContext = surface !== 'audit' ? productLinks.find(link => link.view === surface)?.label : task ? task.number : recovery ? reportView ? '审核报告' : '审核任务' : '新建审核'
  // Inside Feishu the window title names the current page (Feishu shows the address when it is blank).
  useEffect(() => {if (inFeishu) document.title = pageContext || 'PPAP Audit'}, [inFeishu, pageContext])
  const taskCoverage = task?.customer_preference ? coverageOf(task.customer_preference) : null
  // The one step line of the audit: 上传文件 › 预检并匹配规则 › AI 审核, each with its number, or a check once done.
  const stepper = <ol aria-label="审核步骤" className="flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-muted-foreground">{['上传文件', '预检并匹配规则', 'AI 审核'].map((label, index) => {
    const current = step === 'match' ? 1 : 0, done = index < current, now = current === index
    return <li key={label} aria-current={now ? 'step' : undefined} data-state={done ? 'complete' : now ? 'current' : 'upcoming'} className={`flex items-center gap-1.5 ${now ? 'font-semibold text-primary' : done ? 'font-medium text-primary' : ''}`}>
      <span aria-hidden="true" className={`flex size-4 items-center justify-center rounded-full border text-2xs leading-none ${now ? 'border-primary bg-primary text-primary-foreground' : done ? 'border-primary-line bg-primary-soft' : 'bg-muted'}`}>{done ? <Icon icon={iconCheck} className="size-2"/> : index + 1}</span>{label}{index < 2 && <span aria-hidden="true" className="ml-1.5 font-normal text-muted-foreground/60">›</span>}</li>
  })}</ol>
  const reauditing = Boolean(predecessorId || task?.predecessor_id)
  const reauditNote = '原任务和报告都保留。本次重新审核所有文件，人工复核从头开始。'
  // Rules that changed since the original audit, named once each; switching versions recognizes the files again.
  const newerRules = [...new Set(files.filter(file => file.selection === 'selected').map(newerRuleOf).filter(rule => rule !== undefined).map(ruleName))]
  function chooseRuleVersion(choice: RuleVersionChoice) {
    setFiles(rows => rows.map(row => withRuleVersion(row, choice)))
    setTemplateApply(value => value + 1)
  }
  const fileSlots = customerFileSlots(coverageSource, customer)
  const neededTypes = listedTypes(fileSlots ?? [], typesOpen)
  // Transfers still on their way are lines among the files. A removed one whose outcome is still open joins the removed files, where its
  // original request can be read back; a known failure (a retried attempt among them) created nothing and needs no line.
  const pendingTransfers = transferRows.filter(row => row.receipt.state !== 'ready')
  const removedTransfers = uploads.rows.filter(row => row.removed && ['uploading', 'unknown'].includes(row.receipt.state))
  const addedFiles = active.filter(row => row.validation.state !== 'invalid' && row.validation.state !== 'error')
  const hasUploadWork = active.length > 0 || transferRows.length > 0
  const compactUpload = hasUploadWork || Boolean(fileSlots?.length)
  const transferAttach = useTransferAttach((id, file) => void uploads.attach(id, file))
  const transferActions = {onRead: uploads.read, onRetry: uploads.retry, onResume: uploads.resume, onAttach: transferAttach.choose, onRemove: uploads.remove, onCheckDrive: adapter.drive ? (trigger: HTMLButtonElement) => {driveTrigger.current = trigger; setDriveOpen(true)} : undefined}
  // Recognition results are read on the confirm-rules page. Unchanged files return to them; changed files are recognized again.
  const canViewResults = Boolean(task && !task.round_id && ['matching', 'pending_confirm'].includes(task.state) && (!recognitionDirty || canEnd) && !workbench)
  // Recognition starts only when the person asks for it; it does not charge.
  const canStartRecognition = surface === 'audit' && !workbench && active.length > 0 && (!task || recognitionDirty) && task?.state !== 'matching' && !canViewResults
  // Why recognition cannot start now.
  const recognitionHeld = recognitionHold({recognizing, saving: preparingMatchEpoch === draftEpoch.current, preparationPending: !task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'error', needsCurrent: recognitionNeedsCurrent, transferBlocked, uploading: uploadingCount, checking: waiting.length, exceptions: exceptions.length, selected: selected.length})
  const uploadStatus = recognizing ? recognitionHeld : canViewResults || !hasUploadWork ? '' : recognitionHeld
  const setPurpose = (id: string, value: string) => setFiles(rows => rows.map(file => file.file.id === id ? {...file, reference_only: value === 'reference' ? true : undefined, preferred_rule_version_id: undefined} : file))
  // Files that do not enter the audit, each with its reason once. Unmatched files are explained in their own confirm-rules section and are not repeated here.
  const shownMatchIds = new Set(shownMatchRows.filter(row => ['outside', 'unmatched', 'handle'].includes(sectionOf(row)) || !scope?.excluded_files.some(item => item.file_version_id === row.file.id)).map(row => row.file.id))
  const scopeReasons = new Map((task && !recognitionDirty ? scope?.excluded_files ?? [] : []).map(row => [row.file_version_id, row.reason]))
  const excludedFiles = step === 'upload' ? task ? files.filter(row => row.selection === 'removed').map(row => ({id: row.file.id, name: row.file.original_filename, reason: row.removal_reason || '移除原因未提供'})) : []
    : files.filter(row => !shownMatchIds.has(row.file.id) && (row.selection !== 'selected' || scopeReasons.has(row.file.id))).map(row => ({id: row.file.id, name: row.file.original_filename,
      reason: scopeReasons.get(row.file.id) || (row.selection === 'removed' ? row.removal_reason : null) || (row.selection === 'unselected' ? '本次未选择，不进入审核' : '移除原因未提供')}))
  const removedList = (excludedFiles.length > 0 || step === 'upload' && removedTransfers.length > 0) && <section aria-label="已移除文件" className="space-y-2 border-t pt-4"><h2 className="text-sm font-semibold">已移除文件</h2><ul className="grid gap-2">
    {excludedFiles.map(row => <li key={row.id} className="grid gap-0.5"><FileName name={row.name}/><span className="text-xs text-muted-foreground">{row.reason}</span></li>)}
    {step === 'upload' && removedTransfers.map(row => <TransferItem key={row.id} row={row} {...transferActions}/>)}
  </ul></section>
  // One line per file: name, then compact controls at its end (purpose; for a re-audit, how its rule is found and what it replaces), then its state.
  const uploadFile = (row: AuditTaskFile, showStatus = true) => {
    const name = row.file.original_filename
    const statusId = `upload-file-status-${row.file.id}`
    const selectable = row.validation.state === 'valid'
    const reaudit = Boolean(predecessorId) && row.selection === 'selected'
    const purpose = selectable && v4Mode && <Select value={row.reference_only ? 'reference' : 'target'} onValueChange={value => setPurpose(row.file.id, value)}><SelectTrigger size="sm" aria-label={`${name} 本轮用途`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="target" description="正式审核收费">待审文件</SelectItem><SelectItem value="reference" description="不单独审核或收费">比对资料</SelectItem></SelectContent></Select>
    const replacement = reaudit && (row.replacement_of_file_version_id || !row.rule_options?.length && !row.reference_only) && <Select value={row.replacement_of_file_version_id || 'new'} onValueChange={id => chooseReplacement(row.file.id, id)}><SelectTrigger size="sm" aria-label={`${name} 的补件关系`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="new">新增文件 · 重新匹配</SelectItem>{files.filter(other => other.file.id !== row.file.id && !other.replacement_of_file_version_id && (other.rule_options?.length || other.reference_only)).map(other => <SelectItem key={other.file.id} value={other.file.id} disabled={files.some(file => file.file.id !== row.file.id && file.selection === 'selected' && file.replacement_of_file_version_id === other.file.id)}>替换：{other.file.original_filename}</SelectItem>)}</SelectContent></Select>
    const status = uploadFileStatus(row)
    // A reference file's purpose control already says so; without one, the state says it is not audited on its own.
    const statusLabel = !showStatus || !status ? '' : status.label === '比对资料' ? purpose ? '' : '比对资料 · 不单独审核' : status.label
    return <li key={row.file.id} className="grid gap-1">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        {selectable && <Checkbox aria-label={`选择 ${name}`} aria-describedby={statusLabel ? statusId : undefined} checked={row.selection === 'selected'} onCheckedChange={value => toggle(row.file.id, value === true)}/>}
        <FileTypeBadge name={name}/>
        <span className="min-w-0 flex-1 basis-40 truncate text-sm" title={name}>{name}</span>
        {purpose}{replacement}
        {statusLabel && <Badge id={statusId} variant={status!.tone}>{statusLabel}</Badge>}
        <Button variant="ghost" size="icon-sm" aria-label={`移除 ${name}`} onClick={() => remove(row.file.id)}><Icon icon={iconXmark}/></Button>
      </div>
      {(row.validation.state === 'invalid' || row.validation.state === 'error') && <p className="text-xs text-destructive">{row.validation.reasons.join('；') || '暂未完成检查'}</p>}
      {row.validation.state === 'error' && <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} aria-label={`重新读取 ${name} 检查结果`} onClick={() => void readValidation(row)}>{checking.includes(row.file.id) ? '读取中…' : '重新读取'}</Button>}
      {customer === 'UAES' && /\.(?:png|jpe?g|webp|pdf)$/i.test(name) && (row.validation_scope || photoScopes.current.has(row.file.id) ? <p className="text-xs text-muted-foreground">本轮用途：生产件样品照片；内容仍需人工确认</p> : <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} onClick={() => void usePhotoPurpose(row)}>作为生产件样品照片检查</Button>)}
    </li>
  }
  const flowPage = frame.flow && surface === 'audit'
  // Only what the start depends on: how many go to the audit, and how many are held back or need a hand.
  const sectionCount = (key: string) => matchSections.find(section => section.key === key)?.rows.length ?? 0
  const verdict = [submitted === null ? '送审 待确认' : `送审 ${submitted} 份`, ...([['block', '阻断'], ['handle', '需要处理'], ['unmatched', '未匹配']] as const).flatMap(([key, label]) => sectionCount(key) ? [`${label} ${sectionCount(key)} 份`] : [])].join(' · ')
  const notices = <>
        {error && <Alert variant="destructive"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
        {preparingReaudit && <ReadSkeleton label="正在读取原审核的文件和规则…" kind={'detail'}/>}
        {predecessorId && !workbench && step === 'upload' && <p className="text-sm text-muted-foreground"><span className="font-medium text-foreground">关联重审</span> · {reauditNote}</p>}
        {recognitionState && recognitionLocked && <Alert data-request-id={recognitionState.requestId}><AlertTitle>{recognitionState.phase === 'sending' ? '正在提交识别请求' : '识别请求待确认'}</AlertTitle><AlertDescription><p>{recognitionState.message || '正在等待同一请求的结果，文件与选择已保留。'}</p>{['unknown', 'reading'].includes(recognitionState.phase) && <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={recognitionState.phase === 'reading'} onClick={readRecognition}>{recognitionState.phase === 'reading' ? '正在读取识别结果…' : '读取识别结果'}</Button>{recognitionSupport}</div>}</AlertDescription></Alert>}
        {recognitionState?.phase === 'rejected' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>本次识别未被接收</AlertTitle><AlertDescription><p>{recognitionState.message}</p><div className="flex flex-wrap gap-2">{task?.id === recognitionState.taskId && <Button variant="outline" size="sm" onClick={readCurrentRecognition}>读取当前任务</Button>}{recognitionSupport}</div></AlertDescription></Alert>}
        {recognitionState?.phase === 'conflict' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>任务输入版本已更新</AlertTitle><AlertDescription><p>{recognitionState.message}</p><Button variant="outline" size="sm" onClick={readCurrentRecognition}>读取当前任务</Button></AlertDescription></Alert>}
        {task && task.state !== 'completed' && endStatus[task.id] === 'rejected' && <Alert><AlertTitle>本次未被接收</AlertTitle><AlertDescription><p>输入版本可能已更新。当前文件与选择已保留，请读取当前任务后再操作。</p><Button variant="outline" size="sm" onClick={() => void recover(task.id)}>读取当前任务</Button></AlertDescription></Alert>}
        {task && task.state !== 'completed' && endStatus[task.id] === 'unknown' && <Alert><AlertTitle>提交结果待确认</AlertTitle><AlertDescription><p>尚未确认任务已完成。请读取同一任务，不要重复开始审核。</p>{readStatus[task.id]?.error && <p>{readStatus[task.id].error}</p>}<Button variant="outline" size="sm" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>读取任务状态</Button></AlertDescription></Alert>}
  </>
  return <TooltipProvider><SupportEntryContext.Provider value={openSupport}><div className="flex h-dvh overflow-hidden bg-canvas text-sm text-foreground">
    <a href="#audit-main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:p-3 focus-visible:outline-ring focus-visible:ring-2 focus-visible:ring-ring">跳到主要内容</a>
    {!inFeishu && <aside aria-label="产品侧栏" className="flex h-dvh w-16 shrink-0 flex-col items-center justify-between border-r border-sidebar-border bg-sidebar py-4">
      <div className="grid justify-items-center gap-6"><span className="flex size-10 items-center justify-center rounded-lg bg-linear-135 from-brand-light to-primary text-primary-foreground"><Icon icon={iconWandMagicSparkles} aria-hidden="true" className="size-5"/><span className="sr-only">PPAP Audit</span></span>{navigation}</div>
      <Tooltip><TooltipTrigger asChild><button type="button" className={railItem} aria-label="联系支持" onClick={() => openSupport({scenario: supportScenario})}><Icon icon={iconCircleQuestion} aria-hidden="true" className="size-4.5"/></button></TooltipTrigger><TooltipContent side="right">联系支持</TooltipContent></Tooltip>
    </aside>}
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      {inFeishu && <div className="flex h-12 shrink-0 items-center gap-4 border-b bg-card px-4">
        <span className="flex shrink-0 items-center gap-2 text-base font-semibold"><span aria-hidden="true" className="flex size-6 items-center justify-center rounded-md bg-linear-135 from-brand-light to-primary text-primary-foreground"><Icon icon={iconWandMagicSparkles} className="size-3"/></span>PPAP Audit</span>
        {topNavigation}
        <div ref={setPageActions} role="group" aria-label="页面操作" className="ml-auto flex min-w-0 items-center gap-2"/>
        <Button variant="ghost" size="icon-sm" className="shrink-0" aria-label="联系支持" onClick={() => openSupport({scenario: supportScenario})}><Icon icon={iconCircleQuestion} className="text-muted-foreground"/></Button>
      </div>}
      {inFeishu && quietHeader ? <h1 className="sr-only">{pageContext}</h1> : <header className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-3 border-b bg-card px-6 py-2"><div className="flex min-w-0 items-center gap-3">
        {frame.heading === 'page' && workbench && !reportView ? <div ref={setPageTitle} className="min-w-0"/> : surface === 'audit' && reportView ? <nav aria-label="当前位置" className="min-w-0"><ol className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <li className="flex min-w-0 items-center gap-1">{task && !recovery ? <a href={taskResultsAddress().href} aria-label="返回审核结果" className="font-medium hover:underline" onClick={event => {if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {event.preventDefault(); showTaskResults()}}}>{task.number}</a> : <span className="text-muted-foreground">审核任务</span>}</li>
          <li aria-hidden="true" className="text-muted-foreground">/</li><li aria-current="page" className="font-medium">审核报告</li>
        </ol></nav> : <nav aria-label="当前位置" className="min-w-0"><ol className="flex flex-wrap items-center gap-x-3 gap-y-1">{surface === 'audit' && task && !recovery && <><li className="text-xs text-muted-foreground">任务详情</li><li aria-hidden="true" className="text-muted-foreground/60">/</li></>}<li aria-current="page" className="flex min-w-0 items-center gap-1 font-medium">{surface === 'requirements' && ruleDetailOpen ? <a href="/requirements" className="min-w-0 truncate hover:underline" onClick={event => {if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); const url = new URL(location.href); for (const key of rulesAddressKeys) url.searchParams.delete(key); url.pathname = surfaceAddress('requirements'); url.searchParams.delete('view'); writeAuditAddress(url)}}>{pageContext}</a> : (surface === 'templates' || (surface === 'requirements' && !ruleDetailOpen) || surface === 'audit' && !task && !recovery && step === 'upload') ? <h1 className={`min-w-0 truncate ${surface === 'audit' && step === 'upload' ? 'text-base font-semibold' : 'text-sm font-medium'}`}>{pageContext}</h1> : surface === 'audit' && task && !recovery ? <span className="min-w-0 truncate font-semibold">{task.number}</span> : surface === 'archive' ? <button type="button" className="min-w-0 truncate hover:underline" onClick={() => showSurface('archive')}>{pageContext}</button> : <span className="min-w-0 truncate">{pageContext}</span>}</li>{surface === 'archive' && <li ref={setPageTitle} className="flex min-w-0 items-center gap-2 empty:hidden"/>}{surface === 'audit' && task && !recovery && !workbench && <li><span role="status"><Badge variant={taskStateTone(task.state)}>{task.state === 'matching' ? `识别中 ${matchingFiles.filter(row => row.match && ['matched', 'unmatched', 'reference', 'failed'].includes(row.match.state)).length}/${matchingFiles.length}` : taskLabels[task.state]}</Badge></span></li>}</ol></nav>}
      </div>{!inFeishu && <div className="flex shrink-0 items-center gap-4"><div ref={setPageActions} role="group" aria-label="页面操作" className="flex min-w-0 flex-wrap items-center gap-2"/>{access?.principal && <AccountMenu compact/>}</div>}</header>}
      {/* PPAP Audit keeps its task list on the left, on the new-audit page and on every task. */}
      <main id="audit-main" tabIndex={-1} className="flex min-h-0 w-full flex-1 overflow-hidden">
        {taskRail}
        <div className={surface === 'audit' && workbench && !reportView ? 'h-full w-full min-w-0' : flowPage ? 'flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden' : surface === 'archive' || surface === 'templates' || (surface === 'requirements' && !ruleDetailOpen) ? 'flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden p-6' : 'min-w-0 flex-1 overflow-y-auto p-6'}>
        <div className={surface === 'audit' && workbench && !reportView ? 'h-full' : flowPage ? 'flex min-h-0 min-w-0 flex-1 flex-col' : surface === 'archive' || surface === 'templates' || (surface === 'requirements' && !ruleDetailOpen) ? 'flex min-h-0 min-w-0 flex-1 flex-col' : `mx-auto w-full space-y-6 ${recovery || workbench || surface !== 'audit' ? '' : step === 'match' ? '' : 'max-w-5xl'}`}>
        {archiveOpened && <div className={surface === 'archive' ? 'flex min-h-0 min-w-0 flex-1 flex-col' : 'hidden'}>{archiveAdapter ? <AuditArchive active={surface === 'archive'} headerTitle={surface === 'archive' && !inFeishu ? pageTitle : null} adapter={archiveAdapter} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} precheckAdapter={adapter.precheck} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}}/> : <Alert><AlertTitle>审核文件暂不可用</AlertTitle><AlertDescription>请稍后重试。</AlertDescription></Alert>}</div>}
        {rulesOpened && <div className={surface !== 'requirements' ? 'hidden' : ruleDetailOpen ? 'min-w-0' : 'flex min-h-0 min-w-0 flex-1 flex-col'}>{rules?.(surface === 'requirements', surface === 'requirements' ? pageActions : null)}</div>}
        {templatesOpened && templateAdapter && <div className={surface === 'templates' ? 'flex min-h-0 min-w-0 flex-1 flex-col' : 'hidden'}><TemplateLibraryPage adapter={templateAdapter}/></div>}
        {usageOpened && <div hidden={surface !== 'usage'}><AuditUsage headerActions={surface === 'usage' ? pageActions : null} active={surface === 'usage'} adapter={usageAdapter} archiveAdapter={archiveAdapter} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}} onSupport={notice => openSupport({scenario: `用量记录 · ${notice}`})}/></div>}
        <div className={surface !== 'audit' ? 'hidden' : workbench && !reportView ? 'flex h-full flex-col' : flowPage ? 'flex min-h-0 flex-1 flex-col' : 'space-y-6'}>
        {!flowPage && notices}
        {recovery ? <TaskRecovery recovery={recovery} report={reportView} onRead={() => void recover(recovery.id, recovery.state === 'unauthenticated')} onSupport={() => openSupport({scenario: '读取审核任务', traceId: recovery.traceId, facts: [{label: '任务标识', value: recovery.id}, {label: '服务说明', value: recovery.message}]})}/> : workbench && task ? <AuditWorkspace live={observation.mode === 'live'} steps={observation.steps} onFileOpen={setFileOpen} readRound={adapter.readRound} onReaudit={adapter.prepareReaudit ? () => void prepareReaudit(task.round_id!) : undefined} headerActions={surface === 'audit' ? pageActions : null} headerTitle={surface === 'audit' ? pageTitle : null} key={task.id} task={task} busy={readStatus[task.id]?.busy || false} readError={readStatus[task.id]?.error || ''} onReadSupport={() => contactReadSupport(task)} onRead={() => void readMatching(task)} onChange={saveTask} renderOriginal={renderOriginal} reportAdapter={reportAdapter} notificationAdapter={adapter.notifications} onReportChange={changeReport} onReportView={setReportView} sharingAdapter={sharingAdapter} onEditDisplay={displayAdapter ? file => displayEditor.open({task_id: task.id, round_id: task.round_id!, file_version_id: file.file.id}, file) : undefined}/> : task?.state === 'completed' ? <section aria-labelledby="zero-target-title" className="space-y-4 py-8">
          {/* A task with nothing to submit completes here on the confirm page: no Round, no report, no charge. */}
          <Icon icon={iconCheck} aria-hidden="true" className="size-8 text-success"/><h1 id="zero-target-title" className="text-xl font-semibold">本次任务无送审文件</h1>
          <p>{zeroTargetNote(task.files)}未开始审核，未扣费，也没有生成审核报告。</p>
          <AuditExcludedFiles files={task.files}/>
          {precheck.result && adapter.precheck && <PrecheckReport adapter={adapter.precheck} taskId={task.id} result={precheck.result}/>}
          <div className="flex flex-wrap items-center gap-2"><Button onClick={newTask}>返回上传新文件<Icon icon={iconArrowRight}/></Button></div>
        </section> : <>
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'reading' && <section role="status" aria-label="正在恢复上传准备" className="space-y-4"><h1 className="sr-only">上传准备</h1><ReadSkeleton label="正在恢复上传的文件…" kind="list" rows={3}/></section>}
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'saving' && <ReadSkeleton label="正在保存上传准备…" kind="inline"/>}
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'error' && <Alert variant="destructive"><AlertTitle>上传准备待确认</AlertTitle><AlertDescription><p>{preparationStatus.message}</p><div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => void readPreparation(activeUploadDraftId.current!)}>读取准备记录</Button>{preparationStatus.conflict && <Button variant="outline" size="sm" onClick={() => {retainDraft(); void readPreparation(activeUploadDraftId.current!, true)}}>载入已保存资料</Button>}</div></AlertDescription></Alert>}
        <fieldset hidden={preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'reading'} disabled={inputLocked || preparingReaudit || foreign} className={preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'reading' ? 'hidden' : 'contents'}>
        <input ref={input} disabled={!customerReady} type="file" className="sr-only" aria-label="选择审核文件" multiple accept={acceptedUploads(customer).accept} onChange={event => {receive(Array.from(event.target.files || [])); event.target.value = ''}}/>
        {step === 'upload' ? <PageLayout narrow top={stepper} bottom={(uploadStatus || canStartRecognition || canViewResults) && <>
          {(uploadStatus || canStartRecognition || canViewResults) && <div className="flex flex-wrap items-center justify-end gap-3">
            <p role="status" className="mr-auto text-sm text-muted-foreground">{uploadStatus}</p>
            {canStartRecognition && <Button disabled={recognizing || recognitionNeedsCurrent || Boolean(blocked) || inputLocked || confirmationLocked} onClick={() => void startMatching()}>开始识别<Icon icon={iconArrowRight}/></Button>}
            {canViewResults && <Button onClick={() => setStep('match')}>查看识别结果<Icon icon={iconArrowRight}/></Button>}
          </div>}
        </>}>
        {notices}
        {step === 'upload' && <section aria-label="上传文件" data-dragging={dragging} className="rounded-lg border border-transparent bg-card transition-colors data-[dragging=true]:border-primary" onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={event => {if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false)}} onDrop={event => {event.preventDefault(); setDragging(false); receive(Array.from(event.dataTransfer.files))}}>
          <fieldset disabled={recognizing} className="min-w-0 space-y-5 p-6">
          {surface === 'audit' && <CustomerMode key={draftEpoch.current} value={customer} ready={customerReady} options={adapter.customers ?? []} adapter={adapter.customerDefaults} onInitialDefault={initializeCustomer} onRefresh={onRefreshCustomers} onChange={changeCustomer} coverage={coverageOf} typeSetMissing={value => customerTypeSetMissing(coverageSource, value)}/>}
          <div className={compactUpload ? 'flex flex-wrap items-center gap-3 rounded-md border border-dashed border-primary-line px-4 py-3' : 'flex flex-col items-center gap-3 rounded-md border border-dashed border-primary-line bg-primary-soft/40 px-4 py-8 text-center'}>
            <div className="flex flex-wrap items-center gap-2">
              {compactUpload && hasUploadWork && <span className="text-sm font-medium">继续添加</span>}
              <Button variant="outline" disabled={!customerReady} onClick={() => input.current?.click()}>选择本地文件</Button>
              {driveButton}
              {adapter.samples && <Button variant="ghost" onClick={() => {setFiles(rows => [...rows, ...adapter.samples!()]); setError('')}}>使用三类文件示例</Button>}
            </div>
            <p className="text-sm text-muted-foreground">{acceptedUploads(customer).hint}</p>
            {hasUploadWork && <Button variant="link" size="sm" className="ml-auto" onClick={clearDraft}>清空</Button>}
          </div>
          {transferAttach.picker}
          {neededTypes.shown.length > 0 && <p className="text-sm text-muted-foreground">该客户需要：{neededTypes.shown.map(slot => slot.label).join('、')}{neededTypes.toggle && <Button variant="link" size="sm" aria-expanded={typesOpen} onClick={() => setTypesOpen(open => !open)}>{neededTypes.toggle}</Button>}</p>}
          {(pendingTransfers.length > 0 || addedFiles.length > 0 || uploads.skipped.length > 0) && <section aria-label="已添加" className="space-y-2"><div className="flex flex-wrap items-baseline justify-between gap-2"><h2 className="text-sm font-medium">已添加</h2><AuditDurationEstimate count={pendingTransfers.length + addedFiles.length}/></div><ul className="grid gap-2">{pendingTransfers.map(row => <TransferItem key={row.id} row={row} {...transferActions}/>)}{addedFiles.map(row => uploadFile(row))}{uploads.skipped.map(row => <SkippedItem key={row.id} row={row} onDismiss={uploads.dismiss}/>)}</ul></section>}
          {exceptions.length > 0 && <section aria-label="异常文件" className="space-y-2"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-medium text-destructive">异常文件 · {exceptions.length} 份</h2><Button variant="outline" size="sm" onClick={() => exceptions.forEach(row => remove(row.file.id))}>全部移除</Button></div><ul className="grid gap-2">{exceptions.map(row => uploadFile(row, false))}</ul></section>}
          {removedList}
          </fieldset>
        </section>}
        </PageLayout> : <PageLayout top={<>
          <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-1"><div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-semibold tracking-tight">预检并匹配规则</h1>{reauditing && <Badge variant="info">关联重审</Badge>}</div>
            {task && <p className="text-xs text-muted-foreground">{reauditing ? reauditNote : '系统识别每份文件的要素并匹配客户规则；核对规则后点击【开始审核】。'}</p>}
            {task && foreign && <p className="text-xs text-muted-foreground">{task.creator.name || '同事'} 发起，只有发起人可以确认规则和开始审核。</p>}
          </div>{task?.state === 'pending_confirm' && <p role="status" className="pt-1 text-xs tabular-nums text-muted-foreground">当前客户：{task.customer_preference ? customerShortName(coverageSource?.customers, task.customer_preference) : '自动识别'}{taskCoverage && <> · 规则覆盖 {taskCoverage.supported}/{taskCoverage.total} 类</>} · {verdict}</p>}</header>
          {task ? <>{/* One row: the customer rules on the left, the step line on the right, kept there when there is no rule choice. */}
          <div className="flex flex-wrap items-center justify-between gap-3">{task.state === 'pending_confirm' && templates.length > 0 ? <div className="flex flex-wrap items-center gap-3"><Label htmlFor="rule-template">客户规则</Label><Select value={templateChoice || undefined} disabled={recognitionLocked || confirmationLocked || Boolean(blocked)} onValueChange={chooseTemplate}><SelectTrigger id="rule-template" aria-describedby="rule-template-note" className="w-64 max-w-full"><SelectValue placeholder="按文件自动匹配"/></SelectTrigger><SelectContent>{templates.map(key => <SelectItem key={key} value={key} description={key === lastTemplate() ? '上次使用' : undefined}>{templateName(key)}</SelectItem>)}</SelectContent></Select><span id="rule-template-note" className="text-xs text-muted-foreground">改选后将按新规则重新识别本批全部文件</span></div> : <span/>}{stepper}</div></> : <div className="flex justify-end">{stepper}</div>}
        </>} bottom={task && <>
                    <>{noMatches && <p className="text-muted-foreground">{zeroTargetNote(files)}点击【开始审核】后任务直接完成，不扣费、不生成审核报告。</p>}
            {precheckHolds.length > 0 && !recognitionDirty && <p role="status" className="text-destructive">预检发现 {precheckHolds.length} 项阻断（{[...new Set(precheckHolds.map(item => item.title))].join('、')}），请先处理：{precheckHolds[0].action}</p>}
            {task.state === 'pending_confirm' && scope && !recognitionDirty && !noMatches && !scope.target_file_version_ids.length && <p role="status" className="text-muted-foreground">没有可接收的待审文件。请先处理排除原因，再核对审核依据。</p>}
            {(noMatches ? endInputsDirty : recognitionDirty) && <div className="flex flex-wrap items-center gap-3"><p role="status" className="text-muted-foreground">文件或规则选择已有变更，请重新识别后再开始审核。</p><Button variant="outline" size="sm" disabled={recognitionLocked || Boolean(blocked)} onClick={() => void startMatching()}>重新识别</Button></div>}
            {confirmation?.message && <Alert data-request-id={confirmation.requestId} variant={confirmation.phase === 'rejected' ? 'default' : 'destructive'}><AlertTitle>{confirmation.phase === 'rejected' ? '本次未被接收' : '提交结果待确认'}</AlertTitle><AlertDescription><p>{confirmation.staleBasisDigest && task.confirmation_basis_digest && !basisNeedsRefresh ? '已读取当前审核依据，请核对后重新确认。' : confirmation.message}</p><Button variant="outline" size="sm" onClick={() => openSupport({scenario: '确认审核', traceId: confirmation.traceId, facts: [{label: '任务标识', value: task.id}, {label: '确认请求', value: confirmation.requestId}, {label: '接收状态', value: confirmation.phase === 'rejected' ? '明确未被接收' : '尚未确认'}, {label: '服务说明', value: confirmation.message}]})}>联系支持</Button></AlertDescription></Alert>}
            {task.state === 'pending_confirm' && suiteBlocks.length > 0 && <Alert><AlertTitle>部分文件的检查程序需要先验证</AlertTitle><AlertDescription><p>检查程序由平台离线准备；如需准备或更新，请联系支持。处理后点“刷新审核依据”。</p><ul className="mb-2">{suiteBlocks.map(file => <li key={file.file.id}><FileName name={file.file.original_filename}/></li>)}</ul><Button variant="outline" size="sm" disabled={confirmationLocked || readStatus[task.id]?.busy} onClick={() => void recover(task.id)}>刷新审核依据</Button></AlertDescription></Alert>}
            {task.state === 'pending_confirm' && matched.length > 0 && !confirmationLocked && (basisNeedsRefresh || !task.confirmation_basis_digest || !scope) && <Alert><AlertTitle>请刷新审核依据</AlertTitle><AlertDescription><p>刷新只读取当前规则，不会开始审核。核对后需要再次确认。</p><Button variant="outline" size="sm" onClick={() => void recover(task.id)}>刷新审核依据</Button></AlertDescription></Alert>}
            <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-2"><Button variant="outline" disabled={confirmationLocked || endStatus[task.id] === 'sending' || endStatus[task.id] === 'unknown'} onClick={() => setStep('upload')}><Icon icon={iconArrowLeft}/>上一步</Button>{/* The same files can be recognized again, e.g. after a rule changed under them. */}{task.state === 'pending_confirm' && !noMatches && !recognitionDirty && <Button variant="outline" disabled={recognitionLocked || confirmationLocked || inputLocked || Boolean(blocked)} onClick={() => void startMatching()}><Icon icon={iconArrowsRotate} aria-hidden="true"/>重新识别</Button>}{task.state === 'pending_confirm' && adapter.deepMode && !recognitionDirty && !noMatches && (() => {
              // Deep mode is part of the start decision, so it sits with it; it does not change the points, so it stays apart from them.
              const control = deepModeControl(task, deepSaving === task.id || confirmationLocked)
              return <div className="flex items-center gap-2 border-l pl-3"><Label htmlFor="deep-mode">{deepModeTitle}</Label><Tooltip><TooltipTrigger asChild><span><Switch id="deep-mode" aria-describedby="deep-mode-description" checked={task.deep_mode} disabled={control.disabled} onCheckedChange={value => void changeDeepMode(task, value)}/></span></TooltipTrigger><TooltipContent className="max-w-80">{deepModeDescription}</TooltipContent></Tooltip><span id="deep-mode-description" className="sr-only">{deepModeDescription}</span>{control.reason && <span role="status" className="text-xs text-muted-foreground">{control.reason}</span>}{deepError?.taskId === task.id && <span role="alert" className="text-xs text-destructive">{deepError.message}</span>}</div>
            })()}</div>{noMatches ? <Button disabled={Boolean(endStatus[task.id]) || endInputsDirty} onClick={() => void endTask(task)}>{endStatus[task.id] === 'sending' ? '正在提交…' : '开始审核'}<Icon icon={iconArrowRight}/></Button> : <div className="flex flex-wrap gap-2">{confirmation && ['unknown', 'reading'].includes(confirmation.phase) && <Button variant="outline" disabled={confirmation.phase === 'reading'} onClick={() => void acceptance.reconcile(task)}>{confirmation.phase === 'reading' ? '正在读取提交结果…' : '读取提交结果'}</Button>}{task.estimated_points != null && <Tooltip><TooltipTrigger asChild><span tabIndex={0} className="self-center text-sm tabular-nums text-muted-foreground underline decoration-dotted underline-offset-4">预计 {task.estimated_points} 点</span></TooltipTrigger><TooltipContent className="max-w-80">飞书通用AI点数{pointsBasis(task)}</TooltipContent></Tooltip>}<Button disabled={!canConfirm || precheckHolds.length > 0} onClick={() => confirm(task)}>{confirmation?.phase === 'sending' ? '正在确认…' : '开始审核'}<Icon icon={iconArrowRight}/></Button></div>}</div>
          </>
        </>}>
        {notices}
        {task && <>
          {task.state === 'matching' && readStatus[task.id]?.error && <Alert variant="destructive"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription><p>{readStatus[task.id].error}</p><div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>{readStatus[task.id]?.busy ? '正在读取识别结果…' : '重新读取识别结果'}</Button><Button variant="outline" size="sm" onClick={() => contactReadSupport(task)}>联系支持</Button></div></AlertDescription></Alert>}
          {task.state === 'pending_confirm' && reauditing && newerRules.length > 0 && <section aria-labelledby="rule-update-title" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary-line bg-primary-soft p-3.5">
          <div className="flex min-w-0 items-center gap-3"><span aria-hidden="true" className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary-soft-hover text-primary"><Icon icon={iconCircleInfo} className="size-3"/></span><div className="min-w-0"><h2 id="rule-update-title" className="text-sm font-semibold">审核规则已更新</h2><p className="text-xs">{newerRules.join('、')}在上次审核后有新版本。</p></div></div>
          <div className="flex items-center gap-3"><Segmented label="本次使用的规则版本" value={ruleVersionChoice(files)} options={[['newer', '使用新版本'], ['original', '沿用上次版本']]} disabled={recognitionLocked || confirmationLocked || Boolean(blocked)} onChange={chooseRuleVersion}/><span className="text-xs text-muted-foreground">可随时切换。</span></div>
        </section>}
          {!precheck.result && task.state !== 'matching' && matchGroups.length > 1 && <dl aria-label="识别结果统计" className="grid grid-cols-4 gap-3">{matchGroups.map(group => <div key={group.title} className="flex flex-col-reverse rounded-lg border bg-card px-4 py-3 text-center"><dt className="text-xs text-muted-foreground">{group.title}</dt><dd className={`text-2xl font-semibold tabular-nums ${matchGroupTone[group.title]?.[0] ?? ''}`}>{group.rows.length}</dd></div>)}</dl>}
          {task.state === 'matching' ? <div className="space-y-2">{matchingFiles.map(row => matchRow(row))}</div> : <div className="space-y-5">
            {matchSections.map(section => <section key={section.key} aria-label={section.title} className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="flex items-center gap-2 text-xs font-semibold"><span aria-hidden="true" className={`size-2 rounded-full bg-current ${section.tone}`}/>{section.title} · {section.rows.length} 份{section.note && <span className="font-normal text-muted-foreground"> · {section.note}</span>}</h2>
                {section.selectable ? <label className="flex items-center gap-1.5 text-xs text-foreground"><Checkbox aria-label={`全选${section.title}`} disabled={confirmationLocked} checked={section.rows.every(row => row.selection === 'selected') ? true : section.rows.some(row => row.selection === 'selected') ? 'indeterminate' : false} onCheckedChange={value => section.rows.forEach(row => toggle(row.file.id, value === true))}/>全选</label>
                  : (section.key === 'block' || section.key === 'handle' && section.rows.length > 1) && <Button variant="outline" size="sm" disabled={confirmationLocked} onClick={() => section.rows.forEach(row => remove(row.file.id))}>全部移除</Button>}</div>
              {(section.key === 'handle' || section.key === 'unmatched' ? sameReasonBatches(section.rows) : section.rows.map(row => [row])).map(batch => batch.length > 1 ? <section key={batch[0].file.id} className="space-y-2">
            <MatchingFeedback gaps={batch[0].match!.gaps} onCheck={() => setStep('upload')} onRules={rules ? () => showSurface('requirements') : undefined} onTemplates={templateAdapter ? openTemplates : undefined}/>
            <ul aria-label="同一原因的文件" className="divide-y rounded-lg bg-card">{batch.map(row => <li key={row.file.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-3 py-2"><h3 className="min-w-0 flex-1 basis-64 font-medium"><FileName name={row.file.original_filename}/></h3><dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">{matchFields(row).filter(([, value]) => value).map(([label, value]) => <div className="flex min-w-0 gap-1.5" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="break-words">{value}</dd></div>)}</dl>{ruleChoiceButton(row)}<Button variant="ghost" size="sm" onClick={() => remove(row.file.id)}>移除</Button>{ruleChoiceOpen(row) && <div className="basis-full pt-2">{ruleChoice(row)}</div>}</li>)}</ul>
          </section> : matchRow(batch[0], true, section.card, Boolean(section.selectable)))}
            </section>)}
          </div>}
          {precheck.result && adapter.precheck && !recognitionDirty && <PrecheckReport adapter={adapter.precheck} taskId={task.id} result={precheck.result}/>}
          {precheck.error && <p className="text-xs text-muted-foreground">{precheck.error}</p>}
          {removedList}
        </>}
        </PageLayout>}
        </fieldset>
        </>}
        </div>
        </div>
        </div>
      </main>
      {displayEditor.dialog}
    </div>
    {driveOpen && adapter.drive && <FeishuPicker source={adapter.drive} onAdd={receiveDrive} onClose={() => setDriveOpen(false)} trigger={driveTrigger.current}/>}
    {localFiles.length > 0 && adapter.drive && <FeishuFolderPicker source={adapter.drive} files={localFiles} onChoose={receiveInFolder} onClose={() => setLocalFiles([])}/>}
    {supportSnapshot && <AuditSupport adapter={supportAdapter} snapshot={supportSnapshot} pageReference={supportPageReference} returnTargets={supportReturnTargets.current} onClose={() => setSupportSnapshot(null)}/>}
    <Dialog open={Boolean(ruleSource)} onOpenChange={open => {if (!open) setRuleSource(null)}}><DialogContent data-rule-version={ruleSource?.rule.id} onCloseAutoFocus={event => {event.preventDefault(); ruleTrigger.current?.focus()}}><DialogHeader><DialogTitle title={ruleSource?.clause?.id}>{ruleSource?.clause ? clauseLabel(ruleSource.rule.body.kind === 'engine' ? ruleSource.rule.body.rules : [], ruleSource.clause.id) : '文件存在性检查'}</DialogTitle><DialogDescription><span className="block">{ruleSource?.rule.title}</span><span className="block break-words">{ruleSource?.filename}</span></DialogDescription></DialogHeader><p className="max-h-80 overflow-y-auto whitespace-pre-wrap text-sm">{ruleSource?.clause?.rule_to_check ?? '文件存在性检查。'}</p></DialogContent></Dialog>
  </div></SupportEntryContext.Provider></TooltipProvider>
}
