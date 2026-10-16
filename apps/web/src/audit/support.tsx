// Stitch: projects/13261964020862199279/screens/3976a221e25c4216a89f46d42d69e065
import {useEffect, useState} from 'react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import type {AuditSupportAdapter} from './template-contract'

export function AuditSupport({adapter, scenario, traceId, onClose}: {adapter?: AuditSupportAdapter; scenario: string; traceId: string; onClose: () => void}) {
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
        } catch {if (current) setNotice('二维码暂不可用，未替换为其他二维码。')}
      }
      if (current) setLoading(false)
    }).catch(() => {if (current) {setInfo(null); setLoading(false)}})
    return () => {current = false; if (url) URL.revokeObjectURL(url)}
  }, [adapter, retry])
  async function copy(value: string) {try {await navigator.clipboard.writeText(value); setNotice('已复制。')} catch {setNotice('浏览器未允许复制，请选择文字后手动复制。')}}
  const available = info?.state === 'configured' && Boolean(info.config_version) && Boolean(info.phone?.trim() || info.email?.trim() || qr)
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent className="max-h-[88dvh] overflow-y-auto" onEscapeKeyDown={event => {if (zoom) {event.preventDefault(); setZoom(false)}}}><DialogHeader><DialogTitle>联系我们</DialogTitle><DialogDescription>关闭后返回当前页面，不会暂停或重新启动审核。</DialogDescription></DialogHeader>
    {loading ? <p role="status">正在读取服务信息…</p> : !available ? <div className="space-y-3"><p>服务信息暂不可用</p><p className="text-sm text-muted-foreground">未取得有效服务配置，不显示未经确认的电话、邮箱或二维码。</p><Button variant="outline" onClick={() => setRetry(value => value + 1)}>重新读取服务信息</Button></div> : <dl className="space-y-4 text-sm">{info?.phone && <div className="flex flex-wrap items-center justify-between gap-2"><div><dt className="text-muted-foreground">电话</dt><dd className="break-all">{info.phone}</dd></div><Button variant="outline" size="sm" onClick={() => void copy(info.phone!)}>复制电话</Button></div>}{info?.email && <div className="flex flex-wrap items-center justify-between gap-2"><div><dt className="text-muted-foreground">邮箱</dt><dd className="break-all">{/^[^\s@?\r\n]+@[^\s@?\r\n]+\.[^\s@?\r\n]+$/.test(info.email) ? <a className="underline" href={`mailto:${encodeURIComponent(info.email)}`}>{info.email}</a> : info.email}</dd></div><Button variant="outline" size="sm" onClick={() => void copy(info.email!)}>复制邮箱</Button></div>}{info?.service_hours && <div><dt className="text-muted-foreground">服务时间</dt><dd>{info.service_hours}</dd></div>}{qr && <div><dt className="text-muted-foreground">飞书服务二维码</dt><dd><Dialog open={zoom} onOpenChange={setZoom}><DialogTrigger asChild><Button variant="ghost" className="h-auto" aria-label="放大服务二维码"><img className="size-32 object-contain" src={qr} alt="已配置的飞书服务二维码"/></Button></DialogTrigger><DialogContent><DialogHeader><DialogTitle>服务二维码</DialogTitle><DialogDescription>请核对企业提供的服务信息。</DialogDescription></DialogHeader><img className="w-full object-contain" src={qr} alt="放大的飞书服务二维码"/></DialogContent></Dialog></dd></div>}</dl>}
    <div className="space-y-2 border-t pt-4"><p className="text-sm">当前场景：{scenario}</p><Label htmlFor="support-trace">问题追踪编号</Label><Input id="support-trace" readOnly value={traceId}/><p className="text-xs text-muted-foreground">本页参考编号，不代表已创建客服工单或服务端请求。</p><Button variant="outline" size="sm" onClick={() => void copy(`${scenario}\n${traceId}`)}>复制场景与编号</Button></div>
    {notice && <p role="status" className="text-sm">{notice}</p>}<Button variant="outline" className="justify-self-end" onClick={onClose}>关闭</Button>
  </DialogContent></Dialog>
}
