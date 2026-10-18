import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Label} from '../components/ui/label'

type Task = components['schemas']['AuditTask']
type Preference = components['schemas']['AuditNotificationPreference']
export type AuditNotificationAdapter = {
  read: (taskId: string) => Promise<Preference>
  save: (taskId: string, enabled: boolean, revision: number) => Promise<Preference>
}
const states: Record<Preference['delivery']['state'], string> = {not_due: '', queued: '等待发送', preparing: '正在准备通知', sending: '正在发送通知', unknown: '发送结果待核对', sent: '通知已发送', failed: '飞书未接收通知', suppressed: '本次完成通知未发送'}
function valid(value: Preference, taskId: string) {
  return value?.task_id === taskId && typeof value.enabled === 'boolean' && Number.isSafeInteger(value.revision) && value.revision >= 0 && typeof value.available === 'boolean' && typeof value.can_change === 'boolean' && value.delivery && Object.hasOwn(states, value.delivery.state) && (value.delivery.reason === null || typeof value.delivery.reason === 'string')
}

export function CompletionNotification({task, adapter, onChange}: {task: Task; adapter: AuditNotificationAdapter; onChange: (preference: Preference) => void}) {
  const [preference, setPreference] = useState<Preference | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const epoch = useRef(0), known = useRef<Preference | null>(null), latest = useRef({task, onChange})
  latest.current = {task, onChange}
  function apply(next: Preference) {
    if (!valid(next, latest.current.task.id)) throw new Error('invalid_notification_snapshot')
    if (known.current && next.revision < known.current.revision) throw new Error('stale_notification_snapshot')
    if (known.current && next.revision === known.current.revision && next.enabled !== known.current.enabled) throw new Error('conflicting_notification_snapshot')
    known.current = next; setPreference(next); setError(''); latest.current.onChange(next)
  }
  async function read() {
    const request = ++epoch.current
    setBusy(true)
    try {const next = await adapter.read(task.id); if (request === epoch.current) apply(next)}
    catch {if (request === epoch.current) setError('通知设置暂时无法确认，请重新读取。')}
    finally {if (request === epoch.current) setBusy(false)}
  }
  useEffect(() => {void read(); return () => {epoch.current += 1}}, [task.id, task.state, task.notification_revision, adapter])
  async function save(enabled: boolean) {
    if (!preference || busy) return
    const request = ++epoch.current, revision = preference.revision
    setBusy(true); setError('')
    try {
      const next = await adapter.save(task.id, enabled, revision)
      if (request !== epoch.current) return
      if (next.revision !== revision + 1 || next.enabled !== enabled) throw new Error('notification_save_unconfirmed')
      apply(next)
    } catch {if (request === epoch.current) setError('设置保存结果待核对，请读取当前设置。')}
    finally {if (request === epoch.current) setBusy(false)}
  }
  const unavailable = preference && !preference.available
  const status = preference?.delivery.reason === 'feishu_connection_unavailable' ? '等待飞书连接恢复' : preference ? states[preference.delivery.state] : ''
  return <div className="grid gap-1 text-sm"><Label><Checkbox checked={preference?.enabled ?? task.notification_enabled ?? false} disabled={busy || Boolean(error) || !preference?.available || !preference.can_change} onCheckedChange={value => void save(value === true)}/>完成后飞书通知</Label>{busy && <span role="status" className="text-muted-foreground">正在核对设置…</span>}{unavailable && <span className="text-muted-foreground">飞书通知暂不可用</span>}{!unavailable && status && <span role="status" className="text-muted-foreground">{status}</span>}{error && <p role="alert">{error}</p>}{(error || preference?.delivery.state === 'unknown' || preference?.delivery.state === 'failed') && <Button size="sm" variant="outline" disabled={busy} onClick={() => void read()}>读取通知设置</Button>}</div>
}
