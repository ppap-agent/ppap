// Reading one work item's PPAP picture: its attachments, this person's audits of it, and what
// decides the expected document types. Shared by the detail tab and the node card.
import type { Rule } from './ledger';
import type { Attachment, AuditSummary, Task } from './model';
import { call } from './ppap';

export type Item = { project_key: string; work_item_type_key: string; work_item_id: string };
// The work item's PPAP AI node, when its flow has one; null when it has none.
export type AiNode = { status: 'idle' | 'running' | 'done' | 'stopped' | 'completed' | 'removed' } | null;
export type Data = { attachments: Attachment[]; fields: Record<string, string>; tasks: Task[]; written: string[] | null; sources: Record<string, string>; rules: Rule[]; defaultCustomer: string | null; aiNode: AiNode };

// A work item rarely has more audits than this; older ones remain in PPAP.
const RECENT = 8;
export const itemPath = (item: Item) => `/integrations/meego/work-items/${item.project_key}/${item.work_item_type_key}/${item.work_item_id}`;

export async function loadWorkItem(item: Item, names: Record<string, string>): Promise<Data> {
  // The rule list and the enterprise's default customer only decide which document types to expect;
  // the page still works when either cannot be read.
  const [files, audits, rules, customer] = await Promise.all([
    call<{ files: Attachment[] }>(`${itemPath(item)}/files`),
    call<{ audits: AuditSummary[]; written_round_ids: string[] | null; sources: Record<string, string>; ai_node?: AiNode }>(`${itemPath(item)}/audits`),
    call<{ rules: Rule[] }>('/rules').catch(() => ({ rules: [] as Rule[] })),
    call<{ customer_preference: string | null }>('/audit-customers/default').catch(() => ({ customer_preference: null })),
  ]);
  // A draft that was never confirmed only matters while it is the latest thing here.
  const wanted = audits.audits.filter((audit, index) => audit.round_id || index === 0).slice(0, RECENT);
  const tasks = await Promise.all(wanted.map(audit => call<Task>(`/audit-tasks/${audit.id}`)));
  // Attachment fields keep the names the space gave them; a field that cannot be read shows no heading.
  for (const key of new Set(files.files.map(file => file.field_key))) {
    if (key && names[key] === undefined) {
      names[key] = await window.JSSDK.Field.load({ spaceId: item.project_key, workObjectId: item.work_item_type_key, fieldId: key }).then(field => field.name, () => '');
    }
  }
  return { attachments: files.files, fields: { ...names }, tasks, written: audits.written_round_ids, sources: audits.sources ?? {}, rules: rules.rules, defaultCustomer: customer.customer_preference, aiNode: audits.ai_node ?? null };
}
