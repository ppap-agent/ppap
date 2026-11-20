// Reading one work item's PPAP picture: its attachments, the enterprise's audits of it, and what
// decides the expected document types. Shared by the detail tab and the node card.
import type { RegisteredCustomer, Rule } from './ledger';
import type { Attachment, AuditSummary, FactView, Task } from './model';
import { call } from './ppap';

export type Item = { project_key: string; work_item_type_key: string; work_item_id: string };
// The work item's PPAP AI node, when its flow has one; null when it has none. `outside`: a person
// completed it in Feishu Project, without PPAP's writeback or release.
export type AiNode = { status: 'idle' | 'running' | 'done' | 'stopped' | 'completed' | 'removed'; outside?: boolean } | null;
// Said the same way wherever the node is shown.
export const outsideText = '这个节点是在飞书项目里直接完成的，没有经过 PPAP 的写回或人工放行。';
// A person's release of a round's PPAP node; kept apart from the round's own conclusion.
export type Release = { by: string; reason: string; at: string };
export type Data = { attachments: Attachment[]; fields: Record<string, string>; tasks: Task[]; written: string[] | null; sources: Record<string, string>; rules: Rule[]; customers: RegisteredCustomer[] | null; defaultCustomer: string | null; aiNode: AiNode; releases: Record<string, Release>; facts: FactView | null };

// A work item rarely has more audits than this; older ones remain in PPAP.
const RECENT = 8;
export const itemPath = (item: Item) => `/integrations/meego/work-items/${item.project_key}/${item.work_item_type_key}/${item.work_item_id}`;

export async function loadWorkItem(item: Item, names: Record<string, string>): Promise<Data> {
  // The rule list, the customer register and the enterprise's default customer only decide templates,
  // names and which document types to expect; the page still works when any cannot be read.
  const [files, audits, rules, customer, register] = await Promise.all([
    call<{ files: Attachment[] }>(`${itemPath(item)}/files`),
    call<{ audits: AuditSummary[]; written_round_ids: string[] | null; sources: Record<string, string>; ai_node?: AiNode; releases?: Record<string, Release> }>(`${itemPath(item)}/audits`),
    call<{ rules: Rule[] }>('/rules').catch(() => ({ rules: [] as Rule[] })),
    call<{ customer_preference: string | null }>('/audit-customers/default').catch(() => ({ customer_preference: null })),
    call<{ customers: RegisteredCustomer[] }>('/audit-customers').then(reply => reply.customers, () => null),
  ]);
  // A draft that was never confirmed only matters while it is the latest thing here.
  const wanted = audits.audits.filter((audit, index) => audit.round_id || index === 0).slice(0, RECENT);
  const tasks = await Promise.all(wanted.map(audit => call<Task>(`/audit-tasks/${audit.id}`)));
  // The original facts the latest finished round asks a person to confirm. Everyone reads them; only the
  // round's creator answers.
  const finished = tasks.find(task => task.state === 'completed' && task.round_id);
  const facts = finished ? await call<FactView>(`/audit-rounds/${finished.round_id}/fact-answers`).catch(() => null) : null;
  // Attachment fields keep the names the space gave them; a field that cannot be read shows no heading.
  for (const key of new Set(files.files.map(file => file.field_key))) {
    if (key && names[key] === undefined) {
      names[key] = await window.JSSDK.Field.load({ spaceId: item.project_key, workObjectId: item.work_item_type_key, fieldId: key }).then(field => field.name, () => '');
    }
  }
  return { attachments: files.files, fields: { ...names }, tasks, written: audits.written_round_ids, sources: audits.sources ?? {}, rules: rules.rules, customers: register, defaultCustomer: customer.customer_preference, aiNode: audits.ai_node ?? null, releases: audits.releases ?? {}, facts };
}
