// The PPAP AI node's card. It is built for the AI node app (apps/meego-ai-node) from this project, so it
// shows the same conclusion as the plugin's node card: one implementation, one wording. Above it, where
// the flow stands; the audit itself, its confirmation and the issues stay in the "PPAP 审核" tab.
import { Tag, Typography } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import Card from '../ppap_node/Card';

const { Text } = Typography;
type Props = { ai_node_running?: string };

const view: Record<string, { label: string; color: 'blue' | 'green' | 'grey' | 'orange'; hint: string }> = {
  idle: { label: '未开始', color: 'grey', hint: '流程到达这个节点后开始等待 PPAP 审核。' },
  running: { label: '等待审核', color: 'blue', hint: '在工作项的“PPAP 审核”标签中核对资料并确认；结论为“符合”并写回后，这个节点自动完成。' },
  done: { label: '审核通过', color: 'green', hint: '节点正在完成。' },
  stopped: { label: '已停止', color: 'grey', hint: '节点已停止。重新启动后，继续等待 PPAP 审核结论。' },
  error: { label: '出错', color: 'orange', hint: '节点出错，请重新启动。' },
};

// Stable across renders, so the card reads its work item once.
const where = () => window.JSSDK.aiNode.getContext();

export default function App() {
  const [status, setStatus] = useState<string | null>(null);
  useEffect(() => {
    let off: undefined | (() => void);
    window.JSSDK.aiNode
      .getProps()
      .then(async props => {
        setStatus((props as Props).ai_node_running || 'idle');
        off = await window.JSSDK.aiNode.watch((next: Props) => setStatus(next.ai_node_running || 'idle'), ['ai_node_running' as never]);
      })
      .catch(() => setStatus('idle'));
    return () => off?.();
  }, []);

  const shown = view[status ?? 'idle'] ?? view.idle;
  const lead = status && (
    <Text size="small">
      <Tag size="small" color={shown.color}>{shown.label}</Tag> 流程节点
    </Text>
  );
  return <Card context={where} lead={lead} hint={shown.hint} />;
}
