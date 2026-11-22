// Checking an audit before it is accepted: what each file will be judged by, for which
// customer, how deeply, and at what cost. Nothing here charges; only "确认并开始审核" does.
import { Banner, Button, Collapse, Dropdown, Input, List, Radio, RadioGroup, Select, Space, Spin, Switch, Tag, Typography } from '@douyinfe/semi-ui';
import { useEffect, useMemo, useState } from 'react';
import { currentTemplate, rememberedTemplate, rememberTemplate, templateName, templateOwner, templates } from './ledger';
import type { RegisteredCustomer, Rule } from './ledger';
import { checkerUnavailable, matchLabel, newerRuleOf, ruleName, ruleVersionChoice, withRuleVersion } from './model';
import type { Precheck, PrecheckFinding, RuleVersionChoice, Task, TaskFile } from './model';
import { call, newId, say, taskLink } from './ppap';

const { Text, Title } = Typography;

// Deep mode as the web app describes it (apps/web/src/audit/deep-mode.ts); it costs the same per file.
const deepModeDescription = '规则范围与标准审核相同，另逐条复核每条结论，并核对同批文件之间是否一致，审核时长约增加 2 倍。';

/** The precheck's overall rating, in the web app's words (apps/web/src/audit/precheck.tsx). */
function precheckRating(result: Precheck): string {
  if (result.conclusion === 'fail') return '不通过：存在阻断项';
  if (result.conclusion === 'not_run') return result.files.length ? '未执行：识别尚未完成' : '未执行：没有送审文件';
  return result.warnings.length ? `有条件通过 · 含警告 ${result.warnings.length} 项：可以进入正式审核` : '通过：可以进入正式审核';
}
// A warning never holds the start; a file-level block does until the file is removed; a content-level block goes to the formal audit.
const precheckEffect = (item: PrecheckFinding) => (item.level === 'warn' ? '仍可勾选送审' : item.scope === 'file' ? '移除后才能开始审核' : '不阻止开始审核，正式审核将核对');
type Quota = { accept_allowed?: boolean; accept_reason?: string; message?: string; remaining_trial_ai_points?: number; unavailable?: boolean; usage_charge_enabled?: boolean };

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
      // A re-audit keeps naming its earlier round; without it the server treats the draft as locked.
      await call('/audit-tasks', { method: 'POST', body: { request_id: newId(), task_id: task.id, expected_revision: task.input_revision, customer_preference: customer, files, ...(task.predecessor_id ? { predecessor_id: task.predecessor_id } : {}) } });
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
        <Dropdown trigger="click" clickToHide position="bottomRight" menu={menu} disabled={disabled}>
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

