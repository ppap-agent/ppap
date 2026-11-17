// The work item as one PPAP package. PPAP judges a package one round at a time: every file of
// a round is frozen together, and cross-file requirements are checked against the others in
// that round. So the conclusion shown here is always the latest finished round's own
// conclusion, never a mix of rounds; this file only relates that round to today's attachments.
import { bucket, checkerUnavailable, needsAttention, reviewOf } from './model';
import type { Attachment, Check, Task, TaskFile } from './model';

export type DocState = 'unsupported' | 'new' | 'added' | 'matching' | 'confirm' | 'running' | 'done' | 'failed' | 'reference' | 'no_rule' | 'unavailable' | 'unread';

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

export function templateName(key: string): string {
  if (key === SHARED_TEMPLATE) return '通用规则';
  if (key.startsWith('system:')) return `${key.slice('system:'.length)} 规则`;
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
  const owned = [...all.keys()].filter(key => customer && templateOwner(key) && sameCustomer(templateOwner(key)!, customer));
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
  if (row.reference_only || row.match?.state === 'reference') return 'reference';
  if (checkerUnavailable(row)) return 'unavailable';
  if (row.match?.state === 'failed') return 'unread';
  return 'no_rule';
}

export type Verdict = 'PASS' | 'NG' | 'UNKNOWN' | 'ERROR' | 'NONE';

/** The latest round's own conclusion. Anything failed, unfinished or unknown is never conforming. */
export function verdict(task: Task | null): Verdict {
  if (!task) return 'NONE';
  const audits = task.files.filter(row => row.selection === 'selected' && row.audit).map(row => row.audit!);
  if (task.execution === 'failed' || task.has_execution_error || audits.some(audit => audit.execution === 'review_failed')) return 'ERROR';
  if (!audits.length || task.execution !== 'completed' || audits.some(audit => !audit.conclusion)) return 'UNKNOWN';
  if (audits.some(audit => audit.conclusion === 'NG')) return 'NG';
  return audits.every(audit => audit.conclusion === 'PASS') ? 'PASS' : 'UNKNOWN';
}

export type Change = 'added' | 'persisted' | null;
export type OpenIssue = { row: TaskFile; check: Check; change: Change };
export type Standing = { issues: OpenIssue[]; resolved: { row: TaskFile; check: Check }[] };

const settled = (check: Check | undefined, status: string) => Boolean(check && check.execution === 'completed' && check.coverage === 'executed' && check.finding?.status === status);
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
      if (!needsAttention(check)) continue;
      const change: Change = comparable && settled(check, 'NG') ? (settled(earlier, 'NG') ? 'persisted' : settled(earlier, 'PASS') ? 'added' : null) : null;
      issues.push({ row, check, change });
    }
  }
  const rank = { NG: 0, ERR: 1, UNABLE: 2, uncovered: 3, PASS: 4, NA: 5, running: 6 } as const;
  issues.sort((left, right) => rank[bucket(left.check)] - rank[bucket(right.check)]);
  return { issues, resolved };
}

const plain = (name: string) => name.trim().toLowerCase().replace(/[（(].*?[)）]/g, '').replace(/汽车$/, '');
/** "理想" and "理想汽车" name one customer, as in the web app. */
const sameCustomer = (left: string, right: string) => {
  const a = plain(left);
  const b = plain(right);
  return Boolean(a && b) && (a === b || a.startsWith(b) || b.startsWith(a));
};

/** The document types this customer's current rules cover; the customer type decides what a part must submit. */
export function requiredTypes(rules: Rule[], customer: string | null): string[] {
  if (!customer) return [];
  const superseded = new Set(rules.map(rule => rule.predecessor_id).filter(Boolean));
  const types = rules.filter(rule => !superseded.has(rule.id) && rule.type && (rule.customers || []).some(name => sameCustomer(name, customer))).map(rule => rule.type!);
  return [...new Set(types)];
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
