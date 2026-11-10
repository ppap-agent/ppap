import {useEffect, useId, useRef, useState} from 'react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {modelConfigFailure, readPersonalModelConfig, revokePersonalModelConfig, savePersonalModelConfig, type PersonalModelConfig} from './model-config-contract'

/** Only redacted settings are read back. Secrets stay in memory until submission. */
export function PersonalModelSettings({onConfig}: {onConfig?: (config: PersonalModelConfig | null) => void}) {
  const id = useId(), lock = useRef(false), [config, setConfig] = useState<PersonalModelConfig | null>(null)
  const [key, setKey] = useState(''), [baseUrl, setBaseUrl] = useState(''), [model, setModel] = useState('')
  const [busy, setBusy] = useState(true), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [confirmRemove, setConfirmRemove] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    lock.current = true
    void readPersonalModelConfig(controller.signal).then(value => {
      if (!controller.signal.aborted) {setConfig(value); setBaseUrl(value.base_url); setModel(value.model); onConfig?.(value); setError('')}
    }).catch(() => {if (!controller.signal.aborted) {setConfig(null); onConfig?.(null); setError('个人模型配置暂不可读，请稍后读取当前配置。')}})
      .finally(() => {if (!controller.signal.aborted) {lock.current = false; setBusy(false)}})
    return () => controller.abort()
  }, [revision, onConfig])
  function read() {
    if (lock.current) return
    lock.current = true; setBusy(true); setKey(''); setConfig(null); onConfig?.(null); setConfirmRemove(false); setRevision(value => value + 1)
  }
  async function save(remove = false) {
    if (lock.current || !config || !remove && (!key.trim() || !baseUrl.trim() || !model.trim())) return
    lock.current = true
    const submitted = key.trim()
    setKey(''); setBusy(true); setError(''); setConfirmRemove(false); onConfig?.(null)
    try {
      const value = await (remove ? revokePersonalModelConfig() : savePersonalModelConfig(submitted, baseUrl, model))
      setConfig(value); setBaseUrl(value.base_url); setModel(value.model); onConfig?.(value)
    } catch (cause) {
      // Unknown mutations require explicit GET readback, never an automatic retry.
      setConfig(null); onConfig?.(null)
      const known = modelConfigFailure(cause)
      setError(known ? `${known} 密钥输入已清除，请读取当前配置后再修改。` : '保存结果待确认。密钥输入已清除，请读取当前配置后再决定是否修改。')
    } finally {lock.current = false; setBusy(false)}
  }
  return <section aria-label="我的模型配置" className="space-y-3 rounded-md border p-3">
    <h3 className="text-sm font-medium">我的模型账户</h3>
    <p role="status" className="text-sm text-muted-foreground">{config ? config.configured ? '已配置，仅供你发起的模型操作使用。' : '尚未配置。普通审核已有的检查不需要生成代码。' : '尚未读到个人配置。'}</p>
    {config?.configured && <p className="break-all text-sm">已保存地址：{config.base_url}；模型：{config.model}</p>}
    {error && <p role="alert" className="text-sm">{error}</p>}
    <div className="space-y-2"><Label htmlFor={`${id}-url`}>Base URL</Label><Input id={`${id}-url`} type="url" autoComplete="off" spellCheck={false} maxLength={2048} value={baseUrl} disabled={busy} onChange={event => {setBaseUrl(event.target.value); onConfig?.(null)}}/></div>
    <div className="space-y-2"><Label htmlFor={`${id}-model`}>模型名称</Label><Input id={`${id}-model`} autoComplete="off" spellCheck={false} maxLength={128} value={model} disabled={busy} onChange={event => {setModel(event.target.value); onConfig?.(null)}}/></div>
    <div className="space-y-2"><Label htmlFor={`${id}-key`}>API Key</Label><Input id={`${id}-key`} type="password" autoComplete="off" spellCheck={false} maxLength={4096} value={key} disabled={busy} onChange={event => {setKey(event.target.value); onConfig?.(null)}} placeholder="每次保存均须重新输入 Key"/></div>
    {/* The technical requirements stay one hover away; the visible line says what matters to the person. */}
    <p className="text-xs text-muted-foreground" title="需兼容 Responses API，支持 function tools、图片输入和严格 JSON Schema；内部 HTTPS 地址须先由部署管理员批准。">支持兼容 OpenAI Responses 接口、能读图片的模型。</p>
    <p className="text-xs text-muted-foreground">Key 加密保存，只用于你发起的操作，其他同事看不到。</p>
    <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={busy || !config || !key.trim() || !baseUrl.trim() || !model.trim()} onClick={() => void save()}>保存模型配置</Button><Button variant="ghost" size="sm" disabled={busy} onClick={read}>读取当前配置</Button>{config?.configured && <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmRemove(true)}>撤销 Key</Button>}</div>
    {confirmRemove && <div className="space-y-2 border-t pt-3"><p className="text-sm">撤销后，新模型操作需要重新配置；已经发生的消费不会撤销。</p><Button variant="destructive" size="sm" disabled={busy} onClick={() => void save(true)}>确认撤销</Button><Button variant="ghost" size="sm" onClick={() => setConfirmRemove(false)}>取消</Button></div>}
  </section>
}
