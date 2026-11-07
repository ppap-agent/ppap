import {createElement} from 'react'
import type {components} from '../generated/openapi'

type Verification = components['schemas']['AuditCheckerVerification']

/** Always visible beside the verdict, including all-PASS and focused review. */
export function VerificationDisclosure({verification, evidenceUnverified = false}: {verification?: Verification | null; evidenceUnverified?: boolean}) {
  if (!verification && !evidenceUnverified) return null
  const warning = evidenceUnverified || verification?.status === 'unverified' || verification?.samples_not_rerun
  return createElement('section', {role: 'note', 'aria-label': '本轮验证状态', 'data-suite-revision': verification?.suite_revision_id, 'data-verification-status': verification?.status, className: `space-y-1 rounded-md border p-3 text-sm ${warning ? 'border-warning/40 bg-warning/10' : 'text-muted-foreground'}`},
    verification && createElement('p', null, verification.notice),
    evidenceUnverified && createElement('p', null, '部分判定的证据未经验证，请核对原件中的证据和检查范围。'))
}
