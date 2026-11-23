import type {components} from '../generated/openapi'
import {beijingTime} from './receipts-contract.ts'
import {conclusionLabel} from './status-labels.ts'

/** Where a round's signoff stands (#357): signed (by whom, when, what) or not, and what signing now would freeze. */
export type RoundSignoff = components['schemas']['RoundSignoff']
export type SignoffContent = components['schemas']['SignoffContent']
type SignoffFile = components['schemas']['SignoffFile']
type SignoffEvent = components['schemas']['SignoffEvent']
type Conclusion = SignoffFile['conclusion']

/** Said at the top of every result: the machine's result is a first pass; a person's confirmation and signoff decide. */
export const machineFirstPass = '机器初审结果，以人工确认和签发为准。'
/** Why review editing is closed on a signed round. */
export const signedReviewLock = '撤回签发后才能更正复核'
/** A page that learns a round's signoff changed (a review refused as signed) tells the page holding its status. */
export const signoffChanged = 'ppap-signoff-changed'

/** 2026-11-22 14:32 in Beijing time. */
export const signoffTime = (value: string) => beijingTime(value).slice(0, 16)

/** 已签发 with who and when, or 未签发. */
export function signoffStatus(signoff: Pick<RoundSignoff, 'state' | 'signer' | 'signed_at'>): {label: '已签发' | '未签发'; detail: string} {
  if (signoff.state !== 'signed') return {label: '未签发', detail: ''}
  return {label: '已签发', detail: [signoff.signer?.name || '签发人', signoff.signed_at ? signoffTime(signoff.signed_at) : ''].filter(Boolean).join(' · ')}
}
export const signoffStatusText = (signoff: Pick<RoundSignoff, 'state' | 'signer' | 'signed_at'>) => {
  const {label, detail} = signoffStatus(signoff)
  return detail ? `${label} · ${detail}` : label
}

/** A file's (or the round's) conclusion as the result page shows it, else the machine's; without either it is 待审核, never 符合. */
export const fileConclusion = (file: Pick<SignoffFile, 'conclusion' | 'machine_conclusion'>): NonNullable<Conclusion> | 'UNKNOWN' => file.conclusion ?? file.machine_conclusion ?? 'UNKNOWN'
export const conclusionText = (value: Conclusion | undefined) => conclusionLabel[value ?? 'UNKNOWN'] ?? conclusionLabel.UNKNOWN

/** The head of a signoff confirmation: the round's conclusion, how many files, and how their items were settled; the
 * 待审核 items, which stay pending after signing, are counted apart. */
export function signoffSummary(content: SignoffContent) {
  return {
    conclusion: fileConclusion(content),
    line: `${content.files.length} 份文件 · 人工确认 ${content.reviewed_count} 项 · 按机器结论接受 ${content.accepted_count} 项`,
    pending: content.files.reduce((sum, file) => sum + file.pending_count, 0),
  }
}

/** One line of the signoff record: 签发 · 张三 · 2026-11-22 14:32, a withdrawal with its reason. */
export function signoffEventText(event: SignoffEvent): {action: '签发' | '撤回签发'; who: string; when: string; reason: string} {
  return {action: event.action === 'signed' ? '签发' : '撤回签发', who: event.by || '签发人', when: signoffTime(event.at), reason: event.reason || ''}
}

/** Review editing closes while the round is signed; the signer withdraws it first. */
export const reviewLockOf = (signoff: Pick<RoundSignoff, 'state'> | null | undefined) => signoff?.state === 'signed' ? signedReviewLock : undefined

export const WITHDRAW_REASON_MAX = 500
