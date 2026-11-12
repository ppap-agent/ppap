import type {TemplateLibrary, TemplateLibraryAdapter} from '../src/audit/template-library'

const xlsx = (id: string, name: string, document_type: string, extension = '.xlsx') => ({id, name, document_type, extension, filename: name + extension, byte_size: 24_576})
const library: TemplateLibrary = {customers: [
  {id: 'li', name: '理想汽车', templates: [xlsx('li-process-flow', '过程流程图', '过程流程图'), xlsx('li-pfmea', '潜在过程失效模式及后果分析（PFMEA）', '潜在过程失效模式及后果分析（PFMEA）'), xlsx('li-control-plan', '生产控制计划', '控制计划'), xlsx('li-mould-acceptance', '模具验收报告', '模具验收报告', '.xls')]},
  {id: 'nio', name: '蔚来汽车', templates: [xlsx('nio-measurement-report', '尺寸测量报告', '测量报告', '.xlsm'), xlsx('nio-control-plan', '控制计划', '控制计划')]},
  {id: 'uaes', name: '联合汽车电子（UAES）', templates: [xlsx('uaes-psw', '零件提交保证书（F-PU-206）', '零件提交保证书（PSW）', '.xls'), xlsx('uaes-traceability', '零件追溯检查单', '零件追溯说明')]},
]}

/** Fixture transport for the template library. Scenes: `templates-error` fails the first read; `templates-no-feishu` hides saving. */
export function fixtureTemplates(scene: string): TemplateLibraryAdapter {
  let reads = 0
  const pause = () => new Promise(resolve => setTimeout(resolve, 300))
  return {
    read: async () => {await pause(); if (scene === 'templates-error' && ++reads === 1) throw new Error('preview_templates_unavailable'); return structuredClone(library)},
    download: async (_customer, templateId) => {await pause(); return new Blob([`preview ${templateId}`], {type: 'application/octet-stream'})},
    downloadAll: async customer => {await pause(); return new Blob([`preview ${customer}`], {type: 'application/zip'})},
    ...scene === 'templates-no-feishu' ? {} : {feishu: {
      drive: {
        rootFolder: async () => ({token: 'preview-root', name: '我的文件夹', type: 'folder'}),
        list: async ({folderToken} = {}) => ({files: folderToken === 'preview-root' ? [{token: 'preview-ppap', name: 'PPAP 模板', type: 'folder'}] : []}),
      },
      save: async (customerId, folderToken) => {
        await pause()
        const templates = library.customers.find(row => row.id === customerId)!.templates
        return {customer_id: customerId, folder_token: folderToken, results: templates.map((template, index) => ({template_id: template.id, filename: template.filename, state: index === 0 ? 'exists' as const : 'saved' as const, file_token: index === 0 ? null : `preview-${template.id}`, reason: index === 0 ? '目录中已有同名文件，未覆盖，也未重复保存。' : null}))}
      },
    }},
    submit: async ({requestId, files}) => {await pause(); return {request_id: requestId, state: 'received', file_count: files.length, created_at: new Date().toISOString()}},
  }
}
