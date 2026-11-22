// The work item as one PPAP package. PPAP judges a package one round at a time: every file of
// a round is frozen together, and cross-file requirements are checked against the others in
// that round. So the conclusion shown here is always the latest finished round's own
// conclusion, never a mix of rounds; this file only relates that round to today's attachments.
import { bucket, checkerUnavailable, openCount, pendingCount, pendingNote, reviewOf } from './model';
import type { Attachment, Check, Task, TaskFile } from './model';

export type DocState = 'unsupported' | 'new' | 'added' | 'matching' | 'confirm' | 'running' | 'done' | 'failed' | 'reference' | 'no_rule' | 'unavailable' | 'unread' | 'removed';

export type Doc = {
  attachment: Attachment;
  state: DocState;
  /** This upload's row in the audit that describes it: the one in flight, else the latest finished round. */
  row: TaskFile | null;
  type: string | null;
};

// The formats PPAP's file check accepts for audit (worker/intake/file_validation.py).
const AUDITABLE = ['.xlsx', '.xls', '.xlsm', '.docx', '.doc', '.pdf'];
export const auditable = (name: string) => AUDITABLE.some(suffix => name.toLowerCase().endsWith(suffix));

export type Rule = { id: string; title: string; type: string | null; customers: string[] | null; predecessor_id: string | null; kind?: string; rule_profile?: string | null; customer_scope?: string | null };

// Audit templates: the same keys and names as the PPAP web rules page (apps/web/src/audit/rule-templates.ts).
// A template is one customer's set of rules, one per document type; system templates come with PPAP,
// enterprise templates are the enterprise's own.
const SHARED_TEMPLATE = 'shared';
const PENDING_TEMPLATE = 'pending';

export function templateKey(rule: Rule): string {
  const customer = rule.customers?.[0]?.trim();
  if (rule.rule_profile === 'v4' && customer) return `system:${customer}`;
  if (rule.customer_scope === 'all') return SHARED_TEMPLATE;
  return customer ? `customer:${customer}` : PENDING_TEMPLATE;
}

/** A template's name; a customer's built-in rules are named by the customer's registered name. */
export function templateName(key: string, customers: RegisteredCustomer[] | null): string {
  if (key === SHARED_TEMPLATE) return '通用规则';
  if (key.startsWith('system:')) return `${customerName(customers, key.slice('system:'.length))} 规则`;
  return key.replace(/^customer:/, '');
}

/** The customer a template's rules belong to; shared rules belong to none. */
export function templateOwner(key: string): string | null {
  return /^(?:system|customer):(.+)$/.exec(key)?.[1] ?? null;
}

/** Each template's newest audit rules, as recognition accepts them. Unsorted uploads are not a template. */
export function templates(rules: Rule[]): Map<string, Rule[]> {
  const replaced = new Set(rules.map(rule => rule.predecessor_id).filter(Boolean));
  const grouped = new Map<string, Rule[]>();
  for (const rule of rules) {
    if (rule.kind !== 'engine' || replaced.has(rule.id)) continue;
    const key = templateKey(rule);
    if (key !== PENDING_TEMPLATE) grouped.set(key, [...(grouped.get(key) ?? []), rule]);
  }
  return grouped;
}

/** The template a task is using: one whose customer is the task's, the space's last choice first. */
export function currentTemplate(all: Map<string, Rule[]>, customer: string | null, remembered: string | null): string | null {
  const owned = [...all.keys()].filter(key => customer && templateOwner(key) === customer.trim());
  return remembered && owned.includes(remembered) ? remembered : owned.find(key => key.startsWith('system:')) ?? owned[0] ?? null;
}

// The last template used in a space, per viewer; a convenience that the page works without.
export function rememberedTemplate(space: string): string | null {
  try {
    return localStorage.getItem(`ppap.template.${space}`);
  } catch {
    return null;
  }
}

export function rememberTemplate(space: string, key: string): void {
  try {
    localStorage.setItem(`ppap.template.${space}`, key);
  } catch {
    // The choice still applies; it is only not remembered.
  }
}

/**
 * An audit this person began on a work item but whose start did not finish: its id and what it was
 * started from. Kept across a reload, so a retry of the same start reads that audit back instead of
 * creating another one.
 */
export type PendingStart = { task: string; from: string };

export function pendingStart(item: string): PendingStart | null {
  try {
    const value = JSON.parse(localStorage.getItem(`ppap.start.${item}`) || 'null');
    return value && typeof value.task === 'string' && typeof value.from === 'string' ? value : null;
  } catch {
    return null;
  }
}

