import {useEffect, useRef, useState, type ChangeEvent} from 'react'
import {dateLabel, analysisModeLabels, deepOffered} from './api'
import {analysisNotices, findingShortTitle, matchesFilter} from './helpers'
import {parseProductRoute, productHref} from './navigation/route'
import OriginalPreview from './OriginalPreview'
import DrivePicker from './DrivePicker'
import ReportPreview from './ReportPreview'
import Help from './Help'
import FindingReview from './audit/FindingReview'
import ExecutionState from './audit/ExecutionState'
import RoundOverview from './audit/RoundOverview'
import {FindingBadge} from './audit/StatusBadge'
import {useDraftPreparation} from './audit/useDraftPreparation'
import {useRoundReview} from './audit/useRoundReview'
import {useUnsavedGuard} from './audit/useUnsavedGuard'
import {PageLayout} from './ui/PageLayout'
import {ArrowLeft, ArrowRight, BookOpen, FileSpreadsheet, FileText, Files, Plus, X} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Checkbox} from '@/components/ui/checkbox'
import {Input} from '@/components/ui/input'
import {NativeSelect} from '@/components/ui/native-select'
import {Tabs, TabsList, TabsTrigger} from '@/components/ui/tabs'
import {Modal, DraftLink, Pane, ReviewBack, ReviewColumns, ReviewDetail, ReviewItem, ReviewList, Workspace} from './ui/domain'
import {PageHeader} from './ui/PageLayout'

const filters = [{id: 'attention', label: '需关注'}, {id: 'unreviewed', label: '未记录'}, {id: '', label: '全部'}, {id: 'NG', label: '不符合'}, {id: 'UNABLE', label: '无法判断'}, {id: 'NA', label: '不适用'}, {id: 'ERR', label: '检查异常'}, {id: 'PASS', label: '通过'}]

function materialIcon(filename: string) {
  return /\.(xlsx?|xlsm|csv)$/i.test(filename) ? FileSpreadsheet : FileText
}

