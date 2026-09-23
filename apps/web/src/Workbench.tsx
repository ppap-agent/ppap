import {useEffect, useRef, useState, type ChangeEvent} from 'react'
import {dateLabel, resultLabels, analysisModeLabels, deepOffered, type Material} from './api'
import {analysisNotices, matchesFilter} from './helpers'
import {parseProductRoute, productHref} from './navigation/route'
import OriginalPreview from './OriginalPreview'
import DrivePicker from './DrivePicker'
import ReportPreview from './ReportPreview'
import Modal from './Modal'
import Help from './Help'
import FindingReview from './audit/FindingReview'
import ExecutionState from './audit/ExecutionState'
import RoundOverview from './audit/RoundOverview'
import {useDraftPreparation} from './audit/useDraftPreparation'
import {useRoundReview} from './audit/useRoundReview'
import {useUnsavedGuard} from './audit/useUnsavedGuard'
import {ArrowLeft, ArrowRight, X} from 'lucide-react'

const filters = [{id: 'attention', label: '需关注'}, {id: 'unreviewed', label: '未记录'}, {id: '', label: '全部'}, {id: 'PASS', label: '通过'}]

export default function Workbench({onReviewing}: {onReviewing: (value: boolean) => void}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [initialized, setInitialized] = useState(false)
  const busyRef = useRef(false)

  async function action(work: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError('')
    try {
      await work()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '操作失败，请重试。')
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
    startResize,
    resizePreview,
    resizeKey,
    stopResize,
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
    previousRound,
    analysisMode,
    estimate,
    dragActive,
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
    dropped,
    setRole,
    setRule,
    removeItem,
    setMode,
    matchingHelp,
    availableRules,
    unchanged,
    markCurrentClean,
  } = draftModel

  async function saveCurrent() {
    if (round) return saveReview()
    let saved = false
    await action(async () => {
      await saveDraft()
      saved = true
    })
    return saved
  }

  function discardCurrent() {
    if (round) discardReview()
    else markCurrentClean()
  }

  const {
    pendingChange,
    setPendingChange,
    guardChange,
    resolveChange,
  } = useUnsavedGuard({
    dirty: reviewDirty || draftDirty,
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
      await openRound(accepted.id)
    })
  }

  function prepareAgain() {
    guardChange(() => {
      void action(async () => {
        if (!round) return
        rememberWorkspace()
        await startReaudit(round)
        clearForPreparation()
      })
    })
  }

  function chooseFilter(status: string) {
    guardChange(() => chooseFilterDirect(status))
  }

  function nextFinding(direction = 1) {
    guardChange(() => nextFindingDirect(direction))
  }

  function pickFinding(event: ChangeEvent<HTMLSelectElement>) {
    const key = event.currentTarget.value
    event.currentTarget.value = selectedKey
    guardChange(() => selectFindingByKey(key))
  }

  function selectFile(event: ChangeEvent<HTMLSelectElement>) {
    const fileId = event.currentTarget.value
    event.currentTarget.value = selectedFileId
    guardChange(() => selectFileById(fileId))
  }

  return (
    <>
      {error && (!round || roundView === 'report') && <p className="error" role="alert">{error}</p>}
      {message && <p className="notice" role="status">{message}</p>}
      {!initialized && <p className="loading-state" role="status">正在加载资料…</p>}
      {initialized && !round && (
        <div id="audit-preparation">
          {predecessorId && <div className="context-bar"><span>再次审核</span><a href={productHref({page: 'audit-round', roundId: predecessorId})}>查看上一轮 →</a></div>}
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
                    <select value={item.role} disabled={busy} onChange={event => setRole(item.file_version_id, event.target.value as Material['role'])}>
                      <option value="target">待审文件</option>
                      <option value="reference">参考资料</option>
                    </select>
                  </label>
                  {item.role === 'target' ? (
                    <div className="material-rule">
                      <label>审核规则
                        <select data-testid="item-rule" value={item.rule_version_id || ''} disabled={busy} aria-label={item.original_filename + '的匹配规则'} onChange={event => setRule(item.file_version_id, event.target.value || null)}>
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
                  <button type="button" className="icon-button material-remove" disabled={busy} title="移除资料" aria-label={'移除 ' + item.original_filename} onClick={() => removeItem(item.file_version_id)}><X size={18} aria-hidden="true" /></button>
                </article>
              ))}
              {!!targetCount && <div className="scope-note scope-inline">所选规则条目即本轮机审清单<Help title="审核范围"><p>受理后冻结所选规则版本和条目。能映射到内置检查的条目给出 PASS/NG/ERR；不能自动执行的条目进入未覆盖，需人工核对。不会改成另一套内置清单。</p></Help></div>}
              <div className="action-bar sticky-actions">
                <div>
                  {deepOffered && <label className="mode-select">
                    <span>审核模式</span>
                    <select data-testid="analysis-mode" value={analysisMode} disabled={busy} aria-label="审核模式" onChange={event => setMode(event.target.value)}>
                      <option value="standard">{analysisModeLabels.standard}</option>
                      <option value="deep">{analysisModeLabels.deep}</option>
                    </select>
                  </label>}
                  <strong>{targetCount} 份待审</strong>
                  {!!missingRules && <span className="status-NG"> · {missingRules} 份未匹配规则</span>}
                  {!!targetCount && <small className="pc-muted" data-testid="quota-estimate">{estimate?.unavailable ? '无法读取 Partner AI' : estimate ? `预计 ${estimate.estimated_points ?? '—'} 点 · 试用剩余 ${estimate.remaining_trial_ai_points ?? '—'} 点 · ${estimate.quota_sufficient ? 'Partner AI 可用' : 'Partner AI 不可用'}${estimate.accept_allowed === false && estimate.accept_reason ? ` · ${estimate.accept_reason}` : ''}` : '正在读取额度…'}</small>}
                  <small className="pc-muted">{draftDirty ? '有未保存的修改' : '草稿已保存'}</small>
                </div>
                <div className="button-group">
                  <button type="button" data-testid="save-draft" disabled={busy || !draftDirty} onClick={() => void action(async () => { await saveDraft(); setMessage('草稿已保存。') })}>保存草稿</button>
                  <button type="button" className="pc-primary" data-testid="submit-audit" id={predecessorId ? 'resubmit-audit' : undefined} disabled={busy || !targetCount || !!missingRules || estimate?.accept_allowed === false} onClick={() => void submit()}>{busy ? '处理中…' : '开始审核'}</button>
                </div>
              </div>
            </section>
          )}
          {!items.length && !!drafts.length && (
            <section className="recent-drafts">
              <div className="section-heading"><h2>继续草稿</h2></div>
              {drafts.map(draft => (
                <a key={draft.id} className="draft-row" href={productHref({page: 'audit-new', draftId: draft.id})}>
                  <div>
                    <strong>{draft.items.filter(item => item.role === 'target').map(item => item.original_filename).join('、') || draft.items.map(item => item.original_filename).join('、')}</strong>
                    <small>{draft.items.length} 份资料 · {dateLabel(draft.updated_at)}{draft.predecessor_id ? ' · 再次审核' : ''}{draft.analysis_mode === 'deep' ? ' · 深度' : ''}</small>
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
          <RoundOverview
            round={round}
            counts={counts}
            reviewed={reviewed}
            roundView={roundView}
            workspacePanel={workspacePanel}
            busy={busy}
            onWriteback={() => { void writeBack() }}
            onPrepareAgain={prepareAgain}
            onSwitchView={switchView}
          />
          <div hidden={roundView !== 'review'}>
            <div className="workspace-heading">
              <label>
                <span className="sr-only">审核资料</span>
                <select data-testid="file-version-list" title={selectedFile?.original_filename} value={selectedFileId} disabled={busy} onChange={selectFile}>
                  {round.inputs.map(file => <option key={file.file_version_id} value={file.file_version_id}>{file.original_filename}{file.role === 'reference' ? ' · 参考资料' : ''}</option>)}
                </select>
              </label>
              {selectedFile?.rule_version_id && <a className="context-link" href={productHref({page: 'rule-detail', ruleId: selectedFile.rule_version_id, returnTo: {page: 'audit-round', roundId: round.id}})}>本轮规则 ↗</a>}
            </div>
            <div className="sr-only" data-testid="coverage-panel">{[...round.inputs.map(file => file.original_filename), ...(round.coverage?.unchecked || []), ...(round.coverage?.rule_items || []).map(item => item.rule_to_check)].join(' ')}</div>
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
                      <ExecutionState
                        execution={round.execution}
                        analysisMode={round.analysis_mode}
                        fileFindingCount={fileFindings.length}
                        onShowAll={() => chooseFilter('')}
                      />
                    )}
                    {!!findings.length && (
                      <div className="finding-navigation">
                        <label className="finding-picker">
                          <span className="sr-only">检查项</span>
                          <select data-testid="finding-picker" value={findings.some(item => item.item_key === selectedKey) ? selectedKey : ''} disabled={busy} onChange={pickFinding}>
                            {!findings.some(item => item.item_key === selectedKey) && <option value="" disabled>当前项已保存，可继续下一项</option>}
                            {findings.map((finding, index) => <option key={finding.item_key} value={finding.item_key}>{index + 1}. {finding.item} · {resultLabels[finding.status]}{finding.classification ? ' · ' + finding.classification : ''}{finding.priority ? ' · ' + finding.priority : ''}{finding.review ? ' · 已记录' : ''}</option>)}
                          </select>
                        </label>
                        <div className="finding-arrows">
                          <button type="button" className="icon-button" aria-label="上一检查项" title="上一检查项" disabled={busy || findingIndex <= 0} onClick={() => nextFinding(-1)}><ArrowLeft size={18} aria-hidden="true" /></button>
                          <button type="button" className="icon-button" aria-label="下一检查项" title="下一检查项" disabled={busy || findingIndex >= findings.length - 1} onClick={() => nextFinding()}><ArrowRight size={18} aria-hidden="true" /></button>
                        </div>
                      </div>
                    )}
                    {!!(round.coverage?.rule_items || []).filter(item => item.coverage === 'unsupported' && (!selectedFile || item.file_version_id === selectedFile.file_version_id || (item.item_key || '').startsWith(selectedFile.file_version_id + ':'))).length && (
                      <div className="empty-state compact" data-testid="unchecked-rule-items">
                        <h3>未自动检查</h3>
                        <ul>
                          {(round.coverage?.rule_items || []).filter(item => item.coverage === 'unsupported' && (!selectedFile || item.file_version_id === selectedFile.file_version_id || (item.item_key || '').startsWith(selectedFile.file_version_id + ':'))).map(item => (
                            <li key={item.id}>{item.rule_to_check} · 需人工核对</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {selectedFinding && (
                      <FindingReview
                        finding={selectedFinding}
                        position={fileFindings.findIndex(item => item.item_key === selectedKey) + 1}
                        total={fileFindings.length}
                        busy={busy}
                        review={review}
                        customJudgment={customJudgment}
                        savingReview={savingReview}
                        reviewDirty={reviewDirty}
                        reviewFeedback={reviewFeedback}
                        hasNextUnreviewed={hasNextUnreviewed}
                        headingRef={detailHeading}
                        onFindEvidence={() => { void findEvidence() }}
                        onJudgment={setJudgment}
                        onEnableCustomJudgment={enableCustomJudgment}
                        onReviewChange={updateReview}
                        onSave={() => { void saveReview() }}
                        onContinue={() => { void nextUnreviewed() }}
                      />
                    )}
                  </>
                )}
              </section>
              <div className="preview-resizer" role="separator" tabIndex={0} aria-label="调整原件预览宽度" aria-orientation="vertical" aria-valuenow={Math.round(previewWidth)} aria-valuemin={30} aria-valuemax={65} onPointerDown={startResize} onPointerMove={resizePreview} onPointerUp={stopResize} onPointerCancel={stopResize} onLostPointerCapture={stopResize} onKeyDown={resizeKey} />
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
