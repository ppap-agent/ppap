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
  // The clause's standing as PPAP defines it, once, for every reader: the machine result, or the result
  // recomputed from confirmed original facts; and whether it still needs a person.
  outcome: Bucket;
  attention: boolean;
  objected: boolean;
  confirmation?: Confirmation;
};
export type Confirmation = { status: FindingStatus; suggestions: string; confirmed_by: string[]; confirmed_at: string };
/** An original fact the machine could not read, asked of the task's creator; answering recomputes its clauses without a model. */
export type FactQuestion = { file_version_id: string; field: string; prompt: string; answer_type: 'boolean' | 'text'; locator: Locator | null; item_keys: string[]; fingerprint: string; answered: boolean };
export type FactView = { round_id: string; revision: number; questions: FactQuestion[] };

export type Report = { state: 'not_eligible' | 'pending' | 'generating' | 'ready' | 'failed'; report_id: string | null; artifact: { artifact_id: string; filename: string } | null; regenerate_allowed?: boolean; reason?: string | null };

export type FileAudit = {
  round_id: string;
  file_version_id: string;
  result_attempt: number | null;
  execution: 'queued' | 'running' | 'completed' | 'review_failed';
  progress: { completed: number; total: number };
  conclusion: 'PASS' | 'NG' | 'UNKNOWN' | null;
  // The machine conclusion with clauses settled by confirmed original facts; what the file stands at.
  confirmed_conclusion: 'PASS' | 'NG' | 'UNKNOWN' | null;
  counts: { pass_count: number; ng_count: number; unknown_count: number; not_covered_count: number; error_count: number };
  checks: Check[];
  report: Report;
};

export type Choice = { id: string; title: string };

export type TaskFile = {
  file: FileVersion;
  selection: 'selected' | 'unselected' | 'removed';
  removal_reason: string | null;
  reference_only?: boolean;
  validation_id?: string | null;
  preferred_rule_version_id?: string | null;
  replacement_of_file_version_id?: string | null;
  rule_options?: Choice[];
  match: { state: string; document_type: string | null; rule: { title: string } | null; gaps?: string[]; candidates?: Choice[] } | null;
  audit?: FileAudit | null;
};

export type Task = {
  id: string;
  number: string;
  state: 'matching' | 'pending_confirm' | 'reviewing' | 'completed';
  created_at: string;
  // Everyone in the enterprise sees a task; only its creator (mine) confirms, reviews, writes back or releases it.
  creator: { name: string; mine: boolean };
  customer_preference?: string | null;
  // The earlier round a re-audit continues; every revision of the draft must name it again.
  predecessor_id?: string | null;
  round_id: string | null;
  round_started_at?: string | null;
  completion_reason: 'no_matching_rules' | 'processed' | null;
  estimated_points: number | null;
  // What confirming would accept, as the server saved it; absent until the scope is settled.
  confirmation_scope?: { target_file_version_ids: string[]; reference_file_version_ids: string[]; excluded_files: { file_version_id: string; reason: string }[] } | null;
  input_revision?: number;
  confirmation_basis_digest: string | null;
  deep_mode?: boolean;
  deep_mode_option?: { available: boolean; reason?: string | null };
  execution?: 'queued' | 'running' | 'completed' | 'failed';
  has_execution_error?: boolean;
  // The round's standing, defined by PPAP: what writeback, release and the flow node also read.
  conclusion?: 'PASS' | 'NG' | 'UNKNOWN' | 'ERROR' | null;
  objection?: boolean;
  progress?: { completed: number; total: number };
  files: TaskFile[];
};

export type AuditSummary = { id: string; number: string; state: Task['state']; round_id: string | null; created_at: string };

export type PrecheckFinding = { number: string; code: string; title: string; filename: string; file_version_id: string; level: 'block' | 'warn'; scope: 'file' | 'content'; location?: string; problem: string; action: string };
export type Precheck = { conclusion: 'pass' | 'fail' | 'not_run'; blocks: PrecheckFinding[]; warnings: PrecheckFinding[]; files: unknown[] };

