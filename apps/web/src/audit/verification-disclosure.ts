import {createElement} from 'react'
import type {components} from '../generated/openapi'

type Verification = components['schemas']['AuditCheckerVerification']

/** Always visible beside the verdict, including all-PASS and focused review: one short line, with the frozen notice and exact version a click away. */
export function VerificationDisclosure({verification, evidenceUnverified = false}: {verification?: Verification | null; evidenceUnverified?: boolean}) {
  if (!verification && !evidenceUnverified) return null
  const warning = evidenceUnverified || verification?.status === 'unverified' || verification?.samples_not_rerun
  const version = verification?.suite_revision_number != null ? `检查版本第 ${verification.suite_revision_number} 版` : '检查版本'
  const summary = [
    verification?.status === 'unverified' && `${version}未经样本验证`,
    verification?.samples_not_rerun && '官方已更新，尚未重新验证',
    verification?.status === 'verified' && !verification.samples_not_rerun && `${version}已通过样本验证`,
    evidenceUnverified && '部分证据未验证',
  ].filter(Boolean).join(' · ') + (warning ? '，结论请对照原件核对' : '')
  const detail = [verification?.notice, evidenceUnverified && '部分判定的证据未经验证，请核对原件中的证据和检查范围。'].filter(Boolean).join(' ')
  // One short line; the frozen notice with its exact version stays as the hover text and for screen readers.
  return createElement('p', {role: 'note', 'aria-label': '本轮验证状态', title: detail, 'data-suite-revision': verification?.suite_revision_id, 'data-verification-status': verification?.status, className: 'flex items-center gap-2 text-xs text-muted-foreground'},
    createElement('span', {'aria-hidden': true, className: `size-1.5 shrink-0 rounded-full ${warning ? 'bg-warning' : 'bg-success'}`}),
    summary,
    createElement('span', {className: 'sr-only'}, detail))
}
