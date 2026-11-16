// One surface for a work item's PPAP package: what is attached, what the latest round
// concluded about it, what is still missing, and what needs a person. Starting, confirming and
// following an audit all happen on the document list itself.
import { Banner, Button, Collapse, Empty, List, Progress, Radio, RadioGroup, Select, Space, Spin, Table, TabPane, Tabs, Tag, Typography } from '@douyinfe/semi-ui';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Data, Item } from './data';
import { Confirm, ConfirmRow, useRevise } from './Confirm';
import type { Revise } from './Confirm';
import { PdfView } from './Evidence';
import { Issue } from './Issue';
import { buildPackage, rectification, requiredTypes, standing, unaudited, verdict } from './ledger';
import type { Doc, OpenIssue, Package } from './ledger';
import { bucket, bucketColor, bucketLabel, byUrgency, conclusionLabel, documentTypeLabel, reviewOf, when } from './model';
import type { FileVersion, Job, Task, TaskFile } from './model';
import { bytes, call, newId, PpapError, save, say, taskLink, waitFor } from './ppap';

const { Text } = Typography;
type Props = { item: Item; data: Data; stalled: boolean; refresh: () => Promise<void>; onWritten: (roundId: string) => void };
type Input = { file_version_id: string; validation_id: string; selection: 'selected'; removal_reason: null; preferred_rule_version_id?: string; reference_only?: boolean };
type Carried = { file: FileVersion; validation_id?: string; validation?: { state?: string }; preferred_rule_version_id?: string; reference_only?: boolean };

