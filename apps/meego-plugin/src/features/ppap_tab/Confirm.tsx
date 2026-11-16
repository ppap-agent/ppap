// Checking an audit before it is accepted: what each file will be judged by, for which
// customer, how deeply, and at what cost. Nothing here charges; only "确认并开始审核" does.
import { Banner, Button, Collapse, Dropdown, Input, List, Select, Space, Spin, Switch, Tag, Typography } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { checkerUnavailable, matchLabel } from './model';
import type { Precheck, Task, TaskFile } from './model';
import { call, newId, say, taskLink } from './ppap';

const { Text, Title } = Typography;
type Quota = { quota_sufficient: boolean; message: string };

/** Resubmits the task's files with one change; the server recognizes them again and nothing is accepted. */
export function useRevise(refresh: () => Promise<void>, setBusy: (text: string) => void, setNotice: (text: string) => void) {
  return async function revise(task: Task, change: (row: TaskFile) => TaskFile, customer: string | null = task.customer_preference ?? null) {
    if (task.input_revision === undefined) return;
    setBusy('正在重新识别…');
    setNotice('');
    try {
      const files = task.files.map(change).map(row => ({
        file_version_id: row.file.id,
        validation_id: row.validation_id ?? null,
        selection: row.selection,
        removal_reason: row.selection === 'removed' ? row.removal_reason : null,
        ...(row.preferred_rule_version_id ? { preferred_rule_version_id: row.preferred_rule_version_id } : {}),
        ...(row.reference_only ? { reference_only: true } : {}),
        ...(row.replacement_of_file_version_id ? { replacement_of_file_version_id: row.replacement_of_file_version_id } : {}),
      }));
      await call('/audit-tasks', { method: 'POST', body: { request_id: newId(), task_id: task.id, expected_revision: task.input_revision, customer_preference: customer, files } });
      await refresh();
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  };
}
export type Revise = ReturnType<typeof useRevise>;

/** One file of the audit waiting for confirmation, with the choices a person can still make. */
export function ConfirmRow({ task, row, revise, disabled }: { task: Task; row: TaskFile; revise: Revise; disabled: boolean }) {
  const [removing, setRemoving] = useState(false);
  const [reason, setReason] = useState('');
  const removed = row.selection === 'removed';
  const reference = Boolean(row.reference_only) || row.match?.state === 'reference';
  const choices = row.match?.candidates?.length ? row.match.candidates : row.rule_options ?? [];
  const change = (next: Partial<TaskFile>) => revise(task, other => (other.file.id === row.file.id ? { ...other, ...next } : other));

  const state = removed ? (
    <Text type="tertiary">不审核：{row.removal_reason}</Text>
  ) : checkerUnavailable(row) ? (
    <Space wrap>
      <Tag color="orange" type="light">暂不能审核</Tag>
      <Text type="tertiary" size="small">这类文件还没有可用的检查程序</Text>
    </Space>
  ) : reference ? (
    <Text>作为参考资料</Text>
  ) : row.match?.rule ? (
    <Text ellipsis={{ showTooltip: true }}>规则：{row.match.rule.title}</Text>
  ) : (
    <Text type="tertiary">{matchLabel[row.match?.state ?? 'pending']}</Text>
  );
  const menu = [
    ...(!removed && !reference ? [{ node: 'item' as const, name: '作为参考资料，不单独审核', onClick: () => void change({ reference_only: true, preferred_rule_version_id: null }) }] : []),
    ...(!removed && row.reference_only ? [{ node: 'item' as const, name: '作为待审资料', onClick: () => void change({ reference_only: false }) }] : []),
    ...(!removed ? [{ node: 'item' as const, name: '这份不审核…', onClick: () => setRemoving(true) }] : [{ node: 'item' as const, name: '恢复审核', onClick: () => void change({ selection: 'selected', removal_reason: null }) }]),
  ];

  return (
    <div className="confirm-row">
      <Space wrap>
        {state}
        {!removed && !reference && choices.length > 1 && (
          <Select
            size="small"
            aria-label="适用规则"
            placeholder="选择适用规则"
            disabled={disabled}
            value={row.preferred_rule_version_id ?? undefined}
            optionList={choices.map(choice => ({ value: choice.id, label: choice.title }))}
            onChange={value => void change({ preferred_rule_version_id: String(value), reference_only: false })}
          />
        )}
        <Dropdown trigger="click" position="bottomRight" menu={menu} disabled={disabled}>
          <Button size="small" theme="borderless" disabled={disabled}>调整</Button>
        </Dropdown>
      </Space>
      {removing && (
        <Space className="remove-reason">
          <Input size="small" autoFocus maxLength={500} placeholder="不审核的原因" aria-label="不审核的原因" value={reason} onChange={setReason} />
          <Button size="small" type="primary" disabled={!reason.trim() || disabled} onClick={() => void change({ selection: 'removed', removal_reason: reason.trim() }).then(() => setRemoving(false))}>确定</Button>
          <Button size="small" theme="borderless" onClick={() => setRemoving(false)}>取消</Button>
        </Space>
      )}
    </div>
  );
}

/** The decision before acceptance: customer, depth, precheck, cost; then confirm or end without auditing. */
export function Confirm({ task, revise, refresh, onReselect }: { task: Task; revise: Revise; refresh: () => Promise<void>; onReselect: () => void }) {
  const [precheck, setPrecheck] = useState<Precheck | null | 'unavailable'>(null);
  const [customers, setCustomers] = useState<string[]>([]);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const targets = task.files.filter(row => row.selection === 'selected' && row.match?.state === 'matched' && !row.reference_only).length;
  const unmatched = task.files.filter(row => row.selection === 'selected' && row.match?.state === 'unmatched');

  useEffect(() => {
    call<Precheck>(`/audit-tasks/${task.id}/precheck`).then(setPrecheck, () => setPrecheck('unavailable'));
  }, [task.id, task.input_revision]);
  useEffect(() => {
    call<{ customers: { name: string }[] }>('/audit-customers').then(reply => setCustomers(reply.customers.map(row => row.name)), () => setCustomers([]));
  }, []);
  useEffect(() => {
    if (!targets) return setQuota(null);
    // The server's own wording for the balance; it decides whether acceptance is allowed.
    call<Quota>(`/integrations/feishu/usage/quota?target_count=${targets}&deep_mode=${Boolean(task.deep_mode)}`).then(setQuota, () => setQuota(null));
  }, [targets, task.deep_mode]);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setNotice('');
    try {
      await run();
      await refresh();
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy(false);
    }
  }
  const confirm = () => act(() => call(`/audit-tasks/${task.id}/confirm`, { method: 'POST', body: { request_id: newId(), expected_revision: task.input_revision, expected_confirmation_basis_digest: task.confirmation_basis_digest } }));
  const deep = (on: boolean) => act(() => call(`/audit-tasks/${task.id}/deep-mode`, { method: 'PUT', body: { deep_mode: on, expected_revision: task.input_revision } }));
  // Nothing left to audit: end the task without a round, charge or report, saying why for each file.
  const end = () => act(() => call(`/audit-tasks/${task.id}/end`, { method: 'POST', body: { expected_revision: task.input_revision, removals: unmatched.map(row => ({ file_version_id: row.file.id, removal_reason: '没有可用规则' })) } }));

  const blocks = precheck && precheck !== 'unavailable' ? precheck.blocks : [];
  const warnings = precheck && precheck !== 'unavailable' ? precheck.warnings : [];
  const finding = (entry: Precheck['blocks'][number], index: number) => <List.Item key={index} main={<Text><Text strong>{entry.filename}</Text>：{entry.problem}{entry.action && <Text type="tertiary">（{entry.action}）</Text>}</Text>} />;
  const short = quota && !quota.quota_sufficient;

  return (
    <section className="confirm" aria-label="开始审核前的核对">
      <Title heading={6}>核对识别结果后开始审核</Title>
      <Space wrap align="center">
        <Text type="tertiary">审核客户</Text>
        <Select
          size="small"
          aria-label="审核客户"
          style={{ minWidth: 120 }}
          disabled={busy}
          value={task.customer_preference ?? undefined}
          placeholder="未指定"
          optionList={customers.map(name => ({ value: name, label: name }))}
          onChange={value => void revise(task, row => ({ ...row, preferred_rule_version_id: null }), String(value))}
        />
        {task.deep_mode_option?.available && (
          <Space align="center">
            <Switch size="small" aria-label="深度模式" checked={Boolean(task.deep_mode)} disabled={busy} onChange={on => void deep(on)} />
            <Text>深度模式</Text>
            <Text type="tertiary" size="small">程序检查后再由模型逐条复核，用量更高</Text>
          </Space>
        )}
      </Space>
      <Text type="tertiary">
        将审核 {targets} 份资料，预计用量 {task.estimated_points === null ? '无法预估' : `${task.estimated_points} 点`}
        {quota && !short && <>（{quota.message}）</>}。每份资料的规则见下方列表，可在每行“调整”。
      </Text>
      {short && <Banner type="warning" fullMode={false} closeIcon={null} description={quota!.message} />}
      {notice && <Banner type="warning" fullMode={false} closeIcon={null} description={notice} />}
      {precheck === null && <Text type="tertiary" role="status"><Spin size="small" /> 正在做数据预检…</Text>}
      {blocks.length > 0 && <Banner type="danger" fullMode={false} closeIcon={null} title={`预检发现 ${blocks.length} 个必须先处理的问题`} description={<List size="small" split={false}>{blocks.map(finding)}</List>} />}
      {warnings.length > 0 && (
        <Collapse>
          <Collapse.Panel header={`预检提醒 ${warnings.length} 条`} itemKey="warnings">
            <List size="small" split={false}>{warnings.map(finding)}</List>
          </Collapse.Panel>
        </Collapse>
      )}
      <Space wrap>
        {targets === 0 ? (
          <Button theme="solid" type="primary" loading={busy} onClick={() => void end()}>结束，不审核</Button>
        ) : task.confirmation_basis_digest && task.input_revision !== undefined ? (
          <Button theme="solid" type="primary" loading={busy} disabled={blocks.length > 0 || precheck === null || Boolean(short)} onClick={() => void confirm()}>确认并开始审核</Button>
        ) : (
          <Button theme="solid" type="primary" onClick={() => window.JSSDK.navigation.open(taskLink(task.id), '_blank')}>在 PPAP 中核对</Button>
        )}
        <Button disabled={busy} onClick={onReselect}>重新选择资料</Button>
      </Space>
      {targets === 0 && <Text type="tertiary" size="small">没有可以审核的资料，结束后不产生审核结果，也不扣点。</Text>}
    </section>
  );
}
