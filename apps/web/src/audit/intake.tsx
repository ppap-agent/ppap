import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/5bc532c5a9a142a8a378434ddfe73fd5
import {useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {requestTrace} from '../api'
import {analysisNotice} from '../helpers'
import {MatchingFeedback, isMatchingReason} from './matching-feedback'
import {ArrowLeft, ArrowRight, BookOpen, ChartNoAxesColumn, Check, ChevronDown, Copy, ExternalLink, FileText, HelpCircle, PanelLeftClose, PanelLeftOpen, Plus, Sparkles, SquareCheckBig, UploadCloud, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from '../components/ui/tooltip'
import {FileName} from '../journey/file-name'
import {FileTypeBadge} from './file-type-badge'
import {AccountMenu, useAccess} from '../journey/access'
import {FeishuPicker, FeishuFolderPicker, type DrivePickerSource} from './feishu-picker'
import {checkerSuiteUnavailable, confirmationScope, suiteAdmissionBlocks, suiteVersionConfirmationRequired, useTaskAcceptance, type AuditAcceptanceAdapter} from './task-acceptance'
import {useTaskRecognition, type AuditRecognitionAdapter} from './task-recognition'
import {AuditExcludedFiles, AuditWorkspace} from './workspace'
import type {OriginalRenderer} from './review-content'
import {clauseLabel, sameFrozenInputs, sameSnapshot, progressRegressed, taskProgressError, allTaskReportsReady, reconcileTaskNotification} from './result-projection'
import type {AuditNotificationAdapter} from './completion-notification'
import {canObserveTask, useTaskObservation} from './task-observation'
import {uploadName, useUploads, type UploadAdapter, type UploadRow} from './uploads'
import {TransferList} from './transfer-list'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {AuditArchive} from './archive'
import type {AuditArchiveAdapter} from './archive-contract'
import {AuditSupport, SupportEntryContext, type SupportSnapshot, type AuditSupportAdapter} from './support'
import {TEMPLATE_GUIDE_URL} from './template-links'
import {documentTypeLabel} from './status-labels'
import {AuditUsage} from './usage'
import type {AuditUsageAdapter} from './usage-contract'
import {reconcileTaskDisplay, unconfirmedDisplayAccess, type DisplayInfo, type DisplayReference} from './display-info-contract'
import {useDisplayEditor, type AuditDisplayAdapter} from './display-info-editor'
import {ReviewEditorContext} from './review-editor'
import {reconcileTaskSummary, summarizeTask, taskIsBehindSummary, type TaskSummary} from './task-summary'
import {AuditWorkbench, type WorkbenchProps} from './workbench'
import {auditAddressChanged, readAuditRoute, surfaceAddress, taskAddress as taskHref, writeAuditAddress} from './routes'
import {templateName} from './rule-templates'
import {AuditRoundReader, type ReadRound} from './round-reader'
import {TaskRecovery, type TaskRecoveryState} from './task-recovery'
import {TaskRail, TaskSwitcher} from './task-switcher'
import {TemplateLibraryPage, type TemplateLibraryAdapter} from './template-library'
import {PrecheckCounts, PrecheckNotes, PrecheckReport, usePrecheck} from './precheck'
import {taskListStatus} from './task-list-status'
import {createUploadPreparation, describeUploads, preparationFiles, restoreUploads, type PreparationAdapter, type UploadPreparation} from './upload-preparation'

import {CustomerItem, CustomerListNote, CustomerMode, acceptedUploads, type CustomerDefaultAdapter} from './customer-mode'
import {customerCoverage, type CoverageSource} from './customer-coverage'
import {chooseReauditRecognition, reauditRecognition, type ReauditRecognition} from './reaudit-intent'

export type AuditTask = components['schemas']['AuditTask']
export type AuditTaskFile = components['schemas']['AuditTaskFile']
export type AuditIntakeAdapter = AuditAcceptanceAdapter & AuditRecognitionAdapter & UploadAdapter & {
  preparation?: PreparationAdapter
  samples?: () => AuditTaskFile[]
  drive?: DrivePickerSource
  customerDefaults?: CustomerDefaultAdapter
  customers?: {value: string; label: string}[]
  /** Rules and standard types for each customer's coverage; optional, a failed read shows no numbers. */
  coverage?: (signal?: AbortSignal) => Promise<CoverageSource>
  notifications?: AuditNotificationAdapter
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
export type RuleChoice = {id: string; title: string; type?: string; template?: string}
const lastTemplateKey = 'ppap.rule-template'
const lastTemplate = () => {try {return localStorage.getItem(lastTemplateKey) ?? ''} catch {return ''}}
const taskLabels = {matching: '识别中', pending_confirm: '待确认', reviewing: '审核中', completed: '已完成'}
const productLinks = [
  {view: 'workbench', label: 'PPAP Audit', icon: Sparkles},
  {view: 'archive', label: '审核文件', icon: FileText},
  {view: 'templates', label: '模板库', icon: Copy},
  {view: 'requirements', label: '审核规则库', icon: SquareCheckBig},
  {view: 'usage', label: '用量记录', icon: ChartNoAxesColumn},
] as const
type ProductSurface = typeof productLinks[number]['view'] | 'audit'
const sizeLabel = (bytes: number) => bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
type IntakeDraft = {files: AuditTaskFile[]; uploads: UploadRow[]; customer: string; step: 'upload' | 'check' | 'match'; predecessorId?: string}
const replacementRemovalReason = '本次使用补件替换；原件此前已选中'
const legacyReplacementRemovalReason = '本次使用补件替换'
function reconcileReplacements(files: AuditTaskFile[]): AuditTaskFile[] {
  const replaced = new Set(files.filter(file => file.selection === 'selected' && file.replacement_of_file_version_id).map(file => file.replacement_of_file_version_id))
  return files.map(file => {
    if (replaced.has(file.file.id)) return file.selection === 'selected' ? {...file, selection: 'removed', removal_reason: replacementRemovalReason} : file
    if (file.selection === 'removed' && file.removal_reason === replacementRemovalReason) return {...file, selection: 'selected', removal_reason: null}
    // Older drafts did not retain the original choice. Never guess it was selected.
    return file.selection === 'removed' && file.removal_reason === legacyReplacementRemovalReason ? {...file, selection: 'unselected', removal_reason: null} : file
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
export function AuditIntake({adapter, rules, onRefreshCustomers, autoObserve = true, renderOriginal, reportAdapter, sharingAdapter, archiveAdapter, supportAdapter, usageAdapter, displayAdapter, renderWorkbench, templateAdapter}: {templateAdapter?: TemplateLibraryAdapter; adapter: AuditIntakeAdapter; rules?: (active: boolean, headerActions?: HTMLElement | null) => ReactNode; onRefreshCustomers?: () => void; autoObserve?: boolean; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; archiveAdapter?: AuditArchiveAdapter; supportAdapter?: AuditSupportAdapter; usageAdapter?: AuditUsageAdapter; displayAdapter?: AuditDisplayAdapter; renderWorkbench?: (props: WorkbenchProps) => ReactNode}) {
  const access = useAccess()
  const reviewEditor = useContext(ReviewEditorContext), reviewRevision = useRef(reviewEditor?.revision)
  const readSurface = (): ProductSurface => {
    const {surface} = readAuditRoute(new URL(location.href))
    return surface === 'templates' && !templateAdapter ? 'workbench' : surface === 'requirements' && !rules ? 'workbench' : surface
  }
  const [surface, setSurface] = useState<ProductSurface>(readSurface)
  const [legacyRoundId, setLegacyRoundId] = useState(() => new URL(location.href).searchParams.get('round'))
  const [archiveOpened, setArchiveOpened] = useState(surface === 'archive')
  const [rulesOpened, setRulesOpened] = useState(surface === 'requirements')
  const [usageOpened, setUsageOpened] = useState(surface === 'usage')
  const [templatesOpened, setTemplatesOpened] = useState(surface === 'templates')
  // The product navigation starts as an icon rail; the person may expand it.
  const [navigationMode, setNavigationMode] = useState<'expanded' | 'collapsed'>('collapsed')
  const [navigationTooltip, setNavigationTooltip] = useState<string | null>(null)
  const [pageActions, setPageActions] = useState<HTMLDivElement | null>(null)
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
  const [step, setStepState] = useState<'upload' | 'check' | 'match'>('upload')
  const stepRef = useRef(step)
  function setStep(next: typeof step) {stepRef.current = next; setStepState(next)}
  const [customer, setCustomerState] = useState('auto')
  const currentCustomer = useRef(customer); currentCustomer.current = customer
  const customerInitialized = useRef(Boolean(readAuditRoute(new URL(location.href)).taskId || new URL(location.href).searchParams.has('uploadDraft')))
  const [customerReady, setCustomerReady] = useState(!adapter.customerDefaults || customerInitialized.current)
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
  const [sheet, setSheet] = useState(false)
  // The sheet closes as support opens, so its own button is gone by the time focus returns.
  const [reportView, setReportView] = useState(() => new URL(location.href).searchParams.has('report'))
  const [error, setError] = useState(''), [dragging, setDragging] = useState(false)
  const [checking, setChecking] = useState<string[]>([])
  const [driveOpen, setDriveOpen] = useState(false)
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
  const uploads = useUploads(adapter, (file, removed) => setFiles(rows => rows.some(row => row.file.id === file.id || row.selection !== 'removed' && row.file.file_id === file.file_id) ? rows : [...rows, {file, selection: removed ? 'removed' : 'selected', removal_reason: removed ? '传输期间排除，不纳入识别' : null, validation: {state: 'pending', reasons: ['文件待检查。']}, match: null}]), async rows => {await persistPreparation(rows)})
  const v4Mode = Boolean(adapter.customerDefaults && ['UAES', '理想', '蔚来'].includes(customer))
  const active = files.filter(row => row.selection !== 'removed')
  const selectedUploadCount = active.filter(row => row.selection === 'selected').length + uploads.rows.filter(row => !row.removed && !files.some(file => file.file.id === row.receipt.file?.id)).length
  const valid = active.filter(row => row.validation.state === 'valid')
  const waiting = active.filter(row => row.validation.state === 'pending' || row.validation.state === 'checking')
  const exceptions = active.filter(row => row.validation.state === 'invalid' || row.validation.state === 'error')
  const selected = valid.filter(row => row.selection === 'selected')
  const transferBlocked = uploads.rows.some(row => !row.removed && row.receipt.state !== 'ready')
  const blocked = transferBlocked ? '请先处理或排除未完成的传输，再开始识别。' : waiting.length ? `还有 ${waiting.length} 份文件等待文件检查，不能开始识别。` : exceptions.length ? '请先处理或移除异常文件。' : !selected.length ? '至少选择一份可审核文件。' : ''
  const matchingFiles = task ? files.filter(row => row.selection === 'selected') : []
  const matchFields = (row: typeof matchingFiles[number]) => [['文件类型', row.match?.document_type ? documentTypeLabel(row.match.document_type) : undefined], ['客户名称', row.match?.metadata.customer], ['车型', row.match?.metadata.model], ['零件号', row.match?.metadata.part_number], ['零件名称', row.match?.metadata.part]] as const
  const historicalHead = (row: typeof matchingFiles[number]) => Boolean(row.match && suiteVersionConfirmationRequired(row.match))
  const matched = matchingFiles.filter(row => row.match?.state === 'matched' && row.match.rule && !historicalHead(row))
  const references = matchingFiles.filter(row => row.match?.state === 'reference')
  const matchPending = (row: typeof matchingFiles[number]) => !row.match || row.match.state === 'matching' || row.match.state === 'pending'
  const needsHandling = matchingFiles.filter(row => !matchPending(row) && !(row.match?.state === 'matched' && row.match.rule && !historicalHead(row)) && row.match?.state !== 'reference')
  // Results settle file by file, so rows keep their place until recognition has finished.
  const matchGroupTone: Record<string, [string, string]> = {需要处理: ['text-warning', 'bg-warning/10 text-warning'], 已匹配: ['text-success', 'bg-success/10 text-success'], 比对资料: ['text-primary', 'bg-primary/10 text-primary'], 等待识别: ['text-muted-foreground', 'bg-muted text-muted-foreground']}
  const matchGroups = task?.state === 'matching' ? [{title: '', rows: matchingFiles}] : [{title: '需要处理', rows: needsHandling}, {title: '已匹配', rows: matched}, {title: '比对资料', rows: references}, {title: '等待识别', rows: matchingFiles.filter(matchPending)}].filter(group => group.rows.length)
  const noMatches = Boolean(task?.state === 'pending_confirm' && !matched.length && files.every(row => row.selection !== 'selected' || row.match?.state === 'unmatched' || row.match?.state === 'reference'))
  const recognitionDirty = Boolean(task && !task.round_id && (task.customer_preference !== (customer === 'auto' ? null : customer) || !onlyUnmatchedRemovals(task.files, files)))
  const endInputsDirty = Boolean(task && (task.customer_preference !== (customer === 'auto' ? null : customer) || !onlyEndExclusions(task.files, files)))
  const canEnd = noMatches && !endInputsDirty
  const workbench = task?.state === 'reviewing' || task?.state === 'completed' && task.completion_reason === 'processed'
  const navigationCollapsed = navigationMode === 'collapsed'
  function saveTask(next: AuditTask, {activate = false, updateActive = true} = {}) {
    const listed = mergeTaskSnapshot(latestTasks.current.find(row => row.id === next.id), next)
    const combined = mergeTaskSnapshot(task?.id === next.id ? task : undefined, listed)
    latestTasks.current = latestTasks.current.some(row => row.id === next.id) ? latestTasks.current.map(row => row.id === next.id ? combined : row) : [...latestTasks.current, combined]
    if (activate) activeTaskId.current = next.id
    setTask(current => activate || updateActive && current?.id === next.id ? mergeTaskSnapshot(current?.id === next.id ? current : undefined, combined) : current)
    setTasks(rows => rows.some(row => row.id === next.id) ? rows.map(row => row.id === next.id ? mergeTaskSnapshot(row, combined) : row) : [...rows, combined])
    setSummaries(rows => rows.some(row => row.id === next.id) ? rows.map(row => row.id === next.id ? reconcileTaskSummary(row, summarizeTask(combined)) : row) : [...rows, summarizeTask(combined)])
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
  function invalidateDisplayAccess(id: string) {
    const update = (current: AuditTask): AuditTask => current.id !== id ? current : {...current, files: current.files.map(file => ({...file, display_edit_access: unconfirmedDisplayAccess(file.display_edit_access, '任务读取未完成，编辑许可待重新确认。')}))}
    setTask(current => current ? update(current) : current)
    setTasks(rows => rows.map(update))
  }
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
    const snapshot: IntakeDraft = {files: structuredClone(files), uploads: uploads.snapshot(), customer, step: 'check', predecessorId}
    taskAddress(id); void recover(id)
    drafts.current.set(id, snapshot)
    if (uploadId) uploadDrafts.current.set(uploadId, {taskId: id})
  }
  const confirmation = task ? acceptance.observations[task.id] : undefined
  const confirmationLocked = Boolean(confirmation && confirmation.phase !== 'rejected' || task?.acceptance?.state === 'pending' && confirmation?.phase !== 'rejected')
  const scope = task ? confirmationScope(task) : null
  const basisNeedsRefresh = Boolean(task && acceptance.needsBasisRefresh(task))
  const suiteBlocks = task ? suiteAdmissionBlocks(task) : []
  const canConfirm = suiteBlocks.length === 0 && !matchingFiles.some(historicalHead) && Boolean(scope?.target_file_version_ids.length) && !basisNeedsRefresh && Boolean(task?.confirmation_basis_digest) && task?.state === 'pending_confirm' && matched.length > 0 && matchingFiles.every(row => row.match?.state === 'unmatched' || row.match?.state === 'reference' || row.match?.state === 'matched' && row.match.rule) && !confirmationLocked && !recognitionLocked && !recognitionDirty && recognitionState?.phase !== 'conflict'
  const precheck = usePrecheck(adapter.precheck, task, step === 'match' && task?.state === 'pending_confirm')
  // A file-level block (unreadable, several versions) on a file that would enter the audit holds the start; content-level findings are
  // reported for the formal audit and never hold it, and other files are already excluded.
  const precheckHolds = precheck.result?.blocks.filter(item => item.scope === 'file' && scope?.target_file_version_ids.includes(item.file_version_id)) ?? []
  // Recognized files by precheck level, as on the precheck page; without a precheck the levels come from matching alone.
  // Cards stay where recognition put them, so a file taken out of the audit keeps its unchecked box until it is recognized again.
  const shownMatchRows = task ? task.files.filter(row => row.selection === 'selected').flatMap(row => {const local = files.find(file => file.file.id === row.file.id); return local?.selection === 'removed' ? [] : [local ?? row]}) : []
  const isMatchedRow = (row: AuditTaskFile) => row.match?.state === 'matched' && Boolean(row.match.rule) && !historicalHead(row)
  const personAction = ['matching_customer_required', 'matching_customer_conflict', 'matching_document_type_required', 'ambiguous_available_rules', 'matching_rules_customer_unassigned']
  function sectionOf(row: AuditTaskFile) {
    if (matchPending(row)) return 'pending'
    if (row.match?.state === 'reference') return 'reference'
    const level = precheck.result?.files.find(item => item.file_version_id === row.file.id)?.level
    if (level === 'block') return 'block'
    if (!isMatchedRow(row)) return row.match?.state === 'unmatched' && !row.match.candidates?.length && !row.match.gaps.some(gap => personAction.includes(gap)) ? 'unmatched' : 'handle'
    return level === 'warn' ? 'warn' : 'ok'
  }
  const sectionSpec = [
    {key: 'ok', title: precheck.result ? '合规文件' : '已匹配', note: '可送审', bar: 'border-l-success bg-success/10 text-success', selectable: true, card: 'ok', tone: ''},
    {key: 'warn', title: '警告级', note: '标黄，仍可勾选送审', bar: 'border-l-warning bg-warning/10 text-warning', selectable: true, card: 'warn', tone: ''},
    {key: 'block', title: '阻断级', note: '文件级阻断移除后才能开始审核', bar: 'border-l-destructive bg-destructive/8 text-destructive', selectable: false, card: 'block', tone: ''},
    {key: 'handle', title: '需要处理', note: '可选择规则、手动指定或移除', bar: '', selectable: false, card: 'soft', tone: 'text-warning'},
    {key: 'unmatched', title: '未匹配：无规则覆盖', note: '开始审核时不送审、不计费', bar: '', selectable: false, card: 'soft', tone: 'text-muted-foreground'},
    {key: 'reference', title: '比对资料', note: '不单独审核或收费', bar: '', selectable: false, card: 'soft', tone: 'text-primary'},
    {key: 'pending', title: '等待识别', note: '', bar: '', selectable: false, card: 'soft', tone: 'text-muted-foreground'},
  ] as const
  const matchSections = sectionSpec.map(section => ({...section, rows: shownMatchRows.filter(row => sectionOf(row) === section.key)})).filter(section => section.rows.length)
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
      setStep(transfers.length || restored.length ? 'check' : 'upload'); setError(''); uploads.restore(transfers)
      setPreparationStatus({id, phase: 'ready', message: ''})
      for (const row of transfers) uploads.read(row.id)
    } catch (cause) {
      if (current()) setPreparationStatus({id, phase: 'error', message: cause instanceof Error ? cause.message : '上传准备暂不可读取。请重新查看原准备记录。'})
    }
  }
  function taskAddress(id: string | null, keepSurface = false) {
    if (!keepSurface) {surfaceRef.current = 'audit'; setSurface('audit'); setReportView(false); setLegacyRoundId(null)}
    const url = new URL(location.href)
    if (id) url.searchParams.set('task', id); else url.searchParams.delete('task')
    if (!keepSurface) {
      if (!url.pathname.startsWith('/preview/') && url.pathname !== '/__preview') {url.pathname = taskHref(id); url.searchParams.delete('task')}
      url.searchParams.delete('report'); url.searchParams.delete('view'); url.searchParams.delete('packageRequest')
      url.searchParams.delete('uploadDraft')
      for (const key of ['archiveDoc', 'auditRef', 'archiveReport', 'fromRound', 'file', 'clause', 'focus', 'recognitionRequest', 'acceptRequest', 'checkFilter', 'case', 'draft', 'round', 'stage', 'resume']) url.searchParams.delete(key)
    }
    const state = {...history.state, auditUploadDraft: id ? null : activeUploadDraftId.current}
    writeAuditAddress(url, keepSurface ? 'replace' : 'push', state)
    if (!keepSurface) retainAuditAddress()
    if (window.parent !== window && !keepSurface) window.parent.postMessage({type: 'ppap-audit-task', task: id, report: null}, location.origin)
  }
  function newTask() {restoreEpoch.current += 1; setRecovery(null); retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = crypto.randomUUID(); setPreparationStatus({id: null, phase: 'idle', message: ''}); setFiles([]); setTask(null); setLocalFiles([]); setStep('upload'); customerInitialized.current = false; setCustomerReady(!adapter.customerDefaults); setCustomerState('auto'); setPredecessorId(undefined); setError(''); setSheet(false); taskAddress(null)}
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
  function openRoundReport(taskId: string, fileVersionId: string) {
    taskAddress(taskId)
    const url = new URL(location.href); url.searchParams.set('report', fileVersionId)
    writeAuditAddress(url); setReportView(true)
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: taskId, report: fileVersionId, packageRequest: null}, location.origin)
    void recover(taskId)
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
      setFiles(prepared.files); setCustomer(prepared.customer_preference || 'auto'); setPredecessorId(roundId); setStep('check')
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
      invalidateDisplayAccess(id)
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      const state = status === 401 ? 'unauthenticated' : status === 403 ? 'forbidden' : status === 404 ? 'not_found' : 'error'
      setRecovery({id, state, traceId: requestTrace(failure), message: state === 'unauthenticated' ? '登录已失效，请重新登录。' : state === 'forbidden' ? '当前账号无权访问此任务。' : state === 'not_found' ? '未找到此任务，请核对链接。' : '暂时无法读取任务，请重试。'})
    }
  }
  function restoreAddress() {
    setLocalFiles([])
    setSupportSnapshot(null)
    const params = new URL(location.href).searchParams, view = readSurface()
    setLegacyRoundId(params.get('round'))
    if (view !== 'audit') {
      surfaceRef.current = view; setSurface(view)
      if (view === 'archive') setArchiveOpened(true); else if (view === 'requirements') setRulesOpened(true); else if (view === 'usage') setUsageOpened(true)
      if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view, ...Object.fromEntries(['archiveDoc', 'auditRef', 'archiveReport', 'usageRange', 'usageSearch', 'usagePage', 'packageRequest'].map(key => [key, params.get(key)]))}, location.origin)
      return
    }
    const fromOtherSurface = surfaceRef.current !== 'audit'
    surfaceRef.current = 'audit'
    setSurface('audit')
    if (params.has('round') && adapter.readRound) {retainAuditAddress(); return}
    setReportView(new URL(location.href).searchParams.has('report'))
    const id = readAuditRoute(new URL(location.href)).taskId
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
      setTask(null); setFiles(structuredClone(saved?.files ?? [])); if (saved) setCustomer(saved.customer); else {customerInitialized.current = false; setCustomerReady(!adapter.customerDefaults); setCustomerState('auto')} setStep(saved?.step ?? 'upload'); setError('')
      setPredecessorId(saved?.predecessorId)
    }
  }
  const addressCurrent = useRef(restoreAddress)
  addressCurrent.current = restoreAddress
  useEffect(() => {
    const changed = () => addressCurrent.current()
    const remember = () => {if (readAuditRoute(new URL(location.href)).surface === 'audit') retainAuditAddress()}
    changed(); window.addEventListener('popstate', changed); window.addEventListener(auditAddressChanged, remember)
    return () => {restoreEpoch.current += 1; window.removeEventListener('popstate', changed); window.removeEventListener(auditAddressChanged, remember)}
  }, [])
  function receive(picked: File[]) {
    if (!picked.length || !customerReady || confirmationLocked || inputLocked || workbench) return
    if (adapter.drive?.rootFolder) {setLocalFiles(picked); return}
    uploads.add(picked.map(file => ({kind: 'local', file}))); setStep('check'); setError('')
  }
  function receiveInFolder(destination: {token: string; name: string}) {
    if (!customerReady || confirmationLocked || inputLocked || workbench) return
    uploads.add(localFiles.map(file => ({kind: 'local', file, destination}))); setLocalFiles([]); setStep('check'); setError('')
  }
  function receiveDrive(picked: components['schemas']['DriveFile'][]) {
    if (!customerReady || confirmationLocked || inputLocked || workbench) return
    uploads.add(picked.map(file => ({kind: 'feishu', file}))); setStep('check'); setDriveOpen(false); setError('')
  }
  const driveButton = adapter.drive && <Button variant="outline" disabled={inputLocked || !customerReady} onClick={event => {driveTrigger.current = event.currentTarget; setDriveOpen(true)}}><FileText aria-hidden="true"/>从飞书云文档选择</Button>
  function remove(id: string) {
    if (confirmationLocked || inputLocked || workbench) return
    uploads.rows.filter(row => row.receipt.file?.id === id).forEach(row => uploads.remove(row.id))
    setFiles(rows => reconcileReplacements(rows.map(row => row.file.id === id ? {...row, selection: 'removed', removal_reason: row.validation.reasons[0] || (step === 'match' ? '未匹配到规则' : '识别前移除')} : row)))
  }
  // The upload card lists what this draft holds: received files and transfers that have not produced one yet.
  const transferRows = uploads.rows.filter(row => !row.removed && !files.some(file => file.file.id === row.receipt.file?.id))
  const draftRows = [
    ...active.map(row => ({key: row.file.id, name: row.file.original_filename, size: sizeLabel(row.file.byte_size), blocked: ['invalid', 'error'].includes(row.validation.state), note: ['invalid', 'error'].includes(row.validation.state) ? '无法审核' : row.selection === 'unselected' ? '未选择' : '', remove: () => remove(row.file.id)})),
    ...transferRows.map(row => ({key: row.id, name: uploadName(row.input), size: row.input.kind === 'local' ? sizeLabel(row.input.file.size) : '', blocked: row.receipt.state === 'failed', note: row.receipt.state === 'failed' ? '上传失败' : row.receipt.state === 'unknown' ? '上传待确认' : '上传中', remove: () => uploads.remove(row.id)})),
  ]
  const unusableDrafts = draftRows.filter(row => row.blocked).length
  function clearDraft() {
    if (confirmationLocked || inputLocked || workbench) return
    for (const row of active) remove(row.file.id)
    for (const row of transferRows) uploads.remove(row.id)
  }
  function toggle(id: string, checked: boolean) {if (!confirmationLocked && !inputLocked && !workbench) setFiles(rows => reconcileReplacements(rows.map(row => row.file.id === id ? {...row, selection: checked ? 'selected' : 'unselected', removal_reason: null} : row)))}
  function toggleAll(checked: boolean) {
    if (confirmationLocked || inputLocked || workbench) return
    // Bulk intent includes originals hidden only by an active replacement.
    setFiles(rows => reconcileReplacements(rows.map(row => row.validation.state === 'valid' && (row.selection !== 'removed' || row.removal_reason === replacementRemovalReason) ? {...row, selection: checked ? 'selected' : 'unselected', removal_reason: null} : row)))
  }
  const photoScopes = useRef(new Set<string>())
  async function usePhotoPurpose(row: AuditTaskFile) {
    if (customer !== 'UAES' || inputLocked || validationReads.current.has(row.file.id)) return
    photoScopes.current.add(row.file.id)
    const next = {...row, validation_id: undefined, validation_scope: undefined, validation: {state: 'checking' as const, reasons: ['正在按生产件样品照片检查原件…']}}
    setFiles(rows => rows.map(file => file.file.id === row.file.id ? next : file))
    await readValidation(next)
  }
  function changeCustomer(value: string) {
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
    if (!adapter.autoValidate || step !== 'check' || inputLocked) return
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
    if (endInputsDirty || recognitionState?.phase === 'conflict') {setStep('check'); setError('文件选择或输入版本已有变更。请读取当前任务并重新识别，再结束任务。'); return}
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
      if (activeTaskId.current === chosen.id && stepRef.current === 'match') setFiles(rows => mergeFileIntent(rows, structuredClone(chosen.files)))
      setReadStatus(rows => ({...rows, [current.id]: {busy: true, error: ''}}))
      return true
    } catch (failure) {
      if (taskReadEpoch.current.get(current.id) !== epoch) return null
      invalidateDisplayAccess(current.id)
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
  useTaskObservation(task?.id, autoObserve && surface === 'audit' && !legacyRoundId && !recovery && step === 'match' && canObserveTask(task), () => task && activeTaskId.current === task.id && surfaceRef.current === 'audit' && stepRef.current === 'match' && taskReadEpoch.current.get(task.id) === observedTaskEpoch ? readMatching(task) : Promise.resolve(null))
  // Even an empty successful read belongs to its search and state filter.
  const taskListState = surface === 'workbench' || filter === 'all' ? null : filter as AuditTask['state']
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
    if (surface !== 'audit' && surface !== 'workbench') return
    // A slow read is never aborted by tab focus or polling; the next read waits for it to settle.
    let controller: AbortController | undefined, reading = false
    const read = (silent = true) => {
      if (document.hidden || reading) return
      controller = new AbortController(); reading = true
      void loadTasksCurrent.current(0, controller.signal, silent).finally(() => {reading = false})
    }
    const timer = window.setTimeout(() => read(false), query ? 200 : 0)
    const poll = window.setInterval(read, 15000)
    const visible = () => read()
    document.addEventListener('visibilitychange', visible)
    return () => {window.clearTimeout(timer); window.clearInterval(poll); document.removeEventListener('visibilitychange', visible); controller?.abort(); listEpoch.current += 1}
  }, [query, filter, surface])
  const shownTasks = summaries.filter(row => (surface === 'workbench' || filter === 'all' || row.state === filter) && `${row.number} ${row.files.map(file => file.file.original_filename).join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  // Only a complete list can exclude cached detail/revisit summaries.
  const workbenchTasks = taskList.readScope === taskListScope && taskList.next === null ? shownTasks.filter(row => taskList.ids.includes(row.id)) : shownTasks
  function showSurface(value: ProductSurface) {
    setSheet(false)
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
    for (const key of ['archiveDoc', 'auditRef', 'archiveReport', 'requirement', 'reqVersion', 'reqTab', 'reqQuery', 'packageRequest', 'case', 'draft', 'round', 'stage', 'resume']) url.searchParams.delete(key)
    setLegacyRoundId(null)
    if (value !== 'audit') url.searchParams.set('view', value); else url.searchParams.delete('view')
    if (value === 'audit' && activeTaskId.current) url.searchParams.set('task', activeTaskId.current)
    if (!url.pathname.startsWith('/preview/') && url.pathname !== '/__preview') {
      url.pathname = value === 'audit' ? taskHref(activeTaskId.current) : surfaceAddress(value)
      url.searchParams.delete('view'); url.searchParams.delete('task'); url.searchParams.delete('report')
      // One page's own address state (rule tabs, chosen rule or checker) must not follow the person to another page.
      if (value !== 'requirements') for (const key of ['rulesArea', 'requirement', 'reqVersion', 'reqTab', 'checkerSuite', 'checkerPath', 'template', 'systemRule']) url.searchParams.delete(key)
    }
    history.pushState(history.state, '', url)
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view: value, packageRequest: null}, location.origin)
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
  function openRules(suiteKey?: string) {
    showSurface('requirements')
    const url = new URL(location.href)
    for (const key of ['template', 'systemRule', 'requirement', 'reqVersion', 'reqTab', 'rulesArea', 'checkerSuite', 'checkerPath']) url.searchParams.delete(key)
    if (suiteKey?.startsWith('custom:')) {url.searchParams.set('requirement', suiteKey.slice('custom:'.length))}
    writeAuditAddress(url)
  }
  // Only the collapsed icon rail needs tooltips; text navigation already names each item.
  const navigationTooltipProps = (id: string, compact = navigationCollapsed) => ({
    open: compact && navigationTooltip === id,
    onOpenChange: (open: boolean) => {
      if (!compact) return
      setNavigationTooltip(current => open ? id : current === id ? null : current)
    },
  })
  const navigation = (compact = false) => <nav aria-label="产品导航" className="grid gap-1">
    {productLinks.filter(link => link.view !== 'requirements' || rules).map(({view, label, icon: Icon}) => {
      const active = surface === view || view === 'workbench' && surface === 'audit'
      const itemClass = compact ? 'justify-center' : 'justify-start'
      const item = <Tooltip key={view} {...navigationTooltipProps(view, compact)}><TooltipTrigger asChild>{view === 'templates' && !templateAdapter ? <Button asChild size="lg" className={itemClass} variant="ghost"><a href={TEMPLATE_GUIDE_URL} target="_blank" rel="noopener noreferrer" aria-label="模板库（在飞书新窗口打开）"><Icon aria-hidden="true"/><span className={compact ? 'sr-only' : ''}>{label}</span>{!compact && <ExternalLink className="ml-auto size-3 text-muted-foreground" aria-hidden="true"/>}</a></Button> : <Button size="lg" className={itemClass} aria-label={label} aria-current={active ? 'page' : undefined} variant={active ? 'soft' : 'ghost'} onClick={() => view === 'requirements' ? openRules('templates') : showSurface(view)}><Icon aria-hidden="true"/><span className={compact ? 'sr-only' : ''}>{label}</span></Button>}</TooltipTrigger><TooltipContent side="right">{label}</TooltipContent></Tooltip>
      return item
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
    const options = [...candidates.map(rule => ({id: rule.id, title: rule.title})), ...ruleChoices.filter(rule => !candidates.some(item => item.id === rule.id))]
    return options.length < (match.state === 'matched' ? 2 : 1) ? [] : options
  }
  const ruleChoiceOpen = (row: AuditTaskFile) => (row.match?.candidates?.length ?? 0) > 1 || ruleChanging.includes(row.file.id)
  const ruleChoiceButton = (row: AuditTaskFile) => ruleOptions(row).length > 0 && !ruleChoiceOpen(row) && <Button variant="ghost" size="sm" onClick={() => setRuleChanging(ids => [...ids, row.file.id])}>{row.match?.state === 'matched' ? '更换规则' : '手动选择规则'}</Button>
  const ruleChoice = (row: AuditTaskFile) => {
    const options = ruleOptions(row)
    if (!options.length || !ruleChoiceOpen(row)) return null
    return <div className="space-y-2"><Label>{row.match?.state === 'matched' ? '更换规则后重新识别' : '选择适用规则后重新识别'}</Label><Select value={row.preferred_rule_version_id || ''} onValueChange={value => setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, preferred_rule_version_id: value} : file))}><SelectTrigger aria-label={`${row.file.original_filename} 的适用规则`}><SelectValue placeholder="请选择规则，不会自动选择"/></SelectTrigger><SelectContent>{options.map(rule => <SelectItem key={rule.id} value={rule.id}>{rule.title}</SelectItem>)}</SelectContent></Select>{row.preferred_rule_version_id && row.preferred_rule_version_id !== row.match?.rule?.id && <Button variant="outline" size="sm" disabled={recognitionLocked || Boolean(blocked)} onClick={() => void startMatching()}>按所选规则重新识别</Button>}</div>
  }
  const sameReasonBatches = (rows: typeof matchingFiles) => {
    const batches = new Map<string, typeof matchingFiles>()
    for (const row of rows) {
      const reason = row.match?.state === 'unmatched' && !(row.match.candidates && row.match.candidates.length > 0) && !row.match.gaps.some(gap => !isMatchingReason(gap) && analysisNotice(gap)) ? row.match.gaps.find(isMatchingReason) ?? 'none' : row.file.id
      batches.set(reason, [...(batches.get(reason) ?? []), row])
    }
    return [...batches.values()]
  }
  const cardTone = {ok: 'bg-card', warn: 'border-warning/40 bg-warning/5', block: 'border-destructive/30 bg-destructive/5', soft: 'bg-muted/30'} as const
  const fileCustomer = (row: AuditTaskFile) => row.match?.metadata.customer || task?.customer_preference || ''
  // One card per file: format, name and where it came from; the recognized facts; then the rule or what to do next.
  const matchRow = (row: typeof matchingFiles[number], grouped = false, tone: keyof typeof cardTone = 'ok', selectable = false) => <section key={row.file.id} className={`flex gap-3 rounded-md border px-3.5 py-3 ${cardTone[tone]}`}>
            {selectable && <Checkbox className="mt-1" aria-label={`送审 ${row.file.original_filename}`} checked={row.selection === 'selected'} disabled={confirmationLocked} onCheckedChange={value => toggle(row.file.id, value === true)}/>}
            <div className="min-w-0 flex-1 space-y-2.5">
            <div className="flex flex-wrap items-start justify-between gap-3"><div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2.5 gap-y-1"><h3 className="flex min-w-0 items-center gap-2 font-semibold"><FileTypeBadge name={row.file.original_filename}/><span className="[overflow-wrap:anywhere]">{row.file.original_filename}</span></h3><span className="text-xs text-muted-foreground">{row.file.origin?.available && row.file.origin.provider === 'feishu' ? '飞书云盘' : '本地上传'}</span></div>{/* A group heading already names matched and reference files. */}{!(grouped && (row.match?.state === 'reference' || row.match?.state === 'matched' && row.match.rule)) && <Badge variant={row.match && suiteVersionConfirmationRequired(row.match) ? 'warning' : row.match?.state === 'matched' && row.match.rule ? 'success' : row.match?.state === 'reference' || matchPending(row) ? 'outline' : 'warning'}>{row.match && suiteVersionConfirmationRequired(row.match) ? '需更新检查程序' : row.match?.state === 'matched' && row.match.rule ? '已匹配' : row.match?.state === 'reference' ? '比对资料' : row.match?.state === 'unmatched' ? '未匹配' : row.match?.state === 'matching' ? '识别中' : !row.match || row.match.state === 'pending' ? '等待识别' : checkerSuiteUnavailable(row.match) ? '还不能自动审核' : '识别异常'}</Badge>}</div>
            <p className="text-xs leading-relaxed text-muted-foreground">{matchFields(row).map(([label, value], index) => <span key={label}>{index > 0 && <span aria-hidden="true" className="mx-1.5 text-border">｜</span>}{label}：<span className="font-semibold text-foreground">{value || '—'}</span></span>)}</p>
            <PrecheckNotes result={precheck.result} fileVersionId={row.file.id}/>
            {row.match && suiteVersionConfirmationRequired(row.match) ? <Alert><AlertTitle>检查程序需要更新</AlertTitle><AlertDescription><p>这是旧版检查程序。采用新版本后再刷新审核依据；平台提供的规则请联系管理员。审核没有开始。</p>{rules && row.suite_admission?.suite_key.startsWith('custom:') && <Button variant="outline" size="sm" className="mt-2" onClick={() => openRules(row.suite_admission!.suite_key)}>打开检查程序</Button>}</AlertDescription></Alert>
              : row.match?.state === 'matched' && row.match.rule ? <Collapsible data-rule-version={row.match.rule.id}><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-success">已匹配：{fileCustomer(row) && <>{fileCustomer(row)} · </>}<span className="font-medium">{row.match.rule.title}</span></p><div className="flex flex-wrap items-center gap-1">{ruleChoiceButton(row)}<CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="group" aria-label={`查看 ${row.file.original_filename} 的条款`}>{row.match.rule.body.kind === 'engine' ? `${row.match.rule.body.rules.length} 条条款` : '文件存在性检查'}<ChevronDown aria-hidden="true" className="transition-transform group-data-[state=open]:rotate-180"/></Button></CollapsibleTrigger></div></div><CollapsibleContent><ul aria-label={`${row.file.original_filename} · 匹配条款`} className="grid gap-1 pt-2">{row.match.rule.body.kind === 'engine' ? row.match.rule.body.rules.map(clause => <li key={clause.id}><Button variant="ghost" size="sm" className="h-auto max-w-full justify-start whitespace-normal text-left" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource({rule: row.match!.rule!, clause, filename: row.file.original_filename})}}><BookOpen aria-hidden="true"/>{clauseLabel(row.match!.rule!.body.kind === 'engine' ? row.match!.rule!.body.rules : [], clause.id)}</Button></li>) : <li><Button variant="ghost" size="sm" className="justify-start" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource({rule: row.match!.rule!, clause: null, filename: row.file.original_filename})}}>文件存在性检查</Button></li>}</ul></CollapsibleContent></Collapsible>
              : row.match?.state === 'reference' ? null
              : row.match?.state === 'matching' || !row.match || row.match.state === 'pending' ? <ReadSkeleton label="等待识别结果…" kind="inline"/>
              : row.match.state === 'unmatched' || row.match.gaps.some(isMatchingReason) ? <MatchingFeedback gaps={row.match.gaps} candidates={row.match.candidates} onCheck={() => setStep(row.match!.gaps.some(gap => gap === 'matching_customer_required' || gap === 'matching_customer_conflict') ? 'upload' : 'check')} onRules={rules ? () => showSurface('requirements') : undefined} onTemplates={templateAdapter ? openTemplates : undefined} onRemove={() => remove(row.file.id)}/>
              : checkerSuiteUnavailable(row.match) ? <Alert><AlertTitle>这类文件还没有可用的检查程序</AlertTitle><AlertDescription><p>文件本身没有问题。上传的规则请在规则页“检查程序”中生成；平台提供的规则请联系管理员。处理后回到这里读取最新结果。</p>{rules && <Button variant="outline" size="sm" className="mt-2" onClick={() => openRules()}>前往规则模板</Button>}</AlertDescription></Alert>
              : <Alert><AlertTitle>暂时无法确认匹配结果</AlertTitle><AlertDescription><p>请返回检查文件后重新识别。</p></AlertDescription></Alert>}
            {row.match?.gaps.filter(gap => !isMatchingReason(gap)).map(gap => analysisNotice(gap)).filter((notice): notice is string => Boolean(notice)).map(notice => <p key={notice} className="text-sm text-muted-foreground">{notice}</p>)}
            {row.match?.state !== 'matched' && ruleChoiceButton(row)}{ruleChoice(row)}
          </div></section>
  const taskSwitcher = surface === 'audit' && <TaskSwitcher open={sheet} onOpenChange={setSheet} currentId={legacyRoundId ? undefined : task?.id} tasks={shownTasks} labels={taskLabels} query={query} onQuery={setQuery} filter={filter} onFilter={setFilter} busy={listStatus.busy} ready={listStatus.ready} error={listStatus.error} hasMore={taskListNext !== null} hasTasks={summaries.length > 0} onMore={() => void loadTasks(listStatus.error ? 0 : taskListNext!)} onNew={newTask} onOpen={id => {setSheet(false); taskAddress(id); void recover(id)}}/>
  const table = (rows: AuditTaskFile[], selectable: boolean) => <Table className="table-fixed">
    <TableHeader><TableRow>{selectable && <TableHead className="w-12"><Checkbox aria-label="全选可审核文件" checked={selected.length === valid.length && valid.length > 0 ? true : selected.length ? 'indeterminate' : false} onCheckedChange={value => toggleAll(value === true)}/></TableHead>}<TableHead>文件</TableHead><TableHead className="w-24">大小</TableHead><TableHead className={selectable ? 'w-16' : 'w-36'}><span className="sr-only">操作</span></TableHead></TableRow></TableHeader>
    <TableBody>{rows.map(row => <TableRow key={row.file.id}>{selectable && <TableCell><Checkbox aria-label={`选择 ${row.file.original_filename}`} checked={row.selection === 'selected'} onCheckedChange={value => toggle(row.file.id, value === true)}/></TableCell>}<TableCell className="whitespace-normal"><FileName name={row.file.original_filename}/>{customer === 'UAES' && /\.(?:png|jpe?g|webp|pdf)$/i.test(row.file.original_filename) && <div className="mt-2">{row.validation_scope || photoScopes.current.has(row.file.id) ? <p className="text-xs text-muted-foreground">本轮用途：生产件样品照片；内容仍需人工确认</p> : <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} onClick={() => void usePhotoPurpose(row)}>作为生产件样品照片检查</Button>}</div>}{selectable && v4Mode && <Select value={row.reference_only ? 'reference' : 'target'} onValueChange={value => setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, reference_only: value === 'reference' ? true : undefined, preferred_rule_version_id: undefined} : file))}><SelectTrigger className="mt-2 w-full max-w-56" aria-label={`${row.file.original_filename} 本轮用途`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="target">待审文件 · 正式审核收费</SelectItem><SelectItem value="reference">比对资料 · 不单独审核或收费</SelectItem></SelectContent></Select>}{!selectable && <p className="mt-2 text-sm text-muted-foreground">{row.validation.reasons.join('；') || '暂未完成检查'}</p>}</TableCell><TableCell><span className="text-muted-foreground">{sizeLabel(row.file.byte_size)}</span></TableCell>{/* Each group's heading names the state; rows carry only the reason. */}<TableCell><div className="flex flex-wrap items-center justify-end gap-1">{!selectable && row.validation.state !== 'invalid' && <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} aria-label={`${row.validation.state === 'error' ? '重新读取' : '读取'} ${row.file.original_filename} 检查结果`} onClick={() => void readValidation(row)}>{checking.includes(row.file.id) ? '读取中…' : row.validation.state === 'error' ? '重新读取' : '读取结果'}</Button>}<Button variant="ghost" size="icon" aria-label={`移除 ${row.file.original_filename}`} onClick={() => remove(row.file.id)}><X/></Button></div></TableCell></TableRow>)}</TableBody>
  </Table>
  const taskRail = <TaskRail currentId={task?.id} tasks={shownTasks} labels={taskLabels} query={query} onQuery={setQuery} onNew={newTask} onAll={() => showSurface('workbench')} onOpen={id => {taskAddress(id); void recover(id)}}/>
  const preparing = surface === 'audit' && !workbench && !recovery && !legacyRoundId && !reportView
  const supportScenario = surface === 'archive' ? '文件档案' : surface === 'requirements' ? '审核规则' : surface === 'usage' ? '用量记录' : surface === 'templates' ? '模板库' : workbench ? '审核结果' : '上传与文件核对'
  function contactReadSupport(current: AuditTask) {openSupport({scenario: '读取审核任务', traceId: readStatus[current.id]?.traceId, facts: [{label: '任务标识', value: current.id}, ...(current.round_id ? [{label: '审核轮次', value: current.round_id}] : []), {label: '服务说明', value: readStatus[current.id]?.error || ''}]})}
  const recognitionSupport = recognitionState && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '识别文件与匹配规则', traceId: recognitionState.traceId, facts: [{label: '任务标识', value: recognitionState.taskId}, {label: '识别请求', value: recognitionState.requestId}, {label: '文件', value: files.map(row => row.file.original_filename).join('\n')}, {label: '服务说明', value: recognitionState.message}]})}>联系支持</Button>
  const pageContext = surface !== 'audit' ? productLinks.find(link => link.view === surface)?.label : legacyRoundId ? '历史审核' : task ? task.number : recovery ? reportView ? '审核报告' : '审核任务' : '新建审核'
  const taskCoverage = task?.customer_preference ? coverageOf(task.customer_preference) : null
  const stepper = <ol aria-label="审核步骤" className="flex shrink-0 items-center gap-1.5 pt-2 text-base whitespace-nowrap text-muted-foreground">{['上传与检查', '确认规则', 'AI 审核'].map((label, index) => {
    const current = step === 'match' ? 1 : 0, done = index < current
    return <li key={label} aria-current={current === index ? 'step' : undefined} data-state={done ? 'complete' : current === index ? 'current' : 'upcoming'} className={`flex items-center gap-1.5 ${current === index ? 'font-semibold text-primary' : done ? 'text-success' : ''}`}><span className={`flex size-5.5 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${current === index ? 'bg-primary text-primary-foreground' : done ? 'bg-success/15 text-success' : 'bg-muted text-muted-foreground'}`}>{done ? <Check aria-label="已完成" className="size-3.5"/> : index + 1}</span>{label}{index < 2 && <span aria-hidden="true" className="ml-1.5 text-muted-foreground/60">&gt;</span>}</li>
  })}</ol>
  const customerHint = <><span className="text-sm text-muted-foreground">选择客户后，系统按该客户的 PPAP 模板与审核规则预检（预检免费，不计点数）</span>{templateAdapter && <Button variant="link" size="xs" onClick={() => openTemplates(true)}><Plus aria-hidden="true"/>更多模板，请补充</Button>}</>
  const workbenchCrumb = <><li><a href={surfaceAddress('workbench')} className="text-muted-foreground hover:text-foreground hover:underline" onClick={event => {if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {event.preventDefault(); showSurface('workbench')}}}>PPAP Audit</a></li><li aria-hidden="true" className="text-muted-foreground">/</li></>
  return <TooltipProvider><SupportEntryContext.Provider value={openSupport}><div className="flex h-dvh overflow-hidden bg-canvas text-sm text-foreground">
    <a href="#audit-main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:p-3 focus-visible:outline-ring focus-visible:ring-2 focus-visible:ring-ring">跳到主要内容</a>
    <aside aria-label="产品侧栏" className={`flex h-dvh shrink-0 flex-col overflow-hidden gap-5 border-r bg-card pt-3.5 pb-2.5 ${navigationCollapsed ? 'w-18 px-3' : 'w-55 px-3'}`}>
      <div className={`flex items-center gap-2 px-2 pt-1 text-base font-semibold ${navigationCollapsed ? 'justify-center' : ''}`}><span className="flex size-6.5 shrink-0 items-center justify-center rounded-md bg-linear-135 from-brand-light to-primary text-primary-foreground"><Sparkles aria-hidden="true" className="size-3.5"/></span><span className={navigationCollapsed ? 'sr-only' : ''}>PPAP Audit</span></div>
      {navigation(navigationCollapsed)}
      <div className={`mt-auto flex gap-1.5 border-t pt-2.5 ${navigationCollapsed ? 'flex-col items-center' : 'items-center'}`}>
        <Tooltip {...navigationTooltipProps('support')}><TooltipTrigger asChild><Button className={navigationCollapsed ? 'justify-center' : 'flex-1 justify-start'} variant="quiet" aria-label="联系我们" onClick={() => openSupport({scenario: supportScenario})}><HelpCircle aria-hidden="true"/><span className={navigationCollapsed ? 'sr-only' : ''}>联系我们</span></Button></TooltipTrigger><TooltipContent side="right">联系我们</TooltipContent></Tooltip>
        <Tooltip {...navigationTooltipProps('toggle')}><TooltipTrigger asChild><Button size="icon" variant="quiet" aria-label={navigationCollapsed ? '展开产品导航' : '折叠产品导航'} onClick={() => {setNavigationTooltip(null); setNavigationMode(navigationCollapsed ? 'expanded' : 'collapsed')}}>{navigationCollapsed ? <PanelLeftOpen aria-hidden="true"/> : <PanelLeftClose aria-hidden="true"/>}<span className="sr-only">{navigationCollapsed ? '展开导航' : '折叠导航'}</span></Button></TooltipTrigger><TooltipContent side="right">展开产品导航</TooltipContent></Tooltip>
      </div>
    </aside>
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b bg-card px-6 py-2"><div className="flex min-w-0 items-center gap-3">
        {surface === 'audit' && !legacyRoundId && reportView ? <nav aria-label="当前位置" className="min-w-0"><ol className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {workbenchCrumb}
          <li className="flex min-w-0 items-center gap-1">{task && !recovery ? <a href={taskResultsAddress().href} aria-label="返回审核结果" className="font-medium hover:underline" onClick={event => {if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {event.preventDefault(); showTaskResults()}}}>{task.number}</a> : <span className="text-muted-foreground">审核任务</span>}{taskSwitcher}</li>
          <li aria-hidden="true" className="text-muted-foreground">/</li><li aria-current="page" className="font-medium">审核报告</li>
        </ol></nav> : <nav aria-label="当前位置" className="min-w-0"><ol className="flex flex-wrap items-center gap-x-2 gap-y-1">{surface === 'audit' && workbenchCrumb}<li aria-current="page" className="flex min-w-0 items-center gap-1 font-medium"><span className="min-w-0 truncate">{pageContext}</span>{taskSwitcher}</li></ol></nav>}
      </div><div className="flex shrink-0 items-center gap-2"><div ref={setPageActions} role="group" aria-label="页面操作" className="flex min-w-0 flex-wrap items-center gap-2"/>{access?.principal && <AccountMenu compact onUsage={() => showSurface('usage')} showLocalRecords={Boolean(rules)}/>}</div></header>
      {/* While preparing a task the task list stays on the left, as on the task pages. */}
      <main id="audit-main" tabIndex={-1} className="flex min-h-0 w-full flex-1 overflow-hidden">
        {preparing && <aside aria-label="审核任务" className="w-72 shrink-0 overflow-y-auto border-r bg-card">{taskRail}</aside>}
        <div className={surface === 'audit' && workbench && !reportView ? 'h-full w-full min-w-0' : 'min-w-0 flex-1 overflow-y-auto p-6'}>
        <div className={surface === 'audit' && workbench && !reportView ? 'h-full' : `mx-auto w-full space-y-6 ${recovery || workbench || surface !== 'audit' ? '' : step === 'match' ? 'max-w-7xl' : 'max-w-5xl'}`}>
        {surface === 'workbench' && (renderWorkbench || (props => <AuditWorkbench {...props}/>))({headerActions: pageActions, tasks: workbenchTasks, query, onQuery: setQuery, onNew: newTask, onResume: auditReturn.current || activeTaskId.current || activeUploadDraftId.current ? () => showSurface('audit') : undefined, onOpen: href => {const route = readAuditRoute(new URL(href, location.href)); if (route.taskId) {taskAddress(route.taskId); void recover(route.taskId)} else location.assign(href)}, loading: listStatus.busy, countsKnown: listStatus.ready, busy: listStatus.busy, errors: listStatus.error ? [listStatus.error] : [], more: taskListNext !== null, onMore: () => void loadTasks(taskListNext || 0), onRefresh: () => void loadTasks()})}
        {archiveOpened && <div hidden={surface !== 'archive'}>{archiveAdapter ? <AuditArchive headerActions={surface === 'archive' ? pageActions : null} active={surface === 'archive'} adapter={archiveAdapter} onUpload={newTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}}/> : <Alert><AlertTitle>审核文件暂不可用</AlertTitle><AlertDescription>请稍后重试。</AlertDescription></Alert>}</div>}
        {rulesOpened && <div hidden={surface !== 'requirements'}>{rules?.(surface === 'requirements', surface === 'requirements' ? pageActions : null)}</div>}
        {templatesOpened && templateAdapter && <div hidden={surface !== 'templates'}><TemplateLibraryPage adapter={templateAdapter} headerActions={surface === 'templates' ? pageActions : null}/></div>}
        {usageOpened && <div hidden={surface !== 'usage'}><AuditUsage headerActions={surface === 'usage' ? pageActions : null} active={surface === 'usage'} adapter={usageAdapter} archiveAdapter={archiveAdapter} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}} onSupport={notice => openSupport({scenario: `用量记录 · ${notice}`})}/></div>}
        {surface === 'audit' && legacyRoundId && adapter.readRound && <AuditRoundReader headerActions={pageActions} roundId={legacyRoundId} readRound={adapter.readRound} renderOriginal={renderOriginal} onReaudit={adapter.prepareReaudit ? () => void prepareReaudit(legacyRoundId) : undefined} onReport={reportAdapter ? openRoundReport : undefined} returnHref="/workbench"/>}
        <div hidden={surface !== 'audit' || Boolean(legacyRoundId)} className={workbench && !reportView ? 'flex h-full flex-col' : 'space-y-6'}>
        {error && <Alert variant="destructive"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
        {preparingReaudit && <ReadSkeleton label="正在读取原审核的文件和规则…" kind={'detail'}/>}
        {predecessorId && !workbench && <Alert><AlertTitle>新建关联重审</AlertTitle><AlertDescription>原任务和报告都保留。本次用当前规则重新审核所有文件，人工复核从头开始。<Button variant="link" asChild><a href={`/?round=${encodeURIComponent(predecessorId)}`}>查看原审核</a></Button></AlertDescription></Alert>}
        {recognitionState && recognitionLocked && <Alert data-request-id={recognitionState.requestId}><AlertTitle>{recognitionState.phase === 'sending' ? '正在提交识别请求' : '识别请求待确认'}</AlertTitle><AlertDescription><p>{recognitionState.message || '正在等待同一请求的结果，文件与选择已保留。'}</p>{['unknown', 'reading'].includes(recognitionState.phase) && <><Button variant="outline" disabled={recognitionState.phase === 'reading'} onClick={readRecognition}>{recognitionState.phase === 'reading' ? '正在读取识别结果…' : '读取识别结果'}</Button>{recognitionSupport}</>}</AlertDescription></Alert>}
        {recognitionState?.phase === 'rejected' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>本次识别未被接收</AlertTitle><AlertDescription><p>{recognitionState.message}</p>{task?.id === recognitionState.taskId && <Button variant="outline" onClick={readCurrentRecognition}>读取当前任务</Button>}{recognitionSupport}</AlertDescription></Alert>}
        {recognitionState?.phase === 'conflict' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>任务输入版本已更新</AlertTitle><AlertDescription><p>{recognitionState.message}</p><Button variant="outline" onClick={readCurrentRecognition}>读取当前任务</Button></AlertDescription></Alert>}
        {task && task.state !== 'completed' && endStatus[task.id] === 'unknown' && <Alert><AlertTitle>结束结果待确认</AlertTitle><AlertDescription><p>尚未确认任务已结束。请读取同一任务，不要重复结束。</p>{readStatus[task.id]?.error && <p>{readStatus[task.id].error}</p>}<Button variant="outline" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>读取任务状态</Button></AlertDescription></Alert>}
        {recovery ? <TaskRecovery recovery={recovery} report={reportView} onRead={() => void recover(recovery.id, recovery.state === 'unauthenticated')} onSupport={() => openSupport({scenario: '读取审核任务', traceId: recovery.traceId, facts: [{label: '任务标识', value: recovery.id}, {label: '服务说明', value: recovery.message}]})}/> : workbench && task ? <AuditWorkspace taskRail={taskRail} readRound={adapter.readRound} onReaudit={adapter.prepareReaudit ? () => void prepareReaudit(task.round_id!) : undefined} headerActions={surface === 'audit' && !legacyRoundId ? pageActions : null} key={task.id} task={task} busy={readStatus[task.id]?.busy || false} readError={readStatus[task.id]?.error || ''} onReadSupport={() => contactReadSupport(task)} onRead={() => void readMatching(task)} onChange={saveTask} renderOriginal={renderOriginal} reportAdapter={reportAdapter} notificationAdapter={adapter.notifications} onReportChange={changeReport} onReportView={setReportView} sharingAdapter={sharingAdapter} onEditDisplay={displayAdapter ? file => displayEditor.open({task_id: task.id, round_id: task.round_id!, file_version_id: file.file.id}, file) : undefined}/> : task?.state === 'completed' ? <section className="space-y-4 py-8"><Check className="size-8"/><h1 className="text-2xl font-semibold">任务已结束</h1><p>{task.files.every(row => row.selection !== 'selected') ? '未选择待审文件，未开始审核' : task.files.filter(row => row.selection === 'selected').every(row => row.match?.state === 'reference') ? '仅保留比对资料，未开始审核' : '未匹配到可用规则，未开始审核'}</p><p className="text-muted-foreground">未产生审核费用，也没有生成报告。</p><AuditExcludedFiles files={task.files}/><Button onClick={newTask}>新建审核任务<ArrowRight/></Button></section> : <>
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'reading' && <section role="status" aria-label="正在恢复上传准备" className="space-y-4"><h1 className="sr-only">上传准备</h1><ReadSkeleton label="正在恢复上传的文件…" kind="list" rows={3}/></section>}
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'saving' && <p role="status" className="text-sm text-muted-foreground">正在保存上传准备…</p>}
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'error' && <Alert variant="destructive"><AlertTitle>上传准备待确认</AlertTitle><AlertDescription><p>{preparationStatus.message}</p><div className="mt-3 flex flex-wrap gap-2"><Button variant="outline" onClick={() => void readPreparation(activeUploadDraftId.current!)}>读取准备记录</Button>{preparationStatus.conflict && <Button variant="outline" onClick={() => {retainDraft(); void readPreparation(activeUploadDraftId.current!, true)}}>载入已保存资料</Button>}</div></AlertDescription></Alert>}
        <fieldset hidden={preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'reading'} disabled={inputLocked || preparingReaudit} className="min-w-0 space-y-6">
        <header className="flex items-start justify-between gap-6"><div className="min-w-0 flex-1 space-y-1.5">
          {step === 'match' && <p className="text-xs text-muted-foreground">PPAP Audit<span aria-hidden="true" className="mx-1.5 text-muted-foreground/60">/</span>预检与匹配</p>}
          <h1 className={step === 'upload' ? 'text-4xl leading-tight font-bold tracking-tight text-primary' : 'text-2xl font-semibold tracking-tight'}>{step === 'upload' ? 'PPAP Audit' : step === 'check' ? '核对本次文件' : task?.state === 'matching' ? '正在识别文件与匹配规则' : '确认每份文件的审核规则'}</h1>
          {step === 'check' && <p className="text-muted-foreground">勾选要审核的文件，然后开始识别。</p>}
          {step === 'match' && task && <div className="space-y-0.5 text-sm leading-relaxed text-muted-foreground"><p>上传后系统自动完成格式预检 → 识别文件要素 → 匹配客户规则（全程免费）；确认后点击【开始审核】计费送审。</p><p className="tabular-nums">当前客户：{task.customer_preference ? adapter.customers?.find(option => option.value === task.customer_preference)?.label ?? task.customer_preference : '自动识别'}{taskCoverage && <> ｜ 规则覆盖 {taskCoverage.supported}/{taskCoverage.total} 类</>} ｜ 本批 {matchingFiles.length} 份文件</p></div>}
        </div>{stepper}</header>
        {step === 'check' && <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" onClick={() => input.current?.click()}><Plus/>继续添加文件</Button>{driveButton}</div>}
        <input ref={input} disabled={!customerReady} type="file" className="sr-only" aria-label="选择审核文件" multiple accept={acceptedUploads(customer).accept} onChange={event => {receive(Array.from(event.target.files || [])); event.target.value = ''}}/>
        {step === 'upload' && <section aria-label="上传文件" className="space-y-5 rounded-lg border bg-card p-6">
          {surface === 'audit' && !legacyRoundId && (adapter.customerDefaults ? <CustomerMode key={draftEpoch.current} value={customer} ready={customerReady} options={adapter.customers ?? []} adapter={adapter.customerDefaults} onInitialDefault={initializeCustomer} onRefresh={onRefreshCustomers} onChange={changeCustomer} coverage={coverageOf} hint={customerHint}/> : <div className="grid gap-2"><div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><Label htmlFor="audit-customer">客户（可选）</Label>{customerHint}</div><Select value={customer} onValueChange={changeCustomer} onOpenChange={open => {if (open) onRefreshCustomers?.()}}><SelectTrigger id="audit-customer" className="h-10 w-80 max-w-full [&_[data-coverage]]:hidden"><SelectValue/></SelectTrigger><SelectContent><CustomerListNote count={(adapter.customers ?? []).length}/><CustomerItem value="auto" label="自动识别客户"/>{(adapter.customers ?? []).map(option => <CustomerItem key={option.value} value={option.value} label={option.label} coverage={coverageOf(option.value)}/>)}</SelectContent></Select></div>)}
          <div className="flex min-h-72 flex-col items-center justify-center gap-2.5 rounded-md border border-dashed border-primary-line bg-primary-soft/40 px-4 py-10 text-center transition-colors hover:border-primary data-[dragging=true]:border-primary data-[dragging=true]:bg-primary-soft" data-dragging={dragging} onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); receive(Array.from(event.dataTransfer.files))}}><Plus aria-hidden="true" className="size-11 stroke-1 text-primary"/><h2 className="text-xl font-semibold">上传文件</h2><p className="text-sm text-muted-foreground">将文件拖到这里，或</p><Button variant="outline" disabled={!customerReady} onClick={() => input.current?.click()}>选择本地文件</Button></div>
          {driveButton && <><div className="flex items-center gap-4 text-xs text-muted-foreground before:h-px before:flex-1 before:bg-border after:h-px after:flex-1 after:bg-border">或</div><div className="grid [&>button]:h-11 [&>button]:text-base">{driveButton}</div></>}
          <p className="text-center text-sm text-muted-foreground">{acceptedUploads(customer).hint}</p>
          {adapter.samples && <div className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" onClick={() => {setFiles(rows => [...rows, ...adapter.samples!()]); setStep('check')}}>使用三类文件示例<ArrowRight/></Button></div>}
          {draftRows.length > 0 && <section aria-label="已选文件" className="space-y-1.5 border-t pt-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex flex-wrap items-baseline gap-x-3 gap-y-1"><h2 className="font-medium">已选 {selectedUploadCount} 个文件</h2><span className="text-xs text-muted-foreground">{draftRows.length} 份文件{unusableDrafts > 0 && `，其中 ${unusableDrafts} 份无法审核`}</span></div><div className="flex items-center gap-1"><Button variant="link" size="sm" onClick={clearDraft}>清空</Button><Button variant="outline" size="sm" onClick={() => setStep('check')}>查看已选文件<ArrowRight/></Button></div></div>
            <ul className="grid">{draftRows.map(row => <li key={row.key} className="flex items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-muted/50"><FileTypeBadge name={row.name}/><span className="min-w-0 flex-1 truncate text-sm" title={row.name}>{row.name}</span>{row.note && <Badge variant={row.blocked ? 'destructive' : 'outline'}>{row.note}</Badge>}{row.size && <span className="text-xs tabular-nums text-muted-foreground">{row.size}</span>}<Button variant="ghost" size="icon-sm" aria-label={`移除 ${row.name}`} onClick={row.remove}><X/></Button></li>)}</ul>
          </section>}
          <div className="flex flex-wrap items-center justify-between gap-4 border-t pt-4"><p className="text-sm text-muted-foreground">识别不消耗点数；确认规则后才开始计费审核。</p><Button disabled={!draftRows.length || recognitionLocked || recognitionNeedsCurrent} onClick={() => {if (blocked || (canEnd && task)) setStep('check'); else void startMatching()}}>开始识别<ArrowRight/></Button></div>
        </section>}
        {step === 'check' && <>
          <TransferList rows={uploads.rows} onRead={uploads.read} onRetry={uploads.retry} onResume={uploads.resume} onAttach={(id, file) => void uploads.attach(id, file)} onRemove={uploads.remove} onCheckDrive={adapter.drive ? trigger => {driveTrigger.current = trigger; setDriveOpen(true)} : undefined}/>
          <section className="space-y-3 rounded-lg border bg-card p-4"><h2 className="flex items-center gap-2 font-semibold"><span aria-hidden="true" className="size-2 rounded-full bg-success"/>可审核文件 · {valid.length} 份</h2>{valid.length ? table(valid, true) : <p className="py-4 text-muted-foreground">尚无完成文件检查的可审核文件。</p>}</section>
          {files.some(file => file.rule_options?.length || predecessorId && file.reference_only) && <section aria-label="重审文件与规则" className="space-y-3 border-t pt-4"><h2 className="font-semibold">重审文件与规则</h2>{files.filter(file => file.selection === 'selected').map(file => <div key={file.file.id} className="flex flex-wrap items-center justify-between gap-3"><FileName name={file.file.original_filename}/>{file.reference_only ? <Badge variant="outline">比对资料 · 不单独审核</Badge> : file.rule_options?.length ? <Select value={reauditRecognition(file)} onValueChange={value => setFiles(rows => rows.map(row => row.file.id === file.file.id ? chooseReauditRecognition(row, value as ReauditRecognition) : row))}><SelectTrigger aria-label={`${file.file.original_filename} 的识别方式`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="source">沿用上次的规则</SelectItem>{reauditRecognition(file) === 'selected' && <SelectItem value="selected">保留已选的规则</SelectItem>}<SelectItem value="rematch">重新识别规则</SelectItem></SelectContent></Select> : <span className="text-sm text-muted-foreground">重新识别规则</span>}</div>)}</section>}
          {predecessorId && files.filter(file => file.selection === 'selected' && (file.replacement_of_file_version_id || !file.rule_options?.length && !file.reference_only)).map(file => <div key={file.file.id} className="flex flex-wrap items-center justify-between gap-3"><FileName name={file.file.original_filename}/><Select value={file.replacement_of_file_version_id || 'new'} onValueChange={id => chooseReplacement(file.file.id, id)}><SelectTrigger aria-label={`${file.file.original_filename} 的补件关系`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="new">新增文件 · 重新匹配</SelectItem>{files.filter(row => row.file.id !== file.file.id && !row.replacement_of_file_version_id && (row.rule_options?.length || row.reference_only)).map(row => <SelectItem key={row.file.id} value={row.file.id} disabled={files.some(other => other.file.id !== file.file.id && other.selection === 'selected' && other.replacement_of_file_version_id === row.file.id)}>替换：{row.file.original_filename}</SelectItem>)}</SelectContent></Select></div>)}
          {waiting.length > 0 && <section className="space-y-3 rounded-lg border bg-card p-4"><h2 className="flex items-center gap-2 font-semibold"><span aria-hidden="true" className="size-2 rounded-full bg-muted-foreground"/>等待文件检查 · {waiting.length} 份</h2>{table(waiting, false)}</section>}
          {exceptions.length > 0 && <section className="space-y-3 rounded-lg border border-destructive/30 bg-card p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="flex items-center gap-2 font-semibold"><span aria-hidden="true" className="size-2 rounded-full bg-destructive"/>异常文件 · {exceptions.length} 份</h2><Button variant="outline" size="sm" onClick={() => exceptions.forEach(row => remove(row.file.id))}>一键移除异常文件</Button></div>{table(exceptions, false)}</section>}
          {task && <AuditExcludedFiles files={files.filter(row => row.selection === 'removed')}/>}
          <footer className="sticky bottom-0 z-10 -mx-6 space-y-4 border-t bg-card/95 px-6 py-4 backdrop-blur-sm"><p role="status" className="text-muted-foreground">{canEnd ? '没有待审目标，可以结束任务，不收费、不生成报告。' : blocked || `已选择 ${selected.length} 份文件，开始识别后逐份匹配规则。`}</p><div className="flex flex-wrap items-center justify-between gap-4"><p className="text-xs text-muted-foreground">识别不消耗点数；确认规则后才开始计费审核。</p><div className="ml-auto flex gap-2"><Button variant="outline" onClick={() => setStep('upload')}><ArrowLeft/>上一步</Button>{canEnd && task ? <Button disabled={recognitionLocked || Boolean(endStatus[task.id])} onClick={() => {setStep('match'); void endTask(task)}}>结束任务</Button> : <Button disabled={Boolean(blocked) || recognitionLocked || recognitionNeedsCurrent} onClick={startMatching}>{recognitionState?.phase === 'sending' ? '正在提交识别…' : recognitionState?.phase === 'rejected' || recognitionState?.phase === 'conflict' ? '重新开始识别' : '开始识别'}<ArrowRight/></Button>}</div></div></footer>
        </>}
        {step === 'match' && task && <>
          <div className="flex flex-wrap items-center gap-2 tabular-nums" role="status"><Badge variant="secondary">{task.state === 'matching' ? `识别中 ${matchingFiles.filter(row => row.match && ['matched', 'unmatched', 'reference', 'failed'].includes(row.match.state)).length}/${matchingFiles.length}` : taskLabels[task.state]}</Badge><span className="text-muted-foreground">{matchingFiles.length} 份文件{task.state === 'matching' ? ` · ${matched.length} 份匹配到规则` : ''}</span></div>
          {task.state === 'matching' && readStatus[task.id]?.error && <Alert variant="destructive"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription><p>{readStatus[task.id].error}</p><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>{readStatus[task.id]?.busy ? '正在读取识别结果…' : '重新读取识别结果'}</Button><Button variant="outline" size="sm" onClick={() => contactReadSupport(task)}>联系支持</Button></div></AlertDescription></Alert>}
          {task.state === 'pending_confirm' && templates.length > 0 && <div className="flex flex-wrap items-center gap-3"><Label htmlFor="rule-template">规则模板</Label><Select value={templateChoice || undefined} disabled={recognitionLocked || confirmationLocked || Boolean(blocked)} onValueChange={chooseTemplate}><SelectTrigger id="rule-template" className="w-64 max-w-full"><SelectValue placeholder="按文件自动匹配"/></SelectTrigger><SelectContent>{templates.map(key => <SelectItem key={key} value={key}>{templateName(key)}{key === lastTemplate() ? '（上次使用）' : ''}</SelectItem>)}</SelectContent></Select></div>}
          {precheck.result ? <PrecheckCounts result={precheck.result} matched={matched.length}/> : task.state !== 'matching' && matchGroups.length > 1 && <dl aria-label="识别结果统计" className="grid grid-cols-4 gap-3">{matchGroups.map(group => <div key={group.title} className="flex flex-col-reverse rounded-md border bg-card px-4 py-3 text-center"><dt className="text-xs text-muted-foreground">{group.title}</dt><dd className={`text-2xl font-semibold tabular-nums ${matchGroupTone[group.title]?.[0] ?? ''}`}>{group.rows.length}</dd></div>)}</dl>}
          {task.state === 'matching' ? <div className="space-y-2">{matchingFiles.map(row => matchRow(row))}</div> : <div className="space-y-5">
            {precheck.result && <p className="text-sm font-semibold">预检分区</p>}
            {matchSections.map(section => <section key={section.key} aria-label={section.title} className="space-y-2">
              {section.bar ? <div className={`flex min-h-9 flex-wrap items-center justify-between gap-2 rounded-md border-l-4 px-3.5 py-1.5 ${section.bar}`}><h2 className="text-sm font-medium">{section.title}（{section.rows.length}）{section.note && ` · ${section.note}`}</h2>
                {section.selectable ? <label className="flex items-center gap-1.5 text-xs text-foreground"><Checkbox aria-label={`全选${section.title}`} disabled={confirmationLocked} checked={section.rows.every(row => row.selection === 'selected') ? true : section.rows.some(row => row.selection === 'selected') ? 'indeterminate' : false} onCheckedChange={value => section.rows.forEach(row => toggle(row.file.id, value === true))}/>全选</label>
                  : <Button variant="outline" size="xs" disabled={confirmationLocked} onClick={() => section.rows.forEach(row => remove(row.file.id))}>一键移除</Button>}</div>
                : <div className="flex flex-wrap items-center justify-between gap-2"><h2 className={`flex items-center gap-2 text-sm font-semibold ${section.tone}`}><span aria-hidden="true" className="size-2 rounded-full bg-current"/>{section.title}（{section.rows.length}）{section.note && <span className="font-normal text-muted-foreground"> · {section.note}</span>}</h2>{section.key === 'handle' && section.rows.length > 1 && <Button variant="outline" size="sm" disabled={confirmationLocked} onClick={() => section.rows.forEach(row => remove(row.file.id))}>全部移除</Button>}</div>}
              {(section.key === 'handle' || section.key === 'unmatched' ? sameReasonBatches(section.rows) : section.rows.map(row => [row])).map(batch => batch.length > 1 ? <section key={batch[0].file.id} className="space-y-3 rounded-md border bg-muted/30 px-3.5 py-3">
            <MatchingFeedback gaps={batch[0].match!.gaps} onCheck={() => setStep(batch[0].match!.gaps.some(gap => gap === 'matching_customer_required' || gap === 'matching_customer_conflict') ? 'upload' : 'check')} onRules={rules ? () => showSurface('requirements') : undefined} onTemplates={templateAdapter ? openTemplates : undefined}/>
            <ul aria-label="同一原因的文件" className="divide-y rounded-lg border">{batch.map(row => <li key={row.file.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-3 py-2"><h3 className="min-w-0 flex-1 basis-64 font-medium"><FileName name={row.file.original_filename}/></h3><dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">{matchFields(row).filter(([, value]) => value).map(([label, value]) => <div className="flex min-w-0 gap-1.5" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="break-words">{value}</dd></div>)}</dl><Badge variant="warning">未匹配</Badge>{ruleChoiceButton(row)}<Button variant="ghost" size="sm" onClick={() => remove(row.file.id)}>移除</Button>{ruleChoiceOpen(row) && <div className="basis-full pt-2">{ruleChoice(row)}</div>}</li>)}</ul>
          </section> : matchRow(batch[0], true, section.card, Boolean(section.selectable)))}
            </section>)}
          </div>}
          {precheck.result && adapter.precheck && !recognitionDirty && <PrecheckReport adapter={adapter.precheck} taskId={task.id} result={precheck.result}/>}
          {task.state === 'pending_confirm' && task.estimated_points != null && !recognitionDirty && !noMatches && <p role="note" className="rounded-md bg-primary-soft px-4 py-3 text-sm leading-relaxed text-primary">本次审核预计消耗 <b className="tabular-nums">{task.estimated_points}</b> 点飞书通用AI点数<span className="block text-xs text-primary/80">审核在后台进行，可关闭页面，完成后飞书通知你</span></p>}
          {precheck.error && <p className="text-xs text-muted-foreground">{precheck.error}</p>}
          {task.state === 'pending_confirm' && scope && !recognitionDirty && <section aria-label="本次审核范围" className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-4 text-sm"><h2 className="font-medium">本次审核范围</h2><p>进入审核 {scope.target_file_version_ids.length} 份 · 比对资料 {scope.reference_file_version_ids.length} 份（不单独审核或收费）</p>{scope.excluded_files.length > 0 && <><p>以下文件不进入本次审核，也不计费：</p><ul className="space-y-2">{scope.excluded_files.map(row => <li key={row.file_version_id} className="break-words">{task.files.find(file => file.file.id === row.file_version_id)!.file.original_filename}：{row.reason}</li>)}</ul></>}{!scope.target_file_version_ids.length && <p>没有可接收的待审文件。请先处理排除原因，再核对审核依据。</p>}</section>}
          {task && <AuditExcludedFiles files={files}/>}
          <footer className="sticky bottom-0 z-10 -mx-6 space-y-4 border-t bg-card/95 px-6 py-4 backdrop-blur-sm">{noMatches && <p className="text-muted-foreground">没有可进入审核的匹配文件。可以结束任务，不扣费、不生成报告。</p>}
            {precheckHolds.length > 0 && !recognitionDirty && <p role="status" className="text-destructive">预检发现 {precheckHolds.length} 项阻断（{[...new Set(precheckHolds.map(item => item.code))].join('、')}），请先处理：{precheckHolds[0].action}</p>}
            {(noMatches ? endInputsDirty : recognitionDirty) && <div className="flex flex-wrap items-center gap-3"><p role="status" className="text-muted-foreground">文件或规则选择已有变更，请重新识别后再确认或结束任务。</p><Button variant="outline" size="sm" disabled={recognitionLocked || Boolean(blocked)} onClick={() => void startMatching()}>重新识别</Button></div>}
            {confirmation?.message && <Alert data-request-id={confirmation.requestId} variant={confirmation.phase === 'rejected' ? 'default' : 'destructive'}><AlertTitle>{confirmation.phase === 'rejected' ? '本次未被接收' : '提交结果待确认'}</AlertTitle><AlertDescription><p>{confirmation.staleBasisDigest && task.confirmation_basis_digest && !basisNeedsRefresh ? '已读取当前审核依据，请核对后重新确认。' : confirmation.message}</p><Button variant="outline" size="sm" onClick={() => openSupport({scenario: '确认审核', traceId: confirmation.traceId, facts: [{label: '任务标识', value: task.id}, {label: '确认请求', value: confirmation.requestId}, {label: '接收状态', value: confirmation.phase === 'rejected' ? '明确未被接收' : '尚未确认'}, {label: '服务说明', value: confirmation.message}]})}>联系支持</Button></AlertDescription></Alert>}
            {task.state === 'pending_confirm' && suiteBlocks.length > 0 && <Alert><AlertTitle>部分文件的检查程序需要先验证</AlertTitle><AlertDescription><p>处理后点“刷新审核依据”。</p><ul>{suiteBlocks.map(file => <li key={file.file.id}><FileName name={file.file.original_filename}/> · {file.suite_admission!.suite_key.startsWith('custom:') ? <a className="underline" href={`${surfaceAddress('requirements')}?requirement=${encodeURIComponent(file.suite_admission!.suite_key.slice('custom:'.length))}`} onClick={event => {if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {event.preventDefault(); openRules(file.suite_admission!.suite_key)}}}>打开检查程序</a> : <span className="text-muted-foreground">平台提供，请联系管理员</span>}</li>)}</ul><Button variant="outline" disabled={confirmationLocked || readStatus[task.id]?.busy} onClick={() => void recover(task.id)}>刷新审核依据</Button></AlertDescription></Alert>}
            {task.state === 'pending_confirm' && matchingFiles.some(historicalHead) && <Alert><AlertTitle>检查程序需要更新</AlertTitle><AlertDescription><p>请在对应规则的“检查程序”中采用新版本，再刷新审核依据；平台提供的规则请联系管理员。审核没有开始。</p></AlertDescription></Alert>}
            {task.state === 'pending_confirm' && matched.length > 0 && !matchingFiles.some(historicalHead) && !confirmationLocked && (basisNeedsRefresh || !task.confirmation_basis_digest || !scope) && <Alert><AlertTitle>请刷新审核依据</AlertTitle><AlertDescription><p>刷新只读取当前规则，不会开始审核。核对后需要再次确认。</p><Button variant="outline" onClick={() => void recover(task.id)}>刷新审核依据</Button></AlertDescription></Alert>}
            {endStatus[task.id] === 'rejected' && <Alert><AlertTitle>结束请求已拒绝</AlertTitle><AlertDescription>输入版本可能已更新。当前文件与选择已保留，请读取当前任务后再操作。<Button variant="outline" onClick={() => void recover(task.id)}>读取当前任务</Button></AlertDescription></Alert>}
            <div className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" disabled={confirmationLocked || endStatus[task.id] === 'sending' || endStatus[task.id] === 'unknown'} onClick={() => setStep('check')}><ArrowLeft/>上一步</Button>{noMatches ? <Button disabled={Boolean(endStatus[task.id])} onClick={() => void endTask(task)}>{endStatus[task.id] === 'sending' ? '正在结束任务…' : '结束任务'}</Button> : <div className="flex flex-wrap gap-2">{confirmation && ['unknown', 'reading'].includes(confirmation.phase) && <Button variant="outline" disabled={confirmation.phase === 'reading'} onClick={() => void acceptance.reconcile(task)}>{confirmation.phase === 'reading' ? '正在读取提交结果…' : '读取提交结果'}</Button>}{task.estimated_points != null && <span className="self-center text-sm tabular-nums text-muted-foreground">预计 {task.estimated_points} 点</span>}<Button disabled={!canConfirm || precheckHolds.length > 0} onClick={() => confirm(task)}>{confirmation?.phase === 'sending' ? '正在确认…' : '开始审核'}<ArrowRight/></Button></div>}</div>
          </footer>
        </>}

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
