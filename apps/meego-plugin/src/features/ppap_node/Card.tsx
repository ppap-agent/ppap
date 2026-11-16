// A small card on the PPAP node: can this node move on? It shows the latest round's conclusion,
// what still needs a person, and whether the documents changed since. Moving the node stays a
// person's decision in Feishu Project; the full work happens in the "PPAP 审核" tab.
import { Button, Space, Spin, Tag, Typography } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import '../ppap_tab/index.css';
import { loadWorkItem } from '../ppap_tab/data';
import type { Data, Item } from '../ppap_tab/data';
import { buildPackage, standing, verdict } from '../ppap_tab/ledger';
import { bucket, bucketColor, when } from '../ppap_tab/model';
import { say, startSession } from '../ppap_tab/ppap';

const { Text } = Typography;
const label = { PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项', ERROR: '审核异常', NONE: '尚未审核' } as const;

export default function Card() {
  const [item, setItem] = useState<Item | null>(null);
  const [data, setData] = useState<Data | null>(null);
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'connect'; url: string } | { kind: 'error'; text: string } | { kind: 'ready' }>({ kind: 'loading' });
  const names = useRef<Record<string, string>>({});

  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try {
      const reply = await startSession();
      if (reply.state === 'connect_required') return setState({ kind: 'connect', url: reply.connect_url });
      const context = await window.JSSDK.control.getContext();
      if (!context.workItemId) return setState({ kind: 'error', text: '工作项创建后才能查看 PPAP 审核。' });
      const next = { project_key: context.spaceId, work_item_type_key: context.workObjectId, work_item_id: String(context.workItemId) };
      setItem(next);
      setData(await loadWorkItem(next, names.current));
      setState({ kind: 'ready' });
    } catch (failure) {
      setState({ kind: 'error', text: say(failure) });
    }
  }, []);
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
  const moving = pack.flight ? (pack.flight.state === 'pending_confirm' ? '有一次审核等待确认' : '审核进行中') : '';

  return (
    <div className="node-card">
      <Space wrap align="center">
        <Tag size="large" type="solid" color={total === 'NONE' ? 'grey' : total === 'ERROR' ? 'orange' : bucketColor(total)}>{label[total]}</Tag>
        {pack.latest && <Text>不符合 <Text strong>{ng}</Text> 项 · 待处理 <Text strong>{issues.length}</Text> 项</Text>}
        {pack.latest && <Text type="tertiary" size="small">{when(pack.latest.round_started_at ?? pack.latest.created_at)} 的审核{written ? '，已写回' : '，尚未写回'}</Text>}
      </Space>
      {pack.stale && <Text type="warning" size="small">资料在这次审核后有变化，结论针对的是变化前的资料。</Text>}
      {moving && <Text type="tertiary" size="small">{moving}。</Text>}
      <Space>
        <Text type="tertiary" size="small">在工作项的“PPAP 审核”标签中发起审核和处理问题。</Text>
        <Button size="small" theme="borderless" onClick={() => void load()}>刷新</Button>
      </Space>
    </div>
  );
}
