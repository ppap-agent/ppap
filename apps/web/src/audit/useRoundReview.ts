import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import {request, type Finding, type Round} from '../api'
import {clampPreviewWidth, matchesFilter, parseWorkspacePosition, reviewJudgments, workspaceStorageKey} from '../helpers'
import {parseProductRoute} from '../navigation/route'
import type {OriginalPreviewHandle} from '../OriginalPreview'
import {isCompactViewport} from '../ui'
import {beginReviewSave, markReviewClean, markReviewDirty, type ReviewPhase} from '../workflow/submission'

type Action = (work: () => Promise<void>) => Promise<void>
export type ReviewDraft = {judgment: string; rationale: string; next_step: string}

export function useRoundReview({
  action,
  setError,
  setMessage,
}: {
  action: Action
  setError: (message: string) => void
  setMessage: (message: string) => void
}) {
  const [round, setRound] = useState<Round | null>(null)
  const [selectedFileId, setSelectedFileId] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [filter, setFilter] = useState('attention')
  const [review, setReview] = useState<ReviewDraft>({judgment: '', rationale: '', next_step: ''})
  const [reviewPhase, setReviewPhase] = useState<ReviewPhase>('clean')
  const [customJudgment, setCustomJudgment] = useState(false)
  const [reviewFeedback, setReviewFeedback] = useState('')
  const [workspacePanel, setWorkspacePanel] = useState('checks')
  const [roundView, setRoundView] = useState('review')
  const [previewWidth, setPreviewWidth] = useState(50)
  const [resizing, setResizing] = useState(false)

  const originalPreview = useRef<OriginalPreviewHandle>(null)
  const workspace = useRef<HTMLDivElement>(null)
  const detailHeading = useRef<HTMLHeadingElement>(null)
  const initialRoute = parseProductRoute(location.pathname, location.search)
  const resumeReview = useRef(initialRoute.page === 'audit-round' && initialRoute.review === 'next')
  const activeRoundId = useRef(initialRoute.page === 'audit-round' ? initialRoute.roundId : '')
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>())
  const disposed = useRef(false)
  const reviewPhaseRef = useRef(reviewPhase)
  const reviewDirtyRef = useRef(reviewPhase !== 'clean')
  const reviewRef = useRef(review)
  const roundRef = useRef(round)
  const selectedFileIdRef = useRef(selectedFileId)
  const selectedKeyRef = useRef(selectedKey)
  const filterRef = useRef(filter)
  const roundViewRef = useRef(roundView)
  const workspacePanelRef = useRef(workspacePanel)
  const previewWidthRef = useRef(previewWidth)
  const restoredScroll = useRef<number | null>(null)
  const resizeStart = useRef({x: 0, width: 50})
  const viewScroll = useRef({review: 0, original: 0, report: 0})
  const findingsRef = useRef<Finding[]>([])
  const fileFindingsRef = useRef<Finding[]>([])

  reviewDirtyRef.current = reviewPhaseRef.current !== 'clean'
  reviewRef.current = review
  roundRef.current = round
  selectedFileIdRef.current = selectedFileId
  selectedKeyRef.current = selectedKey
  filterRef.current = filter
  roundViewRef.current = roundView
  workspacePanelRef.current = workspacePanel
  previewWidthRef.current = previewWidth

  useEffect(() => {
    disposed.current = false
    return () => {
      disposed.current = true
      timers.current.forEach(clearTimeout)
    }
  }, [])

  const selectedFile = round?.inputs.find(item => item.file_version_id === selectedFileId)
  const fileFindings = round?.findings.filter(item => item.locator?.file_version_id === selectedFileId) || []
  const findings = fileFindings.filter(item => matchesFilter(item, filter))
  const selectedFinding = round?.findings.find(item => item.item_key === selectedKey)
  const counts = ['NG', 'ERR', 'PASS'].map(status => ({status, count: (round?.findings || []).filter(row => row.status === status).length}))
  const reviewed = round?.findings.filter(item => item.review).length || 0
  const remaining = round?.findings.filter(item => !item.review) || []
  const hasNextUnreviewed = remaining.some(item => item.item_key !== selectedKey)
  const findingIndex = findings.findIndex(item => item.item_key === selectedKey)
  findingsRef.current = findings
  fileFindingsRef.current = fileFindings

  function later(work: () => void) {
    const timer = setTimeout(() => {
      timers.current.delete(timer)
      if (!disposed.current) work()
    }, 2000)
    timers.current.add(timer)
  }

  function setPhase(phase: ReviewPhase) {
    reviewPhaseRef.current = phase
    reviewDirtyRef.current = phase !== 'clean'
    setReviewPhase(phase)
  }

  function choose(item?: Finding) {
    const judgment = item?.review?.judgment || ''
    const key = item?.item_key || ''
    selectedKeyRef.current = key
    setSelectedKey(key)
    setReview({judgment, rationale: item?.review?.rationale || '', next_step: item?.review?.next_step || ''})
    if (reviewPhaseRef.current !== 'saving') setPhase(markReviewClean())
    setCustomJudgment(!!judgment && !reviewJudgments.includes(judgment as typeof reviewJudgments[number]))
    setReviewFeedback('')
  }

  function rememberWorkspace() {
    const current = roundRef.current
    if (!current) return
    try {
      sessionStorage.setItem(workspaceStorageKey(current.id), JSON.stringify({
        file: selectedFileIdRef.current,
        finding: selectedKeyRef.current,
        filter: filterRef.current,
        view: roundViewRef.current,
        panel: workspacePanelRef.current,
        width: previewWidthRef.current,
        scroll: window.scrollY,
      }))
    } catch {
      // Browser storage is optional; audit state remains server-backed.
    }
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
      restoredScroll.current = Number.isFinite(saved.scroll) ? Math.max(0, saved.scroll) : null
    } catch {
      // Stale local view state must not block the canonical round.
    }
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
    } catch (error) {
      setError(error instanceof Error ? error.message : '无法加载审核')
    }
  }

  async function openRound(id: string) {
    activeRoundId.current = id
    await loadRound(id)
  }

  function restoreScrollPosition() {
    if (restoredScroll.current !== null) window.scrollTo({top: restoredScroll.current})
    restoredScroll.current = null
  }

  async function saveReview() {
    if (!reviewRef.current.judgment.trim()) return false
    let saved = false
    await action(async () => {
      const next = beginReviewSave(reviewPhaseRef.current)
      if (!next) return
      setPhase(next)
      const current = roundRef.current
      const finding = current?.findings.find(item => item.item_key === selectedKeyRef.current)
      if (!current || !finding) {
        setPhase('dirty')
        return
      }
      try {
        const result = await request<Round>(`/rounds/${current.id}/reviews`, 'PUT', {
          item_key: finding.item_key,
          ...reviewRef.current,
          version: finding.review?.version || 0,
        })
        setRound(result)
        roundRef.current = result
        setPhase(markReviewClean())
        setReviewFeedback('复核已保存')
        saved = true
      } catch (error) {
        if (reviewPhaseRef.current === 'saving') setPhase('dirty')
        throw error
      }
    })
    if (!saved && reviewPhaseRef.current === 'saving') setPhase('dirty')
    return saved
  }

  function discardReview() {
    choose(roundRef.current?.findings.find(item => item.item_key === selectedKeyRef.current))
  }

  async function writeBack() {
    const current = roundRef.current
    if (!current) return
    await action(async () => {
      const result = await request<{inserted?: boolean}>(`/integrations/feishu/rounds/${current.id}/writeback`, 'POST', {})
      setMessage(result.inserted ? '已写回飞书文档。' : '未写回。')
    })
  }

  function chooseFilter(status: string) {
    const candidates = fileFindingsRef.current.filter(item => matchesFilter(item, status))
    filterRef.current = status
    setFilter(status)
    choose(candidates.find(item => item.item_key === selectedKeyRef.current) || candidates[0])
  }

  function focusFinding() {
    void Promise.resolve().then(() => {
      detailHeading.current?.focus({preventScroll: true})
      detailHeading.current?.scrollIntoView({block: 'nearest'})
    })
  }

  function selectFinding(finding: Finding) {
    if (finding.item_key === selectedKeyRef.current) return
    choose(finding)
    focusFinding()
  }

  function selectFindingByKey(key: string) {
    const finding = roundRef.current?.findings.find(item => item.item_key === key)
    if (finding) selectFinding(finding)
  }

  function nextFinding(direction = 1) {
    const index = findingsRef.current.findIndex(item => item.item_key === selectedKeyRef.current)
    const next = findingsRef.current[index + direction]
    if (next) selectFinding(next)
  }

  async function nextUnreviewed() {
    if (reviewDirtyRef.current) {
      await saveReview()
      if (reviewDirtyRef.current) return
    }
    const pending = remaining.find(item => item.item_key !== selectedKeyRef.current && item.status !== 'PASS')
      || remaining.find(item => item.item_key !== selectedKeyRef.current)
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
    setPhase(markReviewDirty(reviewPhaseRef.current))
    setReviewFeedback('')
  }

  function enableCustomJudgment() {
    setCustomJudgment(true)
  }

  function updateReview(field: keyof ReviewDraft, value: string) {
    setReview(current => ({...current, [field]: value}))
    setPhase(markReviewDirty(reviewPhaseRef.current))
    setReviewFeedback('')
  }

  function selectFileById(fileId: string) {
    const file = roundRef.current?.inputs.find(item => item.file_version_id === fileId)
    if (!file) return
    const nextFilter = roundRef.current?.findings.some(item => item.locator?.file_version_id === file.file_version_id && item.status !== 'PASS') ? 'attention' : ''
    filterRef.current = nextFilter
    setFilter(nextFilter)
    selectedFileIdRef.current = file.file_version_id
    setSelectedFileId(file.file_version_id)
    workspacePanelRef.current = 'checks'
    setWorkspacePanel('checks')
    choose((roundRef.current?.findings.filter(item => item.locator?.file_version_id === file.file_version_id && matchesFilter(item, nextFilter)) || [])[0])
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
    if (isCompactViewport()) switchView('original')
    await Promise.resolve()
    await originalPreview.current?.findEvidence()
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
    if (change) {
      event.preventDefault()
      setPreviewWidth(width => clampPreviewWidth(width + change))
    }
  }

  function stopResize() {
    setResizing(false)
  }

  function clearForPreparation() {
    activeRoundId.current = ''
    setRound(null)
    roundRef.current = null
    selectedFileIdRef.current = ''
    setSelectedFileId('')
    selectedKeyRef.current = ''
    setSelectedKey('')
    setFilter('attention')
    filterRef.current = 'attention'
    setRoundView('review')
    roundViewRef.current = 'review'
    setWorkspacePanel('checks')
    workspacePanelRef.current = 'checks'
    setPhase(markReviewClean())
    setReviewFeedback('')
  }

  return {
    round,
    selectedFileId,
    selectedFile,
    selectedKey,
    selectedFinding,
    fileFindings,
    findings,
    counts,
    reviewed,
    hasNextUnreviewed,
    findingIndex,
    filter,
    review,
    reviewDirty: reviewPhase !== 'clean',
    reviewDirtyRef,
    customJudgment,
    savingReview: reviewPhase === 'saving',
    reviewFeedback,
    workspacePanel,
    roundView,
    previewWidth,
    resizing,
    originalPreview,
    workspace,
    detailHeading,
    openRound,
    restoreScrollPosition,
    rememberWorkspace,
    saveReview,
    discardReview,
    writeBack,
    chooseFilter,
    selectFindingByKey,
    nextFinding,
    nextUnreviewed,
    setJudgment,
    enableCustomJudgment,
    updateReview,
    selectFileById,
    switchView,
    findEvidence,
    startResize,
    resizePreview,
    resizeKey,
    stopResize,
    clearForPreparation,
  }
}