export default function Workbench({onReviewing}: {onReviewing: (value: boolean) => void}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [initialized, setInitialized] = useState(false)
  const [mobileDetail, setMobileDetail] = useState(() => {
    const opened = parseProductRoute(location.pathname, location.search)
    return opened.page === 'audit-round'
  })
  const listScroll = useRef(0)
  const busyRef = useRef(false)

  async function action(work: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError('')
    try {
      await work()
    } catch (caught) {
      setError(caught instanceof TypeError && caught.message === 'Failed to fetch'
        ? '网络连接失败，请检查网络后重试。'
        : caught instanceof Error ? caught.message : '操作失败，请重试。')
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  function setExternalBusy(value: boolean) {
    busyRef.current = value
    setBusy(value)
  }

  const reviewModel = useRoundReview({action, setError, setMessage})
  const {
    round,
    selectedFileId,
    selectedFile,
    selectedKey,
    selectedFinding,
    previewFile,
    evidenceSource,
    fileFindings,
    findings,
    counts,
    reviewed,
    hasNextUnreviewed,
    findingIndex,
    filter,
    review,
    reviewDirty,
    customJudgment,
    savingReview,
    reviewFeedback,
    workspacePanel,
    roundView,
    originalPreview,
    detailHeading,
    openRound,
    restoreScrollPosition,
    rememberWorkspace,
    saveReview,
    discardReview,
    writeBack,
    chooseFilter: chooseFilterDirect,
    selectFindingByKey,
    nextFinding: nextFindingDirect,
    nextUnreviewed,
    setJudgment,
    enableCustomJudgment,
    updateReview,
    selectFileById,
    switchView,
    findEvidence,
    clearForPreparation,
  } = reviewModel

  const draftModel = useDraftPreparation({
    inReview: !!round,
    action,
    setMessage,
    isBusy: () => busyRef.current,
  })
  const {
    items,
    rules,
    currentRules,
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
    initialize: initializeDraft,
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
  } = draftModel

  async function saveCurrent() {
    if (round) return saveReview()
    if (pendingUpload) return false
    let saved = false
    await action(async () => {
      await saveDraft()
      saved = true
    })
    return saved
  }

  function discardCurrent() {
    if (round) discardReview()
    else {
      discardPendingUpload()
      markCurrentClean()
    }
  }

  const {
    pendingChange,
    setPendingChange,
    guardChange,
    resolveChange,
  } = useUnsavedGuard({
    dirty: reviewDirty || draftDirty || !!pendingUpload,
    busy,
    remember: rememberWorkspace,
    saveCurrent,
    discardCurrent,
  })

  useEffect(() => {
    onReviewing(!!round)
  }, [round, onReviewing])

  useEffect(() => {
    let active = true
    void action(async () => {
      const opened = parseProductRoute(location.pathname, location.search)
      if (opened.page === 'audit-round') await openRound(opened.roundId)
      else await initializeDraft()
    }).then(async () => {
      if (!active) return
      setInitialized(true)
      await Promise.resolve()
      restoreScrollPosition()
    })
    return () => { active = false }
  }, [])

  async function submit() {
    await action(async () => {
      const accepted = await acceptDraft()
      await openRound(accepted.id, accepted)
      setMobileDetail(true)
    })
  }

  function prepareAgain() {
    guardChange(() => {
      void action(async () => {
        if (!round) return
        rememberWorkspace()
        await startReaudit(round)
        clearForPreparation()
        setMobileDetail(false)
      })
    })
  }

  function chooseFilter(status: string) {
    guardChange(() => { listScroll.current = 0; chooseFilterDirect(status); setMobileDetail(false) })
  }

  function nextFinding(direction = 1) {
    guardChange(() => nextFindingDirect(direction))
  }

  function selectFile(event: ChangeEvent<HTMLSelectElement>) {
    const fileId = event.currentTarget.value
    event.currentTarget.value = selectedFileId
    guardChange(() => { listScroll.current = 0; selectFileById(fileId); setMobileDetail(false) })
  }

  function selectFromList(key: string) {
    listScroll.current = window.scrollY
    if (key === selectedKey) {
      setMobileDetail(true)
      return
    }
    guardChange(() => { selectFindingByKey(key); setMobileDetail(true) })
  }

  function backToList() {
    setMobileDetail(false)
    requestAnimationFrame(() => window.scrollTo({top: listScroll.current}))
  }

  const unsupported = (round?.coverage?.rule_items || []).filter(item => item.coverage === 'unsupported' && (!selectedFile || item.file_version_id === selectedFile.file_version_id || (item.item_key || '').startsWith(selectedFile.file_version_id + ':')))
  const noFindings = !!round && round.findings.length === 0
  const showFilePicker = !!round && (workspacePanel === 'original' || round.inputs.filter(file => file.role === 'target').length > 1 || selectedFile?.role === 'reference')
  const route = parseProductRoute(location.pathname, location.search)

  return (
    <PageLayout className="gap-4">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {message && <Alert><AlertDescription>{message}</AlertDescription></Alert>}
      {pendingUpload && !round && <Alert><AlertDescription><div className="flex flex-wrap items-center gap-2"><span>待核对：{pendingUpload.material.original_filename}。{pendingUpload.remaining ? `本批次还有 ${pendingUpload.remaining} 份文件未上传。` : ''}</span><Button type="button" disabled={busy} onClick={() => void checkPendingUpload()}>核对上传结果</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => { discardPendingUpload(); setError('') }}>暂不加入</Button></div></AlertDescription></Alert>}
      {!initialized && <Alert><AlertDescription>正在加载资料…</AlertDescription></Alert>}
      {initialized && !round && route.page === 'audit-round' && (
        <Alert><AlertTitle>正在读取审核</AlertTitle><AlertDescription><div className="flex flex-wrap items-center gap-2"><span>本轮已提交，无需再次开始审核。</span><Button type="button" disabled={busy} onClick={() => void action(() => openRound(route.roundId))}>刷新状态</Button></div></AlertDescription></Alert>
      )}
      {initialized && !round && route.page !== 'audit-round' && (
        <section id="audit-preparation" className="flex flex-col gap-8">
          <PageHeader
            back={predecessorId && <a className="text-sm text-primary underline-offset-4 hover:underline" href={productHref({page: 'audit-round', roundId: predecessorId})}>← 上一轮审核</a>}
            title={<span className="flex items-center gap-2">审核资料{!!items.length && <Badge variant="secondary">{items.length}</Badge>}</span>}
            action={<div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void openRules()}>上传规则</Button>
              {!!items.length && <Button asChild type="button" variant="outline" size="sm"><label><Plus className="size-4" aria-hidden="true" />添加文件<input className="sr-only" type="file" multiple disabled={busy || !!pendingUpload} aria-label="添加文件" data-testid="source-upload" onChange={event => void addFiles(event, 'target')} /></label></Button>}
              <DrivePicker disabled={busy || !!pendingUpload} onImport={imported} onBusy={value => { busyRef.current = value; setBusy(value) }} />
            </div>}
          />
          <section id={items.length || draftDirty ? 'draft-items' : undefined} data-testid={items.length || draftDirty ? 'draft-items' : undefined} aria-label="审核资料列表" className="flex min-w-0 flex-col">
              {items.length ? <div role="list" className="flex min-w-0 flex-col divide-y border-y">
                {items.map(item => {
                  const MaterialIcon = materialIcon(item.original_filename)
                  return <div key={item.file_version_id} role="listitem" className="grid min-w-0 gap-3 py-4 sm:grid-cols-[2rem_minmax(0,1fr)_minmax(0,15rem)_auto] sm:items-center">
                    <div className="grid size-8 place-items-center rounded-lg bg-muted text-muted-foreground" aria-hidden="true"><MaterialIcon className="size-5" /></div>
                    <div className="flex min-w-0 flex-col gap-1">
                      <strong className="break-words text-base">{item.original_filename}</strong>
                      {item.analysis && <small className="text-muted-foreground">{[item.analysis.metadata.customer, item.analysis.metadata.model, item.analysis.metadata.part].filter(Boolean).join(' · ')}</small>}
                      {item.analysisReadFailed ? (
                        <div className="flex flex-wrap items-center gap-1"><small className="text-muted-foreground">识别结果暂不可用</small><Button variant="ghost" size="sm" disabled={busy} aria-label={`重新读取 ${item.original_filename} 的识别结果`} onClick={() => void action(() => reloadAnalysis(item))}>重试</Button></div>
                      ) : <>
                        {['queued', 'leased'].includes(item.analysisStatus || '') && <small className="text-muted-foreground">正在识别…</small>}
                        {['failed', 'unknown'].includes(item.analysisStatus || '') && <small className="text-muted-foreground">识别未完成，请核对原件</small>}
                      </>}
                      {(item.analysis?.notices || []).filter(notice => notice !== 'metadata_uses_excerpt').map(notice => <small className="text-muted-foreground" key={notice}>{analysisNotices[notice] || notice}</small>)}
                    </div>
                    {item.role === 'target' && <div className="grid min-w-0 gap-1 sm:col-start-3">
                      <div className="flex items-center gap-1">
                        <NativeSelect data-testid="item-rule" value={item.rule_version_id || ''} disabled={busy} aria-label={item.original_filename + '的匹配规则'} onChange={event => setRule(item.file_version_id, event.target.value || null)}>
                          <option value="">选择规则</option>
                          {availableRules(item).map(rule => <option key={rule.id} value={rule.id}>{rule.title}{currentRules.some(current => current.id === rule.id) ? '' : '（历史版本）'}</option>)}
                        </NativeSelect>
                        {item.rule_version_id && <Button variant="ghost" size="icon" aria-label={'查看 ' + item.original_filename + ' 的规则'} disabled={busy} onClick={() => void openRules(item)}><BookOpen className="size-4" /></Button>}
                      </div>
                      {!item.rule_version_id && <small className="text-muted-foreground">{matchingHelp(item)}</small>}
                    </div>}
                    <div className="flex items-center gap-1 sm:justify-end">
                      <label className="inline-flex items-center gap-2 text-sm"><Checkbox data-testid={`audit-target-${item.file_version_id}`} aria-label={`参与审核：${item.original_filename}`} checked={item.role === 'target'} disabled={busy} onCheckedChange={checked => setRole(item.file_version_id, checked === true ? 'target' : 'reference')} /><span>审核</span></label>
                      <Button variant="ghost" size="icon" aria-label={'移除 ' + item.original_filename} disabled={busy} onClick={() => removeItem(item.file_version_id)}><X className="size-4" /></Button>
                    </div>
                  </div>
                })}
              </div> : <div className={"flex min-h-56 flex-col items-center justify-center gap-3 rounded-lg border border-dashed bg-muted/20 p-8 text-center transition-colors " + (dragActive ? 'border-primary bg-primary/5' : '')} onDragOver={event => { event.preventDefault(); setDragActive(true) }} onDragLeave={event => { event.preventDefault(); setDragActive(false) }} onDrop={event => { event.preventDefault(); void dropped(event) }}>
                <Files className="size-8 text-muted-foreground" aria-hidden="true" />
                <h2 className="text-base font-semibold">添加审核资料</h2>
                <p className="m-0 text-sm text-muted-foreground">支持 Excel、Word、PDF，单份最大 64 MB</p>
                <Button asChild type="button" variant="outline" size="sm"><label><Plus className="size-4" aria-hidden="true" />选择文件<input className="sr-only" type="file" multiple disabled={busy || !!pendingUpload} aria-label="选择待审文件" data-testid="source-upload" onChange={event => void addFiles(event, 'target')} /></label></Button>
              </div>}
            {(items.length || draftDirty) && <div className="mt-4 flex flex-col items-stretch gap-4 border-t pt-4 sm:flex-row sm:items-end sm:justify-between">
              <div className="flex flex-col gap-2">
                {deepOffered && <fieldset data-testid="analysis-mode" className="grid gap-1.5 border-0 p-0"><legend className="text-xs text-muted-foreground">审核模式</legend><Tabs value={analysisMode} onValueChange={setMode}><TabsList><TabsTrigger value="standard">{analysisModeLabels.standard}</TabsTrigger><TabsTrigger value="deep">{analysisModeLabels.deep}</TabsTrigger></TabsList></Tabs><input type="hidden" name="analysis-mode" value={analysisMode} readOnly /></fieldset>}
                <small className="text-muted-foreground">{targetCount}/{items.length} 份参与审核</small>
              </div>
              <div className="flex flex-col items-start gap-2 sm:items-end">
                {!!missingRules && <small className="text-muted-foreground">请为 {missingRules} 份文件选择规则</small>}
                {estimate?.accept_allowed === false && <Alert variant="destructive"><AlertDescription>{estimate.accept_reason || '当前额度不足，暂无法提交审核。'}</AlertDescription></Alert>}
                {estimate?.accept_allowed !== false && !!estimate?.estimated_points && <small data-testid="quota-estimate" className="text-muted-foreground">预计 {estimate.estimated_points} 点</small>}
                <div className="flex items-center gap-2">
                  <Button type="button" variant="ghost" data-testid="save-draft" disabled={busy || !!pendingUpload || draftPhase !== 'idle' || !draftDirty} onClick={() => void action(async () => { if (await saveDraft()) setMessage('草稿已保存。') })}>保存草稿</Button>
                  <Button type="button" data-testid="submit-audit" id={predecessorId ? 'resubmit-audit' : undefined} disabled={busy || !!pendingUpload || draftPhase !== 'idle' || !targetCount || !!missingRules || estimate?.accept_allowed === false} onClick={() => void submit()}>{draftPhase === 'accepting' ? '正在提交…' : '开始审核'}<ArrowRight className="size-4" /></Button>
                </div>
              </div>
            </div>}
          </section>
          {!items.length && !!drafts.length && (
            <section className="flex flex-col gap-2" aria-labelledby="drafts-heading">
              <h2 id="drafts-heading" className="text-sm font-semibold text-muted-foreground">继续草稿</h2>
              {drafts.map(draft => (
                <DraftLink
                  key={draft.id}
                  href={productHref({page: 'audit-new', draftId: draft.id})}
                  title={draft.items.filter(item => item.role === 'target').map(item => item.original_filename).join('、') || draft.items.map(item => item.original_filename).join('、')}
                  detail={`${draft.items.length} 份资料 · ${dateLabel(draft.updated_at)}${draft.predecessor_id ? ' · 再次审核' : ''}${draft.analysis_mode === 'deep' ? ' · 深度' : ''}`}
                  action="继续 →"
                />
              ))}
              {draftNext !== null && <Button type="button" disabled={draftLoading} onClick={() => void action(() => loadDrafts(true))}>更多草稿</Button>}
            </section>
          )}
        </section>
      )}
      {initialized && round && (
        <section className="flex flex-col gap-4">
          <RoundOverview
            round={round}
            counts={counts}
            reviewed={reviewed}
            roundView={roundView}
            workspacePanel={workspacePanel}
            busy={busy}
            onWriteback={() => { void writeBack() }}
            onRefresh={() => { void openRound(round.id) }}
            onPrepareAgain={prepareAgain}
            onSwitchView={switchView}
          />
          <section hidden={roundView !== 'review'} className="flex flex-col gap-4">
            {noFindings && workspacePanel === 'original' && (
              <div id="audit-state-original" className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2 border-b pb-3">
                  <NativeSelect className="min-w-0 flex-1 basis-64" data-testid="file-version-list" title={selectedFile?.original_filename} aria-label="审核资料" value={selectedFileId} disabled={busy} onChange={selectFile}>
                    {round.inputs.map(file => <option key={file.file_version_id} value={file.file_version_id}>{file.original_filename}{file.role === 'reference' ? ' · 参考资料' : ''}</option>)}
                  </NativeSelect>
                  {selectedFile?.rule_version_id && <Button asChild variant="link"><a href={productHref({page: 'rule-detail', ruleId: selectedFile.rule_version_id, returnTo: {page: 'audit-round', roundId: round.id}})}>本轮规则 ↗</a></Button>}
                </div>
                <OriginalPreview ref={originalPreview} file={previewFile} roundId={round.id} finding={selectedFinding} source={evidenceSource} />
              </div>
            )}
            {noFindings && workspacePanel !== 'original' && <div className="flex flex-col gap-3">
              <ExecutionState
                execution={round.execution}
                fileFindingCount={0}
                progress={round.progress}
                failure={round.failure}
                busy={busy}
                onShowAll={() => chooseFilter('')}
                onShowOriginal={() => switchView('original')}
                onPrepareAgain={prepareAgain}
              />
              {selectedFile?.rule_version_id && <div className="border-t pt-3"><Button asChild variant="link"><a href={productHref({page: 'rule-detail', ruleId: selectedFile.rule_version_id, returnTo: {page: 'audit-round', roundId: round.id}})}>本轮规则 ↗</a></Button></div>}
            </div>}
            {!noFindings && <>
            <div className="flex flex-wrap items-center gap-2 border-b pb-3">
              {showFilePicker && <NativeSelect className="min-w-0 flex-1 basis-64" data-testid="file-version-list" title={selectedFile?.original_filename} aria-label="审核资料" value={selectedFileId} disabled={busy} onChange={selectFile}>
                {round.inputs.map(file => <option key={file.file_version_id} value={file.file_version_id}>{file.original_filename}{file.role === 'reference' ? ' · 参考资料' : ''}</option>)}
              </NativeSelect>}
              {selectedFile?.rule_version_id && <Button asChild variant="link" data-testid="context-link"><a href={productHref({page: 'rule-detail', ruleId: selectedFile.rule_version_id, returnTo: {page: 'audit-round', roundId: round.id}})}>本轮规则 ↗</a></Button>}
              {workspacePanel === 'original' && selectedFinding && <div className="ml-auto flex items-center gap-2 text-sm"><strong className="max-w-[min(60vw,28rem)] truncate">{findingShortTitle(selectedFinding, fileFindings.findIndex(item => item.item_key === selectedFinding.item_key) + 1)}</strong><Button type="button" size="sm" onClick={() => switchView('review')}>返回复核</Button></div>}
            </div>
            <Workspace panel={workspacePanel}>
              <Pane kind="checks">
                {selectedFile?.role === 'reference' ? (
                  <div className="flex flex-wrap items-center justify-between gap-3 border-y py-3">
                    <span className="text-sm text-muted-foreground">参考资料 · 不参与机器检查</span>
                    <Button type="button" size="sm" variant="outline" onClick={() => switchView('original')}>查看原件</Button>
                  </div>
                ) : (
                  <div className="flex flex-col gap-3">
                    {!!fileFindings.length && <div role="group" aria-label="结果筛选" className="flex flex-wrap gap-1">
                      {filters.map(entry => ({...entry, count: fileFindings.filter(item => matchesFilter(item, entry.id)).length}))
                        .filter(entry => entry.count > 0 || ['', 'attention', 'unreviewed', filter].includes(entry.id))
                        .map(entry => <Button key={entry.id || 'all'} data-testid={'filter-' + (entry.id || 'all')} disabled={busy} variant={filter === entry.id ? 'secondary' : 'outline'} aria-pressed={filter === entry.id} size="sm" onClick={() => chooseFilter(entry.id)}>{entry.label} {entry.count}</Button>)}
                    </div>}
                    {!findings.length && (
                      <ExecutionState
                        execution={round.execution === 'failed' ? 'completed' : round.execution}
                        fileFindingCount={fileFindings.length}
                        progress={round.progress}
                        failure={round.failure}
                        busy={busy}
                        emptyTitle="此资料没有检查结果"
                        onShowAll={() => chooseFilter('')}
                        onShowOriginal={() => switchView('original')}
                        onPrepareAgain={prepareAgain}
                      />
                    )}
                    {!!findings.length && <ReviewColumns count={findings.length} detail={mobileDetail} onDetailChange={setMobileDetail}>
                      <ReviewList>
                        {findings.map(finding => (
                          <ReviewItem key={finding.item_key} selected={finding.item_key === selectedKey} onClick={() => selectFromList(finding.item_key)}>
                            <strong>{findingShortTitle(finding, fileFindings.findIndex(item => item.item_key === finding.item_key) + 1)}</strong>
                            <span className="flex flex-wrap items-center gap-1"><FindingBadge status={finding.status} />{finding.review && <Badge variant="secondary">复核已记录</Badge>}</span>
                          </ReviewItem>
                        ))}
                      </ReviewList>
                      <ReviewDetail>
                        <div className="flex items-center justify-between gap-2">
                          <ReviewBack onClick={backToList}>← 检查项</ReviewBack>
                          <div className="flex items-center gap-1"><span className="text-sm text-muted-foreground">{findingIndex + 1} / {findings.length}</span><Button variant="ghost" size="icon" aria-label="上一检查项" disabled={busy || findingIndex <= 0} onClick={() => nextFinding(-1)}><ArrowLeft className="size-4" /></Button><Button variant="ghost" size="icon" aria-label="下一检查项" disabled={busy || findingIndex >= findings.length - 1} onClick={() => nextFinding()}><ArrowRight className="size-4" /></Button></div>
                        </div>
                        {selectedFinding && <FindingReview
                          finding={selectedFinding}
                          position={fileFindings.findIndex(item => item.item_key === selectedKey) + 1}
                          busy={busy}
                          review={review}
                          customJudgment={customJudgment}
                          savingReview={savingReview}
                          reviewDirty={reviewDirty}
                          reviewFeedback={reviewFeedback}
                          hasNextUnreviewed={hasNextUnreviewed}
                          headingRef={detailHeading}
                          onFindEvidence={source => { void findEvidence(source) }}
                          onJudgment={setJudgment}
                          onEnableCustomJudgment={enableCustomJudgment}
                          onReviewChange={updateReview}
                          onSave={() => { void saveReview() }}
                          onContinue={() => { void nextUnreviewed() }}
                        />}
                      </ReviewDetail>
                    </ReviewColumns>}
                    {!!unsupported.length && <section data-testid="unchecked-rule-items" className="flex flex-col gap-2 border-t pt-3">
                      <h2 className="text-base font-semibold">未自动检查 · {unsupported.length}</h2>
                      <ul className="m-0 flex list-none flex-col gap-2 p-0">{unsupported.map(item => <li className="flex items-center gap-2" key={item.id}><span>{item.id} · {item.category || '规则条款'}</span><Help title="完整规则"><p className="m-0">{item.rule_to_check}</p></Help></li>)}</ul>
                    </section>}
                  </div>
                )}
              </Pane>
              {workspacePanel === 'original' && <OriginalPreview ref={originalPreview} file={previewFile} roundId={round.id} finding={selectedFinding} source={evidenceSource} />}
            </Workspace>
            </>}
          </section>
          {roundView === 'report' && <ReportPreview round={round} unsavedReview={reviewDirty} onReview={() => switchView('review')} />}
        </section>
      )}
      {pendingChange && (
        <Modal title={pendingUpload ? '离开前处理待核对文件？' : '保存修改后离开？'} busy={busy} onClose={() => setPendingChange(null)}>
          <div className="flex flex-col gap-3">
            <p className="m-0">{pendingUpload
              ? `“${pendingUpload.material.original_filename}”尚未加入草稿。离开会放弃这次核对，已保存资料仍保留。${draftDirty ? '其他未保存修改也会放弃。' : ''}`
              : `${round ? '这条复核还有未保存的修改。' : '审核资料还有未保存的修改。'}你可以保存后继续，也可以留在这里编辑。`}</p>
            {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
            {round && !review.judgment.trim() && <p className="m-0 text-sm text-muted-foreground">请先填写复核判断，才能保存。</p>}
            <div className="flex flex-wrap gap-2">
              <Button type="button" disabled={busy} onClick={() => setPendingChange(null)}>{pendingUpload ? '继续核对' : '继续编辑'}</Button>
              <Button type="button" disabled={busy} onClick={() => void resolveChange(false)}>{pendingUpload ? '暂不加入并离开' : '放弃修改'}</Button>
              {!pendingUpload && <Button type="button" disabled={busy || (!!round && !review.judgment.trim())} onClick={() => void resolveChange(true)}>{busy ? '正在保存…' : '保存并离开'}</Button>}
            </div>
          </div>
        </Modal>
      )}
    </PageLayout>
  )
}
