import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/3976a221e25c4216a89f46d42d69e065
import {createContext, useEffect, useState} from 'react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
export type AuditSupportAdapter = {
  read: () => Promise<components['schemas']['AuditSupportInfo']>
  readQr: (configVersion: string) => Promise<Blob>
}

/** Read-only display data captured when support opens, not another operation store. */
export type SupportSnapshot = {scenario: string; traceId?: string; facts?: readonly {label: string; value: string}[]}
export const SupportEntryContext = createContext<((snapshot: SupportSnapshot) => void) | null>(null)
// The support group, hours and response time are the product's own service promise and need no configuration read.
const supportGroup = 'PPAP Audit 用户支持群'

export function AuditSupport({adapter, snapshot, pageReference, returnTargets, onClose}: {adapter?: AuditSupportAdapter; snapshot: SupportSnapshot; pageReference: string; returnTargets: readonly (HTMLElement | null)[]; onClose: () => void}) {
  const {scenario, traceId, facts = []} = snapshot
  const [info, setInfo] = useState<components['schemas']['AuditSupportInfo'] | null>(null), [loading, setLoading] = useState(true), [retry, setRetry] = useState(0)
  const [qr, setQr] = useState(''), [zoom, setZoom] = useState(false), [notice, setNotice] = useState('')
  useEffect(() => {
    let current = true, url = ''
    setLoading(true); setQr(''); setZoom(false); setNotice('')
    void (adapter?.read() || Promise.resolve(null)).then(async result => {
      if (!current) return
      setInfo(result)
      if (result?.state === 'configured' && result.config_version && result.qr_available && adapter) {
        try {
          const blob = await adapter.readQr(result.config_version)
          if (!current) return
          if (!['image/png', 'image/jpeg', 'image/webp'].includes(blob.type) || !blob.size) throw new Error('invalid_support_qr')
          url = URL.createObjectURL(blob); setQr(url)
        } catch {if (current) setNotice('二维码暂不可用，请稍后重试。')}
      }
      if (current) setLoading(false)
    }).catch(() => {if (current) {setInfo(null); setLoading(false)}})
    return () => {current = false; if (url) URL.revokeObjectURL(url)}
  }, [adapter, retry])
  async function copy(value: string) {try {await navigator.clipboard.writeText(value); setNotice('已复制。')} catch {setNotice('浏览器未允许复制，请选择文字后手动复制。')}}
  const copyText = [scenario, `本页参考编号：${pageReference}`, ...(traceId ? [`服务请求编号：${traceId}`] : []), ...facts.map(fact => `${fact.label}：${fact.value}`)].join('\n')
  const available = info?.state === 'configured' && Boolean(info.config_version) && Boolean(info.phone?.trim() || info.email?.trim() || qr)
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent className="max-h-[88dvh] overflow-y-auto" onCloseAutoFocus={event => {event.preventDefault(); [...returnTargets, document.getElementById('audit-main')].find(element => element?.isConnected && element.getClientRects().length)?.focus()}} onEscapeKeyDown={event => {if (zoom) {event.preventDefault(); setZoom(false)}}}><DialogHeader><DialogTitle>联系我们</DialogTitle><DialogDescription className="sr-only">获取帮助与联系方式。</DialogDescription></DialogHeader>
    <div className="space-y-3"><dl className="space-y-3 text-sm">
      <div><dt className="text-muted-foreground">飞书群</dt><dd className="flex flex-wrap items-center justify-between gap-2"><span>{supportGroup}（在飞书搜索群名加入）</span><Button variant="outline" size="sm" onClick={() => void copy(supportGroup)}>复制群名</Button></dd></div>
      <div><dt className="text-muted-foreground">服务时间</dt><dd>{info?.service_hours?.trim() || '工作日 9:00–18:00'}</dd></div>
      <div><dt className="text-muted-foreground">响应时效</dt><dd>工作日 4 小时内响应</dd></div>
    </dl><p className="text-sm text-muted-foreground">如需补充客户模板，请在模板库点击「更多模板，请补充」提交材料。</p></div>
    {loading ? <ReadSkeleton label="正在读取服务信息…" kind={'detail'}/> : !available ? <div className="space-y-3"><p>服务信息暂不可用</p><Button variant="outline" onClick={() => setRetry(value => value + 1)}>重新读取服务信息</Button></div> : <dl className="space-y-4 text-sm">
      {info?.phone && <div><dt className="text-muted-foreground">电话</dt><dd className="flex flex-wrap items-center justify-between gap-2"><span className="break-all">{info.phone}</span><Button variant="outline" size="sm" onClick={() => void copy(info.phone!)}>复制电话</Button></dd></div>}
      {info?.email && <div><dt className="text-muted-foreground">邮箱</dt><dd className="flex flex-wrap items-center justify-between gap-2"><span className="break-all">{/^[^\s@?\r\n]+@[^\s@?\r\n]+\.[^\s@?\r\n]+$/.test(info.email) ? <a className="underline" href={`mailto:${encodeURIComponent(info.email)}`}>{info.email}</a> : info.email}</span><Button variant="outline" size="sm" onClick={() => void copy(info.email!)}>复制邮箱</Button></dd></div>}
      {qr && <div><dt className="text-muted-foreground">飞书服务二维码</dt><dd><Dialog open={zoom} onOpenChange={setZoom}><DialogTrigger asChild><Button variant="ghost" className="h-auto" aria-label="放大服务二维码"><img className="size-32 object-contain" src={qr} alt="已配置的飞书服务二维码"/></Button></DialogTrigger><DialogContent><DialogHeader><DialogTitle>服务二维码</DialogTitle><DialogDescription>请核对企业提供的服务信息。</DialogDescription></DialogHeader><img className="w-full object-contain" src={qr} alt="放大的飞书服务二维码"/></DialogContent></Dialog></dd></div>}
    </dl>}
    <div className="space-y-2 border-t pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm">当前场景：{scenario}</p><Button variant="outline" size="sm" onClick={() => void copy(copyText)}>{facts.length || traceId ? '复制操作信息' : '复制场景与编号'}</Button></div>
      {facts.length > 0 && <section aria-label="操作信息" className="space-y-3"><dl className="space-y-3 text-sm">{facts.map((fact, index) => <div key={index}><dt className="text-muted-foreground">{fact.label}</dt><dd className="whitespace-pre-wrap break-all">{fact.value}</dd></div>)}</dl></section>}
      {traceId && <><Label htmlFor="support-trace">服务请求编号</Label><Input id="support-trace" readOnly value={traceId}/></>}
      {/* The reference is for support staff; it travels with "复制场景与编号" and stays readable to assistive tech. */}<div className="sr-only"><Label htmlFor="support-page-reference">页面参考编号</Label><Input id="support-page-reference" readOnly value={pageReference}/></div>
    </div>
    {notice && <p role="status" className="text-sm">{notice}</p>}<Button variant="outline" className="justify-self-end" onClick={onClose}>关闭</Button>
  </DialogContent></Dialog>
}
