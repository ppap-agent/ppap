import type {HTMLAttributes} from 'react'
import {executionLabels, resultLabels, type Finding} from '../api'
import {Badge, type BadgeTone} from '../ui/primitives'

function executionTone(execution: string): BadgeTone {
  if (execution === 'failed') return 'danger'
  if (execution === 'completed') return 'success'
  if (execution === 'queued' || execution === 'running') return 'info'
  return 'neutral'
}

function findingTone(status: Finding['status']): BadgeTone {
  if (status === 'PASS') return 'success'
  if (status === 'NG') return 'warning'
  return 'danger'
}

export function ExecutionBadge({execution, ...props}: {execution: string} & HTMLAttributes<HTMLSpanElement>) {
  return <Badge tone={executionTone(execution)} {...props}>{executionLabels[execution] || execution}</Badge>
}

export function FindingBadge({
  status,
  prefix,
  ...props
}: {
  status: Finding['status']
  prefix?: string
} & HTMLAttributes<HTMLSpanElement>) {
  return <Badge tone={findingTone(status)} {...props}>{prefix ? prefix + ' · ' : ''}{resultLabels[status]}</Badge>
}