const verdictLabel = { PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项', ERROR: '审核异常', NONE: '尚未审核' } as const;
const reportReady = (row: TaskFile) => Boolean(row.audit?.report.state === 'ready' && row.audit.report.report_id && row.audit.report.artifact && row.audit.result_attempt !== null);
const reportPath = (task: Task, row: TaskFile) => {
  const audit = row.audit!;
  const query = new URLSearchParams({ round_id: audit.round_id, file_version_id: row.file.id, result_attempt: String(audit.result_attempt), artifact_id: audit.report.artifact!.artifact_id });
  return `/audit-tasks/${task.id}/reports/${audit.report.report_id}/pdf?${query}`;
};

export function Workspace({ item, data, stalled, refresh, onWritten }: Props) {
  const pack = useMemo(() => buildPackage(data.attachments, data.tasks, data.sources), [data]);
  const { docs, flight, latest } = pack;
  const view = useMemo(() => standing(latest, pack.previous), [latest, pack.previous]);
  const total = verdict(latest);
  // Before the first audit the person may pick the customer; after that each round keeps its own.
  const [chosenCustomer, setChosenCustomer] = useState<string | null>(null);
  const [customers, setCustomers] = useState<string[]>([]);
  useEffect(() => {
    call<{ customers: { name: string }[] }>('/audit-customers').then(reply => setCustomers(reply.customers.map(row => row.name)), () => setCustomers([]));
  }, []);
  const customer = (flight ?? latest)?.customer_preference ?? chosenCustomer ?? data.defaultCustomer;
  const missing = useMemo(() => {
    const have = new Set(docs.map(doc => doc.type).filter(Boolean));
    return requiredTypes(data.rules, customer).filter(type => !have.has(type));
  }, [data.rules, customer, docs]);
  const unknownDocs = docs.filter(doc => !doc.type && ['new', 'added', 'matching'].includes(doc.state)).length;

  const [tab, setTab] = useState('docs');
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

  const choosing = !flight || (flight.state === 'pending_confirm' && reselect);
  const chosen = docs.filter(doc => picked.has(doc.attachment.uid));

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
    try {
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
      await call<Task>('/audit-tasks', { method: 'POST', body: { request_id: taskId, customer_preference: audited, files: inputs, ...(earlier ? { predecessor_id: latest!.round_id } : {}) } });
      await call(`/integrations/meego/tasks/${taskId}/work-item`, { method: 'PUT', body: item });
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
      const reply = await call<{ field: string }>(`/integrations/meego/rounds/${latest.round_id}/writeback`, { method: 'POST' });
      onWritten(latest.round_id);
      const field = { written: '，并更新了“PPAP 审核结论”字段', unchanged: '，“PPAP 审核结论”字段已是该结论', not_configured: '' }[reply.field] ?? '';
      void window.JSSDK.toast.success('已写入工作项评论区' + field);
      if (reply.field === 'not_configured') setNotice('结论已写入评论区。这个空间还没有“PPAP 审核结论”字段，管理员添加后，结论会同时写入字段，可用于流转条件和视图筛选。');
      else if (reply.field === 'failed' || reply.field === 'unknown') setNotice('结论已写入评论区，但“PPAP 审核结论”字段没有更新成功，请检查你对这个字段的编辑权限后再写回一次。');
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  }

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
      const done = await window.JSSDK.clipboard.writeText(text.slice(0, 1000)).catch(() => false);
      void (done ? window.JSSDK.toast.success(text.length > 1000 ? '已复制整改清单的前一部分' : '整改清单已复制') : window.JSSDK.toast.error('没有复制成功，请稍后重试'));
    }
  }

  const ngCount = view.issues.filter(issue => bucket(issue.check) === 'NG').length;
  const written = latest?.round_id ? (data.written === null ? null : data.written.includes(latest.round_id)) : null;
  const writable = !flight && Boolean(latest?.round_id) && ['PASS', 'NG', 'UNKNOWN'].includes(total);
  const current = open && shown.find(issue => issue.row.file.id === open.file && issue.check.rule_item.id === open.clause);
  const startLabel = !latest ? `审核 ${chosen.length} 份资料` : `重新审核整包（${chosen.length} 份）`;
  // Once the package has changed, the next step is a new round; until then it is sharing the conclusion.
  const startFirst = !latest || pack.stale || !writable || written === true;

  return (
    <div className="workspace">
      <header className="bar">
        <Space wrap align="center">
          <Tag size="large" type="solid" color={total === 'NONE' ? 'grey' : total === 'ERROR' ? 'orange' : bucketColor(total)}>{verdictLabel[total]}</Tag>
          {latest && <Text>不符合 <Text strong>{ngCount}</Text> 项 · 待处理 <Text strong>{view.issues.length}</Text> 项{view.resolved.length > 0 && <> · 已解决 <Text strong>{view.resolved.length}</Text> 项</>}</Text>}
          {latest && <Text type="tertiary" size="small">{when(latest.round_started_at ?? latest.created_at)} 的审核</Text>}
        </Space>
        <Space wrap>
          {choosing && !latest && customers.length > 1 && (
            <Select
              size="default"
              aria-label="审核客户"
              prefix="审核客户"
              disabled={Boolean(busy)}
              value={customer ?? undefined}
              optionList={customers.map(name => ({ value: name, label: name }))}
              onChange={value => setChosenCustomer(String(value))}
            />
          )}
          {choosing && chosen.length > 0 && <Button theme={startFirst ? 'solid' : 'light'} type="primary" loading={Boolean(busy)} onClick={() => void start()}>{startLabel}</Button>}
          {writable && written !== true && <Button theme={startFirst && choosing && chosen.length > 0 ? 'light' : 'solid'} type="primary" disabled={Boolean(busy)} onClick={() => void writeBack()}>写回工作项</Button>}
          {writable && written === true && (
            <>
              <Tag color="green" type="light">已写回工作项</Tag>
              {/* Writing back again is safe: the comment is not repeated, and the conclusion field is brought up to date. */}
              <Button theme="borderless" size="small" disabled={Boolean(busy)} onClick={() => void writeBack()}>重新写回</Button>
            </>
          )}
        </Space>
      </header>
      {busy && <Text type="tertiary" role="status"><Spin size="small" /> {busy}</Text>}
      {notice && <Banner type="warning" fullMode={false} description={notice} onClose={() => setNotice('')} />}
      {stalled && <Banner type="info" fullMode={false} closeIcon={null} description={<>处理时间较长。<Text link onClick={() => void refresh()}>刷新</Text></>} />}
      {pack.stale && !flight && <Banner type="warning" fullMode={false} closeIcon={null} description={`资料在上次审核后有变化${staleWords(pack)}。当前结论针对的是变化前的资料，重新审核整包后才反映现状。`} />}
      {total === 'ERROR' && <Banner type="warning" fullMode={false} closeIcon={null} description="上次审核出现技术错误，结果不完整，不能写回。请重新审核。" />}
      {flight && flight.state !== 'pending_confirm' && <Running task={flight} />}
      {flight && flight.state === 'pending_confirm' && !reselect && <Confirm task={flight} revise={revise} refresh={refresh} onReselect={() => setReselect(true)} />}

      <Tabs type="line" activeKey={tab} onChange={setTab}>
        <TabPane tab={`资料 ${docs.length}`} itemKey="docs">
          {report && latest ? (
            <article className="report-view" aria-label="审核报告">
              <Space className="report-head" wrap>
                <Button size="small" theme="borderless" onClick={() => setReport(null)}>返回资料</Button>
                <Text strong ellipsis={{ showTooltip: true }}>{report.audit!.report.artifact!.filename}</Text>
                <Button size="small" onClick={() => void download(report)}>下载报告</Button>
              </Space>
              <PdfView key={report.file.id} name={report.audit!.report.artifact!.filename} load={() => bytes(reportPath(latest, report))} />
            </article>
          ) : (
            <DocTable docs={docs} fields={data.fields} flight={flight} revise={revise} selectable={choosing && !busy} busy={Boolean(busy)} picked={picked} onPick={setPicked} onIssues={() => setTab('issues')} onReport={setReport} onDownload={row => void download(row)} onRegenerate={row => void regenerate(row)} onRefresh={() => void refresh().catch(failure => setNotice(say(failure)))} />
          )}
          {customer && missing.length > 0 && (
            <Collapse>
              <Collapse.Panel header={`${customer} 要求的资料中，还有 ${missing.length} 类没有提交`} itemKey="missing">
                {unknownDocs > 0 && <Text type="tertiary" size="small">有 {unknownDocs} 份附件还没有识别类型，审核后这里会更新。</Text>}
                <List size="small" split={false} dataSource={missing} renderItem={type => <List.Item main={<Text>{documentTypeLabel(type)}</Text>} />} />
              </Collapse.Panel>
            </Collapse>
          )}
        </TabPane>
        <TabPane tab={`问题 ${view.issues.length}`} itemKey="issues">
          {latest && (
            <RadioGroup type="button" buttonSize="small" aria-label="显示范围" value={scope} onChange={event => setScope(event.target.value as 'attention' | 'all')} className="scope">
              <Radio value="attention">待处理 {view.issues.length}</Radio>
              <Radio value="all">全部检查项</Radio>
            </RadioGroup>
          )}
          {shown.length === 0 ? (
            <Empty title={latest ? '没有待处理的问题' : '还没有审核结果'} description={!latest ? '在“资料”里开始审核。' : view.resolved.length ? `上一次的 ${view.resolved.length} 个问题已解决。` : undefined} />
          ) : (
            <div className={current ? 'split has-detail' : 'split'}>
              <nav className="issues" aria-label="待处理的问题">
                {ngCount > 0 && <Button size="small" onClick={() => void copyList()}>复制整改清单</Button>}
                <IssueList issues={shown} current={open} onOpen={issue => setOpen({ file: issue.row.file.id, clause: issue.check.rule_item.id })} />
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
                  <Issue task={latest!} row={current.row} check={current.check} position={current.row.audit!.checks.indexOf(current.check) + 1} stale={pack.stale} onBack={() => setOpen(null)} onChanged={refresh} />
                ) : (
                  <Empty className="placeholder" description="选择一项，对照原件核对并记录复核。" />
                )}
              </section>
            </div>
          )}
        </TabPane>
      </Tabs>

      {(flight ?? latest) && <Text link size="small" onClick={() => window.JSSDK.navigation.open(taskLink((flight ?? latest)!.id), '_blank')}>在 PPAP 中打开这次审核</Text>}
    </div>
  );
}

