import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/5bc532c5a9a142a8a378434ddfe73fd5
import {useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {requestTrace} from '../api'
import {analysisNotice} from '../helpers'
import {MatchingFeedback, isMatchingReason} from './matching-feedback'
import {ArrowLeft, ArrowRight, BookOpen, Check, ExternalLink, ClipboardCheck, Files, HelpCircle, Menu, PanelLeftClose, PanelLeftOpen, Plus, ReceiptText, UploadCloud, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from '../components/ui/tooltip'
import {FileName} from '../journey/file-name'
import {AccountMenu, useAccess} from '../journey/access'
import {FeishuPicker, FeishuFolderPicker, type DrivePickerSource} from './feishu-picker'
import {useTaskAcceptance, type AuditAcceptanceAdapter} from './task-acceptance'
import {useTaskRecognition, type AuditRecognitionAdapter} from './task-recognition'
import {AuditExcludedFiles, AuditWorkspace} from './workspace'
import type {OriginalRenderer} from './review-content'
import {sameFrozenInputs, sameSnapshot, progressRegressed, taskProgressError, allTaskReportsReady, reconcileTaskNotification} from './result-projection'
import type {AuditNotificationAdapter} from './completion-notification'
import {canObserveTask, useTaskObservation} from './task-observation'
import {useUploads, type UploadAdapter, type UploadRow} from './uploads'
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
import {AuditRoundReader, type ReadRound} from './round-reader'
import {TaskRecovery, type TaskRecoveryState} from './task-recovery'
import {TaskSwitcher} from './task-switcher'
import {Skeleton} from '../components/ui/skeleton'
import {createUploadPreparation, describeUploads, preparationFiles, restoreUploads, type PreparationAdapter, type UploadPreparation} from './upload-preparation'

import {CustomerMode, acceptedUploads, type CustomerDefaultAdapter} from './customer-mode'
import {TaskPrecheck, type TaskPrecheckAdapter} from './task-precheck'

export type AuditTask = components['schemas']['AuditTask']
export type AuditTaskFile = components['schemas']['AuditTaskFile']
export type AuditIntakeAdapter = AuditAcceptanceAdapter & AuditRecognitionAdapter & UploadAdapter & {
  preparation?: PreparationAdapter
  samples?: () => AuditTaskFile[]
  drive?: DrivePickerSource
  customerDefaults?: CustomerDefaultAdapter
  customers?: {value: string; label: string}[]
  deepOffered?: boolean
  notifications?: AuditNotificationAdapter
  autoValidate?: boolean
  precheck?: TaskPrecheckAdapter
  readValidation: (file: AuditTaskFile, scope?: components['schemas']['FileValidationScope'] | null) => Promise<{validation: AuditTaskFile['validation']; validation_id?: string; validation_scope?: components['schemas']['FileValidationScope']}>
  endTask: (task: AuditTask) => Promise<AuditTask>
  listTasks: (query: {q: string; state: AuditTask['state'] | null; offset: number; signal?: AbortSignal}) => Promise<{tasks: TaskSummary[]; next_offset: number | null}>
  authenticate: () => Promise<void>
  prepareReaudit?: (roundId: string) => Promise<components['schemas']['AuditReauditInputs']>
  readRound?: ReadRound
}
const taskLabels = {matching: '识别中', pending_confirm: '待确认', reviewing: '审核中', completed: '已完成'}
const productLinks = [
  {view: 'workbench', label: '工作台', icon: ClipboardCheck},
  {view: 'archive', label: '审核文件', icon: Files},
  {view: 'requirements', label: '审核规则', icon: ClipboardCheck},
  {view: 'templates', label: '模板库', icon: BookOpen},
  {view: 'usage', label: '用量记录', icon: ReceiptText},
] as const
type ProductSurface = Exclude<typeof productLinks[number]['view'], 'templates'> | 'audit'
const sizeLabel = (bytes: number) => bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
type IntakeDraft = {files: AuditTaskFile[]; uploads: UploadRow[]; mode: AuditTask['analysis_mode']; customer: string; step: 'upload' | 'check' | 'match'; predecessorId?: string}
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
export function AuditIntake({adapter, rules, onRefreshCustomers, autoObserve = true, renderOriginal, reportAdapter, sharingAdapter, archiveAdapter, supportAdapter, usageAdapter, displayAdapter, renderWorkbench, uploadEntry}: {uploadEntry?: ReactNode; adapter: AuditIntakeAdapter; rules?: (active: boolean, headerActions?: HTMLElement | null) => ReactNode; onRefreshCustomers?: () => void; autoObserve?: boolean; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; archiveAdapter?: AuditArchiveAdapter; supportAdapter?: AuditSupportAdapter; usageAdapter?: AuditUsageAdapter; displayAdapter?: AuditDisplayAdapter; renderWorkbench?: (props: WorkbenchProps) => ReactNode}) {
  const access = useAccess()
  const reviewEditor = useContext(ReviewEditorContext), reviewRevision = useRef(reviewEditor?.revision)
  const readSurface = (): ProductSurface => {
    const {surface} = readAuditRoute(new URL(location.href))
    return surface === 'templates' ? 'workbench' : surface === 'requirements' && !rules ? 'workbench' : surface
  }
  const [surface, setSurface] = useState<ProductSurface>(readSurface)
  const [legacyRoundId, setLegacyRoundId] = useState(() => new URL(location.href).searchParams.get('round'))
  const [archiveOpened, setArchiveOpened] = useState(surface === 'archive')
  const [rulesOpened, setRulesOpened] = useState(surface === 'requirements')
  const [usageOpened, setUsageOpened] = useState(surface === 'usage')
  const [navigationMode, setNavigationMode] = useState<'auto' | 'expanded' | 'collapsed'>('auto')
  const [navigationTooltip, setNavigationTooltip] = useState<string | null>(null)
  const [desktopNavigation, setDesktopNavigation] = useState(() => window.matchMedia('(min-width: 64rem)').matches)
  const [wideNavigation, setWideNavigation] = useState(() => window.matchMedia('(min-width: 1281px)').matches)
  const [pageActions, setPageActions] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    const media = window.matchMedia('(min-width: 64rem)')
    const wide = window.matchMedia('(min-width: 1281px)')
    const update = () => {setDesktopNavigation(media.matches); setWideNavigation(wide.matches); setNavigationTooltip(null)}
    media.addEventListener('change', update)
    wide.addEventListener('change', update)
    return () => {media.removeEventListener('change', update); wide.removeEventListener('change', update)}
  }, [])
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
  const [mode, setMode] = useState<AuditTask['analysis_mode']>('standard')
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
  const [navigationSheet, setNavigationSheet] = useState(false)
  // The sheet closes as support opens, so its own button is gone by the time focus returns.
  const navigationTrigger = useRef<HTMLButtonElement>(null)
  const [reportView, setReportView] = useState(() => new URL(location.href).searchParams.has('report'))
  const [error, setError] = useState(''), [dragging, setDragging] = useState(false)
  const [checking, setChecking] = useState<string[]>([])
  const [driveOpen, setDriveOpen] = useState(false)
  const [localFiles, setLocalFiles] = useState<File[]>([])
  const [readStatus, setReadStatus] = useState<Record<string, {busy: boolean; error: string; traceId?: string}>>({})
  const [endStatus, setEndStatus] = useState<Record<string, 'sending' | 'unknown' | 'rejected'>>({})
  const endingTasks = useRef(new Set<string>())
  const [taskList, setTaskList] = useState<{next: number | null; busy: boolean; error: string; readScope: string | null; ids: string[]; pages: number}>({next: null, busy: false, error: '', readScope: null, ids: [], pages: 0})
  const listEpoch = useRef(0)
  const latestAdapter = useRef(adapter); latestAdapter.current = adapter
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
  const preparationInput = useRef({files, mode, customer, predecessorId})
  preparationInput.current = {files, mode, customer, predecessorId}
  function preparationValue(rows: UploadRow[]): UploadPreparation {
    const input = preparationInput.current
    return JSON.parse(JSON.stringify({version: 1, analysis_mode: input.mode, customer_preference: input.customer === 'auto' ? null : input.customer, predecessor_id: input.predecessorId ?? null, uploads: describeUploads(rows), files: preparationFiles(input.files)}))
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
      if (activeUploadDraftId.current === id && draftEpoch.current === epoch) setPreparationStatus({id, phase: 'error', message: cause instanceof Error ? cause.message : '上传准备保存结果待核对，请读取原准备记录。'})
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
  const blocked = transferBlocked ? '请先处理或排除未完成的传输，再开始识别。' : waiting.length ? `还有 ${waiting.length} 份文件等待预检查，不能开始识别。` : exceptions.length ? '请先处理或移除异常文件。' : !selected.length ? '至少选择一份可审核文件。' : ''
  const matchingFiles = task ? files.filter(row => row.selection === 'selected') : []
  const matchFields = (row: typeof matchingFiles[number]) => [['文件类型', row.match?.document_type ? documentTypeLabel(row.match.document_type) : undefined], ['客户', row.match?.metadata.customer], ['车型', row.match?.metadata.model], ['零件号', row.match?.metadata.part_number], ['零件名称', row.match?.metadata.part]] as const
  const sharedFields = matchingFiles.length > 1 ? matchFields(matchingFiles[0]).filter(([label, value]) => value && label !== '文件类型' && matchingFiles.every(row => matchFields(row).find(field => field[0] === label)?.[1] === value)) : []
  const matched = matchingFiles.filter(row => row.match?.state === 'matched' && row.match.rule)
  const references = matchingFiles.filter(row => row.match?.state === 'reference')
  const matchPending = (row: typeof matchingFiles[number]) => !row.match || row.match.state === 'matching' || row.match.state === 'pending'
  const needsHandling = matchingFiles.filter(row => !matchPending(row) && !(row.match?.state === 'matched' && row.match.rule) && row.match?.state !== 'reference')
  // Results settle file by file, so rows keep their place until recognition has finished.
  const matchGroups = task?.state === 'matching' ? [{title: '', rows: matchingFiles}] : [{title: '需要处理', rows: needsHandling}, {title: '已匹配', rows: matched}, {title: '比对资料', rows: references}, {title: '等待识别', rows: matchingFiles.filter(matchPending)}].filter(group => group.rows.length)
  const noMatches = Boolean(task?.state === 'pending_confirm' && !matched.length && files.every(row => row.selection !== 'selected' || row.match?.state === 'unmatched' || row.match?.state === 'reference'))
  const recognitionDirty = Boolean(task && !task.round_id && (task.analysis_mode !== mode || task.customer_preference !== (customer === 'auto' ? null : customer) || !onlyUnmatchedRemovals(task.files, files)))
  const endInputsDirty = Boolean(task && (task.analysis_mode !== mode || task.customer_preference !== (customer === 'auto' ? null : customer) || !onlyEndExclusions(task.files, files)))
  const canEnd = noMatches && !endInputsDirty
  const workbench = task?.state === 'reviewing' || task?.state === 'completed' && task.completion_reason === 'processed'
  const navigationCollapsed = navigationMode === 'collapsed' || navigationMode === 'auto' && workbench && surface === 'audit' && !wideNavigation
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
  }, [files, mode, customer, predecessorId, uploads.rows, task, surface])
  function readRecognition() {
    recognitionTargets.current.set(recognitionKey, {epoch: draftEpoch.current, uploadId: activeUploadDraftId.current})
    void recognition.read(recognitionKey)
  }
  function readCurrentRecognition() {
    if (!recognitionState) return
    const id = recognitionState.taskId, uploadId = activeUploadDraftId.current
    const snapshot: IntakeDraft = {files: structuredClone(files), uploads: uploads.snapshot(), mode, customer, step: 'check', predecessorId}
    taskAddress(id); void recover(id)
    drafts.current.set(id, snapshot)
    if (uploadId) uploadDrafts.current.set(uploadId, {taskId: id})
  }
  const confirmation = task ? acceptance.observations[task.id] : undefined
  const confirmationLocked = Boolean(confirmation && confirmation.phase !== 'rejected' || task?.acceptance?.state === 'pending' && confirmation?.phase !== 'rejected')
  const canConfirm = task?.state === 'pending_confirm' && matched.length > 0 && matchingFiles.every(row => row.match?.state === 'unmatched' || row.match?.state === 'reference' || row.match?.state === 'matched' && row.match.rule) && !confirmationLocked && !recognitionLocked && !recognitionDirty && recognitionState?.phase !== 'conflict'
  function confirm(current: AuditTask) {
    taskReadEpoch.current.set(current.id, (taskReadEpoch.current.get(current.id) || 0) + 1)
    taskReads.current.delete(current.id)
    void acceptance.confirm({...current, files: structuredClone(files)})
  }
  function changeReport(next: AuditTask) {
    taskReadEpoch.current.set(next.id, (taskReadEpoch.current.get(next.id) || 0) + 1); taskReads.current.delete(next.id)
    setReadStatus(rows => ({...rows, [next.id]: {busy: false, error: ''}})); saveTask(next)
  }
  function retainDraft() {
    if (workbench || recovery) return
    const snapshot = {files: structuredClone(files), uploads: uploads.snapshot(), mode, customer, step, predecessorId}
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
          replacement_of_file_version_id: intent.replacement_of_file_version_id ?? undefined, validation: {state: 'pending' as const, reasons: ['正在核对原预检查结果。']}, match: null}
      })
      const transfers = restoreUploads(value.uploads)
      const memory = uploadDrafts.current.get(id)
      if (memory && !('taskId' in memory)) for (const row of transfers) {
        const prior = memory.uploads.find(item => item.receipt.request_id === row.receipt.request_id)
        if (prior?.input.kind === 'local' && row.input.kind === 'local' && prior.input.file instanceof File && prior.input.sha256 === row.input.sha256) row.input = prior.input
      }
      setTask(null); setFiles(restored); setMode(value.analysis_mode); setCustomer(value.customer_preference || 'auto'); setPredecessorId(value.predecessor_id || undefined)
      setStep(transfers.length || restored.length ? 'check' : 'upload'); setError(''); uploads.restore(transfers)
      setPreparationStatus({id, phase: 'ready', message: ''})
      for (const row of transfers) uploads.read(row.id)
    } catch (cause) {
      if (current()) setPreparationStatus({id, phase: 'error', message: cause instanceof Error ? cause.message : '上传准备暂不可读取。请重新读取原准备记录。'})
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
  function newTask() {restoreEpoch.current += 1; setRecovery(null); retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = crypto.randomUUID(); setPreparationStatus({id: null, phase: 'idle', message: ''}); setFiles([]); setTask(null); setLocalFiles([]); setStep('upload'); setMode('standard'); customerInitialized.current = false; setCustomerReady(!adapter.customerDefaults); setCustomerState('auto'); setPredecessorId(undefined); setError(''); setSheet(false); taskAddress(null)}
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
      setFiles(prepared.files); setMode(prepared.analysis_mode); setCustomer(prepared.customer_preference || 'auto'); setPredecessorId(roundId); setStep('check')
    } catch {if (epoch === draftEpoch.current) setError('未能读取原审核的冻结输入。原任务没有改变，请返回原任务后重试。')}
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
      setFiles(structuredClone(saved ? mergeFileIntent(saved.files, current.files) : current.files)); setMode(saved?.mode ?? current.analysis_mode); setCustomer(saved?.customer ?? current.customer_preference ?? 'auto'); setStep(saved?.step ?? 'match'); setRecovery(null)
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
      setTask(null); setFiles(structuredClone(saved?.files ?? [])); setMode(saved?.mode ?? 'standard'); if (saved) setCustomer(saved.customer); else {customerInitialized.current = false; setCustomerReady(!adapter.customerDefaults); setCustomerState('auto')} setStep(saved?.step ?? 'upload'); setError('')
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
  const driveButton = adapter.drive && <Button variant="outline" disabled={inputLocked || !customerReady} onClick={event => {driveTrigger.current = event.currentTarget; setDriveOpen(true)}}>从飞书选择</Button>
  function remove(id: string) {
    if (confirmationLocked || inputLocked || workbench) return
    uploads.rows.filter(row => row.receipt.file?.id === id).forEach(row => uploads.remove(row.id))
    setFiles(rows => reconcileReplacements(rows.map(row => row.file.id === id ? {...row, selection: 'removed', removal_reason: row.validation.reasons[0] || (step === 'match' ? '未匹配到规则' : '识别前移除')} : row)))
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
    const epoch = draftEpoch.current, input = {files: structuredClone(files), mode, customer: customer === 'auto' ? null : customer, previous: task, predecessorId}
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
  const observedTaskEpoch = task ? taskReadEpoch.current.get(task.id) : undefined
  useTaskObservation(task?.id, autoObserve && surface === 'audit' && !legacyRoundId && !recovery && step === 'match' && !reportView && canObserveTask(task), () => task && activeTaskId.current === task.id && surfaceRef.current === 'audit' && stepRef.current === 'match' && taskReadEpoch.current.get(task.id) === observedTaskEpoch ? readMatching(task) : Promise.resolve(null))
  // Even an empty successful read belongs to its search and state filter.
  const taskListState = surface === 'workbench' || filter === 'all' ? null : filter as AuditTask['state']
  const taskListScope = JSON.stringify([query, taskListState])
  const taskListNext = taskList.readScope === taskListScope ? taskList.next : null
  async function loadTasks(offset = 0, signal?: AbortSignal, silent = false) {
    if (offset !== 0 && (taskList.readScope !== taskListScope || offset !== taskList.next)) return
    const epoch = ++listEpoch.current
    if (!silent) setTaskList(current => ({...current, busy: true, error: ''}))
    try {
      // Refresh only this search's opened pages, then publish them together.
      const count = offset === 0 && taskList.readScope === taskListScope ? Math.max(taskList.pages, 1) : 1
      const incoming: TaskSummary[] = []
      let next: number | null = offset, pages = offset === 0 ? 0 : taskList.pages
      for (let index = 0; index < count && next !== null; index++) {
        const at: number = next
        const page = await latestAdapter.current.listTasks({q: query, state: taskListState, offset: at, signal})
        if (listEpoch.current !== epoch || signal?.aborted) return
        if (page.next_offset !== null && page.next_offset <= at) throw new Error('task_list_cursor_invalid')
        incoming.push(...page.tasks); pages++; next = page.next_offset
      }
      const read = [...new Map(incoming.map(row => [row.id, row])).values()]
      setSummaries(rows => [...rows.map(row => {
        const next = read.find(candidate => candidate.id === row.id)
        return next ? reconcileTaskSummary(row, next) : row
      }), ...read.filter(next => !rows.some(row => row.id === next.id))])
      setTaskList({next, pages, busy: false, error: '', readScope: taskListScope, ids: offset === 0 ? read.map(row => row.id) : [...new Set([...taskList.ids, ...read.map(row => row.id)])]})
    } catch {if (listEpoch.current === epoch && !signal?.aborted) setTaskList(current => ({...current, busy: false, error: '任务列表读取失败。当前文件与任务仍保留。'}))}
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
    setNavigationSheet(false)
    if (surfaceRef.current === 'audit' && value !== 'audit') retainAuditAddress()
    if (value === 'audit' && auditReturn.current) {
      history.pushState(auditReturn.current.state, '', auditReturn.current.url)
      restoreAddress()
      return
    }
    if (value === 'archive') setArchiveOpened(true)
    if (value === 'requirements') setRulesOpened(true)
    if (value === 'usage') setUsageOpened(true)
    const url = new URL(location.href)
    for (const key of ['archiveDoc', 'auditRef', 'archiveReport', 'requirement', 'reqVersion', 'reqTab', 'reqQuery', 'packageRequest', 'case', 'draft', 'round', 'stage', 'resume']) url.searchParams.delete(key)
    setLegacyRoundId(null)
    if (value !== 'audit') url.searchParams.set('view', value); else url.searchParams.delete('view')
    if (value === 'audit' && activeTaskId.current) url.searchParams.set('task', activeTaskId.current)
    if (!url.pathname.startsWith('/preview/') && url.pathname !== '/__preview') {
      url.pathname = value === 'audit' ? taskHref(activeTaskId.current) : surfaceAddress(value)
      url.searchParams.delete('view'); url.searchParams.delete('task'); url.searchParams.delete('report')
    }
    history.pushState(history.state, '', url)
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view: value, packageRequest: null}, location.origin)
    if (value === 'audit') {restoreAddress(); return}
    surfaceRef.current = value
    setSurface(value)
    // A repeated top-level entry leaves its detail without changing the active surface.
    if (value === surface || value === 'requirements') window.dispatchEvent(new PopStateEvent('popstate'))
  }
  // Text navigation must not open invisible layers that capture Escape in Sheet.
  const navigationTooltipProps = (id: string, compact = navigationCollapsed) => ({
    open: desktopNavigation && compact && navigationTooltip === id,
    onOpenChange: (open: boolean) => {
      if (!desktopNavigation || !compact) return
      setNavigationTooltip(current => open ? id : current === id ? null : current)
    },
  })
  const navigation = (compact = false, tiles = false) => <nav aria-label="产品导航" className={tiles ? 'grid grid-cols-2 gap-2' : 'grid gap-1'}>
    {productLinks.filter(link => link.view !== 'requirements' || rules).map(({view, label, icon: Icon}) => {
      const active = surface === view || view === 'workbench' && surface === 'audit'
      const itemClass = tiles ? 'relative' : compact ? 'justify-center' : 'justify-start'
      return <Tooltip key={view} {...navigationTooltipProps(view, compact)}><TooltipTrigger asChild>{view === 'templates' ? <Button asChild size={tiles ? 'tile' : 'default'} className={itemClass} variant={tiles ? 'secondary' : 'ghost'}><a href={TEMPLATE_GUIDE_URL} target="_blank" rel="noopener noreferrer" aria-label="模板库（在飞书新窗口打开）"><Icon aria-hidden="true"/><span className={compact ? 'sr-only' : ''}>{label}</span>{!compact && <ExternalLink className={tiles ? 'absolute top-4 right-4 size-3 text-muted-foreground' : 'ml-auto size-3 text-muted-foreground'} aria-hidden="true"/>}</a></Button> : <Button size={tiles ? 'tile' : 'default'} className={itemClass} aria-label={label} aria-current={active ? 'page' : undefined} variant={tiles ? active ? 'default' : 'secondary' : active ? 'secondary' : 'ghost'} onClick={() => showSurface(view)}><Icon aria-hidden="true"/><span className={compact ? 'sr-only' : ''}>{label}</span></Button>}</TooltipTrigger><TooltipContent side="right">{label}</TooltipContent></Tooltip>
    })}
    {tiles && <Button variant="secondary" size="tile" onClick={() => {setNavigationSheet(false); openSupport({scenario: supportScenario}, navigationTrigger.current)}}><HelpCircle aria-hidden="true"/>联系我们</Button>}
  </nav>
  // Files stopped for the same reason need the same action, so the advice appears once above them.
  const sameReasonBatches = (rows: typeof matchingFiles) => {
    const batches = new Map<string, typeof matchingFiles>()
    for (const row of rows) {
      const reason = row.match?.state === 'unmatched' && !(row.match.candidates && row.match.candidates.length > 0) && !row.match.gaps.some(gap => !isMatchingReason(gap) && analysisNotice(gap)) ? row.match.gaps.find(isMatchingReason) ?? 'none' : row.file.id
      batches.set(reason, [...(batches.get(reason) ?? []), row])
    }
    return [...batches.values()]
  }
  const matchRow = (row: typeof matchingFiles[number]) => <section key={row.file.id} className="space-y-4 py-5 first:pt-0">
            <div className="flex flex-wrap items-start justify-between gap-3"><h3 className="min-w-0 flex-1 font-semibold"><FileName name={row.file.original_filename}/></h3><Badge variant={row.match?.state === 'matched' && row.match.rule ? 'success' : row.match?.state === 'reference' || matchPending(row) ? 'outline' : 'warning'}>{row.match?.state === 'matched' && row.match.rule ? '已匹配' : row.match?.state === 'reference' ? '比对资料' : row.match?.state === 'unmatched' ? '未匹配' : row.match?.state === 'matching' ? '识别中' : !row.match || row.match.state === 'pending' ? '等待识别' : '识别异常'}</Badge></div>
            <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">{matchFields(row).filter(([label]) => !sharedFields.some(field => field[0] === label)).map(([label, value]) => <div className="flex min-w-0 gap-1.5" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="break-words">{value || '—'}</dd></div>)}</dl>
            {row.match?.state === 'matched' && row.match.rule ? <div className="space-y-2" data-rule-version={row.match.rule.id}><p className="font-medium">{row.match.rule.title}</p><ul aria-label={`${row.file.original_filename} · 匹配条款`} className="grid gap-1">{row.match.rule.body.kind === 'engine' ? row.match.rule.body.rules.map(clause => <li key={clause.id}><Button variant="ghost" size="sm" className="h-auto max-w-full justify-start whitespace-normal text-left" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource({rule: row.match!.rule!, clause, filename: row.file.original_filename})}}><BookOpen aria-hidden="true"/>{clause.id} · {clause.category}</Button></li>) : <li><Button variant="ghost" size="sm" className="justify-start" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource({rule: row.match!.rule!, clause: null, filename: row.file.original_filename})}}>文件存在性检查</Button></li>}</ul></div>
              : row.match?.state === 'reference' ? <p className="text-muted-foreground">本文件在本轮作为比对资料保留并冻结版本，不单独审核、不计费，也不生成单独报告。</p>
              : row.match?.state === 'matching' || !row.match || row.match.state === 'pending' ? <p className="text-muted-foreground">等待识别结果…</p>
              : row.match.state === 'unmatched' || row.match.gaps.some(isMatchingReason) ? <MatchingFeedback gaps={row.match.gaps} candidates={row.match.candidates} onCheck={() => setStep(row.match!.gaps.some(gap => gap === 'matching_customer_required' || gap === 'matching_customer_conflict') ? 'upload' : 'check')} onRules={rules ? () => showSurface('requirements') : undefined} onRemove={() => remove(row.file.id)}/>
              : <Alert><AlertTitle>暂时无法确认匹配结果</AlertTitle><AlertDescription><p>请返回检查文件后重新识别。</p></AlertDescription></Alert>}
            {row.match?.gaps.filter(gap => !isMatchingReason(gap)).map(gap => analysisNotice(gap)).filter((notice): notice is string => Boolean(notice)).map(notice => <p key={notice} className="text-sm text-muted-foreground">{notice}</p>)}
            {row.match?.candidates && row.match.candidates.length > 1 && <div className="space-y-2"><Label>选择适用规则后重新识别</Label><Select value={row.preferred_rule_version_id || ''} onValueChange={value => setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, preferred_rule_version_id: value} : file))}><SelectTrigger aria-label={`${row.file.original_filename} 的适用规则`}><SelectValue placeholder="请选择规则，不会自动选择"/></SelectTrigger><SelectContent>{row.match.candidates.map(rule => <SelectItem key={rule.id} value={rule.id}>{rule.title}</SelectItem>)}</SelectContent></Select>{row.preferred_rule_version_id && <Button variant="outline" size="sm" disabled={recognitionLocked || Boolean(blocked)} onClick={() => void startMatching()}>按所选规则重新识别</Button>}</div>}
          </section>
  const taskSwitcher = surface === 'audit' && <TaskSwitcher open={sheet} onOpenChange={setSheet} currentId={legacyRoundId ? undefined : task?.id} tasks={shownTasks} labels={taskLabels} query={query} onQuery={setQuery} filter={filter} onFilter={setFilter} busy={taskList.busy} error={taskList.error} hasMore={taskListNext !== null} hasTasks={summaries.length > 0} onMore={() => void loadTasks(taskList.error ? 0 : taskListNext!)} onNew={newTask} onOpen={id => {setSheet(false); taskAddress(id); void recover(id)}}/>
  const table = (rows: AuditTaskFile[], selectable: boolean) => <Table className="table-fixed">
    <TableHeader><TableRow>{selectable && <TableHead className="w-12"><Checkbox aria-label="全选可审核文件" checked={selected.length === valid.length && valid.length > 0 ? true : selected.length ? 'indeterminate' : false} onCheckedChange={value => toggleAll(value === true)}/></TableHead>}<TableHead>文件</TableHead><TableHead className="w-24 max-md:hidden">大小</TableHead><TableHead className="w-28 max-md:hidden">预检查</TableHead><TableHead className={selectable ? 'w-16' : 'w-36'}><span className="sr-only">操作</span></TableHead></TableRow></TableHeader>
    <TableBody>{rows.map(row => <TableRow key={row.file.id}>{selectable && <TableCell><Checkbox aria-label={`选择 ${row.file.original_filename}`} checked={row.selection === 'selected'} onCheckedChange={value => toggle(row.file.id, value === true)}/></TableCell>}<TableCell className="whitespace-normal"><FileName name={row.file.original_filename}/>{customer === 'UAES' && /\.(?:png|jpe?g|webp|pdf)$/i.test(row.file.original_filename) && <div className="mt-2">{row.validation_scope || photoScopes.current.has(row.file.id) ? <p className="text-xs text-muted-foreground">本轮用途：生产件样品照片；内容仍需人工确认</p> : <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} onClick={() => void usePhotoPurpose(row)}>作为生产件样品照片检查</Button>}</div>}{selectable && v4Mode && <Select value={row.reference_only ? 'reference' : 'target'} onValueChange={value => setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, reference_only: value === 'reference' ? true : undefined, preferred_rule_version_id: undefined} : file))}><SelectTrigger className="mt-2 w-full max-w-56" aria-label={`${row.file.original_filename} 本轮用途`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="target">待审文件 · 正式审核收费</SelectItem><SelectItem value="reference">比对资料 · 不单独审核或收费</SelectItem></SelectContent></Select>}{!selectable && <p className="mt-2 text-sm text-muted-foreground">{row.validation.reasons.join('；') || '暂未完成检查'}</p>}<span className="mt-1 block text-xs text-muted-foreground md:hidden">{sizeLabel(row.file.byte_size)} · {row.validation.state === 'valid' ? '可审核' : '待处理'}</span></TableCell><TableCell className="max-md:hidden"><span className="text-muted-foreground">{sizeLabel(row.file.byte_size)}</span></TableCell><TableCell className="max-md:hidden"><Badge variant="outline">{{valid: '可审核', pending: '待预检查', checking: '检查中', invalid: '文件异常', error: '读取失败'}[row.validation.state]}</Badge></TableCell><TableCell><div className="flex flex-wrap items-center justify-end gap-1">{!selectable && row.validation.state !== 'invalid' && <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} aria-label={`${row.validation.state === 'error' ? '重新读取' : '读取'} ${row.file.original_filename} 检查结果`} onClick={() => void readValidation(row)}>{checking.includes(row.file.id) ? '读取中…' : row.validation.state === 'error' ? '重新读取' : '读取结果'}</Button>}<Button variant="ghost" size="icon" aria-label={`移除 ${row.file.original_filename}`} onClick={() => remove(row.file.id)}><X/></Button></div></TableCell></TableRow>)}</TableBody>
  </Table>
  const supportScenario = surface === 'archive' ? '文件档案' : surface === 'requirements' ? '审核规则' : surface === 'usage' ? '用量记录' : workbench ? '审核结果' : '上传与文件核对'
  function contactReadSupport(current: AuditTask) {openSupport({scenario: '读取审核任务', traceId: readStatus[current.id]?.traceId, facts: [{label: '任务标识', value: current.id}, ...(current.round_id ? [{label: '审核轮次', value: current.round_id}] : []), {label: '服务说明', value: readStatus[current.id]?.error || ''}]})}
  const recognitionSupport = recognitionState && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '识别文件与匹配规则', traceId: recognitionState.traceId, facts: [{label: '任务标识', value: recognitionState.taskId}, {label: '识别请求', value: recognitionState.requestId}, {label: '文件', value: files.map(row => row.file.original_filename).join('\n')}, {label: '服务说明', value: recognitionState.message}]})}>联系支持</Button>
  const pageContext = surface !== 'audit' ? productLinks.find(link => link.view === surface)?.label : legacyRoundId ? '历史审核' : task ? task.number : recovery ? reportView ? '审核报告' : '审核任务' : '新建审核'
  return <TooltipProvider><SupportEntryContext.Provider value={openSupport}><div className="flex min-h-dvh bg-background text-sm text-foreground">
    <a href="#audit-main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:p-3 focus-visible:outline-ring focus-visible:ring-2 focus-visible:ring-ring">跳到主要内容</a>
    <aside aria-label="产品侧栏" className={`sticky top-0 flex h-dvh shrink-0 flex-col gap-8 border-r max-lg:hidden ${navigationCollapsed ? 'w-16 p-2' : 'w-54 p-4'}`}>
      <div className={`flex items-center gap-2 text-lg font-semibold ${navigationCollapsed ? 'justify-center py-2' : ''}`}><ClipboardCheck aria-hidden="true"/><span className={navigationCollapsed ? 'sr-only' : ''}>PPAP</span></div>
      {navigation(navigationCollapsed)}
      <div className="mt-auto grid gap-1">
        <Tooltip {...navigationTooltipProps('support')}><TooltipTrigger asChild><Button className={navigationCollapsed ? 'justify-center' : 'justify-start'} variant="ghost" aria-label="联系我们" onClick={() => openSupport({scenario: supportScenario})}><HelpCircle aria-hidden="true"/><span className={navigationCollapsed ? 'sr-only' : ''}>联系我们</span></Button></TooltipTrigger><TooltipContent side="right">联系我们</TooltipContent></Tooltip>
        <Tooltip {...navigationTooltipProps('toggle')}><TooltipTrigger asChild><Button className={navigationCollapsed ? 'justify-center' : 'justify-start'} variant="ghost" aria-label={navigationCollapsed ? '展开产品导航' : '折叠产品导航'} onClick={() => {setNavigationTooltip(null); setNavigationMode(navigationCollapsed ? 'expanded' : 'collapsed')}}>{navigationCollapsed ? <PanelLeftOpen aria-hidden="true"/> : <PanelLeftClose aria-hidden="true"/>}<span className={navigationCollapsed ? 'sr-only' : ''}>{navigationCollapsed ? '展开导航' : '折叠导航'}</span></Button></TooltipTrigger><TooltipContent side="right">展开产品导航</TooltipContent></Tooltip>
      </div>
    </aside>
    <div className="min-w-0 flex-1">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-2 max-sm:px-4"><div className="flex min-w-0 items-center gap-3">
        <Sheet open={navigationSheet} onOpenChange={setNavigationSheet}><SheetTrigger asChild><Button ref={navigationTrigger} variant="outline" size="icon" className="lg:hidden" aria-label="打开产品导航"><Menu aria-hidden="true"/></Button></SheetTrigger><SheetContent side="left" className="w-[min(100%,26rem)] max-w-none"><SheetHeader><SheetTitle>产品导航</SheetTitle><SheetDescription className="sr-only">选择产品页面</SheetDescription></SheetHeader><div className="min-h-0 flex-1 overflow-y-auto">{navigation(false, true)}</div></SheetContent></Sheet>
        {surface === 'audit' && !legacyRoundId && reportView ? <nav aria-label="当前位置" className="min-w-0"><ol className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <li><a href={surfaceAddress('workbench')} className="text-muted-foreground hover:text-foreground hover:underline" onClick={event => {if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {event.preventDefault(); showSurface('workbench')}}}>工作台</a></li>
          <li aria-hidden="true" className="text-muted-foreground">/</li>
          <li className="flex min-w-0 items-center gap-1">{task && !recovery ? <a href={taskResultsAddress().href} aria-label="返回审核结果" className="font-medium hover:underline" onClick={event => {if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {event.preventDefault(); showTaskResults()}}}>{task.number}</a> : <span className="text-muted-foreground">审核任务</span>}{taskSwitcher}</li>
          <li aria-hidden="true" className="text-muted-foreground">/</li><li aria-current="page" className="font-medium">审核报告</li>
        </ol></nav> : <div className="flex min-w-0 items-center gap-1"><span className="text-muted-foreground">PPAP Audit / {pageContext}</span>{taskSwitcher}</div>}
      </div><div className="flex shrink-0 items-center gap-2"><div ref={setPageActions} role="group" aria-label="页面操作" className="flex min-w-0 flex-wrap items-center gap-2"/>{access?.principal && <AccountMenu compact onUsage={() => showSurface('usage')} showLocalRecords={Boolean(rules)}/>}</div></header>
      <main id="audit-main" tabIndex={-1} className={surface === 'audit' && workbench && !reportView ? 'w-full' : `mx-auto w-full space-y-6 p-6 max-sm:p-4 ${recovery || workbench || surface !== 'audit' ? '' : step === 'match' ? 'max-w-7xl' : 'max-w-5xl'}`}>
        {surface === 'workbench' && (renderWorkbench || (props => <AuditWorkbench {...props}/>))({headerActions: pageActions, tasks: workbenchTasks, query, onQuery: setQuery, onNew: newTask, onResume: auditReturn.current || activeTaskId.current || activeUploadDraftId.current ? () => showSurface('audit') : undefined, onOpen: href => {const route = readAuditRoute(new URL(href, location.href)); if (route.taskId) {taskAddress(route.taskId); void recover(route.taskId)} else location.assign(href)}, loading: taskList.busy && !workbenchTasks.length, countsKnown: taskList.readScope === taskListScope, busy: taskList.busy, errors: taskList.error ? [taskList.error] : [], more: taskListNext !== null, onMore: () => void loadTasks(taskListNext || 0), onRefresh: () => void loadTasks()})}
        {archiveOpened && <div hidden={surface !== 'archive'}>{archiveAdapter ? <AuditArchive headerActions={surface === 'archive' ? pageActions : null} active={surface === 'archive'} adapter={archiveAdapter} onUpload={newTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}}/> : <Alert><AlertTitle>审核文件暂不可用</AlertTitle><AlertDescription>请稍后重试。</AlertDescription></Alert>}</div>}
        {rulesOpened && <div hidden={surface !== 'requirements'}>{rules?.(surface === 'requirements', surface === 'requirements' ? pageActions : null)}</div>}
        {usageOpened && <div hidden={surface !== 'usage'}><AuditUsage headerActions={surface === 'usage' ? pageActions : null} active={surface === 'usage'} adapter={usageAdapter} archiveAdapter={archiveAdapter} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}} onSupport={notice => openSupport({scenario: `用量记录 · ${notice}`})}/></div>}
        {surface === 'audit' && legacyRoundId && adapter.readRound && <AuditRoundReader headerActions={pageActions} roundId={legacyRoundId} readRound={adapter.readRound} renderOriginal={renderOriginal} onReaudit={adapter.prepareReaudit ? () => void prepareReaudit(legacyRoundId) : undefined} onReport={reportAdapter ? openRoundReport : undefined} returnHref="/workbench"/>}
        <div hidden={surface !== 'audit' || Boolean(legacyRoundId)} className={workbench && !reportView ? '' : 'space-y-6'}>
        {!reportView && !(workbench && task && allTaskReportsReady(task)) && <div className={workbench ? 'flex flex-wrap items-center gap-x-6 gap-y-2 px-6 pt-3 max-sm:px-4' : 'space-y-6'}><div><Button variant="ghost" size="sm" onClick={() => showSurface('workbench')}><ArrowLeft/>工作台</Button></div>
        {!recovery && <ol aria-label="审核步骤" className={workbench ? 'flex flex-wrap gap-3 text-sm' : 'flex flex-wrap gap-4 border-b pb-4 text-sm'}>{['上传与检查', '确认规则', 'AI审核'].map((label, index) => {
          const current = workbench ? 2 : step === 'match' ? 1 : 0
          const done = index < current || Boolean(workbench && task && allTaskReportsReady(task))
          return <li key={label} aria-current={!done && current === index ? 'step' : undefined} data-state={done ? 'complete' : current === index ? 'current' : 'upcoming'} className={`flex items-center gap-2 ${done ? 'text-success' : current === index ? 'font-medium text-foreground' : 'text-muted-foreground'}`}><span className={`flex size-6 shrink-0 items-center justify-center rounded-full border ${done ? 'border-success' : current === index ? 'border-primary bg-primary text-primary-foreground' : ''}`}>{done ? <Check aria-label="已完成" className="size-3.5"/> : index + 1}</span><span>{label}</span>{index < 2 && <ArrowRight aria-hidden="true" className="ml-1 size-3.5 text-muted-foreground"/>}</li>
        })}</ol>}</div>}
        {error && <Alert variant="destructive"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
        {preparingReaudit && <ReadSkeleton label="正在读取原审核的完整文件与规则版本…" kind={'detail'}/>}
        {predecessorId && !workbench && <Alert><AlertTitle>新建关联重审</AlertTitle><AlertDescription>原任务和历史结果保留。默认沿用原文件与规则版本，所有本次审核文件都会重新审核；人工复核从未复核开始。<Button variant="link" asChild><a href={`/?round=${encodeURIComponent(predecessorId)}`}>查看原审核</a></Button></AlertDescription></Alert>}
        {recognitionState && recognitionLocked && <Alert data-request-id={recognitionState.requestId}><AlertTitle>{recognitionState.phase === 'sending' ? '正在提交识别请求' : '识别请求待核对'}</AlertTitle><AlertDescription><p>{recognitionState.message || '正在等待同一请求的回执，文件与选择已保留。'}</p>{['unknown', 'reading'].includes(recognitionState.phase) && <><Button variant="outline" disabled={recognitionState.phase === 'reading'} onClick={readRecognition}>{recognitionState.phase === 'reading' ? '正在读取识别回执…' : '读取识别回执'}</Button>{recognitionSupport}</>}</AlertDescription></Alert>}
        {recognitionState?.phase === 'rejected' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>本次识别未受理</AlertTitle><AlertDescription><p>{recognitionState.message}</p>{task?.id === recognitionState.taskId && <Button variant="outline" onClick={readCurrentRecognition}>读取当前任务</Button>}{recognitionSupport}</AlertDescription></Alert>}
        {recognitionState?.phase === 'conflict' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>任务输入版本已更新</AlertTitle><AlertDescription><p>{recognitionState.message}</p><Button variant="outline" onClick={readCurrentRecognition}>读取当前任务</Button></AlertDescription></Alert>}
        {task && task.state !== 'completed' && endStatus[task.id] === 'unknown' && <Alert><AlertTitle>结束结果待核对</AlertTitle><AlertDescription><p>尚未确认任务已结束。请读取同一任务，不要重复结束。</p>{readStatus[task.id]?.error && <p>{readStatus[task.id].error}</p>}<Button variant="outline" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>读取任务状态</Button></AlertDescription></Alert>}
        {task && !recovery && adapter.precheck && (task.precheck || task.files.some(file => file.match?.rule?.body.kind === 'engine' && file.match.rule.body.rule_profile === 'v4')) && <TaskPrecheck key={`precheck:${task.id}`} task={task} files={workbench ? task.files : files} renderOriginal={renderOriginal} adapter={adapter.precheck} dirty={recognitionDirty}/>}
        {recovery ? <TaskRecovery recovery={recovery} report={reportView} onRead={() => void recover(recovery.id, recovery.state === 'unauthenticated')} onSupport={() => openSupport({scenario: '读取审核任务', traceId: recovery.traceId, facts: [{label: '任务标识', value: recovery.id}, {label: '服务说明', value: recovery.message}]})}/> : workbench && task ? <AuditWorkspace readRound={adapter.readRound} onReaudit={adapter.prepareReaudit ? () => void prepareReaudit(task.round_id!) : undefined} headerActions={surface === 'audit' && !legacyRoundId ? pageActions : null} key={task.id} task={task} busy={readStatus[task.id]?.busy || false} readError={readStatus[task.id]?.error || ''} onReadSupport={() => contactReadSupport(task)} onRead={() => void readMatching(task)} onChange={saveTask} renderOriginal={renderOriginal} reportAdapter={reportAdapter} notificationAdapter={adapter.notifications} onReportChange={changeReport} onReportView={setReportView} sharingAdapter={sharingAdapter} onEditDisplay={displayAdapter ? file => displayEditor.open({task_id: task.id, round_id: task.round_id!, file_version_id: file.file.id}, file) : undefined}/> : task?.state === 'completed' ? <section className="space-y-4 py-8"><Check className="size-8"/><h1 className="text-2xl font-semibold">任务已结束</h1><p>{task.files.every(row => row.selection !== 'selected') ? '未选择待审文件，未开始审核' : task.files.filter(row => row.selection === 'selected').every(row => row.match?.state === 'reference') ? '仅保留比对资料，未开始审核' : '未匹配到可用规则，未开始审核'}</p><p className="text-muted-foreground">未产生审核费用，也没有生成报告。</p><Button onClick={newTask}>新建审核任务<ArrowRight/></Button></section> : <>
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'reading' && <section role="status" aria-label="正在恢复上传准备" className="space-y-4"><h1 className="sr-only">上传准备</h1><Skeleton className="h-7 w-48"/>{[0, 1, 2].map(row => <Skeleton key={row} className="h-16 w-full"/>)}</section>}
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'saving' && <p role="status" className="text-sm text-muted-foreground">正在保存上传准备…</p>}
        {!task && preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'error' && <Alert variant="destructive"><AlertTitle>上传准备待核对</AlertTitle><AlertDescription><p>{preparationStatus.message}</p><div className="mt-3 flex flex-wrap gap-2"><Button variant="outline" onClick={() => void readPreparation(activeUploadDraftId.current!)}>读取准备记录</Button>{preparationStatus.conflict && <Button variant="outline" onClick={() => {retainDraft(); void readPreparation(activeUploadDraftId.current!, true)}}>载入已保存资料</Button>}</div></AlertDescription></Alert>}
        <fieldset hidden={preparationStatus.id === activeUploadDraftId.current && preparationStatus.phase === 'reading'} disabled={inputLocked || preparingReaudit} className="min-w-0 space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4"><div className="space-y-2"><h1 className="text-2xl font-semibold tracking-tight">{step === 'upload' ? '上传 PPAP 文件' : step === 'check' ? '核对本次文件' : task?.state === 'matching' ? '正在识别文件与匹配规则' : '确认每份文件的审核规则'}</h1>{step !== 'match' && <p className="text-muted-foreground">选择收到的文件，核对后开始审核。</p>}</div>{step === 'upload' && !task && uploadEntry}</header>
        {step !== 'match' && <div className="flex flex-wrap items-end justify-between gap-4"><div className="flex flex-wrap items-end gap-2">{step === 'upload' && surface === 'audit' && !legacyRoundId && (adapter.customerDefaults ? <CustomerMode key={draftEpoch.current} value={customer} ready={customerReady} options={adapter.customers ?? []} adapter={adapter.customerDefaults} onInitialDefault={initializeCustomer} onRefresh={onRefreshCustomers} onChange={changeCustomer}/> : <div className="grid gap-2"><Label htmlFor="audit-customer">客户（可选）</Label><Select value={customer} onValueChange={changeCustomer} onOpenChange={open => {if (open) onRefreshCustomers?.()}}><SelectTrigger id="audit-customer" className="w-56"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="auto">自动识别客户</SelectItem>{(adapter.customers ?? []).map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select></div>)}</div>{step === 'check' && <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => input.current?.click()}><Plus/>继续添加文件</Button>{driveButton}</div>}</div>}
        <input ref={input} disabled={!customerReady} type="file" className="sr-only" aria-label="选择审核文件" multiple accept={acceptedUploads(customer).accept} onChange={event => {receive(Array.from(event.target.files || [])); event.target.value = ''}}/>
        {step === 'upload' && <section className="space-y-5"><div className="flex flex-col items-center gap-4 rounded-lg border border-dashed px-4 py-12 text-center" data-dragging={dragging} onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); receive(Array.from(event.dataTransfer.files))}}><UploadCloud className="size-8 text-muted-foreground"/><h2 className="text-base font-medium">拖入本地文件</h2><p className="text-muted-foreground">{acceptedUploads(customer).hint}</p><div className="flex flex-wrap justify-center gap-2"><Button disabled={!customerReady} onClick={() => input.current?.click()}>选择本地文件</Button>{driveButton}</div></div>{adapter.samples && <div className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" onClick={() => {setFiles(rows => [...rows, ...adapter.samples!()]); setStep('check')}}>使用三类文件示例<ArrowRight/></Button></div>}</section>}
        {step === 'upload' && (active.length > 0 || uploads.rows.some(row => !row.removed)) && <section className="flex flex-wrap items-center justify-between gap-4 border-t pt-5"><div className="space-y-1"><h2 className="font-medium">已选 {selectedUploadCount} 个文件</h2><p className="text-sm text-muted-foreground">继续上传或从飞书选择后，将自动返回文件确认</p></div><Button variant="outline" onClick={() => setStep('check')}>查看已选文件<ArrowRight/></Button></section>}
        {step === 'check' && <>
          <TransferList rows={uploads.rows} onRead={uploads.read} onRetry={uploads.retry} onResume={uploads.resume} onAttach={(id, file) => void uploads.attach(id, file)} onRemove={uploads.remove} onCheckDrive={adapter.drive ? trigger => {driveTrigger.current = trigger; setDriveOpen(true)} : undefined}/>
          <section className="space-y-3"><h2 className="font-semibold">可审核文件 · {valid.length} 份</h2>{valid.length ? table(valid, true) : <p className="py-4 text-muted-foreground">尚无完成预检查的可审核文件。</p>}</section>
          {files.some(file => file.rule_options?.length || predecessorId && file.reference_only) && <section aria-label="重审文件与规则" className="space-y-3 border-t pt-4"><h2 className="font-semibold">沿用的审核依据</h2>{files.filter(file => file.selection === 'selected').map(file => <div key={file.file.id} className="flex flex-wrap items-center justify-between gap-3"><FileName name={file.file.original_filename}/>{file.reference_only ? <Badge variant="outline">比对资料 · 不单独审核</Badge> : file.rule_options?.length ? <Select value={file.preferred_rule_version_id || 'auto'} onValueChange={value => setFiles(rows => rows.map(row => row.file.id === file.file.id ? {...row, preferred_rule_version_id: value === 'auto' ? undefined : value} : row))}><SelectTrigger aria-label={`${file.file.original_filename} 的重审规则`}><SelectValue/></SelectTrigger><SelectContent>{file.rule_options.map(rule => <SelectItem key={rule.id} value={rule.id}>{rule.title}</SelectItem>)}<SelectItem value="auto">重新匹配当前规则</SelectItem></SelectContent></Select> : <span className="text-sm text-muted-foreground">重新匹配当前规则</span>}</div>)}</section>}
          {predecessorId && files.filter(file => file.selection === 'selected' && (file.replacement_of_file_version_id || !file.rule_options?.length && !file.reference_only)).map(file => <div key={file.file.id} className="flex flex-wrap items-center justify-between gap-3"><FileName name={file.file.original_filename}/><Select value={file.replacement_of_file_version_id || 'new'} onValueChange={id => chooseReplacement(file.file.id, id)}><SelectTrigger aria-label={`${file.file.original_filename} 的补件关系`}><SelectValue/></SelectTrigger><SelectContent><SelectItem value="new">新增文件 · 重新匹配</SelectItem>{files.filter(row => row.file.id !== file.file.id && !row.replacement_of_file_version_id && (row.rule_options?.length || row.reference_only)).map(row => <SelectItem key={row.file.id} value={row.file.id} disabled={files.some(other => other.file.id !== file.file.id && other.selection === 'selected' && other.replacement_of_file_version_id === row.file.id)}>替换：{row.file.original_filename}</SelectItem>)}</SelectContent></Select></div>)}
          {waiting.length > 0 && <section className="space-y-3 border-t pt-5"><h2 className="font-semibold">等待预检查 · {waiting.length} 份</h2>{table(waiting, false)}</section>}
          {exceptions.length > 0 && <section className="space-y-3 border-t pt-5"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">异常文件 · {exceptions.length} 份</h2><Button variant="outline" size="sm" onClick={() => exceptions.forEach(row => remove(row.file.id))}>一键移除异常文件</Button></div>{table(exceptions, false)}</section>}
          <footer className="sticky bottom-0 z-10 -mx-6 space-y-4 border-t bg-background/95 px-6 py-4 backdrop-blur-sm max-sm:-mx-4 max-sm:px-4"><p role="status" className="text-muted-foreground">{canEnd ? '没有待审目标，可以结束任务，不收费、不生成报告。' : blocked || `已选择 ${selected.length} 份文件，开始识别后逐份匹配规则。`}</p><div className="flex flex-wrap items-center justify-between gap-4">{adapter.deepOffered !== false && <div className="grid gap-1"><Label><Checkbox checked={mode === 'deep'} onCheckedChange={value => setMode(value === true ? 'deep' : 'standard')}/>深度模式</Label><p className="pl-6 text-xs text-muted-foreground">遇到无法判断的条款时，会在证据旁提出问题；确认解释后可重新分析。</p></div>}<div className="ml-auto flex gap-2"><Button variant="outline" onClick={() => setStep('upload')}><ArrowLeft/>上一步</Button>{canEnd && task ? <Button disabled={recognitionLocked || Boolean(endStatus[task.id])} onClick={() => {setStep('match'); void endTask(task)}}>结束任务</Button> : <Button disabled={Boolean(blocked) || recognitionLocked || recognitionNeedsCurrent} onClick={startMatching}>{recognitionState?.phase === 'sending' ? '正在提交识别…' : recognitionState?.phase === 'rejected' || recognitionState?.phase === 'conflict' ? '重新开始识别' : '开始识别'}<ArrowRight/></Button>}</div></div></footer>
        </>}
        {step === 'match' && task && <>
          <div className="flex flex-wrap items-center gap-2 tabular-nums" role="status"><Badge variant="secondary">{task.state === 'matching' ? `识别中 ${matchingFiles.filter(row => row.match && ['matched', 'unmatched', 'reference', 'failed'].includes(row.match.state)).length}/${matchingFiles.length}` : taskLabels[task.state]}</Badge><span className="text-muted-foreground">{matchingFiles.length} 份文件{task.state === 'matching' ? ` · ${matched.length} 份匹配到规则` : ''}</span></div>
          {task.state === 'matching' && readStatus[task.id]?.error && <Alert variant="destructive"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription><p>{readStatus[task.id].error}</p><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>{readStatus[task.id]?.busy ? '正在读取识别结果…' : '重新读取识别结果'}</Button><Button variant="outline" size="sm" onClick={() => contactReadSupport(task)}>联系支持</Button></div></AlertDescription></Alert>}
          {sharedFields.length > 0 && <dl aria-label="这些文件的共同信息" className="flex flex-wrap gap-x-5 gap-y-1 rounded-lg border px-4 py-3 text-xs">{sharedFields.map(([label, value]) => <div className="flex min-w-0 gap-1.5" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="break-words">{value}</dd></div>)}</dl>}
          {matchGroups.map(group => <section key={group.title} aria-label={group.title || undefined} className="space-y-2">{group.title && <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2"><h2 className="text-sm font-semibold">{group.title} · {group.rows.length} 份</h2>{group.title === '需要处理' && group.rows.length > 1 && <Button variant="outline" size="sm" disabled={confirmationLocked} onClick={() => group.rows.forEach(row => remove(row.file.id))}>全部移除</Button>}</div>}<div className="divide-y">{(group.title === '需要处理' ? sameReasonBatches(group.rows) : group.rows.map(row => [row])).map(batch => batch.length > 1 ? <section key={batch[0].file.id} className="space-y-3 py-5 first:pt-0">
            <MatchingFeedback gaps={batch[0].match!.gaps} onCheck={() => setStep(batch[0].match!.gaps.some(gap => gap === 'matching_customer_required' || gap === 'matching_customer_conflict') ? 'upload' : 'check')} onRules={rules ? () => showSurface('requirements') : undefined}/>
            <ul aria-label="同一原因的文件" className="divide-y rounded-md border">{batch.map(row => <li key={row.file.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-3 py-2"><h3 className="min-w-0 flex-1 basis-64 font-medium"><FileName name={row.file.original_filename}/></h3><dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">{matchFields(row).filter(([label]) => !sharedFields.some(field => field[0] === label)).map(([label, value]) => <div className="flex min-w-0 gap-1.5" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="break-words">{value || '—'}</dd></div>)}</dl><Badge variant="warning">未匹配</Badge><Button variant="ghost" size="sm" onClick={() => remove(row.file.id)}>移除</Button></li>)}</ul>
          </section> : matchRow(batch[0]))}</div></section>)}
          <footer className="sticky bottom-0 z-10 -mx-6 space-y-4 border-t bg-background/95 px-6 py-4 backdrop-blur-sm max-sm:-mx-4 max-sm:px-4">{noMatches && <p className="text-muted-foreground">没有可进入审核的匹配文件。可以结束任务，不扣费、不生成报告。</p>}
            {(noMatches ? endInputsDirty : recognitionDirty) && <p role="status" className="text-muted-foreground">文件或规则选择已有变更，请重新识别后再确认或结束任务。</p>}
            {confirmation?.message && <Alert data-request-id={confirmation.requestId} variant={confirmation.phase === 'rejected' ? 'default' : 'destructive'}><AlertTitle>{confirmation.phase === 'rejected' ? '本次未受理' : '受理结果待核对'}</AlertTitle><AlertDescription><p>{confirmation.message}</p><Button variant="outline" size="sm" onClick={() => openSupport({scenario: '确认审核', traceId: confirmation.traceId, facts: [{label: '任务标识', value: task.id}, {label: '确认请求', value: confirmation.requestId}, {label: '受理状态', value: confirmation.phase === 'rejected' ? '明确未受理' : '尚未确认'}, {label: '服务说明', value: confirmation.message}]})}>联系支持</Button></AlertDescription></Alert>}
            {endStatus[task.id] === 'rejected' && <Alert><AlertTitle>结束请求已拒绝</AlertTitle><AlertDescription>输入版本可能已更新。当前文件与选择已保留，请读取当前任务后再操作。<Button variant="outline" onClick={() => void recover(task.id)}>读取当前任务</Button></AlertDescription></Alert>}
            <div className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" disabled={confirmationLocked || endStatus[task.id] === 'sending' || endStatus[task.id] === 'unknown'} onClick={() => setStep('check')}><ArrowLeft/>上一步</Button>{noMatches ? <Button disabled={Boolean(endStatus[task.id])} onClick={() => void endTask(task)}>{endStatus[task.id] === 'sending' ? '正在结束任务…' : '结束任务'}</Button> : <div className="flex flex-wrap gap-2">{confirmation && ['unknown', 'reading'].includes(confirmation.phase) && <Button variant="outline" disabled={confirmation.phase === 'reading'} onClick={() => void acceptance.reconcile(task)}>{confirmation.phase === 'reading' ? '正在读取受理结果…' : '读取受理结果'}</Button>}<span className="self-center text-sm tabular-nums text-muted-foreground">预计 {task.estimated_points ?? '—'} 点{task.estimated_points == null && '（待确认）'}</span><Button disabled={!canConfirm} onClick={() => confirm(task)}>{confirmation?.phase === 'sending' ? '正在确认…' : '开始审核'}<ArrowRight/></Button></div>}</div>
          </footer>
        </>}

        </fieldset>
        </>}
        {task && !workbench && <AuditExcludedFiles files={step === 'match' ? files : files.filter(row => row.selection === 'removed')}/>}
        </div>
      </main>
      {displayEditor.dialog}
    </div>
    {driveOpen && adapter.drive && <FeishuPicker source={adapter.drive} onAdd={receiveDrive} onClose={() => setDriveOpen(false)} trigger={driveTrigger.current}/>}
    {localFiles.length > 0 && adapter.drive && <FeishuFolderPicker source={adapter.drive} files={localFiles} onChoose={receiveInFolder} onClose={() => setLocalFiles([])}/>}
    {supportSnapshot && <AuditSupport adapter={supportAdapter} snapshot={supportSnapshot} pageReference={supportPageReference} returnTargets={supportReturnTargets.current} onClose={() => setSupportSnapshot(null)}/>}
    <Dialog open={Boolean(ruleSource)} onOpenChange={open => {if (!open) setRuleSource(null)}}><DialogContent data-rule-version={ruleSource?.rule.id} onCloseAutoFocus={event => {event.preventDefault(); ruleTrigger.current?.focus()}}><DialogHeader><DialogTitle>{ruleSource?.clause ? `${ruleSource.clause.id} · ${ruleSource.clause.category}` : '文件存在性检查'}</DialogTitle><DialogDescription><span className="block">{ruleSource?.rule.title}</span><span className="block break-words">{ruleSource?.filename}</span></DialogDescription></DialogHeader><p className="max-h-80 overflow-y-auto whitespace-pre-wrap text-sm">{ruleSource?.clause?.rule_to_check ?? '文件存在性检查。'}</p></DialogContent></Dialog>
  </div></SupportEntryContext.Provider></TooltipProvider>
}
