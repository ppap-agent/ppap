// Preview-only rule data: the rules page reads the product API directly, so the preview answers those reads in place.
const now = '2026-11-01T08:00:00+00:00'
const rule = (index: number, title: string, type: string, customers: string[], profile: string) => ({id: `00000000-0000-4000-8000-00000000010${index}`, title, kind: 'engine', created_at: now, predecessor_id: null, type, rule_profile: profile, customers, models: [], parts: []})
const rules = [
  rule(1, '理想 · 过程流程图评审逻辑', '过程流程图', ['理想汽车'], 'v2'),
  rule(2, '理想 · PFMEA 评审逻辑', '潜在过程失效模式及后果分析', ['理想汽车'], 'v2'),
  rule(3, '理想 · 控制计划评审逻辑', '控制计划', ['理想汽车'], 'v2'),
  rule(4, '蔚来 · 控制计划评审逻辑', '控制计划', ['蔚来汽车'], 'v3'),
  rule(5, 'UAES · 测量系统分析', '测量系统分析评价', ['UAES'], 'v4'),
  rule(6, 'UAES · 控制计划', '控制计划', ['UAES'], 'v4'),
]
const replies: Record<string, unknown> = {
  '/rules': {rules},
  '/rule-types': {types: ['过程流程图', '潜在过程失效模式及后果分析', '控制计划', '测量系统分析评价']},
  '/rule-library': {versions: [{id: rules[2].id, title: '本企业 · 控制计划补充要求', created_at: now, version: 'v1', scope: {customer: '理想汽车', part: '', revision: '', allCustomers: false, allParts: true, allRevisions: true}, reason: '', predecessor_id: null, clause_count: 6}]},
  '/suites': {suites: [], predefined: []},
}

export function installRuleFixtures() {
  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href).pathname.replace(/^\/api/, '')
    return Object.hasOwn(replies, path) && (!init?.method || init.method === 'GET') ? new Response(JSON.stringify(replies[path]), {headers: {'Content-Type': 'application/json'}}) : real(input, init)
  }
}
