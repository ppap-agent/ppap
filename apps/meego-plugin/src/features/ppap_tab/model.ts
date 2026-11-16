// Shapes read from the PPAP Product API, and the one vocabulary shared with the PPAP web app
// (apps/web/src/audit/status-labels.ts). Only the fields this plugin renders are typed.

export type Attachment = { uid: string; name: string; size: string; content_type: string; field_key: string };
export type FileVersion = { id: string; file_id: string; original_filename: string };
export type Job = { id: string; status: string; result: { state?: string; reasons?: string[] } | null };

export type Locator =
  | { kind: 'xlsx_cell'; sheet: string; cell: string }
  | { kind: 'xlsx_range'; sheet: string; start: string; end: string }
  | { kind: 'pdf_page'; page: number }
  | { kind: 'pdf_block'; page: number; block: number }
  | { kind: 'pdf_cell'; page: number; row: number; column: number }
  | { kind: 'docx_paragraph'; paragraph: number }
  | { kind: 'docx_cell'; table: number; row: number; column: number };

export type Evidence = { file_version_id: string; locator: Locator | null; excerpt: string; location_gap: string | null };
export type Citation = { file_version_id: string; location: string; excerpt: string };
export type Review = { judgment: string; rationale: string; next_step: string; version: number; citations?: Citation[] };
export type FindingStatus = 'PASS' | 'NG' | 'UNABLE' | 'ERR' | 'NA';

export type Check = {
  rule_item: { id: string; category: string; rule_to_check: string };
  execution: 'queued' | 'running' | 'completed' | 'failed';
  coverage: string;
  scope_notice: string | null;
  verification_scope?: string | null;
  review_item_key: string | null;
  review: Review | null;
  evidence: Evidence[];
  finding: { status: FindingStatus; classification?: string; suggestions?: string; review?: Review | null } | null;
};

export type Report = { state: 'not_eligible' | 'pending' | 'generating' | 'ready' | 'failed'; report_id: string | null; artifact: { artifact_id: string; filename: string } | null };

export type FileAudit = {
  round_id: string;
  file_version_id: string;
  result_attempt: number | null;
  execution: 'queued' | 'running' | 'completed' | 'review_failed';
  progress: { completed: number; total: number };
  conclusion: 'PASS' | 'NG' | 'UNKNOWN' | null;
  counts: { pass_count: number; ng_count: number; unknown_count: number; not_covered_count: number; error_count: number };
  checks: Check[];
  report: Report;
};

export type TaskFile = {
  file: FileVersion;
  selection: 'selected' | 'unselected' | 'removed';
  removal_reason: string | null;
  reference_only?: boolean;
  match: { state: string; document_type: string | null; rule: { title: string } | null; gaps?: string[] } | null;
  audit?: FileAudit | null;
};

export type Task = {
  id: string;
  number: string;
  state: 'matching' | 'pending_confirm' | 'reviewing' | 'completed';
  created_at: string;
  customer_preference?: string | null;
  round_id: string | null;
  round_started_at?: string | null;
  completion_reason: 'no_matching_rules' | 'processed' | null;
  estimated_points: number | null;
  input_revision?: number;
  confirmation_basis_digest: string | null;
  execution?: 'queued' | 'running' | 'completed' | 'failed';
  has_execution_error?: boolean;
  progress?: { completed: number; total: number };
  files: TaskFile[];
};

export type AuditSummary = { id: string; number: string; state: Task['state']; round_id: string | null; created_at: string };

export type PrecheckFinding = { number?: string; title: string; filename: string; location?: string; problem: string; action?: string };
export type Precheck = { conclusion: 'pass' | 'fail' | 'not_run'; blocks: PrecheckFinding[]; warnings: PrecheckFinding[] };

