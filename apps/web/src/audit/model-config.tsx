import {useEffect, useId, useRef, useState} from 'react'
import {Check, RefreshCw} from 'lucide-react'
import {ReadSkeleton} from '../components/read-skeleton'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {modelConfigFailure, readPersonalModelConfig, revokePersonalModelConfig, savePersonalModelConfig, type PersonalModelConfig} from './model-config-contract'

/** Only redacted settings are read back. Secrets stay in memory until submission. */
export function PersonalModelSettings({onConfig, framed = true}: {onConfig?: (config: PersonalModelConfig | null) => void; framed?: boolean}) {
  const id = useId(), lock = useRef(false), [config, setConfig] = useState<PersonalModelConfig | null>(null)
  const [key, setKey] = useState(''), [baseUrl, setBaseUrl] = useState(''), [model, setModel] = useState('')
  const [busy, setBusy] = useState(true), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [confirmRemove, setConfirmRemove] = useState(false), [editing, setEditing] = useState(false)
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
    lock.current = true; setBusy(true); setKey(''); setConfig(null); onConfig?.(null); setConfirmRemove(false); setEditing(false); setRevision(value => value + 1)
  }
  async function save(remove = false) {
    if (lock.current || !config || !remove && (!key.trim() || !baseUrl.trim() || !model.trim())) return
    lock.current = true
    const submitted = key.trim()
    setKey(''); setBusy(true); setError(''); setConfirmRemove(false); onConfig?.(null)
    try {
      const value = await (remove ? revokePersonalModelConfig() : savePersonalModelConfig(submitted, baseUrl, model))
      setConfig(value); setBaseUrl(value.base_url); setModel(value.model); setEditing(false); onConfig?.(value)
    } catch (cause) {
      const known = modelConfigFailure(cause)
      // A known rejection saved nothing, so the form stays open to correct it.
      // Unknown mutations require explicit GET readback, never an automatic retry.
      if (known) setError(`${known} 密钥输入已清除，请修改后重新输入 Key 保存。`)
      else {setConfig(null); setError('保存结果待确认。密钥输入已清除，请读取当前配置后再决定是否修改。')}
    } finally {lock.current = false; setBusy(false)}
  }
  const configured = Boolean(config?.configured)
  // A saved model reads as one summary line; the form appears only to change it or when nothing is saved.
  const form = editing || !configured
  const host = (() => {try {return new URL(config?.base_url || '').host} catch {return config?.base_url || ''}})()
  function cancelEdit() {
    setEditing(false); setKey(''); setBaseUrl(config?.base_url || ''); setModel(config?.model || ''); onConfig?.(config)
  }
  // Inline it is a bordered panel with its own heading; inside a titled dialog the dialog names it.
  return <section aria-label="我的模型配置" className={framed ? 'space-y-4 rounded-md border p-3' : 'space-y-4'}>
    {framed && <h3 className="text-sm font-medium">我的模型</h3>}
    {busy && !config ? <ReadSkeleton label="正在读取当前配置…" kind="inline"/> : !config ? <div className="space-y-2">{!error && <p role="status" className="text-sm text-muted-foreground">尚未读到个人配置。</p>}</div> : !form && <div className="flex items-start justify-between gap-3 rounded-lg border bg-muted/40 p-3">
      <div className="min-w-0 space-y-0.5"><p className="break-all font-medium">{config.model}</p><p className="break-all text-xs text-muted-foreground" title={config.base_url}>{host}</p></div>
      <div className="flex shrink-0 items-center gap-1"><Badge variant="success"><Check aria-hidden="true"/>已配置</Badge><Button variant="ghost" size="icon-sm" aria-label="读取当前配置" title="重新读取" disabled={busy} onClick={read}><RefreshCw/></Button></div>
    </div>}
    {/* In the dialog its description already says when the key is needed. */}{config && !configured && <p role="status" className="text-sm text-muted-foreground">{framed ? '尚未配置。识别文件、深度分析和让 AI 修复检查时需要。' : '尚未配置。'}</p>}
    {error && <div role="alert" className="space-y-2 text-sm"><p>{error}</p><Button variant="outline" size="sm" disabled={busy} onClick={read}>读取当前配置</Button></div>}
    {config && form && <div className="space-y-3">
      <div className="space-y-2"><Label htmlFor={`${id}-url`}>服务地址</Label><Input id={`${id}-url`} type="url" autoComplete="off" spellCheck={false} maxLength={2048} placeholder="https://…/v1" value={baseUrl} disabled={busy} onChange={event => {setBaseUrl(event.target.value); onConfig?.(null)}}/>
        {/* The technical requirements stay one hover away; the visible line says what matters to the person. */}
        <p className="text-xs text-muted-foreground" title="需兼容 Responses API，支持 function tools、图片输入和严格 JSON Schema；内部 HTTPS 地址须先由部署管理员批准。">需兼容 OpenAI Responses 接口，能读图片。</p></div>
      <div className="space-y-2"><Label htmlFor={`${id}-model`}>模型名称</Label><Input id={`${id}-model`} autoComplete="off" spellCheck={false} maxLength={128} placeholder="例如 gpt-5" value={model} disabled={busy} onChange={event => {setModel(event.target.value); onConfig?.(null)}}/></div>
      <div className="space-y-2"><Label htmlFor={`${id}-key`}>API Key</Label><Input id={`${id}-key`} type="password" autoComplete="off" spellCheck={false} maxLength={4096} placeholder="粘贴 Key（每次保存都需重新输入）" value={key} disabled={busy} onChange={event => {setKey(event.target.value); onConfig?.(null)}}/></div>
    </div>}
    {confirmRemove ? <div className="space-y-2 border-t pt-3"><p className="text-sm">撤销后，新的模型操作需要重新配置；已经发生的消费不会撤销。</p><div className="flex flex-wrap gap-2"><Button variant="destructive" size="sm" disabled={busy} onClick={() => void save(true)}>确认撤销</Button><Button variant="ghost" size="sm" onClick={() => setConfirmRemove(false)}>取消</Button></div></div>
      : config && <div className="flex flex-wrap items-center justify-between gap-2">{form ? <>{configured && editing ? <Button variant="ghost" size="sm" disabled={busy} onClick={cancelEdit}>取消</Button> : <span/>}<Button size="sm" disabled={busy || !key.trim() || !baseUrl.trim() || !model.trim()} onClick={() => void save()}>{busy ? '正在保存…' : '保存'}</Button></> : <><Button variant="outline" size="sm" disabled={busy} onClick={() => setEditing(true)}>更换模型或 Key</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmRemove(true)}>撤销 Key</Button></>}</div>}
    <p className="text-xs text-muted-foreground">Key 加密保存，只用于你发起的操作，其他同事看不到。</p>
  </section>
}
