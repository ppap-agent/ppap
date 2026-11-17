// One surface for a work item's PPAP package: what is attached, what the latest round
// concluded about it, what is still missing, and what needs a person. Starting, confirming and
// following an audit all happen on the document list itself.
import { IconDownload, IconEyeOpened, IconMore } from '@douyinfe/semi-icons';
import { Banner, Button, Collapse, Dropdown, Empty, List, Modal, Progress, Radio, RadioGroup, Select, Space, Spin, Table, Tag, TextArea, Tooltip, Typography } from '@douyinfe/semi-ui';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Data, Item } from './data';
import { itemPath, outsideText } from './data';
import { Confirm, ConfirmRow, useRevise } from './Confirm';
import type { Revise } from './Confirm';
import { PdfView } from './Evidence';
import { Issue, openFacts } from './Issue';
import { buildPackage, currentTemplate, pendingStart, rectification, rememberedTemplate, rememberStart, rememberTemplate, requiredTypes, standing, templateName, templateOwner, templates, unaudited, verdict } from './ledger';
import type { Doc, OpenIssue, Package } from './ledger';
import { bucket, bucketColor, bucketLabel, byUrgency, conclusionLabel, documentTypeLabel, reviewOf, verdictLabel, when } from './model';
import type { FactView, FileVersion, Job, Task, TaskFile } from './model';
import { bytes, call, newId, PpapError, save, say, taskLink, waitFor } from './ppap';

const { Text } = Typography;
type Props = { item: Item; data: Data; user: string; stalled: boolean; refresh: () => Promise<void>; onWritten: (roundId: string) => void; onDisconnect: () => void };
type Input = { file_version_id: string; validation_id: string; selection: 'selected'; removal_reason: null; preferred_rule_version_id?: string; reference_only?: boolean };
type Carried = { file: FileVersion; validation_id?: string; validation?: { state?: string }; preferred_rule_version_id?: string; reference_only?: boolean };

const reportReady = (row: TaskFile) => Boolean(row.audit?.report.state === 'ready' && row.audit.report.report_id && row.audit.report.artifact && row.audit.result_attempt !== null);
const reportPath = (task: Task, row: TaskFile) => {
  const audit = row.audit!;
  const query = new URLSearchParams({ round_id: audit.round_id, file_version_id: row.file.id, result_attempt: String(audit.result_attempt), artifact_id: audit.report.artifact!.artifact_id });
  return `/audit-tasks/${task.id}/reports/${audit.report.report_id}/pdf?${query}`;
};

