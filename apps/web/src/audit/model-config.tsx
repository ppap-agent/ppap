import {useEffect, useId, useState} from 'react'
import {request} from '../api'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'

export type PersonalModelConfig = {provider: 'deepseek'; model: 'deepseek-flash'; configured: boolean; updated_at: string | null}

/** Only the authenticated user's redacted status is read. The secret is never persisted by the browser. */
export function PersonalModelSettings({onReady}: {onReady?: (ready: boolean) => void}) {
  const id = useId(), [config, setConfig] = useState<PersonalModelConfig | null>(null)
  const [key, setKey] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [confirmRemove, setConfirmRemove] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void request<PersonalModelConfig>('/me/model-config', 'GET', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) {setConfig(value); onReady?.(value.configured); setError('')}
    }).catch(cause => {if (!controller.signal.aborted) {setConfig(null); onReady?.(false); setError(cause instanceof Error ? cause.message : '个人模型配置暂不可读。')}})
    return () => controller.abort()
  }, [revision, onReady])
  async function save(remove = false) {
    if (busy || !remove && !key.trim()) return
    const submitted = key.trim()
    setKey(''); setBusy(true); setError(''); setConfirmRemove(false)
    try {
      const value = await request<PersonalModelConfig>('/me/model-config', remove ? 'DELETE' : 'PUT', remove ? undefined : {api_key: submitted})
      setConfig(value); onReady?.(value.configured)
    } catch {
      // Never repeat an unknown credential mutation or retain the submitted secret.
      setConfig(null); onReady?.(false); setError('保存结果待核对。密钥输入已清除，请读取当前配置后再决定是否修改。')
    } finally {setBusy(false)}
  }
  return <section aria-label="我的 DeepSeek 配置" className="space-y-3 rounded-md border p-3">
    <h3 className="text-sm font-medium">我的 DeepSeek 账户</h3>
    <p role="status" className="text-sm text-muted-foreground">{config ? config.configured ? '已配置，仅供你发起的模型操作使用。' : '尚未配置。普通审核已有的检查不需要生成代码。' : '尚未读到个人配置。'}</p>
    {error && <p role="alert" className="text-sm">{error}</p>}
    <div className="space-y-2"><Label htmlFor={id}>{config?.configured ? '更换 API Key' : 'DeepSeek API Key'}</Label><Input id={id} type="password" autoComplete="off" spellCheck={false} value={key} disabled={busy} onChange={event => setKey(event.target.value)} placeholder="输入你自己的 Key"/></div>
    <p className="text-xs text-muted-foreground">保存到你的私人模型配置，不向同企业成员展示。Key 不会出现在地址或操作记录中。</p>
    <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={busy || !key.trim()} onClick={() => void save()}>保存我的 Key</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => setRevision(value => value + 1)}>读取当前配置</Button>{config?.configured && <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmRemove(true)}>撤销 Key</Button>}</div>
    {confirmRemove && <div className="space-y-2 border-t pt-3"><p className="text-sm">撤销后，新模型操作需要重新配置；已经发生的消费不会撤销。</p><Button variant="destructive" size="sm" disabled={busy} onClick={() => void save(true)}>确认撤销</Button><Button variant="ghost" size="sm" onClick={() => setConfirmRemove(false)}>取消</Button></div>}
  </section>
}
