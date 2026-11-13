// Explicit synthetic document content. Callers must establish demo provenance first.
// These are reading fixtures, not parsed uploads or machine-check results.
import type {LocalFileRef} from './local-files'
export type SourceFile = {name: string; revision: string; sourceVersion: number; pending?: boolean; localFile?: LocalFileRef; material?: import('../api').Material; roundId?: string}
export type SourceFocus = {location: string; requestId: number}
export type DemoDocument = SourceFile & {
  origin: string; kind: 'flow' | 'control' | 'dimension'
  process?: string; readable?: boolean; measurements?: number[]
}
export const sourceKey = (file: SourceFile) => JSON.stringify([file.name, file.revision, file.sourceVersion, Boolean(file.pending), ...(file.material ? [file.material.file_version_id] : file.localFile ? [file.localFile.id] : [])])
export const sourceVersionLabel = (file: SourceFile) => file.pending ? '待登记' : file.material ? '本轮固定版本' : `文件 v${file.sourceVersion}`
export function documentRows(doc: DemoDocument) {
  if (doc.kind === 'dimension') return [
    {key: 'B2', cells: ['B2', '图纸修订', doc.revision]},
    ...(doc.measurements ? [
      {key: 'requirement', cells: ['要求', '孔径', 'φ10.00 ± 0.10 mm']},
      ...doc.measurements.map((value, index) => ({key: ['D8', 'E8', 'F8'][index], cells: [['D8', 'E8', 'F8'][index], `样本 ${index + 1}`, `${value.toFixed(2)} mm`]})),
    ] : [{key: 'unavailable', cells: ['—', '实测数据', '此示例未提供可核对实测值']}]),
  ]
  return [
    {key: '16', cells: [doc.kind === 'flow' ? '16' : 'A16:B16', '16', '机加工']},
    {key: '17', cells: [doc.kind === 'flow' ? '17' : 'A17:B17', '17', doc.process ?? '未定义']},
    {key: '18', cells: [doc.kind === 'flow' ? '18' : 'A18:B18', '18', '表面检查']},
    ...(doc.readable === false ? [{key: 'unreadable', cells: ['附加工序', '无法辨认', '此处工序号不可读，未补写猜测值']}] : []),
  ]
}
export function sourceDocumentHTML(doc: DemoDocument) {
  const escape = (value: string) => value.replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]!))
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(doc.name)} · 合成示例</title><style>body{max-width:900px;margin:32px auto;padding:16px;font:16px/1.7 system-ui;color:#1c2635}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ccd3db;padding:12px;text-align:left}small{color:#637084}</style><h1>${escape(doc.name)}</h1><p>合成示例原件 · 不是上传文件或真实检验单据</p><p>支架 P-204 · Rev ${escape(doc.revision)} · ${sourceVersionLabel(doc)}</p><p>来源：${escape(doc.origin)}</p><table><caption>${doc.kind === 'flow' ? '第 3 页 · 工序信息（示例）' : doc.kind === 'control' ? '工作表「控制计划」' : '工作表「尺寸」'}</caption><thead><tr><th>位置</th><th>${doc.kind === 'dimension' ? '项目' : '工序号'}</th><th>${doc.kind === 'dimension' ? '值' : '工序名称'}</th></tr></thead><tbody>${documentRows(doc).map(row=>`<tr>${row.cells.map(cell=>`<td>${escape(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table><p>本文仅包含上述合成事实。没有供应商签署或生产记录；机器定位和人工判断不属于原件内容。</p></html>`
}
