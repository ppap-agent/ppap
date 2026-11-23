// A signing action leaves the person a receipt (#361): a quiet notice names it and links to it, until they close it or
// open it. A proposal completed from a client shows its receipt in the proposal instead (`IntentBanner`).
import {useEffect, useState} from 'react'
import {receiptIssued, request} from '../api'
import {openInPlace, receiptsAddress} from '../audit/routes'
import type {Receipt} from '../audit/receipts-contract'
import {iconReceipt, iconXmark} from '../components/icons'
import {Button} from '../components/ui/button'
import {Icon} from '../components/ui/icon'

export function ReceiptNotice() {
  const [shown, setShown] = useState<{id: string; number: string | null} | null>(null)
  useEffect(() => {
    let latest = ''
    const issued = (event: Event) => {
      const id = (event as CustomEvent<string>).detail
      latest = id; setShown({id, number: null})
      // The number is read from the receipt itself; until then (or if it cannot be read) the link still opens it.
      void request<Receipt>(`/receipts/${encodeURIComponent(id)}`).then(found => {if (latest === id) setShown({id, number: found.number})}, () => undefined)
    }
    window.addEventListener(receiptIssued, issued)
    return () => window.removeEventListener(receiptIssued, issued)
  }, [])
  if (!shown) return null
  return <div role="status" className="fixed right-4 bottom-4 z-40 flex items-center gap-3 rounded-lg border bg-card py-1.5 pr-1.5 pl-3 text-sm shadow-lg">
    <Icon icon={iconReceipt} aria-hidden="true" className="size-4 text-muted-foreground"/>
    <span>已生成回执{shown.number && <span className="ml-1 tabular-nums">{shown.number}</span>}</span>
    <a href={receiptsAddress(shown.id)} className="text-primary underline-offset-4 hover:underline" onClick={event => {openInPlace(event); setShown(null)}}>查看回执</a>
    <Button variant="quiet" size="icon-xs" aria-label="关闭回执提示" onClick={() => setShown(null)}><Icon icon={iconXmark}/></Button>
  </div>
}
