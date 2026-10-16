// Stitch: projects/13261964020862199279/screens/5bc532c5a9a142a8a378434ddfe73fd5
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, Check, ClipboardCheck, Files, HelpCircle, Menu, Plus, Search, UploadCloud, X} from 'lucide-react'
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
import {FileName} from '../journey/file-name'
import {FeishuPicker, type DrivePickerSource} from './feishu-picker'
import {useTaskAcceptance, type AuditAcceptanceAdapter} from './task-acceptance'
import {AuditWorkspace} from './workspace'
import type {OriginalRenderer} from './review-content'
import {sameFrozenInputs, progressRegressed, taskProgressError} from './result-projection'
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

export type AuditTask = components['schemas']['AuditTask']
export type AuditTaskFile = components['schemas']['AuditTaskFile']
export type AuditIntakeAdapter = AuditAcceptanceAdapter & UploadAdapter & {
  samples: () => AuditTaskFile[]
  drive: DrivePickerSource
  readValidation: (file: AuditTaskFile) => Promise<AuditTaskFile['validation']>
  match: (files: AuditTaskFile[], mode: AuditTask['analysis_mode'], customer: string | null, previous: AuditTask | null) => AuditTask
  authenticate: () => Promise<void>
}
const taskLabels = {matching: '识别中', pending_confirm: '待确认', reviewing: '审核中', completed: '已完成'}
const sizeLabel = (bytes: number) => bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
type IntakeDraft = {files: AuditTaskFile[]; uploads: UploadRow[]; mode: AuditTask['analysis_mode']; customer: string; step: 'upload' | 'check' | 'match'}