export function Workspace({ item, data, user, stalled, refresh, onWritten, onDisconnect }: Props) {
  const pack = useMemo(() => buildPackage(data.attachments, data.tasks, data.sources), [data]);
  const { docs, flight, latest } = pack;
  const view = useMemo(() => standing(latest, pack.previous), [latest, pack.previous]);
  const total = verdict(latest);
  // Before the first audit the person may pick the audit template; after that each round keeps its own.
  // The first audit starts from the template last used in this space, else the enterprise's default customer.
  const allTemplates = useMemo(() => templates(data.rules), [data.rules]);
  const [chosenTemplate, setChosenTemplate] = useState<string | null>(() => {
    const remembered = rememberedTemplate(item.project_key);
    return remembered && allTemplates.has(remembered) ? remembered : null;
  });
  const customer = (flight ?? latest)?.customer_preference ?? (chosenTemplate && templateOwner(chosenTemplate)) ?? data.defaultCustomer;
  const shownTemplate = chosenTemplate ?? currentTemplate(allTemplates, customer, null);
  const missing = useMemo(() => {
    const have = new Set(docs.map(doc => doc.type).filter(Boolean));
    return requiredTypes(data.rules, customer).filter(type => !have.has(type));
  }, [data.rules, customer, docs]);
  const unknownDocs = docs.filter(doc => !doc.type && ['new', 'added', 'matching'].includes(doc.state)).length;

  const [showMissing, setShowMissing] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const seen = useRef<Set<string>>(new Set());
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [open, setOpen] = useState<{ file: string; clause: string } | null>(null);
  const [report, setReport] = useState<TaskFile | null>(null);
  const [reselect, setReselect] = useState(false);
  const [scope, setScope] = useState<'attention' | 'all'>('attention');
  const revise = useRevise(refresh, setBusy, setNotice);
  // Every clause of the latest round, for a person who wants to see what conformed too.
  const shown: OpenIssue[] = useMemo(() => {
    if (scope === 'attention' || !latest) return view.issues;
    const changes = new Map(view.issues.map(issue => [issue.row.file.id + '|' + issue.check.rule_item.id, issue.change]));
    return latest.files
      .filter(row => row.selection === 'selected' && row.audit)
      .flatMap(row => row.audit!.checks.slice().sort(byUrgency).map(check => ({ row, check, change: changes.get(row.file.id + '|' + check.rule_item.id) ?? null })));
  }, [scope, latest, view.issues]);

  // A package is everything attached, so each attachment starts selected; a person's own choices are kept.
  useEffect(() => {
    const fresh = docs.filter(doc => !seen.current.has(doc.attachment.uid) && doc.state !== 'unsupported');
    if (fresh.length) setPicked(previous => new Set([...previous, ...fresh.map(doc => doc.attachment.uid)]));
    docs.forEach(doc => seen.current.add(doc.attachment.uid));
  }, [docs]);

  // Someone else's prepared audit waits for them; anyone may start their own audit of the package instead.
  const foreign = Boolean(flight && !flight.creator.mine);
  const choosing = !flight || (flight.state === 'pending_confirm' && (reselect || foreign));
  const mine = Boolean(latest?.creator.mine);
  const chosen = docs.filter(doc => picked.has(doc.attachment.uid));
  const nodeWaiting = data.aiNode?.status === 'running';
  const [releasing, setReleasing] = useState(false);
  const [reason, setReason] = useState('');

  // When the flow is waiting on PPAP and nobody has audited this work item yet, the first audit is
  // prepared without a click: the whole package is read and matched, and stops at confirmation.
  // Nothing is accepted or charged until the person confirms. Once anyone has a task here, every
  // further audit is a person's own choice.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (!nodeWaiting || autoStarted.current || data.tasks.length || busy || !chosen.length) return;
    autoStarted.current = true;
    void start();
  });

  async function validated(version: FileVersion, name: string, skipped: string[]): Promise<string | null> {
    const job = await call<Job>('/file-validations', { method: 'POST', body: { request_id: newId(), source_file_id: version.file_id, source_file_version_id: version.id } });
    const checked = await waitFor(() => call<Job>(`/file-validations/${job.id}`), value => !['queued', 'leased', 'running'].includes(value.status));
    if (checked.status !== 'succeeded') throw new PpapError('validation_failed', 0);
    // A file that cannot be audited is left out, with its reason shown.
    if (checked.result?.state !== 'valid') {
      skipped.push(`${name}（${(checked.result?.reasons ?? []).join('；') || '无法审核'}）`);
      return null;
    }
    return checked.id;
  }

  // Every audit covers the whole chosen package. After a first round this is a re-audit of that
  // round: files it already froze keep their version and rule, new attachments join as new files,
  // so cross-file requirements are checked against the complete set again.
  async function start() {
    setNotice('');
    // The same start (same package, same earlier round, same customer) retried after its reply was lost
    // or its link to the work item failed: read the audit back and link it, never create a second one.
    const key = itemPath(item);
    const from = [latest?.round_id ?? '', customer ?? '', ...chosen.map(doc => doc.attachment.uid).sort()].join('|');
    try {
      const pending = pendingStart(key);
      if (pending?.from === from) {
        setBusy('正在提交…');
        const found = await call<Task>(`/audit-tasks/${pending.task}`).then(
          () => true,
          failure => {
            // PPAP answers a task that does not exist as not accessible; this id was this person's own.
            if (failure instanceof PpapError && (failure.status === 403 || failure.status === 404)) return false;
            throw failure;
          },
        );
        if (found) {
          await call(`/integrations/meego/tasks/${pending.task}/work-item`, { method: 'PUT', body: item });
          rememberStart(key, null);
          setReselect(false);
          await refresh();
          return;
        }
      }
      rememberStart(key, null);
      const earlier = latest?.round_id ? await call<{ customer_preference: string | null; files: Carried[] }>(`/rounds/${latest.round_id}/reaudit-inputs`) : null;
      const carried = new Map((earlier?.files ?? []).map(entry => [entry.file.id, entry]));
      const inputs: Input[] = [];
      const skipped: string[] = [];
      for (const [index, doc] of chosen.entries()) {
        setBusy(`正在准备资料 ${index + 1}/${chosen.length}…`);
        const known = doc.row && latest?.files.includes(doc.row) ? carried.get(doc.row.file.id) : undefined;
        if (known) {
          const validation = known.validation_id && known.validation?.state === 'valid' ? known.validation_id : await validated(known.file, doc.attachment.name, skipped);
          if (validation) inputs.push({ file_version_id: known.file.id, validation_id: validation, selection: 'selected', removal_reason: null, ...(known.preferred_rule_version_id ? { preferred_rule_version_id: known.preferred_rule_version_id } : known.reference_only ? { reference_only: true } : {}) });
          continue;
        }
        const version = await call<FileVersion>('/integrations/meego/files/import', { method: 'POST', body: { request_id: newId(), ...item, uid: doc.attachment.uid } });
        const validation = await validated(version, doc.attachment.name, skipped);
        if (validation) inputs.push({ file_version_id: version.id, validation_id: validation, selection: 'selected', removal_reason: null });
      }
      if (skipped.length) setNotice(`以下附件无法审核，已跳过：${skipped.join('、')}`);
      if (!inputs.length) return;
      setBusy('正在提交…');
      const taskId = newId();
      // Rules are matched for one customer. Without it the match falls on rules that have no checker program.
      const audited = earlier?.customer_preference ?? customer;
      rememberStart(key, { task: taskId, from });
      await call<Task>('/audit-tasks', { method: 'POST', body: { request_id: taskId, customer_preference: audited, files: inputs, ...(earlier ? { predecessor_id: latest!.round_id } : {}) } });
      await call(`/integrations/meego/tasks/${taskId}/work-item`, { method: 'PUT', body: item });
      rememberStart(key, null);
      setReselect(false);
      await refresh();
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  }

  async function writeBack() {
    if (!latest?.round_id) return;
    setBusy('写回中…');
    setNotice('');
    try {
      const reply = await call<{ field: string; reports?: { state: string; written: number; total: number }; node?: string }>(`/integrations/meego/rounds/${latest.round_id}/writeback`, { method: 'POST' });
      onWritten(latest.round_id);
      const field = { written: '，并更新了“PPAP 审核结论”字段', unchanged: '，“PPAP 审核结论”字段已是该结论', not_configured: '' }[reply.field] ?? '';
      const reports = reply.reports?.state === 'written' && reply.reports.written > 0 ? `，${reply.reports.written} 份审核报告已在“PPAP 审核报告”字段` : '';
      void window.JSSDK.toast.success('已写入工作项评论区' + field + reports + (reply.node === 'completed' ? '，流程节点已完成' : ''));
      // One notice at a time, the one the person can act on first.
      const notes: string[] = [];
      if (reply.field === 'not_configured') notes.push('这个空间还没有“PPAP 审核结论”字段，管理员添加后，结论会同时写入字段，可用于流转条件和视图筛选。');
      else if (reply.field === 'failed' || reply.field === 'unknown') notes.push('“PPAP 审核结论”字段没有更新成功，请检查你对这个字段的编辑权限后再写回一次。');
      if (reply.reports?.state === 'not_configured') notes.push('这个空间还没有“PPAP 审核报告”附件字段，管理员添加后，逐文件审核报告会一并写回。');
      else if (reply.reports?.state === 'pending') notes.push('有审核报告还在生成，生成后再写回一次即可补上。');
      else if (reply.reports?.state === 'failed' || reply.reports?.state === 'unknown') notes.push('审核报告没有全部附上，请稍后再写回一次；已附上的不会重复。');
      if (reply.node === 'review_objection') notes.push('复核人对这次“符合”的结论有异议，流程节点继续等待：重新审核整包，或人工放行。');
      else if (reply.node === 'not_passed') notes.push('结论不是“符合”，流程节点继续等待：整改后重新审核整包；确需例外时，可以人工放行。');
      else if (reply.node === 'failed' || reply.node === 'unknown') notes.push('流程节点没有自动完成，请稍后再写回一次。');
      else if (reply.node === 'not_configured') notes.push('流程里的 PPAP 节点尚未配置完成，请联系管理员。');
      if (notes.length) setNotice('结论已写入评论区。' + notes.join(''));
      await refresh();
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  }

  // The round's creator lets the flow pass although the round did not conform; the conclusion itself stays.
  async function release() {
    if (!latest?.round_id || !reason.trim()) return;
    setBusy('正在放行…');
    setNotice('');
    try {
      const reply = await call<{ node: string }>(`/integrations/meego/rounds/${latest.round_id}/release`, { method: 'POST', body: { reason: reason.trim() } });
      setReleasing(false);
      setReason('');
      if (reply.node === 'completed') void window.JSSDK.toast.success('已放行，流程已进入下一步');
      else setNotice('放行已记录，但流程节点没有自动完成，请稍后再试一次。');
      await refresh();
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  }

  // Files and folders go into the work item's own attachment field with the person's Meego identity,
  // so the work item stays the one place that holds the package; the list below then reads them as usual.
  const filePicker = useRef<HTMLInputElement>(null);
  const folderPicker = useRef<HTMLInputElement>(null);
  async function upload(list: FileList | null) {
    // Hidden and system files in a folder are not PPAP material.
    const files = [...(list ?? [])].filter(file => !file.name.startsWith('.') && !['Thumbs.db', 'desktop.ini'].includes(file.name));
    if (!files.length) return;
    // A folder may hold two files of one name in different subfolders; the work item keeps names, so the first one goes.
    const seen = new Set<string>();
    const unique = files.filter(file => !seen.has(file.name) && Boolean(seen.add(file.name)));
    const repeated = files.length - unique.length;
    if (docs.length + unique.length > ATTACHMENT_LIMIT) {
      setNotice(`一个附件字段最多 ${ATTACHMENT_LIMIT} 份文件，这次选了 ${unique.length} 份，已有 ${docs.length} 份。请分开上传或减少文件。`);
      return;
    }
    setNotice('');
    const existed: string[] = [];
    const failed: string[] = [];
    for (const [index, file] of unique.entries()) {
      setBusy(`正在上传 ${index + 1}/${unique.length}…`);
      try {
        const reply = await call<{ state: string }>(`${itemPath(item)}/files?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file });
        if (reply.state === 'exists') existed.push(file.name);
      } catch (failure) {
        failed.push(`${file.name}（${say(failure)}）`);
      }
    }
    setBusy('');
    const notes = [
      `已上传 ${unique.length - existed.length - failed.length} 份。`,
      existed.length ? `${existed.length} 份同名文件已在工作项里，没有重复上传。` : '',
      repeated ? `文件夹里有 ${repeated} 份同名文件，只上传了第一份。` : '',
      failed.length ? `没有上传成功：${failed.join('；')}` : '',
    ];
    setNotice(notes.filter(Boolean).join(''));
    await refresh().catch(failure => setNotice(say(failure)));
  }
  const uploader = (
    <>
      <input ref={filePicker} type="file" multiple hidden onChange={event => { void upload(event.target.files); event.target.value = ''; }} />
      <input ref={folderPicker} type="file" multiple hidden {...{ webkitdirectory: '' }} onChange={event => { void upload(event.target.files); event.target.value = ''; }} />
      <Dropdown
        trigger="click"
        position="bottomRight"
        render={
          <Dropdown.Menu>
            <Dropdown.Item onClick={() => filePicker.current?.click()}>选择文件</Dropdown.Item>
            <Dropdown.Item onClick={() => folderPicker.current?.click()}>选择文件夹</Dropdown.Item>
          </Dropdown.Menu>
        }
      >
        <Button disabled={Boolean(busy)}>上传资料</Button>
      </Dropdown>
    </>
  );

  async function download(row: TaskFile) {
    try {
      save(await bytes(reportPath(latest!, row)), row.audit!.report.artifact!.filename);
    } catch (failure) {
      setNotice(say(failure));
    }
  }

  async function regenerate(row: TaskFile) {
    const audit = row.audit!;
    setBusy('正在重新生成报告…');
    setNotice('');
    try {
      await call(`/audit-tasks/${latest!.id}/reports/${audit.report.report_id}/regenerations`, {
        method: 'POST',
        body: { request_id: newId(), reference: { task_id: latest!.id, round_id: audit.round_id, file_version_id: row.file.id, result_attempt: audit.result_attempt, report_id: audit.report.report_id } },
      });
      await refresh();
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  }

  async function copyList() {
    const text = rectification(view);
    try {
      await navigator.clipboard.writeText(text);
      void window.JSSDK.toast.success('整改清单已复制');
    } catch {
      // The host clipboard takes at most 1000 characters; a longer list is saved whole instead of cut short.
      if (text.length > 1000) {
        save(new Blob([text], { type: 'text/plain;charset=utf-8' }), '整改清单.txt');
        void window.JSSDK.toast.success('整改清单较长，已下载为文本文件');
        return;
      }
      const done = await window.JSSDK.clipboard.writeText(text).catch(() => false);
      void (done ? window.JSSDK.toast.success('整改清单已复制') : window.JSSDK.toast.error('没有复制成功，请稍后重试'));
    }
  }

  const ngCount = view.issues.filter(issue => bucket(issue.check) === 'NG').length;
  const written = latest?.round_id ? (data.written === null ? null : data.written.includes(latest.round_id)) : null;
  // Writing back and releasing speak for the round on the work item: only its creator does either.
  const writable = !flight && mine && Boolean(latest?.round_id) && ['PASS', 'NG', 'UNKNOWN'].includes(total);
  const current = open && shown.find(issue => issue.row.file.id === open.file && issue.check.rule_item.id === open.clause);
  const startLabel = !latest ? `审核 ${chosen.length} 份资料` : mine ? `重新审核整包（${chosen.length} 份）` : `由我重新审核整包（${chosen.length} 份）`;
  // Once the package has changed, the next step is a new round; until then it is sharing the conclusion.
  const startFirst = !latest || pack.stale || !writable || written === true;
  const released = latest?.round_id ? data.releases[latest.round_id] : undefined;
  // Only the latest, unchanged, written-back round of a waiting node can be released.
  // A reviewer's objection keeps a conforming round from completing the node on its own; the conclusion stays as found.
  const objection = Boolean(latest?.objection);
  const releasable = nodeWaiting && mine && !released && !pack.stale && (total === 'NG' || total === 'UNKNOWN' || (total === 'PASS' && objection));
  // Written back and conforming, the node completes itself, unless a reviewer objects.
  const waitingText =
    total === 'PASS' && objection && !pack.stale
      ? '流程正在等待 PPAP 审核：复核人对这次“符合”的结论有异议，写回后流程仍会等待。重新审核整包，或由发起人人工放行。'
      : '流程正在等待 PPAP 审核：核对资料并确认后开始审核；结论为“符合”并写回后，流程自动进入下一步。';

  // One primary action, chosen by where the package stands; the rest wait in the menu.
  const canStart = choosing && chosen.length > 0;
  const canWrite = writable && written !== true;
  const primary: 'start' | 'write' | null = canStart && startFirst ? 'start' : canWrite ? 'write' : canStart ? 'start' : null;
  const outside = data.aiNode?.status === 'completed' && Boolean(data.aiNode.outside);
  // Where the flow stands, said once: the first of these that applies.
  const flow: { tone: Tone; text: string; spin?: boolean; action?: { label: string; run: () => void } } | null =
    total === 'ERROR' && !flight
      ? { tone: 'warning', text: '上次审核出现技术错误，结果不完整，不能写回。请重新审核。' }
      : stalled
        ? { tone: 'info', text: '处理时间较长。', action: { label: '刷新', run: () => void refresh() } }
        : flight && flight.state !== 'pending_confirm' && flight.state !== 'matching'
          ? { tone: 'info', spin: true, text: `审核进行中${flight.progress ? `，已完成 ${flight.progress.completed}/${flight.progress.total} 份` : ''}。可以离开这个页面，审核会继续。` }
          : foreign && flight!.state === 'pending_confirm'
            ? { tone: 'info', text: `${flight!.creator.name || '同事'} 已准备一次审核，等待其确认。你也可以由自己审核整包。` }
          : pack.stale && !flight
            ? { tone: 'warning', text: `资料在上次审核后有变化${staleWords(pack)}。当前结论针对的是变化前的资料，重新审核整包后才反映现状。` }
            : outside
              ? { tone: 'warning', text: `${outsideText}审核结论保持为“${verdictLabel[total]}”。` }
              : nodeWaiting
                ? { tone: 'info', text: waitingText }
                : released
                  ? { tone: 'neutral', text: `${released.by || '有人'} 于 ${when(released.at)} 人工放行了这次审核${data.aiNode?.status === 'completed' ? '，流程已进入下一步' : ''}。理由：${released.reason}。审核结论保持为“${verdictLabel[total]}”。` }
                  : null;
  const meta = [
    `${docs.length} 份资料`,
    latest && !mine && `${latest.creator.name || '同事'} 发起`,
    latest && when(latest.round_started_at ?? latest.created_at),
    latest && (ngCount ? `不符合 ${ngCount} 项` : ''),
    latest && (view.issues.length ? `待处理 ${view.issues.length} 项` : ''),
    latest?.round_id && written !== null && (written ? '已写回' : '尚未写回'),
  ].filter(Boolean);
  const issuesFirst = Boolean(latest && view.issues.length);
  const issuesRef = useRef<HTMLElement>(null);
  const goToIssues = () => {
    setScope('attention');
    issuesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const issuesSection = latest && (
    <section className="section" ref={issuesRef} aria-labelledby="ppap-issues">
      <div className="section-head">
        <h3 id="ppap-issues">需要处理<span className="count">{view.issues.length}</span></h3>
        <RadioGroup type="button" buttonSize="small" aria-label="显示范围" value={scope} onChange={event => setScope(event.target.value as 'attention' | 'all')}>
          <Radio value="attention">待处理</Radio>
          <Radio value="all">全部检查项</Radio>
        </RadioGroup>
      </div>
      {shown.length === 0 ? (
        <Text type="tertiary">没有待处理的问题{view.resolved.length ? `，上一次的 ${view.resolved.length} 个问题已解决` : ''}。</Text>
      ) : (
        <div className={current ? 'split has-detail' : 'split'}>
          <nav className="issues" aria-label="检查项">
            <IssueList issues={shown} facts={data.facts} current={open} onOpen={issue => setOpen({ file: issue.row.file.id, clause: issue.check.rule_item.id })} />
            {view.resolved.length > 0 && (
              <Collapse>
                <Collapse.Panel header={`已解决 ${view.resolved.length} 项`} itemKey="resolved">
                  <List size="small" dataSource={view.resolved} renderItem={entry => <List.Item main={<Text ellipsis={{ showTooltip: true, rows: 2 }}>{entry.check.rule_item.rule_to_check}</Text>} extra={<Tag color="green" type="light" size="small">已解决</Tag>} />} />
                </Collapse.Panel>
              </Collapse>
            )}
          </nav>
          <section className="detail">
            {current ? (
              <Issue task={latest} row={current.row} check={current.check} position={current.row.audit!.checks.indexOf(current.check) + 1} stale={pack.stale} facts={data.facts} onBack={() => setOpen(null)} onChanged={refresh} />
            ) : (
              <Text type="tertiary" className="placeholder">选择一项，对照原件核对并记录复核。</Text>
            )}
          </section>
        </div>
      )}
    </section>
  );

  const docsSection = (
    <section className="section" aria-labelledby="ppap-docs">
      <div className="section-head">
        <h3 id="ppap-docs">资料<span className="count">{docs.length}</span></h3>
        {docs.length > 0 && uploader}
      </div>
      <DocTable uploader={uploader} docs={docs} fields={data.fields} flight={flight} revise={revise} selectable={choosing && !busy} busy={Boolean(busy)} picked={picked} onPick={setPicked} onIssues={goToIssues} onReport={setReport} onDownload={row => void download(row)} onRegenerate={row => void regenerate(row)} onRefresh={() => void refresh().catch(failure => setNotice(say(failure)))} />
      {customer && missing.length > 0 && (
        <div className="missing">
          <Text type="tertiary">{customer} 要求的资料中，还有 {missing.length} 类没有提交。</Text>
          <Button theme="borderless" size="small" onClick={() => setShowMissing(value => !value)}>{showMissing ? '收起' : `查看 ${missing.length} 类`}</Button>
          {showMissing && (
            <div className="missing-list">
              {unknownDocs > 0 && <Text type="tertiary" size="small">有 {unknownDocs} 份附件还没有识别类型，审核后这里会更新。</Text>}
              <Text>{missing.map(documentTypeLabel).join('、')}</Text>
            </div>
          )}
        </div>
      )}
    </section>
  );

  return (
    <div className="workspace">
      <header className="hero">
        <div className="hero-main">
          <span className={`hero-verdict tone-${toneOf(total)}`}>
            <span className="dot" aria-hidden="true" />
            {verdictLabel[total]}
          </span>
          <span className="hero-meta">{meta.join(' · ')}</span>
        </div>
        <div className="hero-actions">
          {choosing && !latest && allTemplates.size > 1 && (
            <Select
              size="default"
              aria-label="审核模板"
              prefix="审核模板"
              disabled={Boolean(busy)}
              value={shownTemplate ?? undefined}
              placeholder="按文件自动匹配"
              optionList={[...allTemplates.keys()].sort((a, b) => templateName(a).localeCompare(templateName(b), 'zh-CN')).map(key => ({ value: key, label: templateName(key) }))}
              onChange={value => {
                setChosenTemplate(String(value));
                rememberTemplate(item.project_key, String(value));
              }}
            />
          )}
          {releasable && <Button type="warning" disabled={Boolean(busy)} onClick={() => setReleasing(true)}>人工放行</Button>}
          {primary === 'start' && <Button theme="solid" type="primary" loading={Boolean(busy)} onClick={() => void start()}>{startLabel}</Button>}
          {primary === 'write' && <Button theme="solid" type="primary" loading={Boolean(busy)} onClick={() => void writeBack()}>写回工作项</Button>}
          <Dropdown
            trigger="click"
            position="bottomRight"
            render={
              <Dropdown.Menu>
                {primary !== 'start' && canStart && <Dropdown.Item disabled={Boolean(busy)} onClick={() => void start()}>{startLabel}</Dropdown.Item>}
                {primary !== 'write' && canWrite && <Dropdown.Item disabled={Boolean(busy)} onClick={() => void writeBack()}>写回工作项</Dropdown.Item>}
                {/* Writing back again is safe: the comment is not repeated, and the conclusion field is brought up to date. */}
                {writable && written === true && <Dropdown.Item disabled={Boolean(busy)} onClick={() => void writeBack()}>重新写回</Dropdown.Item>}
                {ngCount > 0 && <Dropdown.Item onClick={() => void copyList()}>复制整改清单</Dropdown.Item>}
                {(flight ?? latest) && <Dropdown.Item onClick={() => window.JSSDK.navigation.open(taskLink((flight ?? latest)!.id), '_blank')}>在 PPAP 中打开这次审核</Dropdown.Item>}
                <Dropdown.Divider />
                <Dropdown.Title>以 {user} 的 PPAP 身份使用</Dropdown.Title>
                <Dropdown.Item onClick={onDisconnect}>断开连接</Dropdown.Item>
              </Dropdown.Menu>
            }
          >
            <Button theme="borderless" type="tertiary" icon={<IconMore />} aria-label="更多操作" />
          </Dropdown>
        </div>
      </header>
      {busy && <p className="flow tone-info" role="status"><Spin size="small" /><span>{busy}</span></p>}
      {notice && <Banner type="warning" fullMode={false} description={notice} onClose={() => setNotice('')} />}
      {flow && !busy && (
        <p className={`flow tone-${flow.tone}`} role="status">
          {flow.spin ? <Spin size="small" /> : <span className="dot" aria-hidden="true" />}
          <span>
            {flow.text}
            {flow.action && <Button theme="borderless" size="small" onClick={flow.action.run}>{flow.action.label}</Button>}
          </span>
        </p>
      )}
      <Modal
        title="人工放行"
        visible={releasing}
        okText="确认放行"
        okButtonProps={{ type: 'warning', disabled: !reason.trim() }}
        confirmLoading={Boolean(busy)}
        onOk={() => void release()}
        onCancel={() => setReleasing(false)}
      >
        <Space vertical align="start" spacing="tight" style={{ width: '100%' }}>
          <Text>{total === 'PASS' ? '复核人对这次“符合”的结论有异议。' : `这次审核的结论是“${verdictLabel[total]}”，放行后结论不会改为“符合”。`}放行后流程进入下一步，放行人和理由会写入工作项评论。</Text>
          <Text strong>放行理由</Text>
          <TextArea aria-label="放行理由" value={reason} maxLength={500} autosize={{ minRows: 2, maxRows: 6 }} onChange={setReason} placeholder="例如：客户已书面接受该偏差" />
        </Space>
      </Modal>
      {flight && !foreign && flight.state === 'pending_confirm' && !reselect && <Confirm task={flight} rules={data.rules} space={item.project_key} revise={revise} refresh={refresh} onReselect={() => setReselect(true)} />}

      {report && latest ? (
        <article className="report-view" aria-label="审核报告">
          <div className="report-head">
            <Button size="small" theme="borderless" onClick={() => setReport(null)}>返回</Button>
            <Text strong ellipsis={{ showTooltip: true }}>{report.audit!.report.artifact!.filename}</Text>
            <Button size="small" icon={<IconDownload />} onClick={() => void download(report)}>下载</Button>
          </div>
          <PdfView key={report.file.id} name={report.audit!.report.artifact!.filename} load={() => bytes(reportPath(latest, report))} />
        </article>
      ) : issuesFirst ? (
        <>
          {issuesSection}
          {docsSection}
        </>
      ) : (
        <>
          {docsSection}
          {issuesSection}
        </>
      )}
    </div>
  );
}

type Tone = 'pass' | 'ng' | 'warning' | 'info' | 'neutral';
const toneOf = (total: keyof typeof verdictLabel): Tone => (total === 'PASS' ? 'pass' : total === 'NG' ? 'ng' : total === 'NONE' ? 'neutral' : 'warning');

function staleWords(pack: Package): string {
  const added = pack.docs.filter(doc => doc.state === 'added').length;
  const parts = [added ? `新增 ${added} 份` : '', pack.removed.length ? `移除 ${pack.removed.length} 份` : ''].filter(Boolean);
  return parts.length ? `（${parts.join('，')}）` : '';
}

// Feishu Project keeps at most this many files in one attachment field.
const ATTACHMENT_LIMIT = 50;

// The documents ---------------------------------------------------------

type DocTableProps = { uploader: ReactNode; docs: Doc[]; fields: Record<string, string>; flight: Task | null; revise: Revise; selectable: boolean; busy: boolean; picked: Set<string>; onPick: (next: Set<string>) => void; onIssues: () => void; onReport: (row: TaskFile) => void; onDownload: (row: TaskFile) => void; onRegenerate: (row: TaskFile) => void; onRefresh: () => void };

function DocTable(props: DocTableProps) {
  const { docs, fields, selectable, picked } = props;
  if (!docs.length)
    return (
      <Empty title="这个工作项还没有资料" description="上传这一包 PPAP 文件，可以一次选择整个文件夹。在工作项里直接添加的附件，刷新后也会出现。">
        <Space>
          {props.uploader}
          <Button theme="borderless" onClick={props.onRefresh}>刷新</Button>
        </Space>
      </Empty>
    );
  const columns = [
    {
      title: '资料',
      dataIndex: 'name',
      render: (_: unknown, doc: Doc) => (
        <div className="doc-name">
          <Text ellipsis={{ showTooltip: true }}>{doc.attachment.name}</Text>
          <Text type="tertiary" size="small">{[doc.type && documentTypeLabel(doc.type), fields[doc.attachment.field_key], doc.attachment.size].filter(Boolean).join(' · ')}</Text>
        </div>
      ),
    },
    { title: '审核结果', dataIndex: 'state', width: '42%', render: (_: unknown, doc: Doc) =>
        doc.state === 'confirm' && props.flight?.creator.mine ? <ConfirmRow task={props.flight} row={doc.row!} revise={props.revise} disabled={props.busy} /> : <DocStatus doc={doc} onIssues={props.onIssues} onReport={props.onReport} onDownload={props.onDownload} onRegenerate={props.onRegenerate} /> },
  ];
  return (
    <Table
      size="small"
      pagination={false}
      rowKey={(doc?: Doc) => doc!.attachment.uid}
      columns={columns}
      dataSource={docs}
      // Choosing the package is the only time a row is picked; otherwise there is nothing to tick.
      rowSelection={
        selectable
          ? {
              selectedRowKeys: [...picked],
              onChange: keys => props.onPick(new Set((keys || []).map(String))),
            }
          : undefined
      }
    />
  );
}

function DocStatus({ doc, onIssues, onReport, onDownload, onRegenerate }: { doc: Doc; onIssues: () => void; onReport: (row: TaskFile) => void; onDownload: (row: TaskFile) => void; onRegenerate: (row: TaskFile) => void }) {
  const row = doc.row;
  const audit = row?.audit;
  switch (doc.state) {
    case 'unsupported':
      return <Text type="tertiary">不是可审核的格式（Excel、Word、PDF）</Text>;
    case 'new':
      return <Text type="tertiary">未审核</Text>;
    case 'added':
      return <Tag color="orange" type="light">上次审核后新增，尚未审核</Tag>;
    case 'matching':
      return <Text type="tertiary"><Spin size="small" /> 正在识别类型和规则…</Text>;
    case 'confirm':
      return <Text type="tertiary">待发起人确认</Text>;
    case 'running':
      // Only a matched file is audited; the others already have their final answer.
      if (!audit) return row!.selection === 'selected' && row!.match?.state === 'matched' && !row!.reference_only ? <Text type="tertiary">排队中</Text> : <Unaudited state={unaudited(row!)} reason={row!.removal_reason} />;
      return (
        <div className="progress">
          <Progress percent={audit.progress.total ? Math.round((audit.progress.completed / audit.progress.total) * 100) : 0} aria-label={`${doc.attachment.name} 的审核进度`} />
          <Text type="tertiary" size="small">{audit.progress.completed}/{audit.progress.total} 项</Text>
        </div>
      );
    case 'failed':
      return <Tag color="orange" type="light">审核异常</Tag>;
    case 'reference':
    case 'no_rule':
    case 'unavailable':
    case 'unread':
    case 'removed':
      return <Unaudited state={doc.state} reason={row?.removal_reason} />;
    case 'done': {
      const open = audit!.checks.filter(check => check.attention).length;
      const conclusion = audit!.confirmed_conclusion;
      return (
        <Space wrap>
          <Tag color={bucketColor(conclusion)} type="light">{conclusion ? conclusionLabel[conclusion] : '尚有未确定项'}</Tag>
          {open > 0 && <Text link onClick={onIssues}>{open} 项待处理</Text>}
          {reportReady(row!) && (
            <span className="row-actions">
              <Tooltip content="查看报告">
                <Button size="small" theme="borderless" type="tertiary" icon={<IconEyeOpened />} aria-label={`查看 ${doc.attachment.name} 的审核报告`} onClick={() => onReport(row!)} />
              </Tooltip>
              <Tooltip content="下载报告">
                <Button size="small" theme="borderless" type="tertiary" icon={<IconDownload />} aria-label={`下载 ${doc.attachment.name} 的审核报告`} onClick={() => onDownload(row!)} />
              </Tooltip>
            </span>
          )}
          {audit!.report.state === 'pending' || audit!.report.state === 'generating' ? <Text type="tertiary" size="small">报告生成中…</Text> : null}
          {audit!.report.state === 'failed' && (audit!.report.regenerate_allowed && audit!.report.report_id ? <Text link onClick={() => onRegenerate(row!)}>报告生成失败，重新生成</Text> : <Text type="tertiary" size="small">报告生成失败</Text>)}
        </Space>
      );
    }
  }
}

function Unaudited({ state, reason }: { state: string; reason?: string | null }) {
  if (state === 'removed') return <Text type="tertiary">{reason ? `不审核：${reason}` : '不审核'}</Text>;
  if (state === 'unavailable')
    return (
      <Space wrap>
        <Tag color="orange" type="light">暂不能审核</Tag>
        <Text type="tertiary" size="small">这类文件还没有可用的检查程序</Text>
      </Space>
    );
  return <Text type="tertiary">{state === 'reference' ? '作为参考资料，未单独审核' : state === 'unread' ? '识别失败，未审核' : '没有可用规则，未审核'}</Text>;
}

// What needs a person ------------------------------------------------------

function IssueList({ issues, facts, current, onOpen }: { issues: OpenIssue[]; facts: FactView | null; current: { file: string; clause: string } | null; onOpen: (issue: OpenIssue) => void }) {
  const groups = useMemo(() => {
    const byFile = new Map<string, OpenIssue[]>();
    issues.forEach(issue => byFile.set(issue.row.file.id, [...(byFile.get(issue.row.file.id) || []), issue]));
    return [...byFile.values()];
  }, [issues]);
  return (
    <Collapse defaultActiveKey={groups.map(group => group[0].row.file.id)} keepDOM>
      {groups.map(group => (
        <Collapse.Panel key={group[0].row.file.id} itemKey={group[0].row.file.id} header={<Text ellipsis={{ showTooltip: true }} strong>{group[0].row.file.original_filename} · {group.length} 项</Text>}>
          <List
            size="small"
            dataSource={group}
            renderItem={issue => {
              const kind = bucket(issue.check);
              const on = current?.file === issue.row.file.id && current.clause === issue.check.rule_item.id;
              const openIt = () => onOpen(issue);
              return (
                <List.Item
                  className={on ? 'issue-row on' : 'issue-row'}
                  onClick={openIt}
                  main={
                    // The clause gets the row's full width; its state sits under it, not in a column beside it.
                    <span className="issue-text" role="button" tabIndex={0} aria-current={on ? 'true' : undefined} onKeyDown={event => (event.key === 'Enter' || event.key === ' ') && (event.preventDefault(), openIt())}>
                      <Text ellipsis={{ showTooltip: true, rows: 3 }}>{issue.check.rule_item.rule_to_check}</Text>
                      <Space spacing={4} wrap>
                        <Tag size="small" color={bucketColor(kind)} type="light">{bucketLabel(kind)}</Tag>
                        {issue.change && <Tag size="small" color={issue.change === 'added' ? 'red' : 'grey'} type="ghost">{issue.change === 'added' ? '新出现' : '仍存在'}</Tag>}
                        {openFacts(facts, issue.check).length > 0 && <Tag size="small" color="blue" type="ghost">待确认事实</Tag>}
                        {issue.check.confirmation && <Tag size="small" color="grey" type="ghost">依据确认的事实</Tag>}
                        {issue.check.objected ? <Tag size="small" color="orange" type="ghost">复核有异议</Tag> : reviewOf(issue.check) && <Tag size="small" color="blue" type="ghost">已复核</Tag>}
                      </Space>
                    </span>
                  }
                />
              );
            }}
          />
        </Collapse.Panel>
      ))}
    </Collapse>
  );
}
