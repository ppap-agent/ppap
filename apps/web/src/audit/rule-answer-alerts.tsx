import {useState} from 'react'
import {request} from '../api'
import {Button} from '../components/ui/button'
import type {components} from '../generated/openapi'
import {message} from '../journey/remote'

export type AnswerAlerts = components['schemas']['RuleAnswerAlerts']

/** For who is told on Feishu when customer answers arrive on this version (#354): turn it off for the version, or back on. */
export function AnswerAlertsSetting({questionnaireId, alerts, onChange}: {questionnaireId: string; alerts: AnswerAlerts; onChange: (next: AnswerAlerts) => void}) {
  const [saving, setSaving] = useState(false), [error, setError] = useState('')
  async function save(muted: boolean) {
    if (saving) return
    setSaving(true); setError('')
    // Setting the same choice again changes nothing, so a failed save can simply be tried again.
    try {onChange(await request<AnswerAlerts>(`/rule-questionnaires/${encodeURIComponent(questionnaireId)}/answer-alerts`, 'PUT', {muted}))}
    catch (cause) {setError(message(cause))}
    finally {setSaving(false)}
  }
  return <div className="grid gap-2 text-sm">
    <p className="text-muted-foreground">{alerts.muted ? '这个版本已不再提醒，答复仍可随时在这里查看。' : '客户答复时，会在飞书提醒你。'}</p>
    <Button type="button" variant="outline" size="sm" className="justify-self-start" disabled={saving} onClick={() => void save(!alerts.muted)}>{alerts.muted ? '恢复提醒' : '这个版本不再提醒'}</Button>
    {error && <p role="alert" className="text-destructive">{error}</p>}
  </div>
}
