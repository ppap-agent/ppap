import type {HTMLAttributes} from 'react'
import {executionLabels, resultLabels, type Finding} from '../api'
import {Badge} from '@/components/ui/badge'

type BadgePassthrough = HTMLAttributes<HTMLSpanElement>

function executionVariant(execution: string): 'destructive' | 'default' | 'outline' | 'secondary' {
  if (execution === 'failed') return 'destructive'
  if (execution === 'completed') return 'default'
  if (execution === 'queued' || execution === 'running') return 'outline'
  return 'secondary'
}

function findingVariant(status: Finding['status']): 'destructive' | 'default' | 'outline' | 'secondary' {
  if (status === 'PASS') return 'default'
  if (status === 'NG' || status === 'UNABLE') return 'outline'
  if (status === 'ERR') return 'destructive'
  return 'secondary'
}

export function ExecutionBadge({execution, ...props}: {execution: string} & BadgePassthrough) {
  return <Badge variant={executionVariant(execution)} {...props}>{executionLabels[execution] || execution}</Badge>
}

export function FindingBadge({
  status,
  prefix,
  ...props
}: {
  status: Finding['status']
  prefix?: string
} & BadgePassthrough) {
  return <Badge variant={findingVariant(status)} {...props}>{prefix ? prefix + ' · ' : ''}{resultLabels[status]}</Badge>
}
