type JudgmentValue = 'pending' | 'confirm' | 'disagree' | 'unknown' | 'manual-satisfied' | 'manual-unsatisfied'
export const judgmentLabels: Record<JudgmentValue, string> = {
  pending: '尚未判断', confirm: '确认机器结果', disagree: '不同意机器结果', unknown: '仍无法判断',
  'manual-satisfied': '人工确认满足要求', 'manual-unsatisfied': '人工确认不满足要求',
}
