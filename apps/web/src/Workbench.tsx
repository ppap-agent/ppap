import {useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent, type PointerEvent as ReactPointerEvent, type KeyboardEvent as ReactKeyboardEvent} from 'react'
import {request, upload, dateLabel, executionLabels, resultLabels, type Draft, type Material, type Rule, type Round, type Analysis, type Job, type Finding} from './api'
import {analysisNotices, clampPreviewWidth, latestRules, matchesFilter, parseWorkspacePosition, reviewJudgments, workspaceStorageKey} from './helpers'
import OriginalPreview, {type OriginalPreviewHandle} from './OriginalPreview'
import DrivePicker from './DrivePicker'
import ReportPreview from './ReportPreview'
import Modal from './Modal'
import Help from './Help'
import {ArrowLeft, ArrowRight, X, Search} from 'lucide-react'

const filters = [{id: 'attention', label: '需关注'}, {id: 'unreviewed', label: '未记录'}, {id: '', label: '全部'}, {id: 'PASS', label: '通过'}]

export default function Workbench({onReviewing}: {onReviewing: (value: boolean) => void}) {
  const [items, setItems] = useState<Material[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [round, setRound] = useState<Round | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [selectedFileId, setSelectedFileId] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [filter, setFilter] = useState('attention')
  const [initialized, setInitialized] = useState(false)
  const [draftId, setDraftId] = useState(new URLSearchParams(location.search).get('draft') || '')
  const [predecessorId, setPredecessorId] = useState(new URLSearchParams(location.search).get('from') || '')
  const [review, setReview] = useState({judgment: '', rationale: '', next_step: ''})
  const [reviewDirty, setReviewDirty] = useState(false)
  const [customJudgment, setCustomJudgment] = useState(false)
  const [savingReview, setSavingReview] = useState(false)
  const [reviewFeedback, setReviewFeedback] = useState('')
  const [pendingChange, setPendingChange] = useState<(() => void) | null>(null)
  const [workspacePanel, setWorkspacePanel] = useState('checks')
  const [roundView, setRoundView] = useState('review')
  const [previewWidth, setPreviewWidth] = useState(50)
  const [resizing, setResizing] = useState(false)
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [draftNext, setDraftNext] = useState<number | null>(null)
  const [draftLoading, setDraftLoading] = useState(false)
  const [previousRound, setPreviousRound] = useState<Round | null>(null)
  const [savedDraft, setSavedDraft] = useState('[]')
  const [dragActive, setDragActive] = useState(false)
  const originalPreview = useRef<OriginalPreviewHandle>(null)
  const workspace = useRef<HTMLDivElement>(null)
  const detailHeading = useRef<HTMLHeadingElement>(null)
  const resumeReview = useRef(new URLSearchParams(location.search).get('review') === 'next')
  const disposed = useRef(false)
  const activeRoundId = useRef(new URLSearchParams(location.search).get('round') || '')
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>())
  const busyRef = useRef(false)
  const pendingRef = useRef<(() => void) | null>(null)
  const reviewDirtyRef = useRef(false)
  const reviewRef = useRef(review)
  const roundRef = useRef(round)
  const itemsRef = useRef(items)
  const selectedFileIdRef = useRef(selectedFileId)
  const selectedKeyRef = useRef(selectedKey)
  const filterRef = useRef(filter)
  const roundViewRef = useRef(roundView)
  const workspacePanelRef = useRef(workspacePanel)
  const previewWidthRef = useRef(previewWidth)
  const draftIdRef = useRef(draftId)
  const predecessorIdRef = useRef(predecessorId)
  const savedDraftRef = useRef(savedDraft)
  const initializedRef = useRef(false)
  const restoredPosition = useRef<ReturnType<typeof parseWorkspacePosition>>(null)
  const resizeStart = useRef({x: 0, width: 50})
  const viewScroll = useRef({review: 0, original: 0, report: 0})
  const findingsRef = useRef<Finding[]>([])
  const fileFindingsRef = useRef<Finding[]>([])

  reviewDirtyRef.current = reviewDirty
  reviewRef.current = review
  roundRef.current = round
  itemsRef.current = items
  selectedFileIdRef.current = selectedFileId
  selectedKeyRef.current = selectedKey
  filterRef.current = filter
  roundViewRef.current = roundView
  workspacePanelRef.current = workspacePanel
  previewWidthRef.current = previewWidth
  draftIdRef.current = draftId
  predecessorIdRef.current = predecessorId
  savedDraftRef.current = savedDraft
  pendingRef.current = pendingChange

  useEffect(() => { onReviewing(!!round) }, [round, onReviewing])

  const currentRules = useMemo(() => latestRules(rules), [rules])
  const selectedFile = (round?.inputs || items).find(item => item.file_version_id === selectedFileId)
  const fileFindings = round?.findings.filter(item => item.locator?.file_version_id === selectedFileId) || []
  const findings = fileFindings.filter(item => matchesFilter(item, filter))
  const selectedFinding = round?.findings.find(item => item.item_key === selectedKey)
  findingsRef.current = findings
  fileFindingsRef.current = fileFindings
  const counts = ['NG', 'ERR', 'PASS'].map(status => ({status, count: (round?.findings || []).filter(row => row.status === status).length}))
  function materialSnapshot(list = items) {
    return list.map(item => ({file_version_id: item.file_version_id, role: item.role, rule_version_id: item.role === 'target' ? item.rule_version_id : null}))
  }
  const draftDirty = !round && JSON.stringify(materialSnapshot()) !== savedDraft
  const draftDirtyRef = useRef(draftDirty)
  draftDirtyRef.current = draftDirty
  const missingRules = items.filter(item => item.role === 'target' && !item.rule_version_id).length
  const targetCount = items.filter(item => item.role === 'target').length
  const reviewed = round?.findings.filter(item => item.review).length || 0
  const remaining = round?.findings.filter(item => !item.review) || []
  const hasNextUnreviewed = remaining.some(item => item.item_key !== selectedKey)

  function later(work: () => void) {
    const timer = setTimeout(() => { timers.current.delete(timer); if (!disposed.current) work() }, 2000)
    timers.current.add(timer)
  }
  function query(values: Record<string, string | null>) {
    const url = new URL(location.href)
    for (const [key, value] of Object.entries(values)) value ? url.searchParams.set(key, value) : url.searchParams.delete(key)
    history.replaceState(null, '', url)
  }
  async function action(work: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError('')
    try { await work() }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败，请重试。') }
    finally { busyRef.current = false; setBusy(false) }
  }
  function rememberWorkspace() {
    if (!initializedRef.current || !roundRef.current) return
    try {
      sessionStorage.setItem(workspaceStorageKey(roundRef.current.id), JSON.stringify({
        file: selectedFileIdRef.current, finding: selectedKeyRef.current, filter: filterRef.current,
        view: roundViewRef.current, panel: workspacePanelRef.current, width: previewWidthRef.current, scroll: window.scrollY,
      }))
    } catch { /* Navigation also works with browser storage disabled. */ }
  }
  function guardLeave(event: BeforeUnloadEvent) {
    rememberWorkspace()
    if (reviewDirtyRef.current || draftDirtyRef.current) { event.preventDefault(); event.returnValue = '' }
  }
  function guardChange(work: () => void) {
    if (busyRef.current || pendingRef.current) return
    if (reviewDirtyRef.current || draftDirtyRef.current) setPendingChange(() => work)
    else work()
  }
  function choose(item?: Finding) {
    const judgment = item?.review?.judgment || ''
    const key = item?.item_key || ''
    selectedKeyRef.current = key
    setSelectedKey(key)
    setReview({judgment, rationale: item?.review?.rationale || '', next_step: item?.review?.next_step || ''})
    setReviewDirty(false)
    reviewDirtyRef.current = false
    setCustomJudgment(!!judgment && !reviewJudgments.includes(judgment as typeof reviewJudgments[number]))
    setReviewFeedback('')
  }
  function restoreWorkspace(result: Round) {
    try {
      const saved = parseWorkspacePosition(sessionStorage.getItem(workspaceStorageKey(result.id)))
      if (!saved || !result.inputs.some(file => file.file_version_id === saved.file)) return
      selectedFileIdRef.current = saved.file
      setSelectedFileId(saved.file)
      const nextFilter = ['attention', 'unreviewed', '', 'PASS'].includes(saved.filter) ? saved.filter : 'attention'
      filterRef.current = nextFilter
      setFilter(nextFilter)
      const finding = result.findings.find(item => item.item_key === saved.finding && item.locator?.file_version_id === saved.file)
      const restoredFindings = result.findings.filter(item => item.locator?.file_version_id === saved.file && matchesFilter(item, nextFilter))
      choose(finding || restoredFindings[0])
      const nextView = saved.view === 'report' ? 'report' : 'review'
      roundViewRef.current = nextView
      setRoundView(nextView)
      const nextPanel = saved.panel === 'original' ? 'original' : 'checks'
      workspacePanelRef.current = nextPanel
      setWorkspacePanel(nextPanel)
      if (Number.isFinite(saved.width)) {
        const width = clampPreviewWidth(saved.width)
        previewWidthRef.current = width
        setPreviewWidth(width)
      }
      restoredPosition.current = saved
    } catch { /* A stale view state must never block the actual audit. */ }
  }
  async function saveDraft(list = itemsRef.current) {
    let id = draftIdRef.current
    if (!id) {
      id = (await request<{id: string}>('/drafts', 'POST', {predecessor_id: predecessorIdRef.current || null})).id
      setDraftId(id)
      draftIdRef.current = id
    }
    const snapshot = materialSnapshot(list)
    await request(`/drafts/${id}`, 'PUT', {items: snapshot})
    const serialized = JSON.stringify(snapshot)
    setSavedDraft(serialized)
    savedDraftRef.current = serialized
    query({draft: id, round: null, from: predecessorIdRef.current || null})
  }
  async function readAnalysis(versionId: string, id: string) {
    try {
      const job = await request<Job<Analysis>>(`/file-analyses/${id}`)
      if (disposed.current || roundRef.current) return
      setItems(current => current.map(item => {
        if (item.file_version_id !== versionId) return item
        const next = {...item, analysisStatus: job.status}
        if (job.status === 'succeeded' && job.result) {
          next.analysis = job.result
          if (item.role === 'target' && !item.rule_version_id) next.rule_version_id = job.result.matched_rule_id
        }
        return next
      }))
      if (['queued', 'leased'].includes(job.status)) later(() => { void readAnalysis(versionId, id) })
    } catch {
      setItems(current => current.map(item => item.file_version_id === versionId ? {...item, analysisStatus: 'failed'} : item))
    }
  }
  async function analyze(item: Material) {
    setItems(current => current.map(row => row.file_version_id === item.file_version_id ? {...row, analysisStatus: 'queued'} : row))
    try {
      const job = await request<Job<Analysis>>('/file-analyses', 'POST', {request_id: crypto.randomUUID(), source_file_id: item.file_id, source_file_version_id: item.file_version_id})
      setItems(current => current.map(row => row.file_version_id === item.file_version_id ? {...row, analysisId: job.id} : row))
      void readAnalysis(item.file_version_id, job.id)
    } catch {
      setItems(current => current.map(row => row.file_version_id === item.file_version_id ? {...row, analysisStatus: 'failed'} : row))
    }
  }
  async function imported(material: Material) {
    const next = itemsRef.current.concat(material)
    setItems(next)
    itemsRef.current = next
    await saveDraft(next)
    void analyze(material)
  }
  async function loadRound(id: string) {
    if (id !== activeRoundId.current) return
    try {
      const result = await request<Round>(`/rounds/${id}`)
      if (disposed.current || id !== activeRoundId.current) return
      const firstLoad = !roundRef.current
      setRound(result)
      roundRef.current = result
      if (firstLoad && !resumeReview.current) restoreWorkspace(result)
      if (resumeReview.current && result.findings.length) {
        const pending = result.findings.find(item => !item.review && item.status !== 'PASS') || result.findings.find(item => !item.review)
        if (pending) {
          const fileId = pending.locator?.file_version_id || ''
          selectedFileIdRef.current = fileId
          setSelectedFileId(fileId)
          filterRef.current = 'unreviewed'
          setFilter('unreviewed')
          roundViewRef.current = 'review'
          setRoundView('review')
          workspacePanelRef.current = 'checks'
          setWorkspacePanel('checks')
          choose(pending)
        }
        resumeReview.current = false
      }
      if (!selectedFileIdRef.current) {
        const fileId = result.inputs.find(item => item.role === 'target')?.file_version_id || result.inputs[0]?.file_version_id || ''
        selectedFileIdRef.current = fileId
        setSelectedFileId(fileId)
        const nextFilter = result.findings.some(item => item.locator?.file_version_id === fileId && item.status !== 'PASS') ? 'attention' : ''
        filterRef.current = nextFilter
        setFilter(nextFilter)
        choose(result.findings.filter(item => item.locator?.file_version_id === fileId && matchesFilter(item, nextFilter))[0])
      }
      if (!selectedKeyRef.current && result.findings.length && !reviewDirtyRef.current) {
        const fileId = selectedFileIdRef.current
        const nextFilter = result.findings.some(item => item.locator?.file_version_id === fileId && item.status !== 'PASS') ? 'attention' : ''
        filterRef.current = nextFilter
        setFilter(nextFilter)
        choose(result.findings.filter(item => item.locator?.file_version_id === fileId && matchesFilter(item, nextFilter))[0])
      }
      if (['queued', 'running'].includes(result.execution)) later(() => { void loadRound(id) })
    } catch (e) { setError(e instanceof Error ? e.message : '无法加载审核') }
  }
  async function submit() {
    await action(async () => {
      await saveDraft()
      const key = `ppap-idempotency-${draftIdRef.current}`
      const id = sessionStorage.getItem(key) || crypto.randomUUID()
      sessionStorage.setItem(key, id)
      const result = await request<Round>(`/drafts/${draftIdRef.current}/accept`, 'POST', {idempotency_key: id, predecessor_id: predecessorIdRef.current || null})
      query({round: result.id, draft: null, from: null})
      setPredecessorId('')
      predecessorIdRef.current = ''
      activeRoundId.current = result.id
      await loadRound(result.id)
    })
  }
  async function saveReview() {
    if (!reviewRef.current.judgment.trim()) return
    await action(async () => {
      const current = roundRef.current
      const finding = current?.findings.find(item => item.item_key === selectedKeyRef.current)
      if (!current || !finding) return
      setSavingReview(true)
      try {
        const result = await request<Round>(`/rounds/${current.id}/reviews`, 'PUT', {item_key: finding.item_key, ...reviewRef.current, version: finding.review?.version || 0})
        setRound(result)
        roundRef.current = result
        setReviewDirty(false)
        reviewDirtyRef.current = false
        setReviewFeedback('复核已保存')
      } finally { setSavingReview(false) }
    })
  }
  async function resolveChange(save: boolean) {
    if (save) {
      if (roundRef.current) {
        await saveReview()
        if (reviewDirtyRef.current) return
      } else {
        await action(async () => { await saveDraft() })
        if (draftDirtyRef.current) return
      }
    } else {
      choose(roundRef.current?.findings.find(item => item.item_key === selectedKeyRef.current))
      const serialized = JSON.stringify(materialSnapshot())
      setSavedDraft(serialized)
      savedDraftRef.current = serialized
    }
    const work = pendingRef.current
    setPendingChange(null)
    await Promise.resolve()
    work?.()
  }
  function guardNavigation(event: Event) {
    if (!(event instanceof MouseEvent)) return
    const link = (event.target as Element)?.closest<HTMLAnchorElement>('a[href]')
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return
    const url = new URL(link.href)
    if (url.origin !== location.origin || (url.pathname === location.pathname && url.search === location.search && url.hash)) return
    rememberWorkspace()
    if (reviewDirtyRef.current || draftDirtyRef.current) {
      event.preventDefault()
      guardChange(() => { location.href = link.href })
    }
  }
  function prepareAgain() {
    guardChange(() => {
      void action(async () => {
        const current = roundRef.current
        if (!current) return
        rememberWorkspace()
        setReviewDirty(false)
        reviewDirtyRef.current = false
        setSelectedKey('')
        setPreviousRound(current)
        setPredecessorId(current.id)
        predecessorIdRef.current = current.id
        const nextItems = current.inputs.map(item => ({...item}))
        setItems(nextItems)
        itemsRef.current = nextItems
        activeRoundId.current = ''
        setRound(null)
        roundRef.current = null
        setDraftId('')
        draftIdRef.current = ''
        setSelectedFileId('')
        setFilter('attention')
        setRoundView('review')
        await saveDraft(nextItems)
      })
    })
  }
  async function loadDrafts(more = false) {
    setDraftLoading(true)
    try {
      const result = await request<{drafts: Draft[]; next_offset: number | null}>('/drafts?limit=8&offset=' + (more ? draftNext || 0 : 0))
      setDrafts(current => more ? current.concat(result.drafts) : result.drafts)
      setDraftNext(result.next_offset)
    } finally { setDraftLoading(false) }
  }
  async function openRules(item?: Material) {
    await action(async () => {
      if (itemsRef.current.length) await saveDraft()
      const next = new URLSearchParams()
      if (draftIdRef.current) next.set('return_to', '/?draft=' + draftIdRef.current)
      if (item?.rule_version_id) next.set('rule', item.rule_version_id)
      else next.set('new', '1')
      location.href = '/rules?' + next
    })
  }
  async function uploadFiles(files: File[], role: Material['role']) {
    await action(async () => {
      let list = itemsRef.current
      for (const file of files) {
        setMessage('正在上传 ' + file.name)
        const material = await upload(file, role)
        list = list.concat(material)
        setItems(list)
        itemsRef.current = list
        await saveDraft(list)
        void analyze(material)
      }
      setMessage('')
    })
  }
  async function addFiles(event: ChangeEvent<HTMLInputElement>, role: Material['role']) {
    const files = Array.from(event.target.files || [])
    await uploadFiles(files, role)
    event.target.value = ''
  }
  async function dropped(event: DragEvent) {
    setDragActive(false)
    if (busyRef.current || !event.dataTransfer?.files.length) return
    await uploadFiles(Array.from(event.dataTransfer.files), 'target')
  }
  function chooseFilter(status: string) {
    guardChange(() => {
      const candidates = fileFindingsRef.current.filter(item => matchesFilter(item, status))
      filterRef.current = status
      setFilter(status)
      choose(candidates.find(item => item.item_key === selectedKeyRef.current) || candidates[0])
    })
  }
  function focusFinding() {
    void Promise.resolve().then(() => {
      detailHeading.current?.focus({preventScroll: true})
      detailHeading.current?.scrollIntoView({block: 'nearest'})
    })
  }
  function selectFinding(finding: Finding) {
    if (finding.item_key !== selectedKeyRef.current) guardChange(() => { choose(finding); focusFinding() })
  }
  function nextFinding(direction = 1) {
    const index = findingsRef.current.findIndex(item => item.item_key === selectedKeyRef.current)
    const next = findingsRef.current[index + direction]
    if (next) selectFinding(next)
  }
  async function nextUnreviewed(event?: FormEvent) {
    event?.preventDefault()
    if (reviewDirtyRef.current) {
      await saveReview()
      if (reviewDirtyRef.current) return
    }
    const pending = remaining.find(item => item.item_key !== selectedKeyRef.current && item.status !== 'PASS') || remaining.find(item => item.item_key !== selectedKeyRef.current)
    if (!pending) return
    choose(pending)
    const fileId = pending.locator?.file_version_id || ''
    selectedFileIdRef.current = fileId
    setSelectedFileId(fileId)
    filterRef.current = 'unreviewed'
    setFilter('unreviewed')
    workspacePanelRef.current = 'checks'
    setWorkspacePanel('checks')
    focusFinding()
  }
  function setJudgment(value: string) {
    setCustomJudgment(false)
    setReview(current => ({...current, judgment: value}))
    setReviewDirty(true)
    setReviewFeedback('')
  }
  function pickFinding(event: ChangeEvent<HTMLSelectElement>) {
    const finding = roundRef.current?.findings.find(item => item.item_key === event.target.value)
    event.target.value = selectedKeyRef.current
    if (finding) selectFinding(finding)
  }
  function selectFile(event: ChangeEvent<HTMLSelectElement>) {
    const file = roundRef.current?.inputs.find(item => item.file_version_id === event.target.value)
    event.target.value = selectedFileIdRef.current
    if (file) guardChange(() => {
      const nextFilter = roundRef.current?.findings.some(item => item.locator?.file_version_id === file.file_version_id && item.status !== 'PASS') ? 'attention' : ''
      filterRef.current = nextFilter
      setFilter(nextFilter)
      selectedFileIdRef.current = file.file_version_id
      setSelectedFileId(file.file_version_id)
      workspacePanelRef.current = 'checks'
      setWorkspacePanel('checks')
      choose((roundRef.current?.findings.filter(item => item.locator?.file_version_id === file.file_version_id && matchesFilter(item, nextFilter)) || [])[0])
    })
  }
  function switchView(view: 'review' | 'original' | 'report') {
    const current = roundViewRef.current === 'report' ? 'report' : workspacePanelRef.current === 'original' ? 'original' : 'review'
    viewScroll.current[current] = window.scrollY
    roundViewRef.current = view === 'report' ? 'report' : 'review'
    workspacePanelRef.current = view === 'original' ? 'original' : 'checks'
    setRoundView(roundViewRef.current)
    setWorkspacePanel(workspacePanelRef.current)
    void Promise.resolve().then(() => window.scrollTo({top: viewScroll.current[view]}))
  }
  async function findEvidence() {
    if (matchMedia('(max-width:1023px)').matches) switchView('original')
    await Promise.resolve()
    await originalPreview.current?.findEvidence()
  }
  function matchingHelp(item: Material) {
    if (['queued', 'leased'].includes(item.analysisStatus || '')) return '正在匹配。也可以先手动选择适用规则。'
    if (!currentRules.length) return '规则库暂无规则，请先上传客户规则。'
    if (['failed', 'unknown'].includes(item.analysisStatus || '') || item.analysis?.matching_status === 'failed') return '自动匹配未完成，请手动核对并选择规则。'
    if (item.analysis?.document_type && !currentRules.some(rule => rule.type === item.analysis?.document_type)) return `识别为「${item.analysis.document_type}」，规则库暂无同类型规则。请核对类型或上传适用规则。`
    return '请选择适用规则，或上传新规则。'
  }
  function availableRules(item: Material) {
    return rules.filter(rule => currentRules.some(current => current.id === rule.id) || rule.id === item.rule_version_id)
  }
  function unchanged(item: Material) {
    return previousRound?.inputs.some(previous => previous.file_version_id === item.file_version_id)
  }
  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    resizeStart.current = {x: event.clientX, width: previewWidthRef.current}
    setResizing(true)
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function resizePreview(event: ReactPointerEvent<HTMLDivElement>) {
    if (!resizing || !workspace.current) return
    const bounds = workspace.current.getBoundingClientRect()
    setPreviewWidth(clampPreviewWidth(resizeStart.current.width + (resizeStart.current.x - event.clientX) / bounds.width * 100))
  }
  function resizeKey(event: ReactKeyboardEvent<HTMLDivElement>) {
    const change = event.key === 'ArrowLeft' ? 2 : event.key === 'ArrowRight' ? -2 : 0
    if (change) { event.preventDefault(); setPreviewWidth(width => clampPreviewWidth(width + change)) }
  }

  useEffect(() => {
    disposed.current = false
    window.addEventListener('beforeunload', guardLeave)
    window.addEventListener('pagehide', rememberWorkspace)
    document.addEventListener('click', guardNavigation)
    void action(async () => {
      setRules((await request<{rules: Rule[]}>('/rules')).rules)
      const id = new URLSearchParams(location.search).get('round')
      if (id) await loadRound(id)
      else if (draftIdRef.current) {
        const draft = await request<Draft>(`/drafts/${draftIdRef.current}`)
        setItems(draft.items)
        itemsRef.current = draft.items
        const from = draft.predecessor_id || predecessorIdRef.current
        setPredecessorId(from)
        predecessorIdRef.current = from
        const serialized = JSON.stringify(materialSnapshot(draft.items))
        setSavedDraft(serialized)
        savedDraftRef.current = serialized
        if (from) setPreviousRound(await request<Round>(`/rounds/${from}`))
        for (const item of draft.items) {
          const saved = await request<{analysis: Job<Analysis> | null}>(`/files/${item.file_id}/versions/${item.file_version_id}/analysis`)
          if (saved.analysis) {
            setItems(current => current.map(row => row.file_version_id === item.file_version_id ? {...row, analysisId: saved.analysis!.id} : row))
            void readAnalysis(item.file_version_id, saved.analysis.id)
          }
        }
      } else await loadDrafts()
    }).then(async () => {
      initializedRef.current = true
      setInitialized(true)
      await Promise.resolve()
      if (restoredPosition.current && Number.isFinite(restoredPosition.current.scroll)) window.scrollTo({top: Math.max(0, restoredPosition.current.scroll)})
    })
    return () => {
      rememberWorkspace()
      disposed.current = true
      timers.current.forEach(clearTimeout)
      window.removeEventListener('beforeunload', guardLeave)
      window.removeEventListener('pagehide', rememberWorkspace)
      document.removeEventListener('click', guardNavigation)
    }
  }, [])

  const findingIndex = findings.findIndex(item => item.item_key === selectedKey)

  return (
    <>
      {error && (!round || roundView === 'report') && <p className="error" role="alert">{error}</p>}
      {message && <p className="notice" role="status">{message}</p>}
      {!initialized && <p className="loading-state" role="status">正在加载资料…</p>}
      {initialized && !round && (
        <div id="audit-preparation">
          {predecessorId && <div className="context-bar"><span>再次审核</span><a href={'/?round=' + predecessorId}>查看上一轮 →</a></div>}
          <section className={'preparation-upload' + (items.length ? ' has-materials' : '')}>
            {!items.length && (
              <div className={'audit-dropzone' + (dragActive ? ' is-dragging' : '')} onDragOver={event => { event.preventDefault(); setDragActive(true) }} onDragLeave={event => { event.preventDefault(); setDragActive(false) }} onDrop={event => { event.preventDefault(); void dropped(event) }}>
                <h2>添加待审资料</h2>
                <p className="pc-muted">拖入文件，或选择本地资料</p>
                <label className="file-button button pc-primary">选择文件<input type="file" multiple disabled={busy} aria-label="选择待审文件" data-testid="source-upload" onChange={event => void addFiles(event, 'target')} /></label>
                <small className="pc-muted">Excel、Word、PDF · 单份最大 64 MB</small>
              </div>
            )}
            <div className="upload-actions">
              {!!items.length && <label className="file-button button">添加待审文件<input type="file" multiple disabled={busy} aria-label="添加待审文件" data-testid="source-upload" onChange={event => void addFiles(event, 'target')} /></label>}
              <label className="file-button button">添加参考资料<input type="file" multiple disabled={busy} aria-label="添加参考资料" data-testid="reference-upload" onChange={event => void addFiles(event, 'reference')} /></label>
              <DrivePicker disabled={busy} onImport={imported} onBusy={value => { busyRef.current = value; setBusy(value) }} />
            </div>
          </section>
          {(items.length || draftDirty) && (
            <section className="materials-section" id="draft-items" data-testid="draft-items">
              <div className="section-heading"><h2>本次资料 <span className="count">{items.length}</span></h2><button type="button" className="text-button" disabled={busy} onClick={() => void openRules()}>上传客户规则</button></div>
              {items.map(item => (
                <article key={item.file_version_id} className="audit-material">
                  <div className="material-name">
                    <strong>{item.original_filename}</strong>
                    {previousRound && <span className="badge">{unchanged(item) ? '沿用原件' : '本轮新增'}</span>}
                    {item.analysis && <small>{[item.analysis.metadata.customer, item.analysis.metadata.model, item.analysis.metadata.part].filter(Boolean).join(' · ')}</small>}
                    {['queued', 'leased'].includes(item.analysisStatus || '') && <small role="status">正在识别并匹配…</small>}
                    {['failed', 'unknown'].includes(item.analysisStatus || '') && <small>自动匹配未完成，请手动选择。</small>}
                    {(item.analysis?.notices || []).filter(notice => notice !== 'metadata_uses_excerpt').map(notice => <small key={notice}>{analysisNotices[notice] || notice}</small>)}
                  </div>
                  <label>用途
                    <select value={item.role} disabled={busy} onChange={event => setItems(current => current.map(row => row.file_version_id === item.file_version_id ? {...row, role: event.target.value as Material['role'], rule_version_id: null} : row))}>
                      <option value="target">待审文件</option>
                      <option value="reference">参考资料</option>
                    </select>
                  </label>
                  {item.role === 'target' ? (
                    <div className="material-rule">
                      <label>客户规则参考
                        <select data-testid="item-rule" value={item.rule_version_id || ''} disabled={busy} aria-label={item.original_filename + '的匹配规则'} onChange={event => setItems(current => current.map(row => row.file_version_id === item.file_version_id ? {...row, rule_version_id: event.target.value || null} : row))}>
                          <option value="">选择匹配规则</option>
                          {availableRules(item).map(rule => <option key={rule.id} value={rule.id}>{rule.title}{currentRules.some(current => current.id === rule.id) ? '' : '（历史版本）'}</option>)}
                        </select>
                      </label>
                      <div className="material-rule-meta">
                        {item.rule_version_id && <span>{rules.find(rule => rule.id === item.rule_version_id)?.type || '按规则类型'}</span>}
                        {item.rule_version_id && <button type="button" className="text-button" disabled={busy} onClick={() => void openRules(item)}>查看规则</button>}
                      </div>
                      {!item.rule_version_id && <p className="matching-help">{matchingHelp(item)}</p>}
                    </div>
                  ) : <span className="pc-muted material-rule">仅作参考，不单独检查</span>}
                  <button type="button" className="icon-button material-remove" disabled={busy} title="移除资料" aria-label={'移除 ' + item.original_filename} onClick={() => setItems(current => current.filter(row => row.file_version_id !== item.file_version_id))}><X size={18} aria-hidden="true" /></button>
                </article>
              ))}
              {!!targetCount && <div className="scope-note scope-inline">客户规则用于人工复核<Help title="审核范围"><p>自动检查按所选文档类型执行；客户规则中的自定义条目供人工复核。</p></Help></div>}
              <div className="action-bar sticky-actions">
                <div>
                  <strong>{targetCount} 份待审</strong>
                  {!!missingRules && <span className="status-NG"> · {missingRules} 份未匹配规则</span>}
                  <small className="pc-muted">{draftDirty ? '有未保存的修改' : '草稿已保存'}</small>
                </div>
                <div className="button-group">
                  <button type="button" data-testid="save-draft" disabled={busy || !draftDirty} onClick={() => void action(async () => { await saveDraft(); setMessage('草稿已保存。') })}>保存草稿</button>
                  <button type="button" className="pc-primary" data-testid="submit-audit" id={predecessorId ? 'resubmit-audit' : undefined} disabled={busy || !targetCount || !!missingRules} onClick={() => void submit()}>{busy ? '处理中…' : '开始审核'}</button>
                </div>
              </div>
            </section>
          )}
          {!items.length && !!drafts.length && (
            <section className="recent-drafts">
              <div className="section-heading"><h2>继续草稿</h2></div>
              {drafts.map(draft => (
                <a key={draft.id} className="draft-row" href={'/?draft=' + draft.id}>
                  <div>
                    <strong>{draft.items.filter(item => item.role === 'target').map(item => item.original_filename).join('、') || draft.items.map(item => item.original_filename).join('、')}</strong>
                    <small>{draft.items.length} 份资料 · {dateLabel(draft.updated_at)}{draft.predecessor_id ? ' · 再次审核' : ''}</small>
                  </div>
                  <span>继续 →</span>
                </a>
              ))}
              {draftNext !== null && <button type="button" className="load-more" disabled={draftLoading} onClick={() => void action(() => loadDrafts(true))}>更多草稿</button>}
            </section>
          )}
        </div>
      )}
      {initialized && round && (
        <div className="round-page">
          <div className="round-heading">
            <div className="round-context">
              <a className="back-link" href="/audit-files" data-testid="open-history">← 审核结果</a>
              <span className="round-status">{executionLabels[round.execution] || round.execution}</span>
              <span className="round-date">{round.inputs.filter(file => file.role === 'target').length} 份资料 · {dateLabel(round.created_at)}</span>
            </div>
            <div className="button-group">
              {round.predecessor_id && <a className="text-button button" data-testid="previous-round" href={'/?round=' + round.predecessor_id}>上一轮</a>}
              <button type="button" className="text-button" data-testid="prepare-again" disabled={busy || ['queued', 'running'].includes(round.execution)} onClick={prepareAgain}>修改后重审</button>
            </div>
          </div>
          {!!round.findings.length && roundView !== 'report' && (
            <div className="result-summary" aria-label="本轮机器检查结果" data-testid="result-summary">
              {counts.map(count => <span key={count.status} className={count.count ? 'status-' + count.status : 'pc-muted'}><strong>{count.count}</strong>{resultLabels[count.status]}</span>)}
              <span className="review-progress">复核 {reviewed} / {round.findings.length}<progress value={reviewed} max={round.findings.length} aria-label="已记录人工复核" /></span>
            </div>
          )}
          <nav className="view-tabs" aria-label="审核视图">
            <button type="button" aria-pressed={roundView === 'review' && workspacePanel === 'checks'} onClick={() => switchView('review')}>检查与复核</button>
            <button type="button" className="original-tab" aria-pressed={roundView === 'review' && workspacePanel === 'original'} onClick={() => switchView('original')}>原件</button>
            <button type="button" aria-pressed={roundView === 'report'} onClick={() => switchView('report')}>审核报告</button>
          </nav>
          <div hidden={roundView !== 'review'}>
            <div className="workspace-heading">
              <label>
                <span className="sr-only">审核资料</span>
                <select data-testid="file-version-list" title={selectedFile?.original_filename} value={selectedFileId} disabled={busy} onChange={selectFile}>
                  {round.inputs.map(file => <option key={file.file_version_id} value={file.file_version_id}>{file.original_filename}{file.role === 'reference' ? ' · 参考资料' : ''}</option>)}
                </select>
              </label>
              {selectedFile?.rule_version_id && <a className="context-link" href={'/rules?rule=' + selectedFile.rule_version_id + '&return_to=' + encodeURIComponent('/?round=' + round.id)}>客户规则参考 ↗</a>}
            </div>
            <div className="sr-only" data-testid="coverage-panel">{round.inputs.map(file => file.original_filename).join(' ')}</div>
            {workspacePanel === 'original' && selectedFinding && (
              <div className="mobile-finding-context">
                <strong>{selectedFinding.item}</strong>
                <button type="button" onClick={() => switchView('review')}>返回复核</button>
              </div>
            )}
            <div ref={workspace} className={'audit-workspace' + (resizing ? ' is-resizing' : '')} data-panel={workspacePanel} style={{['--preview-width' as string]: previewWidth + '%'}}>
              <section className="audit-checks">
                {selectedFile?.role === 'reference' ? (
                  <div className="empty-state"><h3>参考资料</h3><p>用于对照，不包含在机器检查结果中。</p><button type="button" className="mobile-original-link" onClick={() => switchView('original')}>查看原件</button></div>
                ) : (
                  <>
                    {error && <p className="error" role="alert">{error}</p>}
                    <nav className="audit-filters" aria-label="结果筛选">
                      {filters.map(entry => <button type="button" key={entry.id || 'all'} data-testid={'filter-' + (entry.id || 'all')} disabled={busy} aria-pressed={filter === entry.id} onClick={() => chooseFilter(entry.id)}>{entry.label} <span>{fileFindings.filter(item => matchesFilter(item, entry.id)).length}</span></button>)}
                    </nav>
                    {!findings.length && (
                      <div className="empty-state compact">
                        <h3>{['queued', 'running'].includes(round.execution) ? '正在检查资料…' : round.execution === 'failed' ? '本次检查未完成' : '当前筛选下没有检查项'}</h3>
                        {['queued', 'running'].includes(round.execution)
                          ? <p className="pc-muted">可以离开页面，稍后从审核结果继续查看。</p>
                          : fileFindings.length ? <button type="button" onClick={() => chooseFilter('')}>查看全部检查项</button> : null}
                      </div>
                    )}
                    {!!findings.length && (
                      <div className="finding-navigation">
                        <label className="finding-picker">
                          <span className="sr-only">检查项</span>
                          <select data-testid="finding-picker" value={findings.some(item => item.item_key === selectedKey) ? selectedKey : ''} disabled={busy} onChange={pickFinding}>
                            {!findings.some(item => item.item_key === selectedKey) && <option value="" disabled>当前项已保存，可继续下一项</option>}
                            {findings.map((finding, index) => <option key={finding.item_key} value={finding.item_key}>{index + 1}. {finding.item} · {resultLabels[finding.status]}{finding.review ? ' · 已记录' : ''}</option>)}
                          </select>
                        </label>
                        <div className="finding-arrows">
                          <button type="button" className="icon-button" aria-label="上一检查项" title="上一检查项" disabled={busy || findingIndex <= 0} onClick={() => nextFinding(-1)}><ArrowLeft size={18} aria-hidden="true" /></button>
                          <button type="button" className="icon-button" aria-label="下一检查项" title="下一检查项" disabled={busy || findingIndex >= findings.length - 1} onClick={() => nextFinding()}><ArrowRight size={18} aria-hidden="true" /></button>
                        </div>
                      </div>
                    )}
                    {selectedFinding && (
                      <article className="finding-detail">
                        <div className="section-heading">
                          <span className={'badge status-' + selectedFinding.status} data-testid="finding-status" data-status={selectedFinding.status}>机器检查 · {resultLabels[selectedFinding.status]}</span>
                          <span className="pc-muted finding-position">{fileFindings.findIndex(item => item.item_key === selectedKey) + 1} / {fileFindings.length}</span>
                        </div>
                        <h3 ref={detailHeading} tabIndex={-1}>{selectedFinding.item}</h3>
                        <p className="finding-description" data-testid="finding-suggestion">{selectedFinding.suggestions}</p>
                        <div className="evidence" data-testid="finding-evidence">
                          <div>{selectedFinding.locator?.locator || <span data-testid="locator-gap">定位线索 · 待核实</span>}</div>
                          <button type="button" className="text-button" onClick={() => void findEvidence()}><Search size={16} aria-hidden="true" />在原件中查找</button>
                        </div>
                        <form className="audit-review" onSubmit={event => void nextUnreviewed(event)}>
                          <div className="section-heading"><div className="scope-inline"><h3>人工复核</h3><Help title="复核与自动检查"><p>记录复核不等于问题已解决，也不改变机器结论。客户规则中的自定义要求需另行人工核对。</p></Help></div><span className="review-save-state" role="status">{savingReview ? '正在保存…' : reviewDirty ? '未保存' : selectedFinding.review ? '已保存' : ''}</span></div>
                          <fieldset className="plain-fieldset" disabled={busy}>
                            <div className="judgment-options" aria-label="复核判断">
                              {reviewJudgments.map(value => <button type="button" key={value} aria-pressed={!customJudgment && review.judgment === value} onClick={() => setJudgment(value)}>{value}</button>)}
                              <button type="button" aria-pressed={customJudgment} onClick={() => setCustomJudgment(true)}>其他判断</button>
                            </div>
                            {customJudgment && <label>判断<input data-testid="review-judgment" value={review.judgment} placeholder="填写你的复核判断" onChange={event => { setReview(current => ({...current, judgment: event.target.value})); setReviewDirty(true) }} /></label>}
                            <label>理由<textarea data-testid="review-rationale" value={review.rationale} rows={3} onChange={event => { setReview(current => ({...current, rationale: event.target.value})); setReviewDirty(true) }} /></label>
                            <label>下一步<textarea data-testid="review-next" value={review.next_step} rows={2} onChange={event => { setReview(current => ({...current, next_step: event.target.value})); setReviewDirty(true) }} /></label>
                            <div className="review-actions">
                              <span className="review-save-state" role="status">{reviewFeedback || (selectedFinding.review && !reviewDirty ? '已记录此项' : '')}</span>
                              <div className="button-group">
                                {reviewDirty && hasNextUnreviewed && <button type="button" data-testid="save-review" disabled={!review.judgment.trim()} onClick={() => void saveReview()}>仅保存</button>}
                                <button type="submit" className="pc-primary" data-testid={reviewDirty && !hasNextUnreviewed ? 'save-review' : 'save-and-continue'} disabled={reviewDirty ? !review.judgment.trim() : !hasNextUnreviewed}>{savingReview ? '正在保存…' : reviewDirty ? (hasNextUnreviewed ? '保存并继续' : '保存复核') : hasNextUnreviewed ? '下一未记录项 →' : '复核已记录'}</button>
                              </div>
                            </div>
                          </fieldset>
                        </form>
                      </article>
                    )}
                  </>
                )}
              </section>
              <div className="preview-resizer" role="separator" tabIndex={0} aria-label="调整原件预览宽度" aria-orientation="vertical" aria-valuenow={Math.round(previewWidth)} aria-valuemin={30} aria-valuemax={65} onPointerDown={startResize} onPointerMove={resizePreview} onPointerUp={() => setResizing(false)} onPointerCancel={() => setResizing(false)} onLostPointerCapture={() => setResizing(false)} onKeyDown={resizeKey} />
              <OriginalPreview ref={originalPreview} file={selectedFile} roundId={round.id} finding={selectedFinding} />
            </div>
          </div>
          {roundView === 'report' && <ReportPreview round={round} unsavedReview={reviewDirty} onReview={() => switchView('review')} />}
        </div>
      )}
      {pendingChange && (
        <Modal title="保存修改后离开？" busy={busy} onClose={() => setPendingChange(null)}>
          <p>{round ? '这条复核还有未保存的修改。' : '审核资料还有未保存的修改。'}你可以保存后继续，也可以留在这里编辑。</p>
          {error && <p className="error" role="alert">{error}</p>}
          {round && !review.judgment.trim() && <p className="pc-muted">请先填写复核判断，才能保存。</p>}
          <div className="leave-actions">
            <button type="button" disabled={busy} onClick={() => setPendingChange(null)}>继续编辑</button>
            <button type="button" disabled={busy} onClick={() => void resolveChange(false)}>放弃修改</button>
            <button type="button" className="pc-primary" disabled={busy || (!!round && !review.judgment.trim())} onClick={() => void resolveChange(true)}>{busy ? '正在保存…' : '保存并离开'}</button>
          </div>
        </Modal>
      )}
    </>
  )
}
