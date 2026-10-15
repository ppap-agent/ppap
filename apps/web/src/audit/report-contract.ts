import type {components} from '../generated/openapi'

export type AuditReportReference = components['schemas']['AuditReportReference']
export type AuditReportArtifact = components['schemas']['AuditReportArtifact']
export type AuditReportPackage = components['schemas']['AuditReportPackage']
export type AuditReportAdapter = {
  readPdf: (reference: AuditReportReference, artifact: AuditReportArtifact) => Promise<Blob>
  regenerate: (reference: AuditReportReference, requestId: string) => Promise<components['schemas']['AuditReportUpdate']>
  readRegeneration: (reference: AuditReportReference, requestId: string) => Promise<components['schemas']['AuditReportUpdate']>
  createPackage: (reports: AuditReportReference[], requestId: string) => Promise<AuditReportPackage>
  readPackage: (requestId: string) => Promise<AuditReportPackage>
  readPackageFile: (job: AuditReportPackage) => Promise<Blob>
}

export async function readReportPdf(adapter: Pick<AuditReportAdapter, 'readPdf'>, reference: AuditReportReference, artifact: AuditReportArtifact): Promise<Blob> {
  const blob = await adapter.readPdf(structuredClone(reference), structuredClone(artifact))
  if (artifact.content_type !== 'application/pdf' || blob.type !== 'application/pdf' || blob.size !== artifact.byte_size || await blob.slice(0, 5).text() !== '%PDF-') throw new Error('invalid_pdf_artifact')
  return blob
}