function staleWords(pack: Package): string {
  const added = pack.docs.filter(doc => doc.state === 'added').length;
  const parts = [added ? `新增 ${added} 份` : '', pack.removed.length ? `移除 ${pack.removed.length} 份` : ''].filter(Boolean);
  return parts.length ? `（${parts.join('，')}）` : '';
}

// The documents ---------------------------------------------------------

type DocTableProps = { docs: Doc[]; fields: Record<string, string>; flight: Task | null; revise: Revise; selectable: boolean; busy: boolean; picked: Set<string>; onPick: (next: Set<string>) => void; onIssues: () => void; onReport: (row: TaskFile) => void; onDownload: (row: TaskFile) => void; onRegenerate: (row: TaskFile) => void; onRefresh: () => void };

function DocTable(props: DocTableProps) {
  const { docs, fields, selectable, picked } = props;
  if (!docs.length) return <Empty title="这个工作项还没有附件" description="先在工作项里上传 PPAP 文件，这里会自动出现。"><Button onClick={props.onRefresh}>刷新</Button></Empty>;
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
        doc.state === 'confirm' && props.flight ? <ConfirmRow task={props.flight} row={doc.row!} revise={props.revise} disabled={props.busy} /> : <DocStatus doc={doc} onIssues={props.onIssues} onReport={props.onReport} onDownload={props.onDownload} onRegenerate={props.onRegenerate} /> },
  ];
  return (
    <Table
      size="small"
      pagination={false}
      rowKey={(doc?: Doc) => doc!.attachment.uid}
      columns={columns}
      dataSource={docs}
      rowSelection={{
        selectedRowKeys: [...picked],
        disabled: !selectable,
        getCheckboxProps: () => ({ disabled: !selectable }),
        onChange: keys => props.onPick(new Set((keys || []).map(String))),
      }}
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
      return null;
    case 'running':
      // Only a matched file is audited; the others already have their final answer.
      if (!audit) return row!.match?.state === 'matched' && !row!.reference_only ? <Text type="tertiary">排队中</Text> : <Unaudited state={unaudited(row!)} />;
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
      return <Unaudited state={doc.state} />;
    case 'done': {
      const open = audit!.counts.ng_count + audit!.counts.unknown_count + audit!.counts.not_covered_count + audit!.counts.error_count;
      return (
        <Space wrap>
          <Tag color={bucketColor(audit!.conclusion)} type="light">{audit!.conclusion ? conclusionLabel[audit!.conclusion] : '尚有未确定项'}</Tag>
          {open > 0 && <Text link onClick={onIssues}>{open} 项待处理</Text>}
          {reportReady(row!) && (
            <>
              <Text link onClick={() => onReport(row!)}>查看报告</Text>
              <Text link onClick={() => onDownload(row!)}>下载报告</Text>
            </>
          )}
          {audit!.report.state === 'pending' || audit!.report.state === 'generating' ? <Text type="tertiary" size="small">报告生成中…</Text> : null}
          {audit!.report.state === 'failed' && (audit!.report.regenerate_allowed && audit!.report.report_id ? <Text link onClick={() => onRegenerate(row!)}>报告生成失败，重新生成</Text> : <Text type="tertiary" size="small">报告生成失败</Text>)}
        </Space>
      );
    }
  }
}

