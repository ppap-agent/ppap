import {useEffect, useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {durationRange} from './duration-range'

type Estimate = components['schemas']['AuditDurationEstimate']

const readEstimate = () => request<Estimate>('/audit-duration-estimate')

/**
 * P1: 「已选 N 个文件，预计需要约 X～Y 分钟」, from this company's recent standard audits.
 * Shows nothing while loading, on failure, with too few measured audits, or with no files.
 */
export function AuditDurationEstimate({count, read = readEstimate}: {count: number; read?: () => Promise<Estimate>}) {
  const [perFile, setPerFile] = useState<Estimate['per_file_seconds'] | undefined>(undefined)
  useEffect(() => {
    let live = true
    read().then(value => {if (live) setPerFile(value.per_file_seconds)}, () => {if (live) setPerFile(null)})
    return () => {live = false}
  }, [read])
  const range = durationRange(perFile, count)
  if (!range) return null
  return <p className="shrink-0 text-sm whitespace-nowrap text-muted-foreground tabular-nums">已选 {count} 个文件，预计需要{range}</p>
}