export function rememberStart(item: string, value: PendingStart | null): void {
  try {
    if (value) localStorage.setItem(`ppap.start.${item}`, JSON.stringify(value));
    else localStorage.removeItem(`ppap.start.${item}`);
  } catch {
    // Without storage a retry starts afresh; the earlier attempt stays in PPAP as an unconfirmed draft.
  }
}

export type Package = {
  docs: Doc[];
  /** The audit still being recognized, confirmed or run. */
  flight: Task | null;
  /** The latest finished round; its conclusion is the package's conclusion. */
  latest: Task | null;
  previous: Task | null;
  /** Files the latest round judged that are no longer attached. */
  removed: TaskFile[];
  /** The attachments are no longer the set the latest round judged. */
  stale: boolean;
};

const judged = (task: Task) => task.state === 'completed' && Boolean(task.round_id) && task.completion_reason !== 'no_matching_rules';

/** `tasks` are this work item's audits, newest first. */
/** `sources` says which attachment each audited file version was imported from. */
export function buildPackage(attachments: Attachment[], tasks: Task[], sources: Record<string, string>): Package {
  const uidOf = (row: TaskFile) => sources[row.file.id] ?? '';
  const flight = tasks[0] && tasks[0].state !== 'completed' ? tasks[0] : null;
  const finished = tasks.filter(judged);
  const latest = finished[0] ?? null;
  const previous = finished[1] ?? null;
  const inRound = (task: Task | null, uid: string) => task?.files.find(row => row.selection !== 'unselected' && uidOf(row) === uid) ?? null;
  const docs = attachments.map((attachment): Doc => {
    const moving = inRound(flight, attachment.uid);
    if (moving) {
      const state: DocState = flight!.state === 'matching' ? 'matching' : flight!.state === 'pending_confirm' ? 'confirm' : 'running';
      return { attachment, state, row: moving, type: moving.match?.document_type ?? null };
    }
    const row = inRound(latest, attachment.uid);
    // A format PPAP cannot audit is not part of the package, so it never makes a conclusion stale.
    if (!row) return { attachment, state: !auditable(attachment.name) ? 'unsupported' : latest ? 'added' : 'new', row: null, type: null };
    return { attachment, state: row.audit ? (row.audit.execution === 'review_failed' ? 'failed' : 'done') : unaudited(row), row, type: row.match?.document_type ?? null };
  });
  const attached = new Set(attachments.map(file => file.uid));
  const removed = latest ? latest.files.filter(row => row.selection === 'selected' && !attached.has(uidOf(row))) : [];
  return { docs, flight, latest, previous, removed, stale: Boolean(latest) && (removed.length > 0 || docs.some(doc => doc.state === 'added')) };
}

/** Why a file of a round has no result of its own; a file without a result is never shown as still running. */
export function unaudited(row: TaskFile): DocState {
  // A file the person took out of this audit, with the reason they gave.
  if (row.selection === 'removed') return 'removed';
  if (row.reference_only || row.match?.state === 'reference') return 'reference';
  if (checkerUnavailable(row)) return 'unavailable';
  if (row.match?.state === 'failed') return 'unread';
  return 'no_rule';
}

export type Verdict = 'PASS' | 'NG' | 'UNKNOWN' | 'ERROR' | 'NONE';

/** The latest round's conclusion as PPAP states it; a round still running has none yet and reads as open. */
export const verdict = (task: Task | null): Verdict => (task ? task.conclusion ?? 'UNKNOWN' : 'NONE');

export type Change = 'added' | 'persisted' | null;
export type OpenIssue = { row: TaskFile; check: Check; change: Change };
export type Standing = { issues: OpenIssue[]; resolved: { row: TaskFile; check: Check }[] };

const settled = (check: Check | undefined, status: string) => check?.outcome === status;
const sameRule = (left: Check, right: Check | undefined) => right?.rule_item.rule_to_check.trim() === left.rule_item.rule_to_check.trim();

/** A document keeps its identity across rounds by its recognized type, when each round has exactly one of that type. */
function earlierChecks(previous: Task | null, latest: Task, row: TaskFile): Map<string, Check> | null {
  const type = row.match?.document_type;
  if (!previous || !type) return null;
  const only = (task: Task) => task.files.filter(file => file.selection === 'selected' && file.audit && file.match?.document_type === type);
  const before = only(previous);
  if (before.length !== 1 || only(latest).length !== 1) return null;
  return new Map(before[0].audit!.checks.map(check => [check.rule_item.id, check]));
}

