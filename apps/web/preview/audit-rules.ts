// Preview-only rule data: the rules page reads the product API directly, so the preview answers those reads in place.
const now = '2026-11-01T08:00:00+00:00'
const types = ['全功能检验报告', '零部件设计验证计划和报告', '材料产品及分供清单', '检具验收报告', '模具验收报告', '测量系统分析评价', '过程能力研究', '过程流程图', '潜在过程失效模式及后果分析', '控制计划', '过程特殊特性清单', '设备清单', '生产系统产能评估报告', '项目问题清单', '零部件年度试验要求', '零件提交保证书', 'DV报告_PV报告_试验报告', '设计失效模式及后果分析']
let serial = 0
const rule = (title: string, type: string, customer: string, preset: string, clauses: number, at = now) => ({id: `00000000-0000-4000-8000-${String(++serial).padStart(12, '0')}`, title, kind: 'engine', created_at: at, predecessor_id: null, customer_scope: 'restricted', type, rule_profile: 'v4', customers: [customer], models: [], parts: [], preset_key: preset, clause_count: clauses})
// 理想 has a preset for every standard type but DFMEA; most programs are ready, a few still wait on the rule page.
const lixiang = types.slice(0, 17).map((type, index) => rule(`${String(index + 1).padStart(2, '0')} ${type}`, type, '理想汽车', `L${String(index + 1).padStart(2, '0')}`, 6 + index % 9, `2026-0${1 + index % 9}-1${index % 9}T08:00:00+00:00`))
const nio = ['过程流程图', '潜在过程失效模式及后果分析', '控制计划', '过程特殊特性清单', '测量系统分析评价'].map((type, index) => rule(`${index + 8}规则-${type}`, type, '蔚来汽车', `N${index + 1}`, 5 + index))
const uaes = [rule('控制计划（CP）审核规则', '控制计划', 'UAES', 'U08', 12), rule('生产件样品照片审核规则', 'E07', 'UAES', 'U13', 4), rule('初始样品检验报告审核规则', 'E01', 'UAES', 'U01', 9)]
const rules = [...lixiang, ...nio, ...uaes]
const installed = (item: ReturnType<typeof rule>, readers: number) => ({path: item.preset_key, title: item.title, availability: 'installed', suite_key: `${item.customers[0]}:${item.preset_key}`, revision_id: `rev-${item.preset_key}`, clause_count: item.clause_count, evidence_reader_clause_ids: Array.from({length: readers}, (_, index) => String(index + 1))})
const replies: Record<string, unknown> = {
  '/rules': {rules},
  '/rule-types': {types},
  '/rule-library': {versions: [{id: '00000000-0000-4000-8000-000000000901', title: '本企业 · 控制计划补充要求', created_at: '2026-10-28T03:00:00+00:00', version: 'v1', scope: {customer: '理想汽车', part: '', revision: '', allCustomers: false, allParts: true, allRevisions: true}, reason: '', predecessor_id: null, clause_count: 6, document_type: '控制计划', suite_key: 'custom:lixiang-cp'}]},
  '/suites': {
    suites: [
      {suite_key: '理想汽车:L03', title: '材料产品及分供清单', origin: 'official', selection_mode: 'automatic_official', current_revision_id: 'rev-L03', verification_status: 'verified', rule_update_pending: true, official_update_available: false, program_missing: false},
      {suite_key: 'custom:lixiang-cp', title: '控制计划补充要求', origin: 'enterprise', selection_mode: 'pinned', current_revision_id: 'rev-custom', verification_status: 'unverified', rule_update_pending: false, official_update_available: false, program_missing: false},
    ],
    // 理想: 16 of 17 ready (one rule update pending); 蔚来: two ready; UAES: CP ready, the photo has no reader.
    predefined: [...lixiang.map((item, index) => installed(item, index === 5 ? 3 : item.clause_count)), ...nio.slice(0, 2).map(item => installed(item, item.clause_count)), installed(uaes[0], 12), installed(uaes[1], 0)],
  },
}

export function installRuleFixtures() {
  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href).pathname.replace(/^\/api/, '')
    return Object.hasOwn(replies, path) && (!init?.method || init.method === 'GET') ? new Response(JSON.stringify(replies[path]), {headers: {'Content-Type': 'application/json'}}) : real(input, init)
  }
}
