import {utils, write} from 'xlsx'
import {strToU8, zipSync} from 'fflate'
import type {AuditTemplateAdapter, TemplateItem} from '../src/audit/template-contract'

const excel = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
const allowed = {state: 'allowed' as const, reason: null}
const denied = {state: 'denied' as const, reason: '示例：当前账号无权读取此模板。'}
// Explicit UI dictionary. The real /rule-types has 18 entries, not this #170 denominator.
const universe = ['全功能检验报告', '零部件设计验证计划和报告', '材料产品及分供清单', '检具验收报告', '模具验收报告', '测量系统分析评价', '过程能力研究', '过程流程图', '潜在过程失效模式及后果分析', '控制计划', '过程特殊特性清单', '设备清单', '生产系统产能评估报告', '项目问题清单', '零部件年度试验要求', '零件提交保证书', 'DV报告_PV报告_试验报告']

/** Local template bytes are conspicuously marked; no customer Drive files or rules are changed. */
export function fixtureTemplates(scene: string): AuditTemplateAdapter {
  const bytes = new Map<string, Blob>()
  const listReads = new Map<string, number>()
  const rows = (['a', 'b'] as const).flatMap(customer => Array.from({length: customer === 'a' ? 12 : 1}, (_, position): TemplateItem => {
    const index = position + 1, id = `preview-template-${customer}-${index}-v1`, name = customer === 'b' ? '示例客户B模板.xlsx' : index === 1 ? '示例控制计划模板.xlsx' : index === 2 ? '示例尺寸检验报告模板.xlsx' : `示例归档模板 ${index}.xlsx`
    const book = utils.book_new()
    utils.book_append_sheet(book, utils.aoa_to_sheet([['示例模板，仅用于交互预览'], ['客户', `示例客户 ${customer.toUpperCase()}`], ['模板版本', id], ['项目', '填写值'], ['零件号', ''], ['检查项目', ''], ['判定标准', '']]), '模板')
    const blob = new Blob([write(book, {bookType: 'xlsx', type: 'array'})], {type: excel})
    bytes.set(id, blob)
    return {reference: {customer_id: `preview-customer-${customer}`, template_id: `preview-template-${customer}-${index}`, file_version_id: id}, file: {id, file_id: `preview-template-${customer}-${index}`, original_filename: name, content_type: excel, byte_size: blob.size, status: 'ready', sha256: null}, document_type: index === 2 ? '尺寸检验报告' : '控制计划', updated_at: `2026-10-${String(16 - position).padStart(2, '0')}T08:00:00Z`, preview_access: scene === 'templates-denied' && index === 1 ? denied : allowed, download_access: scene === 'templates-denied' && index === 1 ? denied : allowed, share_access: allowed}
  }))
  return {
    readCatalog: async () => ({coverage_universe: universe, customers: scene === 'templates-empty-customers' ? [] : [{customer_id: 'preview-customer-a', name: '示例客户 A', effective_rule_types: ['控制计划', '全功能检验报告', '潜在过程失效模式及后果分析', '过程流程图', '测量系统分析评价', '零件提交保证书', '控制计划']}, {customer_id: 'preview-customer-b', name: '示例客户 B', effective_rule_types: ['控制计划']}] }),
    listTemplates: async query => {
      listReads.set(query.customer_id, (listReads.get(query.customer_id) || 0) + 1)
      const all = scene === 'templates-empty' ? [] : rows.filter(row => row.reference.customer_id === query.customer_id), filtered = all.filter(row => `${row.file.original_filename} ${row.document_type}`.includes(query.search))
      const manage = scene.startsWith('templates-permissions-') && !(scene === 'templates-permissions-restore-denied' && listReads.get(query.customer_id)! > 1)
      return {query: structuredClone(query), folder_id: `preview-folder-${query.customer_id}`, folder_revision: 'preview-revision-1', folder_template_count: all.length, creator_name: '示例创建人', manage_access: manage ? allowed : {state: 'denied', reason: '只有服务确认的文件夹管理者可修改权限。'}, zip_access: scene === 'templates-denied' ? denied : allowed, items: structuredClone(filtered.slice((query.page - 1) * 10, query.page * 10)), total: filtered.length}
    },
    readFile: async (item, purpose) => {
      const row = rows.find(row => row.reference.file_version_id === item.reference.file_version_id && row.reference.customer_id === item.reference.customer_id && row.reference.template_id === item.reference.template_id)
      if (!row || row[`${purpose}_access`].state !== 'allowed' || scene === 'templates-preview-error' && purpose === 'preview') throw new Error('template_unavailable')
      return {reference: structuredClone(row.reference), blob: bytes.get(row.reference.file_version_id)!}
    },
    readZip: async folder => {
      const files = rows.filter(row => row.reference.customer_id === folder.customer_id)
      if (scene === 'templates-denied' || folder.folder_id !== `preview-folder-${folder.customer_id}` || folder.folder_revision !== 'preview-revision-1') throw new Error('template_folder_unavailable')
      const entries: Record<string, Uint8Array> = {}, index = ['示例模板，不是真实客户文件。']
      for (const item of files) {entries[item.file.original_filename] = new Uint8Array(await bytes.get(item.file.id)!.arrayBuffer()); index.push(`${item.file.original_filename}\n${item.file.id}`)}
      entries['模板清单.txt'] = strToU8(index.join('\n'))
      const blob = new Blob([new Uint8Array(zipSync(entries))], {type: 'application/zip'})
      return {snapshot: {customer_id: folder.customer_id, folder_id: folder.folder_id, folder_revision: folder.folder_revision, templates: files.map(row => ({reference: row.reference, filename: row.file.original_filename, byte_size: row.file.byte_size})), manifest_filename: '模板清单.txt', filename: `${folder.customer_id}-示例模板.zip`, byte_size: blob.size, complete: scene !== 'templates-zip-incomplete', access: allowed}, blob}
    },
  }
}
