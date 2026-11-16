// The PPAP AI node's card: where the flow stands and what the person does next. The audit itself,
// its conclusion and the confirmation live in the work item's "PPAP 审核" tab.
import React, { useEffect, useState } from 'react';
import './index.css';

type Props = { ai_node_running?: string };

const view: Record<string, { label: string; text: string }> = {
  idle: { label: '未开始', text: '流程到达这个节点后开始等待 PPAP 审核。' },
  running: { label: '等待审核', text: '请在工作项的「PPAP 审核」页签核对资料并确认。审核结论为“符合”并写回后，这个节点自动完成。' },
  done: { label: '审核符合', text: '审核结论为“符合”，节点正在完成。' },
  stopped: { label: '已停止', text: '节点已停止。重新启动后，继续等待 PPAP 审核结论。' },
  error: { label: '出错', text: '节点出错，请重新启动。' },
};

const App: React.FC = () => {
  const [status, setStatus] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let off: undefined | (() => void);
    (async () => {
      try {
        const props = (await window.JSSDK.aiNode.getProps()) as Props;
        setStatus(props.ai_node_running || 'idle');
        off = await window.JSSDK.aiNode.watch((next: Props) => setStatus(next.ai_node_running || 'idle'), ['ai_node_running' as never]);
      } catch {
        setFailed(true);
      }
    })();
    return () => off?.();
  }, []);

  if (failed) return <p className="card muted">暂时读不到节点状态，请刷新页面。</p>;
  if (status === null) return <p className="card muted">正在读取节点状态…</p>;
  const shown = view[status] ?? view.idle;
  return (
    <div className="card" role="status">
      <span className="badge">{shown.label}</span>
      <p>{shown.text}</p>
    </div>
  );
};

export default App;
