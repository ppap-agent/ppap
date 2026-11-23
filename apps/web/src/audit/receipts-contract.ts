import type {components} from '../generated/openapi'
import {settlementLabels} from './usage-contract.ts'

/** A signing action as its person did it (#361): number, time, action, subject, channel, result; read from the action record. */
export type Receipt = components['schemas']['Receipt']
export type ReceiptList = components['schemas']['ReceiptList']
/** What a task's accepted round charged; `none` means nothing was charged, never an unknown amount. */
export type Charge = components['schemas']['Charge']

/** The points and how they stand, or that nothing was charged. */
export const pointsText = (charge: Charge) => charge.state === 'none' ? '未扣点' : `${charge.points.toLocaleString('zh-CN')} 点 · ${settlementLabels[charge.state]}`

/** The result page's line: 本轮扣点 500 点 · 待扣费, or 本轮未扣点. */
export const chargeText = (charge: Charge) => charge.state === 'none' ? '本轮未扣点' : `本轮扣点 ${pointsText(charge)}`

/** 2026-11-22 14:32:05 in Beijing time, as the receipt PDF writes it. */
export function beijingTime(value: string): string {
  const parts = new Intl.DateTimeFormat('en', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'}).formatToParts(new Date(value))
  const part = (type: string) => parts.find(row => row.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`
}

/** The receipt a signing response names in its `X-PPAP-Action` header; anything else is no receipt. */
export const receiptOf = (header: string | null) => header && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(header) ? header.toLowerCase() : null