/** What the latest round leaves for a person, and what changed since the round before; changes follow the web app's round comparison. */
export function standing(latest: Task | null, previous: Task | null): Standing {
  const issues: OpenIssue[] = [];
  const resolved: Standing['resolved'] = [];
  for (const row of latest?.files ?? []) {
    if (row.selection !== 'selected' || !row.audit) continue;
    const before = earlierChecks(previous, latest!, row);
    for (const check of row.audit.checks) {
      const earlier = before?.get(check.rule_item.id);
      const comparable = earlier && sameRule(check, earlier);
      if (comparable && settled(check, 'PASS') && settled(earlier, 'NG')) resolved.push({ row, check });
      if (!check.attention) continue;
      const change: Change = comparable && settled(check, 'NG') ? (settled(earlier, 'NG') ? 'persisted' : settled(earlier, 'PASS') ? 'added' : null) : null;
      issues.push({ row, check, change });
    }
  }
  const rank = { NG: 0, ERR: 1, UNABLE: 2, uncovered: 3, PASS: 4, NA: 5, running: 6 } as const;
  issues.sort((left, right) => rank[bucket(left.check)] - rank[bucket(right.check)]);
  return { issues, resolved };
}

/** A registered customer as GET /audit-customers lists it: the one name shown, the key rules and a customer choice carry, and its document type set. */
export type RegisteredCustomer = { customer_id: string; name: string; key: string; coverage: { types: { code: string; rule_type: string }[] } | null };
/** GET /audit-customers: the registered customers and the one name of every document type code. */
export type CustomerCatalog = { customers: RegisteredCustomer[]; type_names: Record<string, string> };

/** The registered customer whose rule set a key names (a task's choice, a rule's customers); a company name a file writes is never looked up. */
export const registeredCustomer = (customers: RegisteredCustomer[] | null, key: string | null) => (key ? customers?.find(customer => customer.key === key) : undefined);
/** The name people read for a chosen rule set: the registered name by its key; an enterprise's own rule set is named by itself. */
export const customerName = (customers: RegisteredCustomer[] | null, key: string) => registeredCustomer(customers, key)?.name ?? key;

/** A document type's name from the register's one table; an unlisted type, or one before the register is read, is shown as recorded. */
export const documentTypeName = (typeNames: Record<string, string> | null, type: string) => typeNames?.[type] ?? type;

/** The document types of the customer's registered type set; the customer type decides what a part must submit. */
export function requiredTypes(customers: RegisteredCustomer[] | null, customer: string | null): string[] {
  return [...new Set(registeredCustomer(customers, customer)?.coverage?.types.map(type => type.rule_type) ?? [])];
}

/** The round's 待处理 and 待审核 counts, said per file the way the web app says them: 待审核 clauses beside a 符合/不符合 file are not 待处理. */
export function openTotals(task: Task | null): { open: number; pending: number } {
  const audits = (task?.files ?? []).filter(row => row.selection === 'selected' && row.audit).map(row => row.audit!);
  return {
    open: audits.reduce((sum, audit) => sum + openCount(audit), 0),
    pending: audits.reduce((sum, audit) => sum + (pendingNote(audit) ? pendingCount(audit) : 0), 0),
  };
}

/** A plain-text list a quality engineer can send to the supplier as it is. */
export function rectification(view: Standing): string {
  const lines: string[] = [];
  const byFile = new Map<string, OpenIssue[]>();
  // A result the reviewer overruled is not something to send back to the supplier.
  const overruled = (check: Check) => ['disagree', 'manual-satisfied'].includes(reviewOf(check)?.judgment || '');
  view.issues.filter(issue => bucket(issue.check) === 'NG' && !overruled(issue.check)).forEach(issue => byFile.set(issue.row.file.original_filename, [...(byFile.get(issue.row.file.original_filename) || []), issue]));
  for (const [name, list] of byFile) {
    lines.push(`【${name}】`);
    list.forEach((issue, index) => {
      const review = reviewOf(issue.check);
      lines.push(`${index + 1}. ${issue.check.rule_item.rule_to_check}`);
      const why = issue.check.finding?.suggestions || issue.check.finding?.classification;
      if (why) lines.push(`   问题：${why}`);
      if (review?.next_step) lines.push(`   要求：${review.next_step}`);
    });
  }
  return lines.join('\n');
}
