import type {components} from '../generated/openapi'

export type ShareTarget = components['schemas']['AuditShareTarget']
export type ShareRequest = components['schemas']['AuditReportShareRequest']
export type ReportShare = components['schemas']['AuditReportShare']
export type AuditSharingAdapter = {
  listTargets: (query: string, kind: ShareTarget['kind'], pageToken?: string) => Promise<components['schemas']['AuditShareTargetsPage']>
  send: (request: ShareRequest) => Promise<ReportShare>
  read: (request: components['schemas']['AuditShareReadRequest']) => Promise<ReportShare>
  retryFailed: (requestId: string, target: components['schemas']['AuditShareTargetReference'], attemptId: string) => Promise<ReportShare>
}
export const targetKey = (target: {kind: string; id: string}) => `${target.kind}:${target.id}`
