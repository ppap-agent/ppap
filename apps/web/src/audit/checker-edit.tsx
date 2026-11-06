import {useEffect, useId, useRef, useState} from 'react'
import {Button} from '../components/ui/button'
import {Label} from '../components/ui/label'
import {Textarea} from '../components/ui/textarea'
import {definiteCheckerRejection, type CheckerRevision} from './checker-contract'
import {candidateChanges, checkerEditorDirty, editableRules, nativeCheckerEditAdapter} from './checker-edit-contract'
import {writeAuditAddress} from './routes'

export function CheckerEditor({scope, revision, onSaved, disabled = false, onBlockedChange}: {scope: string; revision: CheckerRevision; onSaved: (value: CheckerRevision) => void; disabled?: boolean; onBlockedChange?: (blocked: boolean) => void}) {
  const id = useId(), locked = useRef(false), live = useRef(true)
  const [rules, setRules] = useState(() => {try {return editableRules(revision.files)} catch {return []}})
  const [templates, setTemplates] = useState(revision.files['templates.json'] || '[]'), [code, setCode] = useState(Object.fromEntries(Object.entries(revision.files).filter(([key]) => ['checker.py', 'resources.py'].includes(key))))
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [pending, setPending] = useState(() => {const p = new URL(location.href).searchParams; return p.get('checkerEditScope') === scope && p.get('checkerEditBase') === revision.id ? p.get('checkerEditOperation') : null})
  useEffect(() => {live.current = true; return () => {live.current = false}}, [])
  const dirty = checkerEditorDirty(revision.files, rules, templates, code)
  useEffect(() => {onBlockedChange?.(dirty || busy || Boolean(pending))}, [dirty, busy, pending, onBlockedChange])
  useEffect(() => () => onBlockedChange?.(false), [onBlockedChange])
  function remember(operation: string | null, expectedOperation?: string) {
    const url = new URL(location.href)
    if (!operation && (url.searchParams.get('checkerEditScope') !== scope || url.searchParams.get('checkerEditBase') !== revision.id || url.searchParams.get('checkerEditOperation') !== expectedOperation)) return
    for (const key of ['checkerEditScope', 'checkerEditBase', 'checkerEditOperation']) url.searchParams.delete(key)
    if (operation) {url.searchParams.set('checkerEditScope', scope); url.searchParams.set('checkerEditBase', revision.id); url.searchParams.set('checkerEditOperation', operation)}
    writeAuditAddress(url)
  }
  async function save() {
    if (locked.current || pending || disabled) return
    let files: Record<string, string>
    try {files = candidateChanges(revision.files, rules, templates, code)} catch (cause) {setError(cause instanceof Error ? cause.message : '修改内容需要核对。'); return}
    locked.current = true; setBusy(true); onBlockedChange?.(true); setError('')
    const operation = crypto.randomUUID(); setPending(operation); remember(operation)
    try {
      const value = await nativeCheckerEditAdapter.save(revision.id, {operation_id: operation, files})
      if (!live.current) return
      if (value.suite_key !== revision.suite_key || value.id === revision.id) throw new Error('保存回执不一致。')
      setPending(null); remember(null, operation); onSaved(value)
    } catch (cause) {
      if (!live.current) return
      if (definiteCheckerRejection(cause)) {setPending(null); remember(null, operation); setError(cause instanceof Error ? cause.message : '修改被拒绝。')}
      else setError('保存结果待核对，请读取原保存操作，不要再次提交。')
    } finally {locked.current = false; setBusy(false)}
  }
  async function recover() {
    if (!pending || locked.current) return
    locked.current = true; setBusy(true)
    try {const value = await nativeCheckerEditAdapter.read(revision.id, pending); if (!live.current) return; if (value.suite_key !== revision.suite_key || value.id === revision.id) throw new Error('保存回执不一致。'); setPending(null); remember(null, pending); onSaved(value)}
    catch {setError('尚未读取到原保存结果，请稍后读取；不会自动重复创建修改。')} finally {locked.current = false; setBusy(false)}
  }
  const readOnly = busy || disabled || Boolean(pending)
  return <details className="space-y-3 text-sm"><summary className="cursor-pointer font-medium">单独修改规则或模板</summary><p>保存为新的待发布修改，不改写原审核。规则、模板或代码修改后，需要重新验证；不会自动调用模型或发布。</p>
    {error && <p role="alert">{error}</p>}
    {!rules.length && <p>当前条款格式暂不支持直接编辑，请联系支持。</p>}
    {rules.map((row, index) => <div key={row.id} className="space-y-2"><Label htmlFor={`${id}-rule-${index}`}>条款 {row.id} 的规则原文</Label><Textarea id={`${id}-rule-${index}`} value={row.text} disabled={readOnly} onChange={event => setRules(values => values.map((item, at) => at === index ? {...item, text: event.target.value} : item))}/></div>)}
    <details className="space-y-2"><summary className="cursor-pointer">修改模板结构映射</summary><Label htmlFor={`${id}-templates`}>模板映射（JSON 对象列表）</Label><Textarea id={`${id}-templates`} value={templates} disabled={readOnly} onChange={event => setTemplates(event.target.value)} className="min-h-32"/><p>只填写有权使用的结构映射；原件不会因编辑模板而被共享。</p></details>
    <details className="space-y-2 hidden sm:block"><summary className="cursor-pointer">高级：修改 Python 检查代码</summary>{Object.entries(code).map(([path, value], index) => <div key={path} className="space-y-2"><Label htmlFor={`${id}-code-${index}`}>{path}</Label><Textarea id={`${id}-code-${index}`} value={value} disabled={readOnly} spellCheck={false} className="min-h-48" onChange={event => setCode(values => ({...values, [path]: event.target.value}))}/></div>)}</details>
    <p className="sm:hidden text-muted-foreground">代码编辑和完整对比请在电脑上进行。</p>
    <Button variant="outline" disabled={readOnly || !rules.length} onClick={() => void save()}>仅保存这些修改</Button>{pending && <Button variant="outline" disabled={busy} onClick={() => void recover()}>读取原保存操作</Button>}
  </details>
}