export const resultLabel: Record<FindingStatus, string> = { PASS: '符合', NG: '不符合', UNABLE: '无法判断', ERR: '技术错误', NA: '不适用' };
export const uncoveredLabel = '未覆盖';
export const conclusionLabel: Record<string, string> = { PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项' };
export const matchLabel: Record<string, string> = { matched: '已匹配规则', reference: '作为参考资料', unmatched: '没有可用规则', failed: '识别失败', pending: '识别中', matching: '识别中' };
/** The file is fine but its type has no checker program yet (the web app's "暂不能审核"). */
export const checkerUnavailable = (row: TaskFile) => Boolean(row.match?.gaps?.includes('checker_suite_unavailable'));
export const judgmentLabel: Record<string, string> = {
  confirm: '确认机器结果',
  disagree: '不同意机器结果',
  unknown: '仍无法判断',
  'manual-satisfied': '人工确认满足要求',
  'manual-unsatisfied': '人工确认不满足要求',
};

// UAES source documents are stored as codes; people only ever read their names (as in the web app).
const sourceDocumentNames: Record<string, string> = { E01: '初始样品检验报告', E02: '测量比较报告', E03: '包装方案', E04: '实验室资质证书', E05: '材料规范', E06: '样品阶段履历', E07: '生产件样品照片', E08: '订货规范', E09: '试装单', E10: '零件追溯说明', E11: '顾客工程批准', E12: '采购件质量计划' };
export const documentTypeLabel = (type: string) => sourceDocumentNames[type] ?? type;

export type Bucket = FindingStatus | 'uncovered' | 'running';

/** The same sorting of a clause the web app uses when it counts results. */
export function bucket(check: Check): Bucket {
  if (check.execution === 'queued' || check.execution === 'running') return 'running';
  if (check.execution === 'failed' || check.finding?.status === 'ERR') return 'ERR';
  if (check.coverage !== 'executed') return 'uncovered';
  if (!check.finding || check.finding.status === 'UNABLE') return 'UNABLE';
  return check.finding.status;
}

export const bucketLabel = (value: Bucket) => (value === 'uncovered' ? uncoveredLabel : value === 'running' ? '审核中' : resultLabel[value]);
export const bucketColor = (value: Bucket | 'UNKNOWN' | null | undefined): 'red' | 'green' | 'orange' | 'grey' =>
  value === 'NG' ? 'red' : value === 'PASS' ? 'green' : value === 'UNABLE' || value === 'ERR' || value === 'uncovered' || value === 'UNKNOWN' ? 'orange' : 'grey';

/** A clause needs a person's attention unless it conforms or does not apply. */
export const needsAttention = (check: Check) => !['PASS', 'NA', 'running'].includes(bucket(check));
const rank: Record<Bucket, number> = { NG: 0, ERR: 1, UNABLE: 2, uncovered: 3, PASS: 4, NA: 5, running: 6 };
export const byUrgency = (left: Check, right: Check) => rank[bucket(left)] - rank[bucket(right)];

export const reviewOf = (check: Check): Review | null => check.review ?? check.finding?.review ?? null;

/** Why the machine reached this result, in the words the web app shows. */
export function observation(check: Check): string {
  if (check.execution === 'queued' || check.execution === 'running') return '本项审核中…';
  const program = check.verification_scope === 'checker_execution' && check.scope_notice;
  const notice = program ? null : check.scope_notice === 'manual-only' || check.scope_notice === 'unsupported' ? '本条要求未完成自动检查，请核对原件并记录人工复核。' : check.scope_notice;
  return [...new Set([check.finding?.suggestions || check.finding?.classification, notice].filter(Boolean))].join('；') || '未取得判定理由。';
}

export function locationLabel(locator: Locator | null): string {
  if (!locator) return '';
  switch (locator.kind) {
    case 'xlsx_cell':
      return `工作表「${locator.sheet}」${locator.cell}`;
    case 'xlsx_range':
      return `工作表「${locator.sheet}」${locator.start}:${locator.end}`;
    case 'pdf_page':
      return `第 ${locator.page} 页`;
    case 'pdf_block':
      return `第 ${locator.page} 页 第 ${locator.block} 段`;
    case 'pdf_cell':
      return `第 ${locator.page} 页 表格第 ${locator.row} 行第 ${locator.column} 列`;
    case 'docx_paragraph':
      return `第 ${locator.paragraph} 段`;
    case 'docx_cell':
      return `第 ${locator.table} 个表格 第 ${locator.row} 行第 ${locator.column} 列`;
  }
}

export function when(raw: string | null | undefined): string {
  const date = raw ? new Date(raw) : null;
  if (!date || Number.isNaN(date.getTime())) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
