import {useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent} from 'react'
import {
  deepOffered,
  request,
  upload,
  UploadResultUnknown,
  type AnalysisJob,
  type AnalysisMode,
  type Draft,
  type DraftList,
  type FileVersion,
  type LatestAnalysis,
  type Material,
  type QuotaEstimate,
  type Round,
  type Rule,
  type RulesList,
} from '../api'
import {latestRules} from '../helpers'
import {parseProductRoute, productHref} from '../navigation/route'
import {beginDraftAccept, beginDraftSave, finishDraft, type DraftPhase} from '../workflow/submission'

type Action = (work: () => Promise<void>) => Promise<void>

function materialSnapshot(list: Material[]) {
  return list.map(item => ({
    file_version_id: item.file_version_id,
    role: item.role,
    rule_version_id: item.role === 'target' ? item.rule_version_id : null,
  }))
}

export function useDraftPreparation({
  inReview,
  action,
  setMessage,
  isBusy,
}: {
  inReview: boolean
  action: Action
  setMessage: (message: string) => void
  isBusy: () => boolean
}) {
  const [items, setItems] = useState<Material[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const initialRoute = parseProductRoute(location.pathname, location.search)
  const [draftId, setDraftId] = useState(initialRoute.page === 'audit-new' ? initialRoute.draftId || '' : '')
  const [predecessorId, setPredecessorId] = useState(initialRoute.page === 'audit-new' ? initialRoute.fromRoundId || '' : '')
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [draftNext, setDraftNext] = useState<number | null>(null)
  const [draftLoading, setDraftLoading] = useState(false)
  const [savedDraft, setSavedDraft] = useState(JSON.stringify({items: [], analysis_mode: 'standard'}))
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>('standard')
  const [estimate, setEstimate] = useState<QuotaEstimate | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [pendingUpload, setPendingUpload] = useState<{material: Material; remaining: number} | null>(null)
  const [draftPhase, setDraftPhase] = useState<DraftPhase>('idle')

  const itemsRef = useRef(items)
  const draftIdRef = useRef(draftId)
  const predecessorIdRef = useRef(predecessorId)
  const analysisModeRef = useRef(analysisMode)
  const draftPhaseRef = useRef<DraftPhase>('idle')
  const disposed = useRef(false)
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>())

  itemsRef.current = items
  draftIdRef.current = draftId
  predecessorIdRef.current = predecessorId
  analysisModeRef.current = analysisMode

  useEffect(() => {
    disposed.current = false
    return () => {
      disposed.current = true
      timers.current.forEach(clearTimeout)
    }
  }, [])

  const currentRules = useMemo(() => latestRules(rules), [rules])
  const targetCount = items.filter(item => item.role === 'target').length
  const missingRules = items.filter(item => item.role === 'target' && !item.rule_version_id).length

  function draftSnapshot(list = itemsRef.current, mode = analysisModeRef.current) {
    return {items: materialSnapshot(list), analysis_mode: mode}
  }

  const draftDirty = !inReview && JSON.stringify(draftSnapshot(items, analysisMode)) !== savedDraft
  useEffect(() => {
    if (inReview || targetCount < 1) {
      setEstimate(null)
      return
    }
    const ac = new AbortController()
    void request<QuotaEstimate>(`/integrations/feishu/usage/quota?target_count=${targetCount}`, 'GET', undefined, ac.signal)
      .then(body => { if (!ac.signal.aborted) setEstimate(body) })
      .catch(() => { if (!ac.signal.aborted) setEstimate({unavailable: true}) })
    return () => ac.abort()
  }, [inReview, targetCount])

  function later(work: () => void) {
    const timer = setTimeout(() => {
      timers.current.delete(timer)
      if (!disposed.current) work()
    }, 2000)
    timers.current.add(timer)
  }

  function showDraft(id: string) {
    history.replaceState(null, '', productHref({
      page: 'audit-new',
      draftId: id,
      fromRoundId: predecessorIdRef.current || undefined,
    }))
  }

  async function ensureRules() {
    if (rules.length) return
    setRules((await request<RulesList>('/rules')).rules)
  }

  function claimDraft(next: DraftPhase | null) {
    if (!next || next === draftPhaseRef.current) return false
    draftPhaseRef.current = next
    setDraftPhase(next)
    return true
  }

  function releaseDraft() {
    const next = finishDraft(draftPhaseRef.current)
    draftPhaseRef.current = next
    setDraftPhase(next)
  }

  async function persistDraft(list = itemsRef.current) {
    let id = draftIdRef.current
    if (!id) {
      id = (await request<Draft>('/drafts', 'POST', {predecessor_id: predecessorIdRef.current || null})).id
      setDraftId(id)
      draftIdRef.current = id
    }
    const snapshot = draftSnapshot(list)
    await request(`/drafts/${id}`, 'PUT', snapshot)
    const serialized = JSON.stringify(snapshot)
    setSavedDraft(serialized)
    showDraft(id)
  }

  async function saveDraft(list = itemsRef.current) {
    if (!claimDraft(beginDraftSave(draftPhaseRef.current))) return false
    try {
      await persistDraft(list)
      return true
    } finally {
      if (draftPhaseRef.current === 'saving') releaseDraft()
    }
  }

  async function readAnalysis(versionId: string, id: string) {
    try {
      const job = await request<AnalysisJob>(`/file-analyses/${id}`)
      if (disposed.current) return
      setItems(current => current.map(item => {
        if (item.file_version_id !== versionId) return item
        const next = {...item, analysisStatus: job.status, analysisReadFailed: false}
        if (job.status === 'succeeded' && job.result && !('error' in job.result)) {
          next.analysis = job.result
          if (item.role === 'target' && !item.rule_version_id) next.rule_version_id = job.result.matched_rule_id
        }
        return next
      }))
      if (['queued', 'leased'].includes(job.status)) later(() => { void readAnalysis(versionId, id) })
    } catch {
      if (!disposed.current) setItems(current => current.map(item => item.file_version_id === versionId ? {...item, analysisReadFailed: true} : item))
    }
  }

  async function reloadAnalysis(item: Material) {
    try {
      const saved = await request<LatestAnalysis>(`/files/${item.file_id}/versions/${item.file_version_id}/analysis`)
      if (disposed.current) return
      setItems(current => current.map(row => row.file_version_id === item.file_version_id
        ? {...row, analysisId: saved.analysis?.id, analysisReadFailed: false} : row))
      if (saved.analysis) await readAnalysis(item.file_version_id, saved.analysis.id)
    } catch {
      if (!disposed.current) setItems(current => current.map(row => row.file_version_id === item.file_version_id
        ? {...row, analysisReadFailed: true} : row))
    }
  }

  async function analyze(item: Material) {
    setItems(current => current.map(row => row.file_version_id === item.file_version_id ? {...row, analysisStatus: 'queued'} : row))
    try {
      const job = await request<AnalysisJob>('/file-analyses', 'POST', {
        request_id: crypto.randomUUID(),
        source_file_id: item.file_id,
        source_file_version_id: item.file_version_id,
      })
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

  async function loadDrafts(more = false) {
    setDraftLoading(true)
    try {
      const result = await request<DraftList>('/drafts?limit=8&offset=' + (more ? draftNext || 0 : 0))
      setDrafts(current => more ? current.concat(result.drafts) : result.drafts)
      setDraftNext(result.next_offset)
    } finally {
      setDraftLoading(false)
    }
  }

  async function initialize() {
    await ensureRules()
    if (draftIdRef.current) {
      const draft = await request<Draft>(`/drafts/${draftIdRef.current}`)
      setItems(draft.items)
      itemsRef.current = draft.items
      const nextMode = draft.analysis_mode === 'deep' && deepOffered ? 'deep' : 'standard'
      setAnalysisMode(nextMode)
      analysisModeRef.current = nextMode
      const from = draft.predecessor_id || predecessorIdRef.current
      setPredecessorId(from)
      predecessorIdRef.current = from
      const serialized = JSON.stringify(draftSnapshot(draft.items, nextMode))
      setSavedDraft(serialized)
      void Promise.all(draft.items.map(reloadAnalysis))
      return
    }
    await loadDrafts()
  }

  async function acceptDraft() {
    if (!claimDraft(beginDraftAccept(draftPhaseRef.current))) {
      throw new Error('草稿正在保存，请稍后再提交。')
    }
    try {
      await persistDraft()
      const key = `ppap-idempotency-${draftIdRef.current}`
      const idempotencyKey = sessionStorage.getItem(key) || crypto.randomUUID()
      sessionStorage.setItem(key, idempotencyKey)
      const result = await request<Round>(`/drafts/${draftIdRef.current}/accept`, 'POST', {
        idempotency_key: idempotencyKey,
        predecessor_id: predecessorIdRef.current || null,
      })
      history.replaceState(null, '', productHref({page: 'audit-round', roundId: result.id}))
      setPredecessorId('')
      predecessorIdRef.current = ''
      return result
    } finally {
      releaseDraft()
    }
  }

  async function startReaudit(current: Round) {
    await ensureRules()
    setPredecessorId(current.id)
    predecessorIdRef.current = current.id
    const nextItems = current.inputs.map(item => ({...item}))
    setItems(nextItems)
    itemsRef.current = nextItems
    const nextMode = current.analysis_mode === 'deep' && deepOffered ? 'deep' : 'standard'
    setAnalysisMode(nextMode)
    analysisModeRef.current = nextMode
    setDraftId('')
    draftIdRef.current = ''
    await saveDraft(nextItems)
  }

  async function openRules(item?: Material) {
    await action(async () => {
      if (itemsRef.current.length) await saveDraft()
      const returnTo = draftIdRef.current ? {page: 'audit-new' as const, draftId: draftIdRef.current} : undefined
      location.href = productHref(item?.rule_version_id
        ? {page: 'rule-detail', ruleId: item.rule_version_id, returnTo}
        : {page: 'rule-import', returnTo})
    })
  }

  async function uploadFiles(files: File[], role: Material['role']) {
    if (pendingUpload) return
    await action(async () => {
      try {
        let list = itemsRef.current
        for (const [index, file] of files.entries()) {
          setMessage('正在上传 ' + file.name)
          let material: Material
          try {
            material = await upload(file, role)
          } catch (error) {
            if (error instanceof UploadResultUnknown) {
              setPendingUpload({material: error.material, remaining: files.length - index - 1})
              throw error
            }
            if (error instanceof TypeError) throw new Error(`上传失败：${file.name}。请检查网络后重新选择文件。`)
            throw error
          }
          list = list.concat(material)
          setItems(list)
          itemsRef.current = list
          await saveDraft(list)
          void analyze(material)
        }
      } finally {
        setMessage('')
      }
    })
  }

  async function checkPendingUpload() {
    if (!pendingUpload) return
    await action(async () => {
      const material = pendingUpload.material
      let status: string
      try {
        status = (await request<FileVersion>(`/files/${material.file_id}/versions/${material.file_version_id}`)).status
      } catch {
        throw new Error(`暂时无法读取 ${material.original_filename} 的上传结果，请稍后再次核对。`)
      }
      if (status !== 'ready') throw new Error(`${material.original_filename} 仍未就绪，请稍后再次核对。`)
      const list = itemsRef.current.some(item => item.file_version_id === material.file_version_id)
        ? itemsRef.current : itemsRef.current.concat(material)
      setItems(list)
      itemsRef.current = list
      await saveDraft(list)
      setPendingUpload(null)
      void analyze(material)
    })
  }

  function discardPendingUpload() {
    setPendingUpload(null)
  }

  async function addFiles(event: ChangeEvent<HTMLInputElement>, role: Material['role']) {
    const files = Array.from(event.target.files || [])
    await uploadFiles(files, role)
    event.target.value = ''
  }

  async function dropped(event: DragEvent) {
    setDragActive(false)
    if (isBusy() || pendingUpload || !event.dataTransfer?.files.length) return
    await uploadFiles(Array.from(event.dataTransfer.files), 'target')
  }

  function setRole(versionId: string, role: Material['role']) {
    setItems(current => current.map(row => row.file_version_id === versionId ? {...row, role, rule_version_id: null} : row))
  }

  function setRule(versionId: string, ruleVersionId: string | null) {
    setItems(current => current.map(row => row.file_version_id === versionId ? {...row, rule_version_id: ruleVersionId} : row))
  }

  function removeItem(versionId: string) {
    setItems(current => current.filter(row => row.file_version_id !== versionId))
  }

  function setMode(value: string) {
    setAnalysisMode(value === 'deep' ? 'deep' : 'standard')
  }

  function matchingHelp(item: Material) {
    if (['queued', 'leased'].includes(item.analysisStatus || '')) return '匹配中，可手动选择'
    if (!currentRules.length) return '规则库为空'
    if (['failed', 'unknown'].includes(item.analysisStatus || '') || item.analysis?.matching_status === 'failed') return '匹配失败，请手动选择'
    if (item.analysis?.document_type && !currentRules.some(rule => rule.type === item.analysis?.document_type)) return `无「${item.analysis.document_type}」规则`
    return '未匹配规则'
  }

  function availableRules(item: Material) {
    return rules.filter(rule => currentRules.some(current => current.id === rule.id) || rule.id === item.rule_version_id)
  }

  function markCurrentClean() {
    const serialized = JSON.stringify(draftSnapshot())
    setSavedDraft(serialized)
  }

  return {
    items,
    rules,
    currentRules,
    draftId,
    predecessorId,
    drafts,
    draftNext,
    draftLoading,
    analysisMode,
    estimate,
    dragActive,
    pendingUpload,
    draftPhase,
    draftDirty,
    targetCount,
    missingRules,
    setDragActive,
    initialize,
    saveDraft,
    acceptDraft,
    startReaudit,
    imported,
    loadDrafts,
    openRules,
    addFiles,
    checkPendingUpload,
    discardPendingUpload,
    dropped,
    setRole,
    setRule,
    removeItem,
    setMode,
    matchingHelp,
    reloadAnalysis,
    availableRules,
    markCurrentClean,
  }
}