/** Controlled data comes through the adapter; this view never calls business APIs. */
export function AuditIntake({adapter, autoObserve = true, renderOriginal, reportAdapter, sharingAdapter, archiveAdapter, templateAdapter, supportAdapter, feedbackAdapter, templateSharingAdapter, templatePermissionAdapter, usageAdapter, displayAdapter}: {adapter: AuditIntakeAdapter; autoObserve?: boolean; renderOriginal?: OriginalRenderer; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; archiveAdapter?: AuditArchiveAdapter; templateAdapter?: AuditTemplateAdapter; supportAdapter?: AuditSupportAdapter; feedbackAdapter?: AuditFeedbackAdapter; templateSharingAdapter?: AuditTemplateSharingAdapter; templatePermissionAdapter?: AuditTemplatePermissionAdapter; usageAdapter?: AuditUsageAdapter; displayAdapter?: AuditDisplayAdapter}) {
  const [surface, setSurface] = useState<'audit' | 'archive' | 'templates' | 'usage'>(() => {const view = new URL(location.href).searchParams.get('view'); return view === 'archive' || view === 'templates' || view === 'usage' ? view : 'audit'})
  const [archiveOpened, setArchiveOpened] = useState(surface === 'archive')
  const [templatesOpened, setTemplatesOpened] = useState(surface === 'templates')
  const [usageOpened, setUsageOpened] = useState(surface === 'usage')
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
  const [supportTrace] = useState(() => `client-${crypto.randomUUID()}`)
  const surfaceRef = useRef(surface)
  const [files, setFiles] = useState<AuditTaskFile[]>([])
  const [step, setStepState] = useState<'upload' | 'check' | 'match'>('upload')
  const stepRef = useRef(step)
  function setStep(next: typeof step) {stepRef.current = next; setStepState(next)}
  const [mode, setMode] = useState<AuditTask['analysis_mode']>('standard')
  const [customer, setCustomer] = useState('auto')
  const [task, setTask] = useState<AuditTask | null>(null)
  const [tasks, setTasks] = useState<AuditTask[]>([])
  const latestTasks = useRef(tasks); latestTasks.current = tasks
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all')
  const [sheet, setSheet] = useState(false)
  const [reportView, setReportView] = useState(() => new URL(location.href).searchParams.has('report'))
  const [error, setError] = useState(''), [dragging, setDragging] = useState(false)
  const [checking, setChecking] = useState<string[]>([])
  const [driveOpen, setDriveOpen] = useState(false)
  const [readStatus, setReadStatus] = useState<Record<string, {busy: boolean; error: string}>>({})
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
  const [recovery, setRecovery] = useState<{id: string; state: 'loading' | 'error' | 'unauthenticated' | 'forbidden' | 'not_found'; message: string} | null>(() => {
    const id = new URL(location.href).searchParams.get('task')
    return id ? {id, state: 'loading', message: ''} : null
  })
  const uploads = useUploads(adapter, (file, removed) => setFiles(rows => rows.some(row => row.file.id === file.id || row.selection !== 'removed' && row.file.file_id === file.file_id) ? rows : [...rows, {file, selection: removed ? 'removed' : 'selected', removal_reason: removed ? '传输期间排除，不纳入识别' : null, validation: {state: 'pending', reasons: ['待真实预检查：本预览只登记本机文件，尚未执行真实解析。']}, match: null}]))
  const active = files.filter(row => row.selection !== 'removed')
  const valid = active.filter(row => row.validation.state === 'valid')
  const waiting = active.filter(row => row.validation.state === 'pending' || row.validation.state === 'checking')
  const exceptions = active.filter(row => row.validation.state === 'invalid' || row.validation.state === 'error')
  const selected = valid.filter(row => row.selection === 'selected')
  const transferBlocked = uploads.rows.some(row => !row.removed && row.receipt.state !== 'ready')
  const blocked = transferBlocked ? '请先处理或排除未完成的传输，再开始识别。' : waiting.length ? `还有 ${waiting.length} 份文件等待预检查，不能开始识别。` : exceptions.length ? '请先处理或移除异常文件。' : !selected.length ? '至少选择一份可审核文件。' : ''
  const matchingFiles = task?.files.filter(row => row.selection === 'selected') ?? []
  const matched = matchingFiles.filter(row => row.match?.state === 'matched' && row.match.rule)
  const noMatches = Boolean(task?.state === 'pending_confirm' && !matched.length && task.files.every(row => row.selection !== 'selected' || row.match?.state === 'unmatched'))
  const workbench = task?.state === 'reviewing' || task?.state === 'completed' && task.completion_reason === 'processed'
  function saveTask(next: AuditTask, {activate = false, preserveNotification = false} = {}) {
    const merge = (current?: AuditTask | null) => {
      const combined = reconcileTaskDisplay(current?.id === next.id ? current : latestTasks.current.find(row => row.id === next.id), next)
      return preserveNotification && current?.id === next.id && current.notification_enabled !== undefined ? {...combined, notification_enabled: current.notification_enabled} : combined
    }
    if (activate) activeTaskId.current = next.id
    setTask(current => activate || current?.id === next.id ? merge(current) : current)
    setTasks(rows => rows.some(row => row.id === next.id) ? rows.map(row => row.id === next.id ? merge(row) : row) : [...rows, next])
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
    saveTask(next)
    if (activeTaskId.current === next.id) {setFiles(structuredClone(next.files)); setStep('match')}
  })
  const confirmation = task ? acceptance.observations[task.id] : undefined
  const confirmationLocked = Boolean(confirmation && confirmation.phase !== 'rejected' || task?.acceptance?.state === 'pending' && confirmation?.phase !== 'rejected')
  const canConfirm = task?.state === 'pending_confirm' && matched.length > 0 && matchingFiles.every(row => row.match?.state === 'unmatched' || row.match?.state === 'matched' && row.match.rule) && !confirmationLocked
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
    const snapshot = {files: structuredClone(files), uploads: uploads.snapshot(), mode, customer, step}
    if (task) drafts.current.set(task.id, snapshot)
    else if (activeUploadDraftId.current) uploadDrafts.current.set(activeUploadDraftId.current, snapshot)
  }
  function clearReads() {draftEpoch.current += 1; validationReads.current.clear(); setChecking([]); uploads.reset()}
  function taskAddress(id: string | null) {
    surfaceRef.current = 'audit'
    setSurface('audit')
    setReportView(false)
    const url = new URL(location.href)
    if (id) url.searchParams.set('task', id); else url.searchParams.delete('task')
    url.searchParams.delete('report')
    url.searchParams.delete('view')
    for (const key of ['archiveDoc', 'auditRef', 'archiveReport']) url.searchParams.delete(key)
    history.pushState({...history.state, auditUploadDraft: id ? null : activeUploadDraftId.current}, '', url)
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: id, report: null}, location.origin)
  }
  function newTask() {restoreEpoch.current += 1; setRecovery(null); retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = crypto.randomUUID(); setFiles([]); setTask(null); setStep('upload'); setMode('standard'); setCustomer('auto'); setError(''); setSheet(false); taskAddress(null)}
  function openTask(row: AuditTask) {
    if (!acceptance.restore(row)) {setError('任务回执与原请求不一致，未替换当前文件。'); return}
    restoreEpoch.current += 1; setRecovery(null)
    retainDraft()
    clearReads()
    activeUploadDraftId.current = null
    activeTaskId.current = row.id
    const saved = row.round_id ? undefined : drafts.current.get(row.id)
    if (saved) uploads.restore(saved.uploads)
    saveTask(row, {activate: true}); setFiles(structuredClone(saved?.files ?? row.files)); setMode(saved?.mode ?? row.analysis_mode); setCustomer(saved?.customer ?? row.customer_preference ?? 'auto'); setStep(saved?.step ?? 'match'); setSheet(false)
    taskAddress(row.id)
  }
  async function recover(id: string, login = false) {
    const epoch = ++restoreEpoch.current
    acceptance.suspend(id)
    taskReadEpoch.current.set(id, (taskReadEpoch.current.get(id) || 0) + 1); taskReads.current.delete(id); setReadStatus(rows => ({...rows, [id]: {busy: false, error: ''}}))
    retainDraft(); clearReads(); activeTaskId.current = null; activeUploadDraftId.current = null; setTask(null); setFiles([]); setRecovery({id, state: 'loading', message: ''})
    try {
      if (login) await adapter.authenticate()
      const next = await adapter.readTask(id)
      if (restoreEpoch.current !== epoch) return
      if (next.id !== id) throw new Error('task_identity_mismatch')
      if (!acceptance.restore(next)) throw new Error('acceptance_snapshot_mismatch')
      const saved = !next.round_id && next.acceptance?.state !== 'pending' ? drafts.current.get(next.id) : undefined
      if (saved) uploads.restore(saved.uploads)
      saveTask(next, {activate: true}); setFiles(structuredClone(saved?.files ?? next.files)); setMode(saved?.mode ?? next.analysis_mode); setCustomer(saved?.customer ?? next.customer_preference ?? 'auto'); setStep(saved?.step ?? 'match'); setRecovery(null)
    } catch (failure) {
      if (restoreEpoch.current !== epoch) return
      invalidateDisplayAccess(id)
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      const state = status === 401 ? 'unauthenticated' : status === 403 ? 'forbidden' : status === 404 ? 'not_found' : 'error'
      setRecovery({id, state, message: state === 'unauthenticated' ? '登录已失效。恢复登录后，只读取原任务，不会再次受理。' : state === 'forbidden' ? '当前账号无权访问此任务。未扩大文件或任务权限。' : state === 'not_found' ? '未找到此任务。此预览不会持久化临时任务；请使用固定恢复样例。' : '暂时不能恢复任务。未创建新任务，也未重发审核确认。'})
    }
  }
  function restoreAddress() {
    setFeedbackOpen(false)
    setSupportSnapshot(null)
    const params = new URL(location.href).searchParams, view = params.get('view')
    if (view === 'archive' || view === 'templates' || view === 'usage') {
      surfaceRef.current = view; setSurface(view)
      if (view === 'archive') setArchiveOpened(true); else if (view === 'templates') setTemplatesOpened(true); else setUsageOpened(true)
      if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view, ...Object.fromEntries(['archiveDoc', 'auditRef', 'archiveReport', 'usageRange', 'usageSearch', 'usagePage'].map(key => [key, params.get(key)]))}, location.origin)
      return
    }
    const fromOtherSurface = surfaceRef.current !== 'audit'
    surfaceRef.current = 'audit'
    setSurface('audit')
    setReportView(new URL(location.href).searchParams.has('report'))
    const id = new URL(location.href).searchParams.get('task')
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-task', task: id, report: new URL(location.href).searchParams.get('report')}, location.origin)
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
    if (!picked.length || confirmationLocked || workbench) return
    uploads.add(picked.map(file => ({kind: 'local', file}))); setStep('check'); setError('')
  }
  function receiveDrive(picked: components['schemas']['DriveFile'][]) {
    if (confirmationLocked || workbench) return
    uploads.add(picked.map(file => ({kind: 'feishu', file}))); setStep('check'); setDriveOpen(false); setError('')
  }
  const driveButton = <Button variant="outline" onClick={event => {driveTrigger.current = event.currentTarget; setDriveOpen(true)}}>从飞书选择</Button>
  function remove(id: string) {
    if (confirmationLocked || workbench) return
    uploads.rows.filter(row => row.receipt.file?.id === id).forEach(row => uploads.remove(row.id))
    const update = (rows: AuditTaskFile[]) => rows.map(row => row.file.id === id ? {...row, selection: 'removed' as const, removal_reason: row.validation.reasons[0] || (step === 'match' ? '未匹配到规则' : '识别前移除')} : row)
    setFiles(update)
    if (step === 'match' && task) saveTask({...task, files: update(task.files)})
  }
  function toggle(id: string, checked: boolean) {if (!confirmationLocked && !workbench) setFiles(rows => rows.map(row => row.file.id === id ? {...row, selection: checked ? 'selected' : 'unselected'} : row))}
  async function readValidation(row: AuditTaskFile) {
    if (validationReads.current.has(row.file.id)) return
    const epoch = draftEpoch.current
    validationReads.current.add(row.file.id); setChecking(ids => [...ids, row.file.id])
    try {
      const validation = await adapter.readValidation(row)
      if (draftEpoch.current !== epoch) return
      setFiles(rows => rows.map(file => file.file.id === row.file.id && file.selection !== 'removed' ? {...file, validation} : file))
    } catch {
      if (draftEpoch.current !== epoch) return
      setFiles(rows => rows.map(file => file.file.id === row.file.id && file.selection !== 'removed' ? {...file, validation: {state: 'error', reasons: ['检查结果读取失败。已有文件仍保留；重新读取不会重启审核。']}} : file))
    } finally {
      if (draftEpoch.current === epoch) {validationReads.current.delete(row.file.id); setChecking(ids => ids.filter(id => id !== row.file.id))}
    }
  }
  function startMatching() {
    if (blocked || confirmationLocked || workbench) return
    try {
      const next = adapter.match(files, mode, customer === 'auto' ? null : customer, task)
      if (activeUploadDraftId.current) uploadDrafts.current.set(activeUploadDraftId.current, {taskId: next.id})
      activeUploadDraftId.current = null
      taskReadEpoch.current.set(next.id, (taskReadEpoch.current.get(next.id) || 0) + 1)
      taskReads.current.delete(next.id)
      setReadStatus(rows => ({...rows, [next.id]: {busy: false, error: ''}}))
      saveTask(next, {activate: true}); setStep('match'); setError(''); taskAddress(next.id)
    }
    catch {setError('读取匹配结果失败，文件与选择已保留。请返回检查后再读取。')}
  }
  async function readMatching(current: AuditTask) {
    if (taskReads.current.has(current.id)) return null
    const epoch = taskReadEpoch.current.get(current.id)
    taskReads.current.add(current.id)
    setReadStatus(rows => ({...rows, [current.id]: {busy: true, error: rows[current.id]?.error || ''}}))
    try {
      const next = await adapter.readTask(current.id)
      if (taskReadEpoch.current.get(current.id) !== epoch) return null
      if (next.id !== current.id) throw new Error('task_identity_mismatch')
      if (current.round_id && !sameFrozenInputs(current, next)) throw new Error('frozen_input_mismatch')
      if (progressRegressed(current, next)) throw new Error('regressive_progress')
      saveTask(next, {preserveNotification: true})
      const saved = drafts.current.get(next.id)
      if (saved?.step === 'match') saved.files = structuredClone(next.files)
      if (activeTaskId.current === next.id && stepRef.current === 'match') setFiles(structuredClone(next.files))
      setReadStatus(rows => ({...rows, [current.id]: {busy: true, error: ''}}))
      return true
    } catch (failure) {
      if (taskReadEpoch.current.get(current.id) !== epoch) return null
      invalidateDisplayAccess(current.id)
      setReadStatus(rows => ({...rows, [current.id]: {busy: false, error: failure instanceof Error && failure.message === 'regressive_progress' ? '收到较早或不完整的审核进度，已保留当前结果。请稍后重新读取。' : '任务结果读取失败，任务与文件仍保留。重新读取不会重新创建任务或开始审核。'}}))
      return false
    } finally {
      if (taskReadEpoch.current.get(current.id) === epoch) {taskReads.current.delete(current.id); setReadStatus(rows => ({...rows, [current.id]: {error: rows[current.id]?.error || '', busy: false}}))}
    }
  }
  const observedTaskEpoch = task ? taskReadEpoch.current.get(task.id) : undefined
  useTaskObservation(task?.id, autoObserve && surface === 'audit' && !recovery && step === 'match' && !reportView && canObserveTask(task), () => task && activeTaskId.current === task.id && surfaceRef.current === 'audit' && stepRef.current === 'match' && taskReadEpoch.current.get(task.id) === observedTaskEpoch ? readMatching(task) : Promise.resolve(null))
  const shownTasks = tasks.filter(row => (filter === 'all' || row.state === filter) && `${row.number} ${row.files.map(file => file.file.original_filename).join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  function showSurface(value: 'audit' | 'archive' | 'templates' | 'usage') {
    setSheet(false)
    if (value === 'archive') setArchiveOpened(true)
    if (value === 'templates') setTemplatesOpened(true)
    if (value === 'usage') setUsageOpened(true)
    const url = new URL(location.href)
    for (const key of ['archiveDoc', 'auditRef', 'archiveReport']) url.searchParams.delete(key)
    if (value !== 'audit') url.searchParams.set('view', value); else url.searchParams.delete('view')
    history.pushState(history.state, '', url)
    if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view: value}, location.origin)
    if (value === 'audit') {restoreAddress(); return}
    surfaceRef.current = value
    setSurface(value)
    // A repeated top-level entry leaves its detail without changing the active surface.
    if (value === surface) window.dispatchEvent(new PopStateEvent('popstate'))
  }
  const navigation = <nav aria-label="产品导航" className="grid gap-1">
    <Button className="justify-start" variant={surface === 'audit' ? 'secondary' : 'ghost'} onClick={() => showSurface('audit')}><ClipboardCheck/>PPAP Audit</Button>
    <Button className="justify-start" variant={surface === 'archive' ? 'secondary' : 'ghost'} onClick={() => showSurface('archive')}><Files/>审核文件</Button>
    <Button className="justify-start" variant={surface === 'templates' ? 'secondary' : 'ghost'} onClick={() => showSurface('templates')}><BookOpen/>模板库</Button>
    <Button className="justify-start" variant={surface === 'usage' ? 'secondary' : 'ghost'} onClick={() => showSurface('usage')}>用量记录</Button>
  </nav>
  const taskRail = <div className="flex h-full min-w-0 flex-col gap-4">
    <Button variant="outline" onClick={newTask}><Plus/>新建审核任务</Button>
    <label className="flex items-center gap-2"><span className="sr-only">搜索任务文件</span><Search className="size-4 shrink-0 text-muted-foreground"/><Input placeholder="搜索文件名" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <Select value={filter} onValueChange={setFilter}><SelectTrigger className="w-full" aria-label="任务状态"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem>{Object.entries(taskLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
    <nav className="grid gap-2" aria-label="任务列表">{shownTasks.length ? shownTasks.map(row => <Button key={row.id} variant={task?.id === row.id ? 'secondary' : 'ghost'} className="h-auto min-w-0 justify-start whitespace-normal text-left" onClick={() => openTask(row)}><span className="grid min-w-0 gap-1"><span>{row.number}</span><span className="text-xs">{taskProgressError(row) ? '任务进度待核对' : taskLabels[row.state]} · {row.files.filter(file => file.selection === 'selected').length} 份文件</span></span></Button>) : <p className="text-sm text-muted-foreground">{tasks.length ? '没有符合条件的任务。' : '开始识别后，任务显示在这里。'}</p>}</nav>
    <p className="mt-auto text-xs text-muted-foreground">此预览的任务仅存于页面内存，刷新会清空；不代表服务端已保存。</p>
  </div>
  const table = (rows: AuditTaskFile[], selectable: boolean) => <Table className="table-fixed">
    <TableHeader><TableRow>{selectable && <TableHead className="w-12"><Checkbox aria-label="全选可审核文件" checked={selected.length === valid.length && valid.length > 0 ? true : selected.length ? 'indeterminate' : false} onCheckedChange={value => setFiles(items => items.map(row => row.validation.state === 'valid' && row.selection !== 'removed' ? {...row, selection: value === true ? 'selected' : 'unselected'} : row))}/></TableHead>}<TableHead>文件</TableHead><TableHead className="w-24 max-md:hidden">大小</TableHead><TableHead className="w-28 max-md:hidden">预检查</TableHead><TableHead className={selectable ? 'w-16' : 'w-36'}><span className="sr-only">操作</span></TableHead></TableRow></TableHeader>
    <TableBody>{rows.map(row => <TableRow key={row.file.id}>{selectable && <TableCell><Checkbox aria-label={`选择 ${row.file.original_filename}`} checked={row.selection === 'selected'} onCheckedChange={value => toggle(row.file.id, value === true)}/></TableCell>}<TableCell className="whitespace-normal"><FileName name={row.file.original_filename}/>{!selectable && <p className="mt-2 text-sm text-muted-foreground">{row.validation.reasons.join('；') || '暂未完成检查'}</p>}<span className="mt-1 block text-xs text-muted-foreground md:hidden">{sizeLabel(row.file.byte_size)} · {row.validation.state === 'valid' ? '示例：可审核' : '待处理'}</span></TableCell><TableCell className="max-md:hidden"><span className="text-muted-foreground">{sizeLabel(row.file.byte_size)}</span></TableCell><TableCell className="max-md:hidden"><Badge variant="outline">{{valid: '可审核', pending: '待预检查', checking: '检查中', invalid: '文件异常', error: '读取失败'}[row.validation.state]}</Badge></TableCell><TableCell><div className="flex flex-wrap items-center justify-end gap-1">{!selectable && row.validation.state !== 'invalid' && <Button variant="outline" size="sm" disabled={checking.includes(row.file.id)} aria-label={`${row.validation.state === 'error' ? '重新读取' : '读取'} ${row.file.original_filename} 检查结果`} onClick={() => void readValidation(row)}>{checking.includes(row.file.id) ? '读取中…' : row.validation.state === 'error' ? '重新读取' : '读取结果'}</Button>}<Button variant="ghost" size="icon" aria-label={`移除 ${row.file.original_filename}`} onClick={() => remove(row.file.id)}><X/></Button></div></TableCell></TableRow>)}</TableBody>
  </Table>
  const supportScenario = surface === 'templates' ? '客户模板库' : surface === 'archive' ? '文件档案' : surface === 'usage' ? '用量记录' : workbench ? '审核结果' : '上传与文件核对'
  return <SupportEntryContext.Provider value={openSupport}><div className="flex min-h-dvh bg-background text-sm text-foreground">
    <a href="#audit-main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:p-3">跳到主要内容</a>
    <aside className="sticky top-0 flex h-dvh w-44 shrink-0 flex-col gap-8 border-r p-4 max-lg:hidden"><div className="flex items-center gap-2 text-lg font-semibold"><ClipboardCheck/>PPAP</div>{navigation}<Button className="mt-auto justify-start" variant="ghost" onClick={() => openSupport({scenario: supportScenario})}><HelpCircle/>联系我们</Button></aside>
    {surface === 'audit' && !reportView && <aside aria-label="审核任务" className="sticky top-0 h-dvh w-56 shrink-0 border-r p-4 max-xl:hidden">{taskRail}</aside>}
    <div className="min-w-0 flex-1">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-3 max-sm:px-4"><div className="flex items-center gap-3"><Sheet open={sheet} onOpenChange={setSheet}><SheetTrigger asChild><Button variant="outline" size="icon" className={surface !== 'audit' ? 'lg:hidden' : reportView ? '' : 'xl:hidden'} aria-label={surface !== 'audit' ? '打开产品导航' : '打开审核任务'}><Menu/></Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>{surface !== 'audit' ? '产品导航' : '审核任务'}</SheetTitle><SheetDescription>查看与切换本页示例，不启动真实审核。</SheetDescription></SheetHeader><div className="grid flex-1 gap-6 overflow-y-auto p-4"><div className="lg:hidden">{navigation}<Button variant="ghost" className="w-full justify-start" onClick={() => {setSheet(false); openSupport({scenario: supportScenario})}}><HelpCircle/>联系我们</Button></div>{surface === 'audit' && taskRail}</div></SheetContent></Sheet><span className="text-muted-foreground">PPAP Audit{surface === 'archive' ? ' / 审核文件' : surface === 'templates' ? ' / 模板库' : surface === 'usage' ? ' / 用量记录' : task ? ` / ${task.number}` : ' / 新建审核'}</span></div><Badge variant="outline">交互预览</Badge></header>
      <main id="audit-main" tabIndex={-1} className={`mx-auto w-full space-y-6 p-6 max-sm:p-4 ${workbench || surface !== 'audit' ? '' : 'max-w-5xl'}`}>
        <p role="note" className="text-xs text-muted-foreground">{surface === 'templates' ? '交互预览：示例模板，不会启动审核或收费。' : '示例数据，不会启动真实审核或收费。实际上传、格式检查、规则匹配与持久化尚未接入。'}</p>
        {archiveOpened && <div hidden={surface !== 'archive'}>{archiveAdapter ? <AuditArchive active={surface === 'archive'} adapter={archiveAdapter} onUpload={newTask} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}}/> : <Alert><AlertTitle>档案读取接口尚未接入</AlertTitle><AlertDescription>不能把当前任务文件名当作已归档的来源文档。</AlertDescription></Alert>}</div>}
        {templatesOpened && <div hidden={surface !== 'templates'}>{templateAdapter ? <AuditTemplates active={surface === 'templates'} adapter={templateAdapter} sharingAdapter={templateSharingAdapter} permissionAdapter={templatePermissionAdapter} onReturn={() => showSurface('audit')} onFeedback={openFeedback}/> : <Alert><AlertTitle>模板库尚未接入</AlertTitle><AlertDescription>未使用审核规则或示例报告冒充客户模板。<Button variant="outline" onClick={() => showSurface('audit')}>返回审核</Button></AlertDescription></Alert>}</div>}
        {usageOpened && <div hidden={surface !== 'usage'}><AuditUsage active={surface === 'usage'} adapter={usageAdapter} archiveAdapter={archiveAdapter} reportAdapter={reportAdapter} sharingAdapter={sharingAdapter} renderOriginal={renderOriginal} onOpenTask={id => {taskAddress(id); void recover(id)}} onSupport={notice => openSupport({scenario: `用量记录 · ${notice}`})}/></div>}
        <div hidden={surface !== 'audit'} className="space-y-6">
        {!reportView && <ol aria-label="审核步骤" className="flex flex-wrap gap-4 border-b pb-4">{['上传与检查', '确认规则', 'AI审核'].map((label, index) => <li key={label} aria-current={(workbench ? 2 : step === 'match' ? 1 : 0) === index ? 'step' : undefined} className="flex items-center gap-2"><span className="text-muted-foreground">{index + 1}</span><span>{label}</span>{index < 2 && <ArrowRight className="size-3 text-muted-foreground"/>}</li>)}</ol>}
        {error && <Alert variant="destructive"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
        {recovery ? <section className="space-y-4 py-8"><h1 className="text-2xl font-semibold">{recovery.state === 'loading' ? '正在恢复任务' : '任务暂不可用'}</h1><p className="break-all text-xs text-muted-foreground">任务：{recovery.id}</p><p role="status">{recovery.state === 'loading' ? '正在读取原任务，不创建任务或启动审核。' : recovery.message}</p><div className="flex flex-wrap gap-2">{recovery.state !== 'loading' && <Button variant="outline" onClick={() => void recover(recovery.id, recovery.state === 'unauthenticated')}>{recovery.state === 'unauthenticated' ? '恢复登录' : '重新读取任务'}</Button>}<Button variant="ghost" onClick={newTask}>返回新建</Button></div></section> : workbench && task ? <AuditWorkspace key={task.id} task={task} busy={readStatus[task.id]?.busy || false} readError={readStatus[task.id]?.error || ''} onRead={() => void readMatching(task)} onChange={saveTask} renderOriginal={renderOriginal} reportAdapter={reportAdapter} onReportChange={changeReport} onReportView={setReportView} sharingAdapter={sharingAdapter} onEditDisplay={displayAdapter ? file => displayEditor.open({task_id: task.id, round_id: task.round_id!, file_version_id: file.file.id}, file) : undefined}/> : task?.state === 'completed' ? <section className="space-y-4 py-8"><Check className="size-8"/><h1 className="text-2xl font-semibold">任务已结束</h1><p>未匹配到可用规则，未开始审核</p><p className="text-muted-foreground">未产生审核费用，也没有生成报告。</p><Button onClick={newTask}>新建审核任务<ArrowRight/></Button></section> : <>
        <header className="space-y-2"><h1 className="text-2xl font-semibold tracking-tight">{step === 'upload' ? '上传 PPAP 文件' : step === 'check' ? '核对本次文件' : task?.state === 'matching' ? '正在识别文件与匹配规则' : '确认每份文件的审核规则'}</h1><p className="text-muted-foreground">{step === 'match' ? '分别核对每份文件匹配的规则。未匹配文件不会进入审核。' : '先选择收到的文件，再核对可审核范围。不需要填写额外的事项表单。'}</p></header>
        {step !== 'match' && <div className="flex flex-wrap items-end justify-between gap-4"><div className="flex flex-wrap items-end gap-2"><div className="grid gap-2"><Label htmlFor="audit-customer">客户（可选）</Label><Select value={customer} onValueChange={setCustomer}><SelectTrigger id="audit-customer" className="w-56"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="auto">自动识别客户</SelectItem><SelectItem value="sample-customer">示例客户</SelectItem></SelectContent></Select></div><Button variant="ghost" onClick={() => showSurface('templates')}>查看客户模板</Button><Button variant="ghost" onClick={openFeedback}>补充模板</Button></div>{step === 'check' && <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => input.current?.click()}><Plus/>继续添加文件</Button>{driveButton}</div>}</div>}
        <input ref={input} type="file" className="sr-only" aria-label="选择审核文件" multiple accept=".xlsx,.xls,.xlsm,.doc,.docx,.pdf" onChange={event => {receive(Array.from(event.target.files || [])); event.target.value = ''}}/>
        {step === 'upload' && <section className="space-y-5"><div className="flex flex-col items-center gap-4 rounded-lg border border-dashed px-4 py-12 text-center" data-dragging={dragging} onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); receive(Array.from(event.dataTransfer.files))}}><UploadCloud className="size-8 text-muted-foreground"/><h2 className="text-base font-medium">拖入本地文件</h2><p className="text-muted-foreground">Excel、Word、PDF · 设计上限每份 200 MB</p><p className="max-w-md text-xs text-muted-foreground">本预览只读取文件名和大小，不上传字节，也不会把任意文件判为可审核。</p><div className="flex flex-wrap justify-center gap-2"><Button onClick={() => input.current?.click()}>选择本地文件</Button>{driveButton}</div></div><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-muted-foreground">要走查后续交互，可使用明确的三文件示例。</p><Button variant="outline" onClick={() => {setFiles(rows => [...rows, ...adapter.samples()]); setStep('check')}}>使用三类文件示例<ArrowRight/></Button></div></section>}
        {step === 'upload' && active.length > 0 && <section className="space-y-4 border-t pt-5"><h2 className="font-semibold">已选择文件 · {active.length} 份</h2><ItemGroup>{active.map(row => <Item role="listitem" key={row.file.id}><ItemContent><FileName name={row.file.original_filename}/></ItemContent><Button variant="ghost" size="icon" aria-label={`移除 ${row.file.original_filename}`} onClick={() => remove(row.file.id)}><X/></Button></Item>)}</ItemGroup><Button onClick={() => setStep('check')}>继续核对<ArrowRight/></Button></section>}
        {step === 'check' && <>
          <TransferList rows={uploads.rows} onRead={uploads.read} onRetry={uploads.retry} onRemove={uploads.remove}/>
          <section className="space-y-3"><h2 className="font-semibold">可审核文件 · {valid.length} 份</h2>{valid.length ? table(valid, true) : <p className="py-4 text-muted-foreground">尚无完成预检查的可审核文件。</p>}</section>
          {waiting.length > 0 && <section className="space-y-3 border-t pt-5"><h2 className="font-semibold">等待预检查 · {waiting.length} 份</h2><p className="text-muted-foreground">等待检查不代表文件异常。读取结果不会创建审核任务。</p>{table(waiting, false)}</section>}
          {exceptions.length > 0 && <section className="space-y-3 border-t pt-5"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">异常文件 · {exceptions.length} 份</h2><Button variant="outline" size="sm" onClick={() => exceptions.forEach(row => remove(row.file.id))}>一键移除异常文件</Button></div>{table(exceptions, false)}</section>}
          <footer className="space-y-4 border-t pt-5"><p role="status" className="text-muted-foreground">{blocked || `已选择 ${selected.length} 份文件，开始识别后逐份匹配规则。`}</p><div className="flex flex-wrap items-center justify-between gap-4"><Label><Checkbox checked={mode === 'deep'} onCheckedChange={value => setMode(value === true ? 'deep' : 'standard')}/>深度模式</Label><div className="flex gap-2"><Button variant="outline" onClick={() => setStep('upload')}><ArrowLeft/>上一步</Button><Button disabled={Boolean(blocked)} onClick={startMatching}>开始识别<ArrowRight/></Button></div></div></footer>
        </>}
        {step === 'match' && task && <>
          <div className="flex flex-wrap gap-2" role="status"><Badge variant="secondary">{taskLabels[task.state]}</Badge><span className="text-muted-foreground">{matchingFiles.length} 份文件 · {matched.length} 份匹配到规则{task.state === 'matching' ? ' · 等待其余识别结果' : ''}</span></div>
          {task.state === 'matching' && <div className="space-y-3"><p className="text-muted-foreground">识别尚未完成。可以切换或新建任务，不会取消这个任务；此预览不显示模拟完成百分比。</p>{readStatus[task.id]?.error && <Alert variant="destructive"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription>{readStatus[task.id].error}</AlertDescription></Alert>}<Button variant="outline" disabled={readStatus[task.id]?.busy} onClick={() => void readMatching(task)}>{readStatus[task.id]?.busy ? '正在读取识别结果…' : readStatus[task.id]?.error ? '重新读取识别结果' : '读取识别结果'}</Button></div>}
          <div className="divide-y">{matchingFiles.map(row => <section key={row.file.id} className="space-y-4 py-5 first:pt-0"><div className="flex flex-wrap items-start justify-between gap-3"><h2 className="min-w-0 flex-1 font-semibold"><FileName name={row.file.original_filename}/></h2><Badge variant="outline">{row.match?.state === 'matched' && row.match.rule ? '已匹配' : row.match?.state === 'unmatched' ? '未匹配' : row.match?.state === 'matching' ? '识别中' : !row.match || row.match.state === 'pending' ? '等待识别' : '识别异常'}</Badge></div><dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3 xl:grid-cols-5">{[['文件类型', row.match?.document_type], ['客户', row.match?.metadata.customer], ['车型', row.match?.metadata.model], ['零件号', row.match?.metadata.part_number], ['零件名称', row.match?.metadata.part]].map(([label, value]) => <div className="min-w-0" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 break-words">{value || '—'}</dd></div>)}</dl>{row.match?.state === 'matched' && row.match.rule ? <div className="space-y-2"><p className="font-medium">{row.match.rule.title}</p><p className="text-xs text-muted-foreground">当前候选规则版本：{row.match.rule.id} · 尚未确认受理</p>{row.match.gaps.map(gap => <p key={gap} className="text-muted-foreground">{gap}</p>)}</div> : row.match?.state === 'matching' || !row.match || row.match.state === 'pending' ? <p className="text-muted-foreground">等待识别结果，尚未作出规则匹配结论。</p> : <Alert><AlertTitle>{row.match?.state === 'unmatched' ? '未匹配到生效规则' : '暂时无法确认匹配结果'}</AlertTitle><AlertDescription><p>{row.match?.state === 'unmatched' ? '此文件不会自动套用通用标准，也不会产生审核结论。' : '技术错误不是零匹配，也不是审核不通过。请返回文件检查。'}</p>{row.match?.state === 'unmatched' && <div className="flex flex-wrap gap-2 pt-2"><Button variant="outline" size="sm" onClick={() => showSurface('templates')}>标准模板</Button><Button variant="outline" size="sm" onClick={openFeedback}>反馈对应模板</Button><Button variant="ghost" size="sm" onClick={() => remove(row.file.id)}>移除</Button></div>}</AlertDescription></Alert>}</section>)}</div>
          <footer className="space-y-4 border-t pt-5"><p className="text-muted-foreground">{noMatches ? '没有可进入审核的匹配文件。可以结束任务，不扣费、不生成报告。' : `预计点数（示例）：${task.estimated_points ?? '待服务端确认'}`}</p>
            {confirmation?.message && <Alert variant={confirmation.phase === 'rejected' ? 'default' : 'destructive'}><AlertTitle>{confirmation.phase === 'rejected' ? '本次未受理' : '受理结果待核对'}</AlertTitle><AlertDescription><p>{confirmation.message}</p><p className="break-all text-xs">请求：{confirmation.requestId}</p><Button variant="outline" size="sm" onClick={() => openSupport({scenario: '确认审核', facts: [{label: '任务标识', value: task.id}, {label: '确认请求', value: confirmation.requestId}, {label: '受理状态', value: confirmation.phase === 'rejected' ? '明确未受理' : '尚未确认'}, {label: '服务说明', value: confirmation.message}]})}>联系支持</Button></AlertDescription></Alert>}
            <div className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" disabled={confirmationLocked} onClick={() => setStep('check')}><ArrowLeft/>上一步</Button>{noMatches ? <Button onClick={() => saveTask({...task, state: 'completed', completion_reason: 'no_matching_rules'})}>结束任务</Button> : <div className="flex flex-wrap gap-2">{confirmation && ['unknown', 'reading'].includes(confirmation.phase) && <Button variant="outline" disabled={confirmation.phase === 'reading'} onClick={() => void acceptance.reconcile(task)}>{confirmation.phase === 'reading' ? '正在读取受理结果…' : '读取受理结果'}</Button>}<Button disabled={!canConfirm} onClick={() => confirm(task)}>{confirmation?.phase === 'sending' ? '正在确认…' : '开始审核'}<ArrowRight/></Button></div>}</div>
            {!noMatches && <p className="text-xs text-muted-foreground">此预览演示同一任务的受理与审核结果。不会调用真实审核或收费；真实受理政策仍待确认。</p>}
          </footer>
        </>}
        {step === 'match' && matched.length > 0 && <section className="space-y-3 border-t pt-5"><h2 className="font-semibold">匹配条款</h2><p className="text-xs text-muted-foreground">只读查看当前候选版本，不切换或编辑规则。</p><div className="flex flex-wrap gap-2">{matched.map(row => <Button key={row.file.id} variant="outline" className="max-w-full whitespace-normal text-left" onClick={event => {ruleTrigger.current = event.currentTarget; setRuleSource(row.match!.rule)}}>查看 {row.match!.rule!.title} 条款</Button>)}</div></section>}
        </>}
        {files.some(row => row.selection === 'removed') && <section className="space-y-3 border-t pt-5"><h2 className="font-semibold">已移除文件</h2>{files.filter(row => row.selection === 'removed').map(row => <div key={row.file.id} className="space-y-1"><FileName name={row.file.original_filename}/><p className="text-xs text-muted-foreground">{row.removal_reason}</p></div>)}</section>}
        </div>
      </main>
      {displayEditor.dialog}
    </div>
    {driveOpen && <FeishuPicker source={adapter.drive} onAdd={receiveDrive} onClose={() => setDriveOpen(false)} trigger={driveTrigger.current}/>}
    <TemplateFeedback open={feedbackOpen} onClose={() => setFeedbackOpen(false)} adapter={feedbackAdapter} trigger={feedbackTrigger.current}/>
    {supportSnapshot && <AuditSupport adapter={supportAdapter} snapshot={supportSnapshot} traceId={supportTrace} returnTargets={supportReturnTargets.current} onClose={() => setSupportSnapshot(null)}/>}
    <Dialog open={Boolean(ruleSource)} onOpenChange={open => {if (!open) setRuleSource(null)}}><DialogContent onCloseAutoFocus={event => {event.preventDefault(); ruleTrigger.current?.focus()}}><DialogHeader><DialogTitle>{ruleSource?.title}</DialogTitle><DialogDescription>当前匹配的只读候选条款。预览不发布规则，也不开始审核。</DialogDescription></DialogHeader><div className="max-h-80 space-y-4 overflow-y-auto">{ruleSource?.body.kind === 'engine' ? ruleSource.body.rules.map(rule => <article key={rule.id} className="space-y-2 border-t pt-3"><h3 className="text-sm font-semibold">{rule.id} · {rule.category}</h3><p className="whitespace-pre-wrap text-sm">{rule.rule_to_check}</p></article>) : <p className="text-sm">文件存在性检查。</p>}</div></DialogContent></Dialog>
  </div></SupportEntryContext.Provider>
}
