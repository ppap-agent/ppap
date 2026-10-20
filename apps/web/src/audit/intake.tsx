// Stitch: projects/13261964020862199279/screens/5bc532c5a9a142a8a378434ddfe73fd5
import {useContext, useEffect, useRef, useState, type ReactNode} from 'react'
import {requestTrace} from '../api'
import {analysisNotice} from '../helpers'
import {MatchingFeedback, isMatchingReason} from './matching-feedback'
import {ArrowLeft, ArrowRight, BookOpen, Check, ClipboardCheck, Files, HelpCircle, Menu, PanelLeftClose, PanelLeftOpen, Plus, ReceiptText, Search, UploadCloud, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from '../components/ui/tooltip'
import {FileName} from '../journey/file-name'
import {AccountMenu, useAccess} from '../journey/access'
import {FeishuPicker, type DrivePickerSource} from './feishu-picker'
import {useTaskAcceptance, type AuditAcceptanceAdapter} from './task-acceptance'
import {useTaskRecognition, type AuditRecognitionAdapter} from './task-recognition'
import {AuditExcludedFiles, AuditWorkspace} from './workspace'
import type {OriginalRenderer} from './review-content'
import {sameFrozenInputs, sameSnapshot, progressRegressed, taskProgressError, reconcileTaskNotification} from './result-projection'
import type {AuditNotificationAdapter} from './completion-notification'
import {canObserveTask, useTaskObservation} from './task-observation'
import {useUploads, type UploadAdapter, type UploadRow} from './uploads'
import {TransferList} from './transfer-list'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {AuditArchive} from './archive'
import type {AuditArchiveAdapter} from './archive-contract'
import {AuditTemplates} from './templates'
import {AuditSupport, SupportEntryContext, type SupportSnapshot} from './support'
import type {AuditSupportAdapter, AuditTemplateAdapter} from './template-contract'
import {TemplateFeedback} from './template-feedback'
import type {AuditFeedbackAdapter} from './feedback-contract'
import type {AuditTemplateSharingAdapter} from './template-sharing-contract'
import type {AuditTemplatePermissionAdapter} from './template-permission-contract'
import {AuditUsage} from './usage'
import type {AuditUsageAdapter} from './usage-contract'
import {reconcileTaskDisplay, unconfirmedDisplayAccess, type DisplayInfo, type DisplayReference} from './display-info-contract'
import {useDisplayEditor, type AuditDisplayAdapter} from './display-info-editor'
import {ReviewEditorContext} from './review-editor'
import {reconcileTaskSummary, summarizeTask, taskIsBehindSummary, type TaskSummary} from './task-summary'

export type AuditTask = components['schemas']['AuditTask']
export type AuditTaskFile = components['schemas']['AuditTaskFile']
export type AuditIntakeAdapter = AuditAcceptanceAdapter & AuditRecognitionAdapter & UploadAdapter & {
  samples?: () => AuditTaskFile[]
  drive?: DrivePickerSource
  customers?: {value: string; label: string}[]
  deepOffered?: boolean
  notifications?: AuditNotificationAdapter
  autoValidate?: boolean
  readValidation: (file: AuditTaskFile) => Promise<{validation: AuditTaskFile['validation']; validation_id?: string}>
  endTask: (task: AuditTask) => Promise<AuditTask>
  listTasks: (query: {q: string; state: AuditTask['state'] | null; offset: number; signal?: AbortSignal}) => Promise<{tasks: TaskSummary[]; next_offset: number | null}>
  authenticate: () => Promise<void>
}
const taskLabels = {matching: '识别中', pending_confirm: '待确认', reviewing: '审核中', completed: '已完成'}
const productLinks = [
  {view: 'audit', label: 'PPAP Audit', icon: ClipboardCheck},
  {view: 'archive', label: '审核文件', icon: Files},
  {view: 'requirements', label: '审核规则', icon: ClipboardCheck},
  {view: 'templates', label: '模板库', icon: BookOpen},
  {view: 'usage', label: '用量记录', icon: ReceiptText},
] as const
type ProductSurface = typeof productLinks[number]['view']
const sizeLabel = (bytes: number) => bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
type IntakeDraft = {files: AuditTaskFile[]; uploads: UploadRow[]; mode: AuditTask['analysis_mode']; customer: string; step: 'upload' | 'check' | 'match'}
const sameSelections = (before: AuditTaskFile[], after: AuditTaskFile[]) => before.length === after.length && before.every(file => after.some(row => sameSnapshot(row.file, file.file) && row.selection === file.selection && row.removal_reason === file.removal_reason && row.validation_id === file.validation_id))
const onlyUnmatchedRemovals = (before: AuditTaskFile[], after: AuditTaskFile[]) => before.length === after.length && before.every(file => after.some(row => sameSnapshot(row.file, file.file) && row.validation_id === file.validation_id && (row.selection === file.selection && row.removal_reason === file.removal_reason || file.selection === 'selected' && file.match?.state === 'unmatched' && row.selection === 'removed' && Boolean(row.removal_reason?.trim()))))
function mergeFileIntent(local: AuditTaskFile[], current: AuditTaskFile[]): AuditTaskFile[] {
  const kept = local.map(file => {
    const next = current.find(row => row.file.id === file.file.id)
    return next ? {...next, selection: file.selection, removal_reason: file.removal_reason} : file
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
export function AuditIntake({adapter, rules, onRefreshCustomers, autoObserve = true, renderOriginal, reportAdapter, sharingAdapter, archiveAdapter, templateAdapter, supportAdapter, feedbackAdapter, templateSharingAdapter, templatePermissionAdapter, usageAdapter, displayAdapter}: {adapter: AuditIntakeAdapter; rules?: (active: boolean) => ReactNode; onRefreshCustomers?: () => void; autoObserve?: boolean; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; archiveAdapter?: AuditArchiveAdapter; templateAdapter?: AuditTemplateAdapter; supportAdapter?: AuditSupportAdapter; feedbackAdapter?: AuditFeedbackAdapter; templateSharingAdapter?: AuditTemplateSharingAdapter; templatePermissionAdapter?: AuditTemplatePermissionAdapter; usageAdapter?: AuditUsageAdapter; displayAdapter?: AuditDisplayAdapter}) {
  const access = useAccess()
  const reviewEditor = useContext(ReviewEditorContext), reviewRevision = useRef(reviewEditor?.revision)
  const readSurface = (): ProductSurface => {
    const view = new URL(location.href).searchParams.get('view')
    return productLinks.find(link => link.view === view && (link.view !== 'requirements' || rules))?.view ?? 'audit'
  }
  const [surface, setSurface] = useState<ProductSurface>(readSurface)
  const [archiveOpened, setArchiveOpened] = useState(surface === 'archive')
  const [rulesOpened, setRulesOpened] = useState(surface === 'requirements')
  const [templatesOpened, setTemplatesOpened] = useState(surface === 'templates')
  const [usageOpened, setUsageOpened] = useState(surface === 'usage')
  const [navigationMode, setNavigationMode] = useState<'auto' | 'expanded' | 'collapsed'>('auto')
  const [navigationTooltip, setNavigationTooltip] = useState<string | null>(null)
  const [desktopNavigation, setDesktopNavigation] = useState(() => window.matchMedia('(min-width: 64rem)').matches)
  const [wideNavigation, setWideNavigation] = useState(() => window.matchMedia('(min-width: 1281px)').matches)
  const [workspaceActions, setWorkspaceActions] = useState<HTMLDivElement | null>(null)
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
  function openSupport(snapshot: SupportSnapshot) {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    supportReturnTargets.current = [trigger, trigger?.closest<HTMLElement>('[role="dialog"]') || null]
    setSupportSnapshot(structuredClone(snapshot))
  }
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const feedbackTrigger = useRef<HTMLElement | null>(null)
  function openFeedback() {feedbackTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setFeedbackOpen(true)}
  const [supportPageReference] = useState(() => `client-${crypto.randomUUID()}`)
  const surfaceRef = useRef(surface)
  const [files, setFiles] = useState<AuditTaskFile[]>([])
  const [step, setStepState] = useState<'upload' | 'check' | 'match'>('upload')
  const stepRef = useRef(step)
  function setStep(next: typeof step) {stepRef.current = next; setStepState(next)}
  const [mode, setMode] = useState<AuditTask['analysis_mode']>('standard')
  const [customer, setCustomer] = useState('auto')
  const [task, setTask] = useState<AuditTask | null>(null)
  const [tasks, setTasks] = useState<AuditTask[]>([])
  const [summaries, setSummaries] = useState<TaskSummary[]>([])
  const latestSummaries = useRef(summaries); latestSummaries.current = summaries
  const latestTasks = useRef(tasks); latestTasks.current = tasks
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all')
  const [sheet, setSheet] = useState(false)
  const [reportView, setReportView] = useState(() => new URL(location.href).searchParams.has('report'))
  const [error, setError] = useState(''), [dragging, setDragging] = useState(false)
  const [checking, setChecking] = useState<string[]>([])
  const [driveOpen, setDriveOpen] = useState(false)
  const [readStatus, setReadStatus] = useState<Record<string, {busy: boolean; error: string; traceId?: string}>>({})
  const [endStatus, setEndStatus] = useState<Record<string, 'sending' | 'unknown' | 'rejected'>>({})
  const endingTasks = useRef(new Set<string>())
  const [taskList, setTaskList] = useState<{next: number | null; busy: boolean; error: string}>({next: null, busy: false, error: ''})
  const listEpoch = useRef(0)
  const latestAdapter = useRef(adapter); latestAdapter.current = adapter
  const [ruleSource, setRuleSource] = useState<components['schemas']['BoundRule'] | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const ruleTrigger = useRef<HTMLButtonElement | null>(null)
  const driveTrigger = useRef<HTMLButtonElement | null>(null)
  const drafts = useRef(new Map<string, IntakeDraft>())
  // History stores only an identity. File bytes and receipts remain in this page.
  const uploadDrafts = useRef(new Map<string, IntakeDraft | {taskId: string}>())
  const activeUploadDraftId = useRef<string | null>(null)
  const draftEpoch = useRef(0)
  const validationReads = useRef(new Set<string>())
  const activeTaskId = useRef<string | null>(null)
  const taskReadEpoch = useRef(new Map<string, number>())
  const taskReads = useRef(new Set<string>())
  const restoreEpoch = useRef(0)
  const recognitionTargets = useRef(new Map<string, {epoch: number; uploadId: string | null}>())
  const [recovery, setRecovery] = useState<{id: string; state: 'loading' | 'error' | 'unauthenticated' | 'forbidden' | 'not_found'; message: string; traceId?: string} | null>(() => {
    const id = new URL(location.href).searchParams.get('task')
    return id ? {id, state: 'loading', message: ''} : null
  })
  const uploads = useUploads(adapter, (file, removed) => setFiles(rows => rows.some(row => row.file.id === file.id || row.selection !== 'removed' && row.file.file_id === file.file_id) ? rows : [...rows, {file, selection: removed ? 'removed' : 'selected', removal_reason: removed ? '传输期间排除，不纳入识别' : null, validation: {state: 'pending', reasons: ['文件待检查。']}, match: null}]))
  const active = files.filter(row => row.selection !== 'removed')
  const valid = active.filter(row => row.validation.state === 'valid')
  const waiting = active.filter(row => row.validation.state === 'pending' || row.validation.state === 'checking')
  const exceptions = active.filter(row => row.validation.state === 'invalid' || row.validation.state === 'error')
  const selected = valid.filter(row => row.selection === 'selected')
  const transferBlocked = uploads.rows.some(row => !row.removed && row.receipt.state !== 'ready')
  const blocked = transferBlocked ? '请先处理或排除未完成的传输，再开始识别。' : waiting.length ? `还有 ${waiting.length} 份文件等待预检查，不能开始识别。` : exceptions.length ? '请先处理或移除异常文件。' : !selected.length ? '至少选择一份可审核文件。' : ''
  const matchingFiles = task ? files.filter(row => row.selection === 'selected') : []
  const matched = matchingFiles.filter(row => row.match?.state === 'matched' && row.match.rule)
  const references = matchingFiles.filter(row => row.match?.state === 'reference')
  const noMatches = Boolean(task?.state === 'pending_confirm' && !matched.length && files.every(row => row.selection !== 'selected' || row.match?.state === 'unmatched' || row.match?.state === 'reference'))
  const recognitionDirty = Boolean(task && !task.round_id && (task.analysis_mode !== mode || task.customer_preference !== (customer === 'auto' ? null : customer) || !onlyUnmatchedRemovals(task.files, files)))
  const workbench = task?.state === 'reviewing' || task?.state === 'completed' && task.completion_reason === 'processed'
  const navigationCollapsed = navigationMode === 'collapsed' || navigationMode === 'auto' && (surface === 'archive' || workbench && surface === 'audit') && !wideNavigation
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
  const inputLocked = recognitionLocked || Boolean(task && ['sending', 'unknown'].includes(endStatus[task.id]))
  function readRecognition() {
    recognitionTargets.current.set(recognitionKey, {epoch: draftEpoch.current, uploadId: activeUploadDraftId.current})
    void recognition.read(recognitionKey)
  }
  function readCurrentRecognition() {
    if (!recognitionState) return
    const id = recognitionState.taskId, uploadId = activeUploadDraftId.current
    const snapshot: IntakeDraft = {files: structuredClone(files), uploads: uploads.snapshot(), mode, customer, step: 'check'}
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
    const snapshot = {files: structuredClone(files), uploads: uploads.snapshot(), mode, customer, step}
    if (task) drafts.current.set(task.id, snapshot)
    else if (activeUploadDraftId.current) uploadDrafts.current.set(activeUploadDraftId.current, snapshot)
  }
  function clearReads() {draftEpoch.current += 1; validationReads.current.clear(); setChecking([]); uploads.reset()}
  function taskAddress(id: string | null, keepSurface = false) {
    if (!keepSurface) {surfaceRef.current = 'audit'; setSurface('audit'); setReportView(false)}
    const url = new URL(location.href)
    if (id) url.searchParams.set('task', id); else url.searchParams.delete('task')
    if (!keepSurface) {
      url.searchParams.delete('report'); url.searchParams.delete('view'); url.searchParams.delete('packageRequest')
      for (const key of ['archiveDoc', 'auditRef', 'archiveReport']) url.searchParams.delete(key)
    }
    const state = {...history.state, auditUploadDraft: id ? null : activeUploadDraftId.current}
    if (keepSurface) history.replaceState(state, '', url); else history.pushState(state, '', url)
    if (window.parent !== window && !keepSurface) window.parent.postMessage({type: 'ppap-audit-task', task: id, report: null}, location.origin)
  }
  function newTask() {restoreEpoch.current += 1; setRecovery(null); retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = crypto.randomUUID(); setFiles([]); setTask(null); setStep('upload'); setMode('standard'); setCustomer('auto'); setError(''); setSheet(false); taskAddress(null)}
  async function recover(id: string, login = false) {
    const epoch = ++restoreEpoch.current
    acceptance.suspend(id)
    taskReadEpoch.current.set(id, (taskReadEpoch.current.get(id) || 0) + 1); taskReads.current.delete(id); setReadStatus(rows => ({...rows, [id]: {busy: false, error: ''}}))
    retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = null; setTask(null); setFiles([]); setRecovery({id, state: 'loading', message: ''})
    try {
      if (login) await adapter.authenticate()
      let next = await adapter.readTask(id)
      if (restoreEpoch.current !== epoch) return
      const behind = () => taskIsBehindSummary(next, latestSummaries.current.find(row => row.id === id))
      if (behind()) next = await adapter.readTask(id)
      if (restoreEpoch.current !== epoch) return
      if (next.id !== id) throw new Error('task_identity_mismatch')
      if (behind()) throw new Error('task_snapshot_changed')
      if (!acceptance.restore(next)) throw new Error('acceptance_snapshot_mismatch')
      if (recognition.get(id)) recognitionTargets.current.set(id, {epoch: draftEpoch.current, uploadId: null})
      setEndStatus(rows => {if (rows[id] !== 'rejected') return rows; const nextRows = {...rows}; delete nextRows[id]; return nextRows})
      const current = saveTask(next, {activate: true})
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
    setFeedbackOpen(false)
    setSupportSnapshot(null)
    const params = new URL(location.href).searchParams, view = readSurface()
    if (view !== 'audit') {
      surfaceRef.current = view; setSurface(view)
      if (view === 'archive') setArchiveOpened(true); else if (view === 'requirements') setRulesOpened(true); else if (view === 'templates') setTemplatesOpened(true); else setUsageOpened(true)
      if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view, ...Object.fromEntries(['archiveDoc', 'auditRef', 'archiveReport', 'usageRange', 'usageSearch', 'usagePage', 'packageRequest'].map(key => [key, params.get(key)]))}, location.origin)
      return
    }
    const fromOtherSurface = surfaceRef.current !== 'audit'
    surfaceRef.current = 'audit'
    setSurface('audit')
    setReportView(new URL(location.href).searchParams.has('report'))
    const id = new URL(location.href).searchParams.get('task')
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: id, report: new URL(location.href).searchParams.get('report'), packageRequest: new URL(location.href).searchParams.get('packageRequest')}, location.origin)
    const localId = typeof history.state?.auditUploadDraft === 'string' ? history.state.auditUploadDraft : null
    if (id ? fromOtherSurface && id === activeTaskId.current : localId && localId === activeUploadDraftId.current) return
    if (id) void recover(id)
    else {
      const draftId = localId || crypto.randomUUID()
      history.replaceState({...history.state, auditUploadDraft: draftId}, '')
      const saved = uploadDrafts.current.get(draftId)
      if (saved && 'taskId' in saved) {
        const url = new URL(location.href); url.searchParams.set('task', saved.taskId)
        history.replaceState({...history.state, auditUploadDraft: null}, '', url)
        if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: saved.taskId, report: null}, location.origin)
        void recover(saved.taskId)
        return
      }
      retainDraft(); restoreEpoch.current += 1; clearReads(); setRecovery(null); activeTaskId.current = null; activeUploadDraftId.current = draftId
      if (recognition.get(draftId)) recognitionTargets.current.set(draftId, {epoch: draftEpoch.current, uploadId: draftId})
      if (saved) uploads.restore(saved.uploads)
      setTask(null); setFiles(structuredClone(saved?.files ?? [])); setMode(saved?.mode ?? 'standard'); setCustomer(saved?.customer ?? 'auto'); setStep(saved?.step ?? 'upload'); setError('')
    }
  }
  const addressCurrent = useRef(restoreAddress)
  addressCurrent.current = restoreAddress
  useEffect(() => {
    const changed = () => addressCurrent.current()
    changed(); window.addEventListener('popstate', changed)
    return () => {restoreEpoch.current += 1; window.removeEventListener('popstate', changed)}
  }, [])
  function receive(picked: File[]) {
    if (!picked.length || confirmationLocked || inputLocked || workbench) return
    uploads.add(picked.map(file => ({kind: 'local', file}))); setStep('check'); setError('')
  }
  function receiveDrive(picked: components['schemas']['DriveFile'][]) {
    if (confirmationLocked || inputLocked || workbench) return
    uploads.add(picked.map(file => ({kind: 'feishu', file}))); setStep('check'); setDriveOpen(false); setError('')
  }
  const driveButton = adapter.drive && <Button variant="outline" disabled={inputLocked} onClick={event => {driveTrigger.current = event.currentTarget; setDriveOpen(true)}}>从飞书选择</Button>
  function remove(id: string) {
    if (confirmationLocked || inputLocked || workbench) return
    uploads.rows.filter(row => row.receipt.file?.id === id).forEach(row => uploads.remove(row.id))
    const update = (rows: AuditTaskFile[]) => rows.map(row => row.file.id === id ? {...row, selection: 'removed' as const, removal_reason: row.validation.reasons[0] || (step === 'match' ? '未匹配到规则' : '识别前移除')} : row)
    setFiles(update)
  }
  function toggle(id: string, checked: boolean) {if (!confirmationLocked && !inputLocked && !workbench) setFiles(rows => rows.map(row => row.file.id === id ? {...row, selection: checked ? 'selected' : 'unselected'} : row))}
  async function readValidation(row: AuditTaskFile) {
    if (inputLocked || validationReads.current.has(row.file.id)) return
    const epoch = draftEpoch.current
    validationReads.current.add(row.file.id); setChecking(ids => [...ids, row.file.id])
    try {
      const result = await adapter.readValidation(row)
      if (draftEpoch.current !== epoch) return
      setFiles(rows => rows.map(file => file.file.id === row.file.id ? {...file, ...result} : file))
    } catch {
      if (draftEpoch.current !== epoch) return
      setFiles(rows => rows.map(file => file.file.id === row.file.id && file.selection !== 'removed' ? {...file, validation: {state: 'error', reasons: ['检查结果读取失败。已有文件仍保留；重新读取不会重启审核。']}} : file))
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
  function startMatching() {
    if (blocked || confirmationLocked || inputLocked || recognitionNeedsCurrent || workbench) return
    const key = task?.id || activeUploadDraftId.current || crypto.randomUUID()
    if (!task && !activeUploadDraftId.current) activeUploadDraftId.current = key
    recognitionTargets.current.set(key, {epoch: draftEpoch.current, uploadId: activeUploadDraftId.current})
    if (task) {taskReadEpoch.current.set(task.id, (taskReadEpoch.current.get(task.id) || 0) + 1); taskReads.current.delete(task.id)}
    void recognition.start(key, {files, mode, customer: customer === 'auto' ? null : customer, previous: task})
  }
  async function endTask(current: AuditTask) {
    if (recognitionDirty || recognitionState?.phase === 'conflict') {setStep('check'); setError('文件选择或输入版本已有变更。请读取当前任务并重新识别，再结束任务。'); return}
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
  useTaskObservation(task?.id, autoObserve && surface === 'audit' && !recovery && step === 'match' && !reportView && canObserveTask(task), () => task && activeTaskId.current === task.id && surfaceRef.current === 'audit' && stepRef.current === 'match' && taskReadEpoch.current.get(task.id) === observedTaskEpoch ? readMatching(task) : Promise.resolve(null))
  async function loadTasks(offset = 0, signal?: AbortSignal) {
    const epoch = ++listEpoch.current
    setTaskList(current => ({...current, busy: true, error: ''}))
    try {
      const page = await latestAdapter.current.listTasks({q: query, state: filter === 'all' ? null : filter as AuditTask['state'], offset, signal})
      if (listEpoch.current !== epoch || signal?.aborted) return
      setSummaries(rows => [...rows.map(row => {
        const next = page.tasks.find(candidate => candidate.id === row.id)
        return next ? reconcileTaskSummary(row, next) : row
      }), ...page.tasks.filter(next => !rows.some(row => row.id === next.id))])
      setTaskList({next: page.next_offset, busy: false, error: ''})
    } catch {if (listEpoch.current === epoch && !signal?.aborted) setTaskList(current => ({...current, busy: false, error: '任务列表读取失败。当前文件与任务仍保留。'}))}
  }
  const loadTasksCurrent = useRef(loadTasks); loadTasksCurrent.current = loadTasks
  useEffect(() => {
    if (surface !== 'audit') return
    let controller: AbortController | undefined
    const read = () => {controller?.abort(); if (document.hidden) return; controller = new AbortController(); void loadTasksCurrent.current(0, controller.signal)}
    const timer = window.setTimeout(read, query ? 200 : 0)
    document.addEventListener('visibilitychange', read)
    return () => {window.clearTimeout(timer); document.removeEventListener('visibilitychange', read); controller?.abort(); listEpoch.current += 1}
  }, [query, filter, surface])
  const shownTasks = summaries.filter(row => (filter === 'all' || row.state === filter) && `${row.number} ${row.files.map(file => file.file.original_filename).join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  function showSurface(value: ProductSurface) {
    setSheet(false)
    if (value === 'archive') setArchiveOpened(true)
    if (value === 'requirements') setRulesOpened(true)
    if (value === 'templates') setTemplatesOpened(true)
    if (value === 'usage') setUsageOpened(true)
    const url = new URL(location.href)
    for (const key of ['archiveDoc', 'auditRef', 'archiveReport', 'requirement', 'reqVersion', 'reqTab', 'reqQuery', 'packageRequest']) url.searchParams.delete(key)
    if (value !== 'audit') url.searchParams.set('view', value); else url.searchParams.delete('view')
    if (value === 'audit' && activeTaskId.current) url.searchParams.set('task', activeTaskId.current)
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
  const navigation = (compact = false) => <nav aria-label="产品导航" className="grid gap-1">
    {productLinks.filter(link => link.view !== 'requirements' || rules).map(({view, label, icon: Icon}) => <Tooltip key={view} {...navigationTooltipProps(view, compact)}><TooltipTrigger asChild><Button className={compact ? 'justify-center' : 'justify-start'} aria-label={label} aria-current={surface === view ? 'page' : undefined} variant={surface === view ? 'secondary' : 'ghost'} onClick={() => showSurface(view)}><Icon aria-hidden="true"/><span className={compact ? 'sr-only' : ''}>{label}</span></Button></TooltipTrigger><TooltipContent side="right">{label}</TooltipContent></Tooltip>)}
  </nav>
  const taskRail = <div className="flex h-full min-w-0 flex-col gap-4">
    <Button variant="outline" onClick={newTask}><Plus/>新建审核任务</Button>
    <label className="flex items-center gap-2"><span className="sr-only">搜索任务文件</span><Search className="size-4 shrink-0 text-muted-foreground"/><Input placeholder="搜索文件名" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <Select value={filter} onValueChange={setFilter}><SelectTrigger className="w-full" aria-label="任务状态"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem>{Object.entries(taskLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
    <nav className="grid gap-2" aria-label="任务列表">{shownTasks.length ? shownTasks.map(row => <Button key={row.id} variant={task?.id === row.id ? 'secondary' : 'ghost'} className="h-auto min-w-0 justify-start whitespace-normal text-left" onClick={() => {setSheet(false); taskAddress(row.id); void recover(row.id)}}><span className="grid min-w-0 gap-1"><span>{row.number}</span><span className="text-xs">{taskLabels[row.state]} · {row.files.filter(file => file.selection === 'selected').length} 份文件</span></span></Button>) : <p className="text-sm text-muted-foreground">{summaries.length ? '没有符合条件的任务。' : '开始识别后，任务显示在这里。'}</p>}</nav>
    {taskList.error && <p role="alert" className="text-xs">{taskList.error}</p>}
    {(taskList.next !== null || taskList.error) && <Button variant="outline" disabled={taskList.busy} onClick={() => void loadTasks(taskList.error ? 0 : taskList.next!)}>{taskList.busy ? '正在读取任务…' : taskList.error ? '重新读取任务列表' : '加载更多任务'}</Button>}
  </div>
  const table = (rows: AuditTaskFile[], selectable: boolean) => <Table className="table-fixed">
    <TableHeader><TableRow>{selectable && <TableHead className="w-12"><Checkbox aria-label="全选可审核文件" checked={selected.length === valid.length && valid.length > 0 ? true : selected.length ? 'indeterminate' : false} onCheckedChange={value => setFiles(items => items.map(row => row.validation.state === 'valid' && row.selection !== 'removed' ? {...row, selection: value === true ? 'selected' : 'unselected'} : row))}/></TableHead>}<TableHead>文件</TableHead><TableHead className="w-24 max-md:hidden">大小</TableHead><TableHead className="w-28 max-md:hidden">预检查</TableHead><TableHead className={selectable ? 'w-16' : 'w-36'}><span className="sr-only">操作</span></TableHead></TableRow></TableHeader>
    <TableBody>{rows.map(row => <TableRow key={row.file.id}>{selectable && <TableCell><Checkbox aria-label={`选择 ${row.file.original_filename}`} checked={row.selection === 'selected'} onCheckedChange={value => toggle(row.file.id, value === true)}/></TableCell>}<TableCell className="whitespace-normal"><FileName name={row.file.original_filename}/>{!selectable && <p className="mt-2 text-sm text-muted-foreground">{row.validation.reasons.join('；') || '暂未完成检查'}</p>}<span className="mt-1 block text-xs text-muted-foreground md:hidden">{sizeLabel(row.file.byte_size)} · {row.validation.state === 'valid' ? '可审核' : '待处理'}</span></TableCell><TableCell className="max-md:hidden"><span className="text-muted-foreground">{sizeLabel(row.file.byte_size)}</span></TableCell><TableCell className="max-md:hidden"><Badge variant="outline">{{valid: '可审核', pending: '待预检查', checking: '检查中', invalid: '文件异常', error: '读取失败'}[row.validation.state]}</Badge></TableCell><TableCell><div className="flex flex-wrap items-center justify-end gap-1">{!selectable && row.validation.state !== 'invalid' && <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} aria-label={`${row.validation.state === 'error' ? '重新读取' : '读取'} ${row.file.original_filename} 检查结果`} onClick={() => void readValidation(row)}>{checking.includes(row.file.id) ? '读取中…' : row.validation.state === 'error' ? '重新读取' : '读取结果'}</Button>}<Button variant="ghost" size="icon" aria-label={`移除 ${row.file.original_filename}`} onClick={() => remove(row.file.id)}><X/></Button></div></TableCell></TableRow>)}</TableBody>
  </Table>
  const supportScenario = surface === 'templates' ? '客户模板库' : surface === 'archive' ? '文件档案' : surface === 'requirements' ? '审核规则' : surface === 'usage' ? '用量记录' : workbench ? '审核结果' : '上传与文件核对'
  function contactReadSupport(current: AuditTask) {openSupport({scenario: '读取审核任务', traceId: readStatus[current.id]?.traceId, facts: [{label: '任务标识', value: current.id}, ...(current.round_id ? [{label: '审核轮次', value: current.round_id}] : []), {label: '服务说明', value: readStatus[current.id]?.error || ''}]})}
  const recognitionSupport = recognitionState && <Button variant="outline" size="sm" onClick={() => openSupport({scenario: '识别文件与匹配规则', traceId: recognitionState.traceId, facts: [{label: '任务标识', value: recognitionState.taskId}, {label: '识别请求', value: recognitionState.requestId}, {label: '文件', value: files.map(row => row.file.original_filename).join('\n')}, {label: '服务说明', value: recognitionState.message}]})}>联系支持</Button>
  return <TooltipProvider><SupportEntryContext.Provider value={openSupport}><div className="flex min-h-dvh bg-background text-sm text-foreground">
    <a href="#audit-main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:p-3">跳到主要内容</a>
    <aside aria-label="产品侧栏" className={`sticky top-0 flex h-dvh shrink-0 flex-col gap-8 border-r max-lg:hidden ${navigationCollapsed ? 'w-16 p-2' : 'w-44 p-4'}`}>
      <div className={`flex items-center gap-2 text-lg font-semibold ${navigationCollapsed ? 'justify-center py-2' : ''}`}><ClipboardCheck aria-hidden="true"/><span className={navigationCollapsed ? 'sr-only' : ''}>PPAP</span></div>
      {navigation(navigationCollapsed)}
      <div className="mt-auto grid gap-1">
        <Tooltip {...navigationTooltipProps('support')}><TooltipTrigger asChild><Button className={navigationCollapsed ? 'justify-center' : 'justify-start'} variant="ghost" aria-label="联系我们" onClick={() => openSupport({scenario: supportScenario})}><HelpCircle aria-hidden="true"/><span className={navigationCollapsed ? 'sr-only' : ''}>联系我们</span></Button></TooltipTrigger><TooltipContent side="right">联系我们</TooltipContent></Tooltip>
        <Tooltip {...navigationTooltipProps('toggle')}><TooltipTrigger asChild><Button className={navigationCollapsed ? 'justify-center' : 'justify-start'} variant="ghost" aria-label={navigationCollapsed ? '展开产品导航' : '折叠产品导航'} onClick={() => {setNavigationTooltip(null); setNavigationMode(navigationCollapsed ? 'expanded' : 'collapsed')}}>{navigationCollapsed ? <PanelLeftOpen aria-hidden="true"/> : <PanelLeftClose aria-hidden="true"/>}<span className={navigationCollapsed ? 'sr-only' : ''}>{navigationCollapsed ? '展开导航' : '折叠导航'}</span></Button></TooltipTrigger><TooltipContent side="right">展开产品导航</TooltipContent></Tooltip>
      </div>
    </aside>
    {surface === 'audit' && !reportView && !workbench && <aside aria-label="审核任务" className="sticky top-0 h-dvh w-56 shrink-0 border-r p-4 max-xl:hidden">{taskRail}</aside>}
    <div className="min-w-0 flex-1">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-2 max-sm:px-4"><div className="flex items-center gap-3"><Sheet open={sheet} onOpenChange={setSheet}><SheetTrigger asChild><Button variant="outline" size="icon" className={surface !== 'audit' ? 'lg:hidden' : reportView || workbench ? '' : 'xl:hidden'} aria-label={surface !== 'audit' ? '打开产品导航' : '打开审核任务'}><Menu/></Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>{surface !== 'audit' ? '产品导航' : '审核任务'}</SheetTitle><SheetDescription className="sr-only">选择页面或审核任务</SheetDescription></SheetHeader><div className="grid flex-1 gap-6 overflow-y-auto p-4"><div className="lg:hidden">{navigation()}<Button variant="ghost" className="w-full justify-start" onClick={() => {setSheet(false); openSupport({scenario: supportScenario})}}><HelpCircle/>联系我们</Button></div>{surface === 'audit' && taskRail}</div></SheetContent></Sheet><span className="text-muted-foreground">PPAP Audit{surface !== 'audit' ? ` / ${productLinks.find(link => link.view === surface)?.label}` : task ? ` / ${task.number}` : ' / 新建审核'}</span></div><div className="flex shrink-0 items-center gap-2"><div hidden={surface !== 'audit' || !workbench || reportView} ref={setWorkspaceActions}/>{access?.principal && <AccountMenu compact onUsage={() => showSurface('usage')} showLocalRecords={Boolean(rules)}/>}</div></header>
      <main id="audit-main" tabIndex={-1} className={surface === 'audit' && workbench && !reportView ? 'w-full' : `mx-auto w-full space-y-6 p-6 max-sm:p-4 ${workbench || surface !== 'audit' ? '' : 'max-w-5xl'}`}>
        {archiveOpened && <div hidden={surface !== 'archive'}>{archiveAdapter ? <AuditArchive active={surface === 'archive'} adapter={archiveAdapter} onUpload={newTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}}/> : <Alert><AlertTitle>审核文件暂不可用</AlertTitle><AlertDescription>请稍后重试。</AlertDescription></Alert>}</div>}
        {rulesOpened && <div hidden={surface !== 'requirements'}>{rules?.(surface === 'requirements')}</div>}
        {templatesOpened && <div hidden={surface !== 'templates'}>{templateAdapter ? <AuditTemplates active={surface === 'templates'} adapter={templateAdapter} sharingAdapter={templateSharingAdapter} permissionAdapter={templatePermissionAdapter} onReturn={() => showSurface('audit')} onFeedback={openFeedback}/> : <Alert><AlertTitle>模板库暂不可用</AlertTitle><AlertDescription><Button variant="outline" onClick={() => showSurface('audit')}>返回审核</Button></AlertDescription></Alert>}</div>}
        {usageOpened && <div hidden={surface !== 'usage'}><AuditUsage active={surface === 'usage'} adapter={usageAdapter} archiveAdapter={archiveAdapter} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}} onSupport={notice => openSupport({scenario: `用量记录 · ${notice}`})}/></div>}
        <div hidden={surface !== 'audit'} className={workbench && !reportView ? '' : 'space-y-6'}>
        {!reportView && <ol aria-label="审核步骤" className={workbench ? 'flex flex-wrap gap-3 px-6 pt-3 text-xs text-muted-foreground max-sm:px-4' : 'flex flex-wrap gap-4 border-b pb-4'}>{['上传与检查', '确认规则', workbench ? '审核结果' : 'AI审核'].map((label, index) => <li key={label} aria-current={(workbench ? 2 : step === 'match' ? 1 : 0) === index ? 'step' : undefined} className="flex items-center gap-1.5">{workbench && index < 2 ? <Check className="size-3 text-success"/> : null}<span>{index + 1}. {label}</span>{index < 2 && <span aria-hidden="true" className="ml-1 text-border">/</span>}</li>)}</ol>}
        {error && <Alert variant="destructive"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
        {recognitionState && recognitionLocked && <Alert data-request-id={recognitionState.requestId}><AlertTitle>{recognitionState.phase === 'sending' ? '正在提交识别请求' : '识别请求待核对'}</AlertTitle><AlertDescription><p>{recognitionState.message || '正在等待同一请求的回执，文件与选择已保留。'}</p>{['unknown', 'reading'].includes(recognitionState.phase) && <><Button variant="outline" disabled={recognitionState.phase === 'reading'} onClick={readRecognition}>{recognitionState.phase === 'reading' ? '正在读取识别回执…' : '读取识别回执'}</Button>{recognitionSupport}</>}</AlertDescription></Alert>}
        {recognitionState?.phase === 'rejected' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>本次识别未受理</AlertTitle><AlertDescription><p>{recognitionState.message}</p>{task?.id === recognitionState.taskId && <Button variant="outline" onClick={readCurrentRecognition}>读取当前任务</Button>}{recognitionSupport}</AlertDescription></Alert>}
        {recognitionState?.phase === 'conflict' && <Alert data-request-id={recognitionState.requestId}><AlertTitle>任务输入版本已更新</AlertTitle><AlertDescription><p>{recognitionState.message}</p><Button variant="outline" onClick={readCurrentRecognition}>读取当前任务</Button></AlertDescription></Alert>}
        {task && task.state !== 'completed' && endStatus[task.id] === 'unknown' && <Alert><AlertTitle>结束结果待核对</AlertTitle><AlertDescription><p>尚未确认任务已结束。请读取同一任务，不要重复结束。</p>{readStatus[task.id]?.error && <p>{readStatus[task.id].error}</p>}<Button variant="outline" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>读取任务状态</Button></AlertDescription></Alert>}
        {recovery ? <section className="space-y-4 py-8"><h1 className="text-2xl font-semibold">{recovery.state === 'loading' ? '正在恢复任务' : '任务暂不可用'}</h1><p className="break-all text-xs text-muted-foreground">任务：{recovery.id}</p><p role="status">{recovery.state === 'loading' ? '正在读取任务…' : recovery.message}</p><div className="flex flex-wrap gap-2">{recovery.state !== 'loading' && <><Button variant="outline" onClick={() => void recover(recovery.id, recovery.state === 'unauthenticated')}>{recovery.state === 'unauthenticated' ? '恢复登录' : '重新读取任务'}</Button><Button variant="outline" onClick={() => openSupport({scenario: '恢复审核任务', traceId: recovery.traceId, facts: [{label: '任务标识', value: recovery.id}, {label: '服务说明', value: recovery.message}]})}>联系支持</Button></>}<Button variant="ghost" onClick={newTask}>返回新建</Button></div></section> : workbench && task ? <AuditWorkspace headerActions={workspaceActions} key={task.id} task={task} busy={readStatus[task.id]?.busy || false} readError={readStatus[task.id]?.error || ''} onReadSupport={() => contactReadSupport(task)} onRead={() => void readMatching(task)} onChange={saveTask} renderOriginal={renderOriginal} reportAdapter={reportAdapter} notificationAdapter={adapter.notifications} onReportChange={changeReport} onReportView={setReportView} sharingAdapter={sharingAdapter} onEditDisplay={displayAdapter ? file => displayEditor.open({task_id: task.id, round_id: task.round_id!, file_version_id: file.file.id}, file) : undefined}/> : task?.state === 'completed' ? <section className="space-y-4 py-8"><Check className="size-8"/><h1 className="text-2xl font-semibold">任务已结束</h1><p>未匹配到可用规则，未开始审核</p><p className="text-muted-foreground">未产生审核费用，也没有生成报告。</p><Button onClick={newTask}>新建审核任务<ArrowRight/></Button></section> : <>
        <fieldset disabled={inputLocked} className="min-w-0 space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4"><div className="space-y-2"><h1 className="text-2xl font-semibold tracking-tight">{step === 'upload' ? '上传 PPAP 文件' : step === 'check' ? '核对本次文件' : task?.state === 'matching' ? '正在识别文件与匹配规则' : '确认每份文件的审核规则'}</h1><p className="text-muted-foreground">{step === 'match' ? '分别核对每份文件匹配的规则。未匹配文件不会进入审核。' : '选择收到的文件，核对后开始审核。'}</p></div>{rules && <Button variant="outline" onClick={() => showSurface('requirements')}><UploadCloud/>上传规则</Button>}</header>
        {step !== 'match' && <div className="flex flex-wrap items-end justify-between gap-4"><div className="flex flex-wrap items-end gap-2"><div className="grid gap-2"><Label htmlFor="audit-customer">客户（可选）</Label><Select value={customer} onValueChange={setCustomer} onOpenChange={open => {if (open) onRefreshCustomers?.()}}><SelectTrigger id="audit-customer" className="w-56"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="auto">自动识别客户</SelectItem>{(adapter.customers ?? []).map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select></div><Button variant="ghost" onClick={() => showSurface('templates')}>查看客户模板</Button><Button variant="ghost" onClick={openFeedback}>补充模板</Button></div>{step === 'check' && <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => input.current?.click()}><Plus/>继续添加文件</Button>{driveButton}</div>}</div>}
        <input ref={input} type="file" className="sr-only" aria-label="选择审核文件" multiple accept=".xlsx,.xls,.xlsm,.doc,.docx,.pdf" onChange={event => {receive(Array.from(event.target.files || [])); event.target.value = ''}}/>
        {step === 'upload' && <section className="space-y-5"><div className="flex flex-col items-center gap-4 rounded-lg border border-dashed px-4 py-12 text-center" data-dragging={dragging} onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); receive(Array.from(event.dataTransfer.files))}}><UploadCloud className="size-8 text-muted-foreground"/><h2 className="text-base font-medium">拖入本地文件</h2><p className="text-muted-foreground">Excel、Word、PDF · 单份最大 200 MB</p><div className="flex flex-wrap justify-center gap-2"><Button onClick={() => input.current?.click()}>选择本地文件</Button>{driveButton}</div></div>{adapter.samples && <div className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" onClick={() => {setFiles(rows => [...rows, ...adapter.samples!()]); setStep('check')}}>使用三类文件示例<ArrowRight/></Button></div>}</section>}
        {step === 'upload' && active.length > 0 && <section className="space-y-4 border-t pt-5"><h2 className="font-semibold">已选择文件 · {active.length} 份</h2><ItemGroup>{active.map(row => <Item role="listitem" key={row.file.id}><ItemContent><FileName name={row.file.original_filename}/></ItemContent><Button variant="ghost" size="icon" aria-label={`移除 ${row.file.original_filename}`} onClick={() => remove(row.file.id)}><X/></Button></Item>)}</ItemGroup><Button onClick={() => setStep('check')}>继续核对<ArrowRight/></Button></section>}
        {step === 'check' && <>
          <TransferList rows={uploads.rows} onRead={uploads.read} onRetry={uploads.retry} onResume={uploads.resume} onRemove={uploads.remove}/>
          <section className="space-y-3"><h2 className="font-semibold">可审核文件 · {valid.length} 份</h2>{valid.length ? table(valid, true) : <p className="py-4 text-muted-foreground">尚无完成预检查的可审核文件。</p>}</section>
          {waiting.length > 0 && <section className="space-y-3 border-t pt-5"><h2 className="font-semibold">等待预检查 · {waiting.length} 份</h2>{table(waiting, false)}</section>}
          {exceptions.length > 0 && <section className="space-y-3 border-t pt-5"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">异常文件 · {exceptions.length} 份</h2><Button variant="outline" size="sm" onClick={() => exceptions.forEach(row => remove(row.file.id))}>一键移除异常文件</Button></div>{table(exceptions, false)}</section>}
          <footer className="space-y-4 border-t pt-5"><p role="status" className="text-muted-foreground">{blocked || `已选择 ${selected.length} 份文件，开始识别后逐份匹配规则。`}</p><div className="flex flex-wrap items-center justify-between gap-4">{adapter.deepOffered !== false && <Label><Checkbox checked={mode === 'deep'} onCheckedChange={value => setMode(value === true ? 'deep' : 'standard')}/>深度模式</Label>}<div className="ml-auto flex gap-2"><Button variant="outline" onClick={() => setStep('upload')}><ArrowLeft/>上一步</Button><Button disabled={Boolean(blocked) || recognitionLocked || recognitionNeedsCurrent} onClick={startMatching}>{recognitionState?.phase === 'sending' ? '正在提交识别…' : recognitionState?.phase === 'rejected' || recognitionState?.phase === 'conflict' ? '重新开始识别' : '开始识别'}<ArrowRight/></Button></div></div></footer>
        </>}
        {step === 'match' && task && <>
          <div className="flex flex-wrap gap-2" role="status"><Badge variant="secondary">{taskLabels[task.state]}</Badge><span className="text-muted-foreground">{matchingFiles.length} 份文件 · {matched.length} 份匹配到规则{references.length > 0 ? ` · ${references.length} 份比对资料` : ''}{task.state === 'matching' ? ' · 等待其余识别结果' : ''}</span></div>
          {task.state === 'matching' && <div className="space-y-3"><p className="text-muted-foreground">识别中，可稍后返回查看。</p>{readStatus[task.id]?.error && <Alert variant="destructive"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription><p>{readStatus[task.id].error}</p><Button variant="outline" size="sm" onClick={() => contactReadSupport(task)}>联系支持</Button></AlertDescription></Alert>}<Button variant="outline" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>{readStatus[task.id]?.busy ? '正在读取识别结果…' : readStatus[task.id]?.error ? '重新读取识别结果' : '读取识别结果'}</Button></div>}
          <div className="divide-y">{matchingFiles.map(row => <section key={row.file.id} className="space-y-4 py-5 first:pt-0">
            <div className="flex flex-wrap items-start justify-between gap-3"><h2 className="min-w-0 flex-1 font-semibold"><FileName name={row.file.original_filename}/></h2><Badge variant="outline">{row.match?.state === 'matched' && row.match.rule ? '已匹配' : row.match?.state === 'reference' ? '比对资料' : row.match?.state === 'unmatched' ? '未匹配' : row.match?.state === 'matching' ? '识别中' : !row.match || row.match.state === 'pending' ? '等待识别' : '识别异常'}</Badge></div>
            <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3 xl:grid-cols-5">{[['文件类型', row.match?.document_type], ['客户', row.match?.metadata.customer], ['车型', row.match?.metadata.model], ['零件号', row.match?.metadata.part_number], ['零件名称', row.match?.metadata.part]].map(([label, value]) => <div className="min-w-0" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 break-words">{value || '—'}</dd></div>)}</dl>
            {row.match?.state === 'matched' && row.match.rule ? <div className="space-y-2" data-rule-version={row.match.rule.id}><p className="font-medium">{row.match.rule.title}</p></div>
              : row.match?.state === 'reference' ? <p className="text-muted-foreground">特殊特性清单作为比对资料保留，随本轮文件一起固定版本。不单独审核、不计费，也不生成单独报告。</p>
              : row.match?.state === 'matching' || !row.match || row.match.state === 'pending' ? <p className="text-muted-foreground">等待识别结果…</p>
              : row.match.state === 'unmatched' || row.match.gaps.includes('ambiguous_available_rules') ? <MatchingFeedback gaps={row.match.gaps} candidates={row.match.candidates} onCheck={() => setStep('check')} onRules={rules ? () => showSurface('requirements') : undefined} onTemplates={() => showSurface('templates')} onFeedback={openFeedback} onRemove={() => remove(row.file.id)}/>
              : <Alert><AlertTitle>暂时无法确认匹配结果</AlertTitle><AlertDescription><p>请返回检查文件后重新识别。</p></AlertDescription></Alert>}
            {row.match?.gaps.filter(gap => !isMatchingReason(gap)).map(gap => analysisNotice(gap)).filter((notice): notice is string => Boolean(notice)).map(notice => <p key={notice} className="text-sm text-muted-foreground">{notice}</p>)}
          </section>)}</div>
          <footer className="space-y-4 border-t pt-5"><p className="text-muted-foreground">{noMatches ? '没有可进入审核的匹配文件。可以结束任务，不扣费、不生成报告。' : `预计点数：${task.estimated_points ?? '待确认'}`}</p>
            {recognitionDirty && <p role="status" className="text-muted-foreground">文件选择已有变更。请返回上一步，重新识别后再确认或结束任务。</p>}
            {confirmation?.message && <Alert data-request-id={confirmation.requestId} variant={confirmation.phase === 'rejected' ? 'default' : 'destructive'}><AlertTitle>{confirmation.phase === 'rejected' ? '本次未受理' : '受理结果待核对'}</AlertTitle><AlertDescription><p>{confirmation.message}</p><Button variant="outline" size="sm" onClick={() => openSupport({scenario: '确认审核', traceId: confirmation.traceId, facts: [{label: '任务标识', value: task.id}, {label: '确认请求', value: confirmation.requestId}, {label: '受理状态', value: confirmation.phase === 'rejected' ? '明确未受理' : '尚未确认'}, {label: '服务说明', value: confirmation.message}]})}>联系支持</Button></AlertDescription></Alert>}
            {endStatus[task.id] === 'rejected' && <Alert><AlertTitle>结束请求已拒绝</AlertTitle><AlertDescription>输入版本可能已更新。当前文件与选择已保留，请读取当前任务后再操作。<Button variant="outline" onClick={() => void recover(task.id)}>读取当前任务</Button></AlertDescription></Alert>}
            <div className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" disabled={confirmationLocked || endStatus[task.id] === 'sending' || endStatus[task.id] === 'unknown'} onClick={() => setStep('check')}><ArrowLeft/>上一步</Button>{noMatches ? <Button disabled={Boolean(endStatus[task.id])} onClick={() => void endTask(task)}>{endStatus[task.id] === 'sending' ? '正在结束任务…' : '结束任务'}</Button> : <div className="flex flex-wrap gap-2">{confirmation && ['unknown', 'reading'].includes(confirmation.phase) && <Button variant="outline" disabled={confirmation.phase === 'reading'} onClick={() => void acceptance.reconcile(task)}>{confirmation.phase === 'reading' ? '正在读取受理结果…' : '读取受理结果'}</Button>}<Button disabled={!canConfirm} onClick={() => confirm(task)}>{confirmation?.phase === 'sending' ? '正在确认…' : '开始审核'}<ArrowRight/></Button></div>}</div>
          </footer>
        </>}
        {step === 'match' && matched.length > 0 && <section className="space-y-3 border-t pt-5"><h2 className="font-semibold">匹配条款</h2><div className="flex flex-wrap gap-2">{matched.map(row => <Button key={row.file.id} variant="outline" className="max-w-full whitespace-normal text-left" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource(row.match!.rule)}}>查看 {row.match!.rule!.title} 条款</Button>)}</div></section>}
        </fieldset>
        </>}
        {task && !workbench && <AuditExcludedFiles files={step === 'match' ? files : files.filter(row => row.selection === 'removed')}/>}
        </div>
      </main>
      {displayEditor.dialog}
    </div>
    {driveOpen && adapter.drive && <FeishuPicker source={adapter.drive} onAdd={receiveDrive} onClose={() => setDriveOpen(false)} trigger={driveTrigger.current}/>}
    <TemplateFeedback open={feedbackOpen} onClose={() => setFeedbackOpen(false)} adapter={feedbackAdapter} trigger={feedbackTrigger.current}/>
    {supportSnapshot && <AuditSupport adapter={supportAdapter} snapshot={supportSnapshot} pageReference={supportPageReference} returnTargets={supportReturnTargets.current} onClose={() => setSupportSnapshot(null)}/>}
    <Dialog open={Boolean(ruleSource)} onOpenChange={open => {if (!open) setRuleSource(null)}}><DialogContent onCloseAutoFocus={event => {event.preventDefault(); ruleTrigger.current?.focus()}}><DialogHeader><DialogTitle>{ruleSource?.title}</DialogTitle><DialogDescription className="sr-only">本次匹配的审核条款</DialogDescription></DialogHeader><div className="max-h-80 space-y-4 overflow-y-auto">{ruleSource?.body.kind === 'engine' ? ruleSource.body.rules.map(rule => <article key={rule.id} className="space-y-2 border-t pt-3"><h3 className="text-sm font-semibold">{rule.id} · {rule.category}</h3><p className="whitespace-pre-wrap text-sm">{rule.rule_to_check}</p></article>) : <p className="text-sm">文件存在性检查。</p>}</div></DialogContent></Dialog>
  </div></SupportEntryContext.Provider></TooltipProvider>
}
