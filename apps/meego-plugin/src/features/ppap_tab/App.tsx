// PPAP 审核 in a Feishu Project work item. The work item supplies the documents and the place
// to collaborate; every audit fact (frozen files, rules, results, reviews) stays in PPAP and is
// read from it each time, so nothing here depends on this browser's own memory.
import { Button, Spin } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import './index.css';
import { loadWorkItem } from './data';
import type { Data, Item } from './data';
import type { Task } from './model';
import { call, say, startSession } from './ppap';
import { Workspace } from './Workspace';

type Context = { spaceId: string; workObjectId: string; workItemId: number };
export type { Data, Item };
type Screen = { kind: 'loading'; text: string } | { kind: 'connect'; url: string } | { kind: 'error'; text: string } | { kind: 'ready' };

const moving = (task: Task | undefined) => Boolean(task && (task.state === 'matching' || task.state === 'reviewing' || task.files.some(row => row.audit && ['pending', 'generating'].includes(row.audit.report.state))));

export default function App() {
  const [screen, setScreen] = useState<Screen>({ kind: 'loading', text: '正在连接 PPAP…' });
  const [user, setUser] = useState('');
  const [item, setItem] = useState<Item | null>(null);
  const [data, setData] = useState<Data | null>(null);
  const [stalled, setStalled] = useState(false);
  const names = useRef<Record<string, string>>({});

  const connect = useCallback(async () => {
    setScreen({ kind: 'loading', text: '正在连接 PPAP…' });
    try {
      const reply = await startSession();
      if (reply.state === 'connect_required') return setScreen({ kind: 'connect', url: reply.connect_url });
      setUser(reply.display_name);
      const context = (await window.JSSDK.tab.getContext()) as Context;
      setItem({ project_key: context.spaceId, work_item_type_key: context.workObjectId, work_item_id: String(context.workItemId) });
      setScreen({ kind: 'ready' });
    } catch (failure) {
      setScreen({ kind: 'error', text: say(failure) });
    }
  }, []);
  useEffect(() => void connect(), [connect]);

  const readAt = useRef(0);
  const refresh = useCallback(async () => {
    if (!item) return;
    readAt.current = Date.now();
    setData(await loadWorkItem(item, names.current));
  }, [item]);

  // Back from PPAP (or anywhere else): what was confirmed or reviewed there shows here without a click.
  useEffect(() => {
    if (!item) return;
    const back = () => {
      if (document.visibilityState === 'visible' && Date.now() - readAt.current > 10_000) void refresh().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', back);
    window.addEventListener('focus', back);
    return () => {
      document.removeEventListener('visibilitychange', back);
      window.removeEventListener('focus', back);
    };
  }, [item, refresh]);

  useEffect(() => {
    if (!item) return;
    refresh().catch(failure => setScreen({ kind: 'error', text: say(failure) }));
  }, [item, refresh]);

  // When someone adds or replaces an attachment in the work item, the list here follows.
  const watched = (data?.attachments || []).map(file => file.field_key).filter(Boolean).sort().join(',');
  useEffect(() => {
    if (!watched) return;
    let off: (() => void) | undefined;
    let live = true;
    window.JSSDK.tab
      .onWorkItemFormValueChanged({ watchKeys: [...new Set(watched.split(','))].map(key => ({ key, type: 'multi_file' as never })) }, () => void refresh().catch(() => undefined))
      .then(stop => (live ? (off = stop) : stop()), () => undefined);
    return () => {
      live = false;
      off?.();
    };
  }, [watched, refresh]);

  // Follow the audit in flight until it settles, with a bounded, slowing read.
  const head = data?.tasks[0];
  const flight = moving(head) ? head!.id : '';
  useEffect(() => {
    if (!flight) return;
    let live = true;
    const started = Date.now();
    let delay = 1500;
    setStalled(false);
    (async () => {
      while (live) {
        await new Promise(resolve => setTimeout(resolve, delay));
        delay = Math.min(delay * 1.5, 10000);
        if (!live) return;
        if (Date.now() - started > 30 * 60 * 1000) return setStalled(true);
        const next = await call<Task>(`/audit-tasks/${flight}`).catch(() => null);
        if (!live || !next) continue;
        if (!moving(next)) return void refresh().catch(() => undefined);
        setData(previous => (previous && previous.tasks[0]?.id === flight ? { ...previous, tasks: [next, ...previous.tasks.slice(1)] } : previous));
      }
    })();
    return () => {
      live = false;
    };
  }, [flight, refresh]);

  async function disconnect() {
    try {
      await call('/integrations/meego/connection', { method: 'DELETE' });
    } finally {
      setData(null);
      setItem(null);
      await connect();
    }
  }

  if (screen.kind === 'loading') return <p className="ppap muted" role="status"><Spin size="small" /> {screen.text}</p>;
  if (screen.kind === 'error')
    return (
      <section className="ppap">
        <p>{screen.text}</p>
        <div><Button onClick={() => void connect()}>重试</Button></div>
      </section>
    );
  if (screen.kind === 'connect')
    return (
      <section className="ppap narrow">
        <h2>连接 PPAP</h2>
        <p>首次使用需要连接你的 PPAP 账号。审核会按你在 PPAP 的权限和额度执行。</p>
        <div className="actions">
          <Button theme="solid" type="primary" onClick={() => window.JSSDK.navigation.open(screen.url, '_blank')}>连接 PPAP</Button>
          <Button onClick={() => void connect()}>我已完成连接</Button>
        </div>
      </section>
    );
  if (!item || !data) return <p className="ppap muted" role="status"><Spin size="small" /> 正在读取这个工作项的资料和审核…</p>;

  return (
    <div className="ppap">
      <Workspace
        item={item}
        data={data}
        user={user}
        stalled={stalled}
        refresh={refresh}
        onWritten={roundId => setData(previous => (previous ? { ...previous, written: [...(previous.written || []), roundId] } : previous))}
        onDisconnect={() => void disconnect()}
      />
    </div>
  );
}
