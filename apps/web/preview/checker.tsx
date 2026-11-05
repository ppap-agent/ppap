import {useState} from 'react'
import {createRoot} from 'react-dom/client'
import '../src/index.css'
import {CheckerFeedbackWorkspace} from '../src/audit/checker-feedback'
import type {FeedbackEvidence} from '../src/audit/checker-feedback-contract'
import {sourceLocation} from '../src/helpers'

// Explicit browser fixture: the UI uses its native Product API adapters.
// Tests supply HTTP receipts; this entry is not shipped in the product build.
function Preview() {
  const [source, setSource] = useState<FeedbackEvidence | null>(null), [reaudit, setReaudit] = useState(false)
  return <main className="mx-auto max-w-3xl space-y-4 p-4"><h1>检查修复界面预览（受控数据）</h1>{source && <p role="status">查看原件：{sourceLocation({locator: source.locator})}</p>}{reaudit && <p role="status">已进入现有显式重审入口</p>}<CheckerFeedbackWorkspace roundId="round-a" fileVersionId="file-a" ruleVersionId="rule-a" clauseId="U08-1" originalStatus="PASS" resultAttempt={1} evidence={[]} originals={[{id: 'file-a', name: '控制计划.xlsx'}]} onEvidence={setSource} onReaudit={() => setReaudit(true)}/></main>
}
createRoot(document.getElementById('root')!).render(<Preview/>)
