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
import {ActionBar, Badge, Button, Callout, DraftLink, Dropzone, FeedbackState, Field, FileInput, FindingContext, Heading, Icon, IconButton, Inline, Input, Link, Material as MaterialRow, Page, Pane, Region, Resizer, Select, Stack, Text, Toggle, ToggleRow, VisuallyHidden, Workspace} from './ui'

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

  const unsupported = (round?.coverage?.rule_items || []).filter(item => item.coverage === 'unsupported' && (!selectedFile || item.file_version_id === selectedFile.file_version_id || (item.item_key || '').startsWith(selectedFile.file_version_id + ':')))

  return (
    <Page>
      {error && (!round || roundView === 'report') && <Callout tone="error">{error}</Callout>}
      {message && <Callout>{message}</Callout>}
      {!initialized && <Callout tone="loading">正在加载资料…</Callout>}
      {initialized && !round && (
        <Region id="audit-preparation">
          {predecessorId && <Inline gap="sm"><Text>再次审核</Text><Link href={productHref({page: 'audit-round', roundId: predecessorId})}>查看上一轮 →</Link></Inline>}
          <Stack gap="md">
            {!items.length && (
              <Dropzone active={dragActive} onDragOver={event => { event.preventDefault(); setDragActive(true) }} onDragLeave={event => { event.preventDefault(); setDragActive(false) }} onDrop={event => { event.preventDefault(); void dropped(event) }}>
                <Heading level={1}>添加待审资料</Heading>
                <Text tone="muted">拖入文件，或选择本地资料</Text>
                <FileInput label="选择文件" variant="primary" multiple disabled={busy} aria-label="选择待审文件" data-testid="source-upload" onChange={event => void addFiles(event, 'target')} />
                <Text as="small" tone="muted">Excel、Word、PDF · 单份最大 64 MB</Text>
              </Dropzone>
            )}
            <Inline gap="sm" wrap>
              {!!items.length && <FileInput label="添加待审文件" multiple disabled={busy} aria-label="添加待审文件" data-testid="source-upload" onChange={event => void addFiles(event, 'target')} />}
              <FileInput label="添加参考资料" variant="secondary" multiple disabled={busy} aria-label="添加参考资料" data-testid="reference-upload" onChange={event => void addFiles(event, 'reference')} />
              <DrivePicker disabled={busy} onImport={imported} onBusy={value => { busyRef.current = value; setBusy(value) }} />
            </Inline>
          </Stack>
          {(items.length || draftDirty) && (
            <Region id="draft-items" testId="draft-items">
              <Inline justify="between" align="center">
                <Heading level={items.length ? 1 : 2}>本次资料 {items.length}</Heading>
                <Button type="button" variant="ghost" disabled={busy} onClick={() => void openRules()}>上传客户规则</Button>
              </Inline>
              {items.map(item => (
                <MaterialRow key={item.file_version_id}>
                  <Stack gap="xs">
                    <Text as="strong">{item.original_filename}</Text>
                    {previousRound && <Badge>{unchanged(item) ? '沿用原件' : '本轮新增'}</Badge>}
                    {item.analysis && <Text as="small" tone="muted">{[item.analysis.metadata.customer, item.analysis.metadata.model, item.analysis.metadata.part].filter(Boolean).join(' · ')}</Text>}
                    {['queued', 'leased'].includes(item.analysisStatus || '') && <Text as="small" tone="muted">正在识别并匹配…</Text>}
                    {['failed', 'unknown'].includes(item.analysisStatus || '') && <Text as="small" tone="muted">自动匹配未完成，请手动选择。</Text>}
                    {(item.analysis?.notices || []).filter(notice => notice !== 'metadata_uses_excerpt').map(notice => <Text as="small" key={notice} tone="muted">{analysisNotices[notice] || notice}</Text>)}
                  </Stack>
                  <Field label="用途">
                    <Select value={item.role} disabled={busy} onChange={event => setRole(item.file_version_id, event.target.value as Material['role'])}>
                      <option value="target">待审文件</option>
                      <option value="reference">参考资料</option>
                    </Select>
                  </Field>
                  {item.role === 'target' ? (
                    <Stack gap="xs">
                      <Field label="审核规则">
                        <Select data-testid="item-rule" value={item.rule_version_id || ''} disabled={busy} aria-label={item.original_filename + '的匹配规则'} onChange={event => setRule(item.file_version_id, event.target.value || null)}>
                          <option value="">选择匹配规则</option>
                          {availableRules(item).map(rule => <option key={rule.id} value={rule.id}>{rule.title}{currentRules.some(current => current.id === rule.id) ? '' : '（历史版本）'}</option>)}
                        </Select>
                      </Field>
                      <Inline gap="sm">
                        {item.rule_version_id && <Text as="span" size="sm" tone="muted">{rules.find(rule => rule.id === item.rule_version_id)?.type || '按规则类型'}</Text>}
                        {item.rule_version_id && <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void openRules(item)}>查看规则</Button>}
                      </Inline>
                      {!item.rule_version_id && <Text size="sm" tone="muted">{matchingHelp(item)}</Text>}
                    </Stack>
                  ) : <Text tone="muted">仅作参考，不单独检查</Text>}
                  <IconButton label={'移除 ' + item.original_filename} disabled={busy} onClick={() => removeItem(item.file_version_id)}><Icon icon={X} size="md" /></IconButton>
                </MaterialRow>
              ))}
              {!!targetCount && <Inline gap="xs" align="center"><Text size="sm">所选规则条目即本轮机审清单</Text><Help title="审核范围"><Text>受理后冻结所选规则版本和条目。能映射到内置检查的条目给出 PASS/NG/ERR；不能自动执行的条目进入未覆盖，需人工核对。不会改成另一套内置清单。</Text></Help></Inline>}
              <ActionBar>
                <Stack gap="xs">
                  {deepOffered && (
                    <Field label="审核模式">
                      <Select data-testid="analysis-mode" value={analysisMode} disabled={busy} aria-label="审核模式" onChange={event => setMode(event.target.value)}>
                        <option value="standard">{analysisModeLabels.standard}</option>
                        <option value="deep">{analysisModeLabels.deep}</option>
                      </Select>
                    </Field>
                  )}
                  <Text as="strong">{targetCount} 份待审</Text>
                  {!!missingRules && <Text as="span" tone="muted">{missingRules} 份未匹配规则</Text>}
                  {!!targetCount && <Text as="small" tone="muted" testId="quota-estimate">{estimate?.unavailable ? '无法读取 Partner AI' : estimate ? `预计 ${estimate.estimated_points ?? '—'} 点 · 试用剩余 ${estimate.remaining_trial_ai_points ?? '—'} 点 · ${estimate.quota_sufficient ? 'Partner AI 可用' : 'Partner AI 不可用'}${estimate.accept_allowed === false && estimate.accept_reason ? ` · ${estimate.accept_reason}` : ''}` : '正在读取额度…'}</Text>}
                  <Text as="small" tone="muted">{draftDirty ? '有未保存的修改' : '草稿已保存'}</Text>
                </Stack>
                <Inline gap="sm">
                  <Button type="button" data-testid="save-draft" disabled={busy || draftPhase !== 'idle' || !draftDirty} onClick={() => void action(async () => { if (await saveDraft()) setMessage('草稿已保存。') })}>保存草稿</Button>
                  <Button type="button" variant="primary" data-testid="submit-audit" id={predecessorId ? 'resubmit-audit' : undefined} disabled={busy || draftPhase !== 'idle' || !targetCount || !!missingRules || estimate?.accept_allowed === false} onClick={() => void submit()}>{busy || draftPhase === 'accepting' ? '处理中…' : '开始审核'}</Button>
                </Inline>
              </ActionBar>
            </Region>
          )}
          {!items.length && !!drafts.length && (
            <Stack gap="sm">
              <Heading level={2}>继续草稿</Heading>
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
            </Stack>
          )}
        </Region>
      )}
      {initialized && round && (
        <Stack gap="md">
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
          <Region hidden={roundView !== 'review'}>
            <Inline justify="between" align="center" gap="md">
              <Select data-testid="file-version-list" title={selectedFile?.original_filename} aria-label="审核资料" value={selectedFileId} disabled={busy} onChange={selectFile}>
                {round.inputs.map(file => <option key={file.file_version_id} value={file.file_version_id}>{file.original_filename}{file.role === 'reference' ? ' · 参考资料' : ''}</option>)}
              </Select>
              {selectedFile?.rule_version_id && <Link data-testid="context-link" href={productHref({page: 'rule-detail', ruleId: selectedFile.rule_version_id, returnTo: {page: 'audit-round', roundId: round.id}})}>本轮规则 ↗</Link>}
            </Inline>
            <VisuallyHidden>
              <VisuallyHidden testId="coverage-panel">{[...round.inputs.map(file => file.original_filename), ...(round.coverage?.unchecked || []), ...(round.coverage?.rule_items || []).map(item => item.rule_to_check)].join(' ')}</VisuallyHidden>
            </VisuallyHidden>
            {workspacePanel === 'original' && selectedFinding && (
              <FindingContext>
                <Text as="strong">{selectedFinding.item}</Text>
                <Button type="button" onClick={() => switchView('review')}>返回复核</Button>
              </FindingContext>
            )}
            <Workspace preview={previewWidth} panel={workspacePanel} resizing={resizing} rootRef={workspace}>
              <Pane kind="checks">
                {selectedFile?.role === 'reference' ? (
                  <FeedbackState title="参考资料" detail="用于对照，不包含在机器检查结果中。">
                    <Button type="button" onClick={() => switchView('original')}>查看原件</Button>
                  </FeedbackState>
                ) : (
                  <Stack gap="sm">
                    {error && <Callout tone="error">{error}</Callout>}
                    <ToggleRow label="结果筛选">
                      {filters.map(entry => <Toggle key={entry.id || 'all'} testId={'filter-' + (entry.id || 'all')} disabled={busy} pressed={filter === entry.id} onClick={() => chooseFilter(entry.id)}>{entry.label} {fileFindings.filter(item => matchesFilter(item, entry.id)).length}</Toggle>)}
                    </ToggleRow>
                    {!findings.length && (
                      <ExecutionState
                        execution={round.execution}
                        analysisMode={round.analysis_mode}
                        fileFindingCount={fileFindings.length}
                        onShowAll={() => chooseFilter('')}
                      />
                    )}
                    {!!findings.length && (
                      <Inline gap="sm" align="center">
                        <Select data-testid="finding-picker" aria-label="检查项" value={findings.some(item => item.item_key === selectedKey) ? selectedKey : ''} disabled={busy} onChange={pickFinding}>
                          {!findings.some(item => item.item_key === selectedKey) && <option value="" disabled>当前项已保存，可继续下一项</option>}
                          {findings.map((finding, index) => <option key={finding.item_key} value={finding.item_key}>{index + 1}. {finding.item} · {resultLabels[finding.status]}{finding.classification ? ' · ' + finding.classification : ''}{finding.priority ? ' · ' + finding.priority : ''}{finding.review ? ' · 已记录' : ''}</option>)}
                        </Select>
                        <IconButton label="上一检查项" disabled={busy || findingIndex <= 0} onClick={() => nextFinding(-1)}><Icon icon={ArrowLeft} size="md" /></IconButton>
                        <IconButton label="下一检查项" disabled={busy || findingIndex >= findings.length - 1} onClick={() => nextFinding()}><Icon icon={ArrowRight} size="md" /></IconButton>
                      </Inline>
                    )}
                    {!!unsupported.length && (
                      <FeedbackState compact title="未自动检查" testId="unchecked-rule-items">
                        <Stack as="ul" gap="xs">
                          {unsupported.map(item => <Text as="span" key={item.id}>{item.rule_to_check} · 需人工核对</Text>)}
                        </Stack>
                      </FeedbackState>
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
                  </Stack>
                )}
              </Pane>
              <Resizer role="separator" tabIndex={0} aria-label="调整原件预览宽度" aria-orientation="vertical" aria-valuenow={Math.round(previewWidth)} aria-valuemin={30} aria-valuemax={65} onPointerDown={startResize} onPointerMove={resizePreview} onPointerUp={stopResize} onPointerCancel={stopResize} onLostPointerCapture={stopResize} onKeyDown={resizeKey} />
              <OriginalPreview ref={originalPreview} file={selectedFile} roundId={round.id} finding={selectedFinding} />
            </Workspace>
          </Region>
          {roundView === 'report' && <ReportPreview round={round} unsavedReview={reviewDirty} onReview={() => switchView('review')} />}
        </Stack>
      )}
      {pendingChange && (
        <Modal title="保存修改后离开？" busy={busy} onClose={() => setPendingChange(null)}>
          <Stack gap="md">
            <Text>{round ? '这条复核还有未保存的修改。' : '审核资料还有未保存的修改。'}你可以保存后继续，也可以留在这里编辑。</Text>
            {error && <Callout tone="error">{error}</Callout>}
            {round && !review.judgment.trim() && <Text tone="muted">请先填写复核判断，才能保存。</Text>}
            <Inline gap="sm" wrap>
              <Button type="button" disabled={busy} onClick={() => setPendingChange(null)}>继续编辑</Button>
              <Button type="button" disabled={busy} onClick={() => void resolveChange(false)}>放弃修改</Button>
              <Button type="button" variant="primary" disabled={busy || (!!round && !review.judgment.trim())} onClick={() => void resolveChange(true)}>{busy ? '正在保存…' : '保存并离开'}</Button>
            </Inline>
          </Stack>
        </Modal>
      )}
    </Page>
  )
}