function Unaudited({ state }: { state: string }) {
  if (state === 'unavailable')
    return (
      <Space wrap>
        <Tag color="orange" type="light">暂不能审核</Tag>
        <Text type="tertiary" size="small">这类文件还没有可用的检查程序</Text>
      </Space>
    );
  return <Text type="tertiary">{state === 'reference' ? '作为参考资料，未单独审核' : state === 'unread' ? '识别失败，未审核' : '没有可用规则，未审核'}</Text>;
}

// The audit in flight ----------------------------------------------------

function Running({ task }: { task: Task }) {
  // While files are being recognized each row says so itself; saying it again here would repeat it.
  if (task.state === 'matching') return null;
  return <Banner type="info" fullMode={false} closeIcon={null} icon={<Spin size="small" />} description={`审核进行中${task.progress ? `，已完成 ${task.progress.completed}/${task.progress.total} 份` : ''}。可以离开这个页面，审核会继续。`} />;
}

// What needs a person ------------------------------------------------------

function IssueList({ issues, current, onOpen }: { issues: OpenIssue[]; current: { file: string; clause: string } | null; onOpen: (issue: OpenIssue) => void }) {
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
                        {reviewOf(issue.check) && <Tag size="small" color="blue" type="ghost">已复核</Tag>}
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