export const resultLabel: Record<FindingStatus, string> = { PASS: '符合', NG: '不符合', UNABLE: '待审核', ERR: '技术错误', NA: '不适用' };
export const uncoveredLabel = '未覆盖';
export const conclusionLabel: Record<string, string> = { PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项' };
/** A round's conclusion as this plugin says it, including no round yet; the one wording for every view. */
export const verdictLabel = { PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项', ERROR: '审核异常', NONE: '尚未审核' } as const;
export const matchLabel: Record<string, string> = { matched: '已匹配规则', reference: '作为参考资料', unmatched: '无适配规则', failed: '识别失败', pending: '识别中', matching: '识别中' };
/** A re-audit file's rule options are the original audit's rule version, then a newer version when that rule has one (as in the web app's reaudit-intent). */
export type RuleVersionChoice = 'newer' | 'original';
export const newerRuleOf = (row: { reference_only?: boolean; rule_options?: Choice[] }): Choice | undefined => (!row.reference_only && row.rule_options && row.rule_options.length > 1 ? row.rule_options[1] : undefined);
/** Points a file with a newer rule version at the chosen version; other files keep their rule. */
export function withRuleVersion<T extends { reference_only?: boolean; rule_options?: Choice[]; preferred_rule_version_id?: string | null }>(row: T, choice: RuleVersionChoice): T {
  const newer = newerRuleOf(row);
  return newer ? { ...row, preferred_rule_version_id: choice === 'newer' ? newer.id : row.rule_options![0].id } : row;
}
/** Which version the selected files with a newer rule use; null when they differ or there are none. */
export function ruleVersionChoice(files: TaskFile[]): RuleVersionChoice | null {
  const versioned = files.filter(row => row.selection === 'selected' && newerRuleOf(row));
  if (!versioned.length) return null;
  if (versioned.every(row => row.preferred_rule_version_id === newerRuleOf(row)!.id)) return 'newer';
  if (versioned.every(row => row.preferred_rule_version_id === row.rule_options![0].id)) return 'original';
  return null;
}
/** The rule's own name: the option titles carry which version they are. */
export const ruleName = (option: Choice) => option.title.replace(/ · (?:原审核版本|较新版本)$/, '');

/** The file is fine but its type has no checker program yet (the web app's "暂不能审核"). */
export const checkerUnavailable = (row: TaskFile) => Boolean(row.match?.gaps?.includes('checker_suite_unavailable'));
export const judgmentLabel: Record<string, string> = {
  confirm: '确认机器结果',
  disagree: '不同意机器结果',
  unknown: '仍待审核',
  'manual-satisfied': '人工确认满足要求',
  'manual-unsatisfied': '人工确认不满足要求',
};


export type Bucket = FindingStatus | 'uncovered' | 'running';

/** Where a clause stands, as PPAP decided it. */
export const bucket = (check: Check): Bucket => check.outcome;

export const bucketLabel = (value: Bucket) => (value === 'uncovered' ? uncoveredLabel : value === 'running' ? '审核中' : resultLabel[value]);
export const bucketColor = (value: Bucket | 'UNKNOWN' | null | undefined): 'red' | 'green' | 'orange' | 'grey' =>
  value === 'NG' ? 'red' : value === 'PASS' ? 'green' : value === 'UNABLE' || value === 'ERR' || value === 'uncovered' || value === 'UNKNOWN' ? 'orange' : 'grey';

export const reviewOf = (check: Check): Review | null => check.review ?? check.finding?.review ?? null;
const rank: Record<Bucket, number> = { NG: 0, ERR: 1, UNABLE: 2, uncovered: 3, PASS: 4, NA: 5, running: 6 };
export const byUrgency = (left: Check, right: Check) => rank[bucket(left)] - rank[bucket(right)];

/** 待审核 clauses of a file (待审核 / 未覆盖), never counted as PASS; as in the web app's result projection. */
export const pendingCount = (audit: FileAudit) => audit.checks.filter(check => check.outcome === 'UNABLE' || check.outcome === 'uncovered').length;
/** 「另 N 项待审核」 beside a file's conclusion when it is 符合 or 不符合; empty otherwise. */
export function pendingNote(audit: FileAudit): string {
  const count = pendingCount(audit);
  const conclusion = audit.confirmed_conclusion ?? audit.conclusion;
  return count && (conclusion === 'PASS' || conclusion === 'NG') ? `另 ${count} 项待审核` : '';
}
/** Clauses still needing a person besides those named by pendingNote. */
export const openCount = (audit: FileAudit) => audit.checks.filter(check => check.attention).length - (pendingNote(audit) ? pendingCount(audit) : 0);

/** A checker's own run notes: shown apart as 程序执行说明, never as the reason for the result. */
export const programNotice = (check: Check): string | null => (check.verification_scope === 'checker_execution' && check.scope_notice ? check.scope_notice : null);

/** Why the machine reached this result, in the words the web app shows. */
export function observation(check: Check): string {
  if (check.execution === 'queued' || check.execution === 'running') return '本项审核中…';
  const notice = programNotice(check) ? null : check.scope_notice === 'manual-only' || check.scope_notice === 'unsupported' ? '本条要求未完成自动检查，请核对原件并记录人工复核。' : check.scope_notice;
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