/** The decision before acceptance: template, depth, precheck, cost; then confirm or end without auditing. */
export function Confirm({ task, rules, customers, space, revise, refresh, onReselect }: { task: Task; rules: Rule[]; customers: RegisteredCustomer[] | null; space: string; revise: Revise; refresh: () => Promise<void>; onReselect: () => void }) {
  const [precheck, setPrecheck] = useState<Precheck | null | 'unavailable'>(null);
  const all = useMemo(() => templates(rules), [rules]);
  const template = currentTemplate(all, task.customer_preference ?? null, rememberedTemplate(space));
  const [quota, setQuota] = useState<Quota | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  // How many files confirming accepts is the server's saved scope; until it is settled the count is 待确认.
  // The one certain local fact is zero: with no selected matched file nothing can be audited.
  const noMatches = !task.files.some(row => row.selection === 'selected' && row.match?.state === 'matched' && !row.reference_only);
  const targetIds = task.confirmation_scope?.target_file_version_ids ?? null;
  const targets = noMatches ? 0 : targetIds ? targetIds.length : null;
  const unmatched = task.files.filter(row => row.selection === 'selected' && row.match?.state === 'unmatched');
  // A re-audit's rules that have a newer version since the earlier round; the newer one is used unless the person keeps the earlier.
  const newerRules = task.predecessor_id ? [...new Set(task.files.filter(row => row.selection === 'selected').map(newerRuleOf).filter(rule => rule !== undefined).map(ruleName))] : [];
  const versionChoice = ruleVersionChoice(task.files);
  const chooseRuleVersion = (choice: RuleVersionChoice) => void revise(task, row => withRuleVersion(row, choice));

  useEffect(() => {
    call<Precheck>(`/audit-tasks/${task.id}/precheck`).then(setPrecheck, () => setPrecheck('unavailable'));
  }, [task.id, task.input_revision]);
  useEffect(() => {
    if (!targets) return setQuota(null);
    // The server's own wording for the balance; it decides whether acceptance is allowed. Every mode costs the same per file.
    call<Quota>(`/integrations/feishu/usage/quota?target_count=${targets}`).then(setQuota, () => setQuota(null));
  }, [targets]);

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
  // A template sets the task's customer and gives each recognized document type that template's rule;
  // recognition then reads every file again against it.
  const chooseTemplate = (key: string) => {
    rememberTemplate(space, key);
    const byType = new Map((all.get(key) ?? []).filter(rule => rule.type).map(rule => [rule.type!, rule.id]));
    void revise(task, row => ({ ...row, preferred_rule_version_id: (row.match?.document_type && byType.get(row.match.document_type)) || null }), templateOwner(key) ?? task.customer_preference ?? null);
  };
  // Nothing left to audit: end the task without a round, charge or report, saying why for each file.
  // A file that has a rule but no checker program yet (暂不能审核) is left out of the task, not marked as rule-less.
  const unauditable = task.files.filter(row => row.selection === 'selected' && !['unmatched', 'reference'].includes(row.match?.state ?? ''));
  const end = () =>
    act(() =>
      call(`/audit-tasks/${task.id}/end`, {
        method: 'POST',
        body: {
          expected_revision: task.input_revision,
          removals: unmatched.map(row => ({ file_version_id: row.file.id, removal_reason: '未匹配到规则' })),
          unselected_file_version_ids: unauditable.map(row => row.file.id),
        },
      }),
    );

  const result = precheck && precheck !== 'unavailable' ? precheck : null;
  const findings = result ? [...result.blocks, ...result.warnings] : [];
  // Only a file-level block on a file being submitted holds the start; content-level blocks go to the formal audit.
  const holds = result?.blocks.filter(item => item.scope === 'file' && targetIds?.includes(item.file_version_id)) ?? [];
  const finding = (item: PrecheckFinding, index: number) => (
    <List.Item
      key={item.number || index}
      main={
        <Space spacing={6} align="start">
          <Tag size="small" color={item.level === 'block' ? 'red' : 'orange'} type="light">{item.level === 'block' ? '阻断' : '警告'}</Tag>
          <Text>
            <Text strong>{item.filename}</Text> · {item.title}：{item.problem}（{precheckEffect(item)}）
            {item.action && <Text type="tertiary"> 整改：{item.action}</Text>}
          </Text>
        </Space>
      }
    />
  );
  // Acceptance is the server's call (trial points count too); a missing AI package alone does not block.
  const short = Boolean(quota && !quota.unavailable && quota.accept_allowed === false);
  const balance = !quota || short ? '' : quota.usage_charge_enabled === false ? '本环境不扣点' : typeof quota.remaining_trial_ai_points === 'number' && quota.remaining_trial_ai_points > 0 ? `试用额度剩余 ${quota.remaining_trial_ai_points} 点` : quota.message;

  return (
    <section className="confirm" aria-label="开始审核前的核对">
      <Title heading={6}>核对识别结果后开始审核</Title>
      {newerRules.length > 0 && (
        <Banner
          type="info"
          fullMode={false}
          closeIcon={null}
          title="审核规则已更新"
          description={
            <Space vertical align="start" spacing="tight">
              <Text>{newerRules.join('、')}在上次审核后有新版本。</Text>
              <RadioGroup type="button" buttonSize="small" aria-label="本次使用的规则版本" value={versionChoice ?? undefined} disabled={busy} onChange={event => chooseRuleVersion(event.target.value as RuleVersionChoice)}>
                <Radio value="newer">使用新版本</Radio>
                <Radio value="original">沿用上次版本</Radio>
              </RadioGroup>
            </Space>
          }
        />
      )}
      <Space wrap align="center">
        <Text type="tertiary">审核模板</Text>
        <Select
          size="small"
          aria-label="审核模板"
          style={{ minWidth: 160 }}
          disabled={busy}
          value={template ?? undefined}
          placeholder="请选择审核客户"
          optionList={[...all.keys()].sort((a, b) => templateName(a, customers).localeCompare(templateName(b, customers), 'zh-CN')).map(key => ({ value: key, label: templateName(key, customers) }))}
          onChange={value => chooseTemplate(String(value))}
        />
        {task.deep_mode_option?.available && (
          <Space align="center">
            <Switch size="small" aria-label="深度模式" checked={Boolean(task.deep_mode)} disabled={busy} onChange={on => void deep(on)} />
            <Text>深度模式</Text>
            <Text type="tertiary" size="small">{deepModeDescription}</Text>
          </Space>
        )}
      </Space>
      <Text type="tertiary">
        {targets === null ? '送审份数待确认' : `将审核 ${targets} 份资料`}，预计用量 {task.estimated_points === null ? '无法预估' : `${task.estimated_points} 点`}
        {balance && <>（{balance}）</>}。每份资料的规则见下方列表，可在每行“调整”。
      </Text>
      {short && <Banner type="warning" fullMode={false} closeIcon={null} description={quota!.accept_reason || quota!.message} />}
      {notice && <Banner type="warning" fullMode={false} closeIcon={null} description={notice} />}
      {precheck === null && <Text type="tertiary" role="status"><Spin size="small" /> 正在做数据预检…</Text>}
      {precheck === 'unavailable' && <Text type="tertiary">预检结果暂时无法读取，不影响识别与审核。</Text>}
      {result && <Text type={result.conclusion === 'fail' ? 'danger' : 'tertiary'}>预检 · {precheckRating(result)}</Text>}
      {holds.length > 0 && <Banner type="danger" fullMode={false} closeIcon={null} description={`预检发现 ${holds.length} 项阻断（${[...new Set(holds.map(item => item.title))].join('、')}），请先处理：${holds[0].action}`} />}
      {findings.length > 0 && (
        <Collapse defaultActiveKey={holds.length ? ['findings'] : []}>
          <Collapse.Panel header={`预检发现 ${findings.length} 项`} itemKey="findings">
            <List size="small" split={false}>{findings.map(finding)}</List>
          </Collapse.Panel>
        </Collapse>
      )}
      <Space wrap>
        {noMatches ? (
          <Button theme="solid" type="primary" loading={busy} onClick={() => void end()}>结束，不审核</Button>
        ) : task.confirmation_basis_digest && task.input_revision !== undefined ? (
          <Button theme="solid" type="primary" loading={busy} disabled={holds.length > 0 || precheck === null || short || !targets} onClick={() => void confirm()}>确认并开始审核</Button>
        ) : (
          <Button theme="solid" type="primary" onClick={() => window.JSSDK.navigation.open(taskLink(task.id), '_blank')}>在 PPAP 中核对</Button>
        )}
        <Button disabled={busy} onClick={onReselect}>重新选择资料</Button>
      </Space>
      {noMatches && <Text type="tertiary" size="small">没有可以审核的资料，结束后不产生审核结果，也不扣点。</Text>}
      {!noMatches && targets === 0 && <Text type="tertiary" size="small">没有可接收的待审文件。请先处理排除原因，再核对审核依据。</Text>}
    </section>
  );
}
