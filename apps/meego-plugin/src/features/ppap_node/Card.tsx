// A small card on the PPAP node: can this node move on? It shows the latest round's conclusion,
// what still needs a person, and whether the documents changed since. Moving the node stays a
// person's decision in Feishu Project; the full work happens in the "PPAP 审核" tab.
import { Button, Space, Spin, Tag, Typography } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import '../ppap_tab/index.css';
import { loadWorkItem, outsideText } from '../ppap_tab/data';
import type { Data, Item } from '../ppap_tab/data';
import { buildPackage, standing, verdict } from '../ppap_tab/ledger';
import { bucket, bucketColor, verdictLabel, when } from '../ppap_tab/model';
import { say, startSession } from '../ppap_tab/ppap';

const { Text } = Typography;

type Where = { spaceId: string; workObjectId: string; workItemId: number | string };
// The node-form control reads its work item from the control context; the AI node card passes the AI
// node's own context, a line about the node above the conclusion, and its own next step.
type Props = { context?: () => Promise<Where>; lead?: ReactNode; hint?: string };

export default function Card({ context: where, lead, hint }: Props = {}) {
  const [item, setItem] = useState<Item | null>(null);
  const [data, setData] = useState<Data | null>(null);
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'connect'; url: string } | { kind: 'error'; text: string } | { kind: 'ready' }>({ kind: 'loading' });
  const names = useRef<Record<string, string>>({});

  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try {
      const reply = await startSession();
      if (reply.state === 'connect_required') return setState({ kind: 'connect', url: reply.connect_url });
      const context = await (where ?? (() => window.JSSDK.control.getContext()))();
      if (!context.workItemId) return setState({ kind: 'error', text: '工作项创建后才能查看 PPAP 审核。' });
      const next = { project_key: context.spaceId, work_item_type_key: context.workObjectId, work_item_id: String(context.workItemId) };
      setItem(next);
      setData(await loadWorkItem(next, names.current));
      setState({ kind: 'ready' });
    } catch (failure) {
      setState({ kind: 'error', text: say(failure) });
    }
  }, [where]);
  useEffect(() => void load(), [load]);

  if (state.kind === 'loading') return <Text type="tertiary" className="node-card"><Spin size="small" /> 正在读取 PPAP 审核…</Text>;
  if (state.kind === 'error')
    return (
      <Space className="node-card">
        <Text type="tertiary">{state.text}</Text>
        <Button size="small" onClick={() => void load()}>重试</Button>
      </Space>
    );
  if (state.kind === 'connect')
    return (
      <Space className="node-card" wrap>
        <Text type="tertiary">连接 PPAP 后可以在这里看到审核结论。</Text>
        <Button size="small" theme="solid" type="primary" onClick={() => window.JSSDK.navigation.open(state.url, '_blank')}>连接 PPAP</Button>
        <Button size="small" onClick={() => void load()}>我已完成连接</Button>
      </Space>
    );
  if (!item || !data) return null;

  const pack = buildPackage(data.attachments, data.tasks, data.sources);
  const total = verdict(pack.latest);
  const issues = standing(pack.latest, pack.previous).issues;
  const ng = issues.filter(issue => bucket(issue.check) === 'NG').length;
  const written = pack.latest?.round_id && data.written?.includes(pack.latest.round_id);
  const released = pack.latest?.round_id ? data.releases[pack.latest.round_id] : undefined;
  const moving = pack.flight ? (pack.flight.state === 'pending_confirm' ? '有一次审核等待确认' : '审核进行中') : '';

  // The one thing about the flow worth a line here; the tab says the rest.
  const note = data.aiNode?.status === 'completed' && data.aiNode.outside ? outsideText : moving ? `${moving}。` : pack.stale ? '资料在这次审核后有变化，结论针对的是变化前的资料。' : released ? `${released.by || '有人'} 人工放行了这次审核：${released.reason}` : '';
  const facts = pack.latest ? [ng ? `不符合 ${ng} 项` : '', issues.length ? `待处理 ${issues.length} 项` : '', when(pack.latest.round_started_at ?? pack.latest.created_at), written ? '已写回' : '尚未写回'].filter(Boolean) : [];

  return (
    <div className="node-card">
      {lead}
      <div className="node-line">
        <Tag type="light" color={total === 'NONE' ? 'grey' : total === 'ERROR' ? 'orange' : bucketColor(total)}>{verdictLabel[total]}</Tag>
        {facts.length > 0 && <Text type="tertiary" size="small">{facts.join(' · ')}</Text>}
      </div>
      {note && <Text size="small" type={moving ? 'tertiary' : 'warning'}>{note}</Text>}
      <div className="node-line">
        <Text type="tertiary" size="small">{hint ?? '在工作项的“PPAP 审核”标签中发起审核和处理问题。'}</Text>
        <Button size="small" theme="borderless" onClick={() => void load()}>刷新</Button>
      </div>
    </div>
  );
}
