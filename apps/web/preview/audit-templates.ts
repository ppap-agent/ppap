import type {TemplateLibrary, TemplateLibraryAdapter} from '../src/audit/template-library'

// The same names, types and type codes as the product manifest (`product.template_library`).
const tpl = (id: string, name: string, document_type: string, type_code: string | null, extension = '.xlsx') => ({id, name, document_type, extension, filename: name + extension, byte_size: 24_576, type_code})
const standard = Array.from({length: 17}, (_, index) => `T${String(index + 1).padStart(2, '0')}`)
const uaesTypes = ['E01', 'E02', 'E03', 'E04', 'E05', 'E06', 'E07', 'E08', 'E09', 'E10', 'E11', 'T04', 'T07', 'T08', 'T09', 'T10', 'T11', 'T12', 'T17']
const library: TemplateLibrary = {customers: [
  {id: 'li', name: '理想汽车', standard_type_codes: standard, templates: [
    tpl('li-design-verification-plan', '零部件设计验证计划', '零部件设计验证计划和报告', 'T02'),
    tpl('li-material-supplier-list', '材料、产品及分供方清单', '材料产品及分供清单', 'T04'),
    tpl('li-gauge-acceptance', '检具认可报告', '检具验收报告', 'T05'),
    tpl('li-mould-acceptance', '模具验收报告', '模具验收报告', 'T06', '.xls'),
    tpl('li-msa', '测量系统分析报告', '测量系统分析评价', 'T07'),
    tpl('li-process-capability', '过程能力分析报告', '过程能力研究', 'T08'),
    tpl('li-process-flow', '过程流程图', '过程流程图', 'T09'),
    tpl('li-pfmea', '潜在过程失效模式及后果分析（PFMEA）', '潜在过程失效模式及后果分析（PFMEA）', 'T10'),
    tpl('li-control-plan', '生产控制计划', '控制计划', 'T11'),
    tpl('li-special-characteristics', '过程特殊特性清单', '过程特殊特性清单', 'T12'),
    tpl('li-ccsc', '产品过程CCSC特性管理清单', 'CC/SC 特性管理清单', null),
    tpl('li-equipment', '设备开发验收清单', '设备清单', 'T13'),
    tpl('li-capacity', '产能评估表', '生产系统产能评估报告', 'T14'),
    tpl('li-issue-list', '项目问题清单', 'APQP项目问题清单', 'T15'),
    tpl('li-annual-test', '零部件年度试验要求', '零部件年度试验要求', 'T16'),
    tpl('li-psw', '零件提交保证书（PSW签署）', '零件提交保证书', 'T17'),
    tpl('li-dimension-report', '尺寸检验报告', '尺寸检验报告', 'T01')]},
  {id: 'nio', name: '蔚来汽车', standard_type_codes: standard, templates: [
    tpl('nio-measurement-report', '尺寸测量报告', '测量报告', 'T01', '.xlsm'),
    tpl('nio-process-flow', '过程流程图', '过程流程图', 'T09'),
    tpl('nio-pfmea', '潜在过程失效模式及后果分析', '潜在过程失效模式及后果分析（PFMEA）', 'T10'),
    tpl('nio-control-plan', '控制计划', '控制计划', 'T11'),
    tpl('nio-special-characteristics', '特殊特性清单', '特殊特性清单', 'T12')]},
  {id: 'uaes', name: '联合汽车电子（UAES）', standard_type_codes: uaesTypes, templates: [
    tpl('uaes-psw', '零件提交保证书（F-PU-206）', '零件提交保证书（PSW）', 'T17', '.xls'),
    tpl('uaes-pgqp', '采购件质量计划 PGQP1（F-PU-127）', '采购件质量计划', 'E12'),
    tpl('uaes-measure-comparison', '测量比较报告（F-PU-166）', '测量比较报告', 'E02', '.xls'),
    tpl('uaes-sample-inspection', '样品检验报告（F-PU-207）', '初始样品检验报告', 'E01', '.xls'),
    tpl('uaes-sample-approval', '样品认可报告', '样品认可报告', null, '.xls'),
    tpl('uaes-material-supplier-list', '材料及分供方清单', '材料产品及分供清单', 'T04'),
    tpl('uaes-traceability', '零件追溯检查单', '零件追溯说明', 'E10')]}
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
