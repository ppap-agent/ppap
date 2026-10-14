// Stitch reference: projects/13261964020862199279/screens/bdfb4223cfbf4e768ecfc41ecccd3cad.
// Three structures on the intake route: clauses first, source comparison, and one clause at a time.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, Check, FileText, Plus, UploadCloud} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Checkbox} from '../src/components/ui/checkbox'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../src/components/ui/dialog'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle} from '../src/components/ui/sheet'
import {basisConfirmed, basisIssues, confirmBasis, demoBasis, localBasis, resolveBasisConflict, type RequirementBasis, type RequirementClause} from './requirements-state'
import './requirements.css'
import {fileAccept, useLocalUploads} from './local-files'
import {SourceReaderButton} from './source-reader'
import {copyForCase, scopeMatches, scopeLabel, sourceContent, type RequirementCollection, type RequirementScope} from './requirement-library-state'

const layouts = [{id: 'clauses', name: '条款优先'}, {id: 'source', name: '原文对照'}, {id: 'guided', name: '逐项核对'}] as const
type Layout = typeof layouts[number]['id']
const currentLayout = (): Layout => layouts.find(item => item.id === new URLSearchParams(location.search).get('basisLayout'))?.id ?? 'clauses'
const applicability = {pending: '待确认', apply: '适用', exclude: '不适用'} as const

export function RequirementOriginal({basis}: {basis: RequirementBasis}) {
  if (!basis.source.localFile) return null
  const file = {name: basis.source.filename, revision: basis.source.version || '待核对', sourceVersion: 1, localFile: basis.source.localFile}
  return <SourceReaderButton file={file} label="阅读要求原件" input={{receiptId: basis.source.version || '要求草稿', scope: basis.source.title, files: [file], evidence: [], clarification: null}}/>
}

export function BasisSummary({basis}: {basis: RequirementBasis | null | undefined}) {
  if (!basis) return <p className="gf-basis-warning">此历史演示没有已确认的条款快照。不能据此声称要求已完整覆盖。</p>
  return <details className="gf-basis-summary"><summary><BookOpen size={17}/><span>{basis.source.title} · {basis.source.version}{basis.library && basis.library.content !== sourceContent(basis) ? ' · 本次有调整' : ''}</span><span>{basis.clauses.filter(item => item.applicability === 'apply').length} 条适用要求</span></summary>
    <div><RequirementOriginal basis={basis}/>{basis.confirmed && <p>确认人：{basis.confirmed.by} · {new Date(basis.confirmed.at).toLocaleString('zh-CN')}</p>}{basis.clauses.map(clause => <article key={clause.id}><strong>{clause.id} · {clause.title} · {applicability[clause.applicability]}</strong><p>{clause.text}</p><small>{clause.source} · {clause.location}</small>{clause.reason && <p>理由：{clause.reason}</p>}</article>)}{basis.conflict.active && <p>冲突裁定：{basis.conflict.choice === 'internal' ? '内部模板 v1' : basis.conflict.choice === 'customer' ? '客户要求 v2' : '待确认'} · {basis.conflict.reason || '未记录理由'}</p>}</div>
  </details>
}

export function Requirements({basis, scopeKey, scope, collections, context, readonly = false, onChange, onConfirm, onBack, backLabel}: {
  basis: RequirementBasis | null; scopeKey: string; context: string; readonly?: boolean
  scope: RequirementScope; collections: RequirementCollection[]
  backLabel?: string
  onChange: (basis: RequirementBasis) => void; onConfirm: (basis: RequirementBasis) => void; onBack: () => void
}) {
  const [layout, setLayout] = useState(currentLayout)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [candidate, setCandidate] = useState<RequirementBasis | null>(null)
  const [focused, setFocused] = useState(basis?.clauses[0]?.id ?? '')
  const [editing, setEditing] = useState<string | null>(null)
  const [sourceClause, setSourceClause] = useState<string | null>(null)
  const upload = useLocalUploads(([entry]) => {setCandidate(localBasis(entry.file.name, entry.ref)); setSourceOpen(true)}, scopeKey)
  const picker = useRef<HTMLInputElement>(null)
  const current = basis?.clauses.find(item => item.id === focused) ?? basis?.clauses[0]
  const issues = basisIssues(basis)
  const confirmed = basisConfirmed(basis, scopeKey)
  const collection = collections.find(item => item.id === basis?.library?.collectionId || item.versions.some(version => version.id === basis?.source.id))
  const latest = collection?.versions.at(-1)
  const selectedVersion = collection?.versions.find(version => version.id === basis?.library?.versionId || version.id === basis?.source.id)
  if (!readonly && selectedVersion && !scopeMatches(scope, selectedVersion.scope)) issues.push('当前事项不在所选版本的声明范围内，请重新选择适用版本或上传本次要求。')
  if (!readonly && basis?.library && !selectedVersion) issues.push('找不到此来源版本，请重新选择可读取的要求来源。')
  function change(next: RequirementBasis) { if (!readonly) onChange({...next, confirmed: null}) }
  function patch(id: string, patch: Partial<RequirementClause>) {
    if (basis) change({...basis, clauses: basis.clauses.map(item => item.id === id ? {...item, ...patch} : item)})
  }
  function switchLayout(next: Layout) {
    setLayout(next)
    const url = new URL(location.href); url.searchParams.set('basisLayout', next); history.replaceState(null, '', url)
  }
  useEffect(() => {
    const sync = () => setLayout(currentLayout())
    const key = (event: KeyboardEvent) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement)?.closest('input,textarea,select,button,[contenteditable]')) return
      const index = layouts.findIndex(item => item.id === layout)
      switchLayout(layouts[(index + (event.key === 'ArrowRight' ? 1 : 2)) % 3].id)
    }
    window.addEventListener('popstate', sync); window.addEventListener('keydown', key)
    return () => {window.removeEventListener('popstate', sync); window.removeEventListener('keydown', key)}
  }, [layout])
  function addClause() {
    if (!basis) return
    const id = `M-${crypto.randomUUID().slice(0, 6)}`
    change({...basis, source: {...basis.source, state: basis.source.origin === 'local' ? 'manual' : basis.source.state}, clauses: [...basis.clauses, {id, title: '', text: '', original: '', source: basis.source.filename, location: '', applicability: 'pending', reason: '', checked: false}]})
    setFocused(id); setEditing(id)
  }
  function sourceText(clause: RequirementClause) {
    return <div className="gf-basis-original"><p className="gf-basis-source-location"><FileText size={17}/>{clause.source} · {clause.location || '位置待补充'}</p><h3>{clause.title || '待录入条款'}</h3>{clause.original ? <blockquote>{clause.original}</blockquote> : <p>未解析原文。请在自己的原件中核对，再录入条款及页码或单元格位置。</p>}{basis?.source.origin === 'demo' && <small>示例来源片段；不是已上传文件的解析结果。</small>}{clause.original && clause.text !== clause.original && <p className="gf-basis-warning">当前条款与来源片段不同。请核对修改是否改变含义。</p>}</div>
  }
  function clauseRow(clause: RequirementClause) {
    return <article className="gf-basis-clause" key={clause.id}>
      <header><div><span className="gf-basis-code">{clause.id}</span><h2>{clause.title || '待录入条款'}</h2><span className="gf-basis-state">{applicability[clause.applicability]}</span></div><Button variant="ghost" onClick={() => {setFocused(clause.id); setSourceClause(clause.id)}}><BookOpen size={16}/>出处</Button></header>
      {editing === clause.id && !readonly ? <div className="gf-basis-edit"><label>条款名称<Input value={clause.title} onChange={event => patch(clause.id, {title: event.target.value, checked: false})}/></label><label>本次核对的条款<Textarea value={clause.text} onChange={event => patch(clause.id, {text: event.target.value, checked: false})}/></label><label>来源文件<Input value={clause.source} onChange={event => patch(clause.id, {source: event.target.value, checked: false})}/></label><label>页码或单元格位置<Input value={clause.location} placeholder="如第 3 页 §2，或工作表 B4" onChange={event => patch(clause.id, {location: event.target.value, checked: false})}/></label><Button variant="outline" onClick={() => setEditing(null)}>收起编辑</Button></div> : <><p className="gf-basis-body">{clause.text || '尚未录入条款正文。'}</p><div className="gf-basis-location"><span>{clause.source} · {clause.location || '出处待补充'}</span>{!readonly && <Button variant="ghost" onClick={() => setEditing(clause.id)}>编辑条款</Button>}</div></>}
      {!readonly && <div className="gf-basis-clause-controls"><fieldset><legend className="sr-only">{clause.id} 本次适用性</legend>{Object.entries(applicability).map(([value, label]) => <label key={value}><input type="radio" name={`apply-${clause.id}`} value={value} checked={clause.applicability === value} onChange={() => patch(clause.id, {applicability: value as RequirementClause['applicability']})}/>{label}</label>)}</fieldset><label className="gf-basis-checked"><Checkbox aria-label={`${clause.id} 已核对条款与出处`} checked={clause.checked} onCheckedChange={value => patch(clause.id, {checked: value === true})}/>已核对条款与出处</label></div>}
      {clause.applicability === 'exclude' && (readonly ? <p>不适用理由：{clause.reason}</p> : <label className="gf-basis-reason">不适用理由<Textarea value={clause.reason} onChange={event => patch(clause.id, {reason: event.target.value})} placeholder="说明本事项为何不适用，并提供依据"/></label>)}
      {!readonly && clause.id.startsWith('M-') && <Button variant="ghost" onClick={() => {if (basis) change({...basis, clauses: basis.clauses.filter(item => item.id !== clause.id)})}}>移除此未提交条款</Button>}
    </article>
  }
  return <section className="gf-basis-page">
    <Button variant="ghost" className="gf-basis-back" onClick={onBack}><ArrowLeft size={16}/>{backLabel ?? (readonly ? '返回当前工作' : '资料归属')}</Button>
    <header className="gf-basis-heading"><div><h1>{readonly ? '本次审核依据' : '确认审核依据'}</h1><p>{context}</p></div>{confirmed && <span className="gf-basis-confirmed"><Check size={17}/>已确认</span>}</header>
    <div className="gf-basis-source"><div><span>要求来源</span><strong>{basis ? `${basis.source.title} · ${basis.source.version || '版本待确认'}` : '尚未选择'}</strong>{basis && <p>{basis.source.filename}</p>}{basis && <RequirementOriginal basis={basis}/>}</div>{!readonly && <div className="gf-basis-source-actions"><Button variant="outline" disabled={upload.busy} onClick={() => {setCandidate(null); setSourceOpen(true)}}>选择已有版本</Button><Button variant="outline" disabled={upload.busy} onClick={() => picker.current?.click()}><UploadCloud size={16}/>上传要求文件</Button></div>}</div>
    {!readonly && <input ref={picker} type="file" className="sr-only" aria-label="要求文件选择器" accept={fileAccept} disabled={upload.busy} onChange={event => {void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>}
    {upload.busy && <p role="status">正在保存要求原件…</p>}{upload.error && <p role="alert" className="gf-basis-warning">{upload.error}</p>}
    {basis?.library && basis.library.content !== sourceContent(basis) && <p className="gf-basis-warning">本次条款已调整，不等同于来源版本。此调整仅保存在当前事项。</p>}
    {basis && latest && latest.id !== basis.source.id && <p className="gf-basis-warning">已有新版本 {latest.basis.source.version}。本次仍使用 {basis.source.version}，未自动替换。{readonly ? '本次依据已固定；新要求的影响仍需另行评估。' : '如需更换，请选择已有版本，并重新确认每条要求。'}</p>}
    {!basis && <div className="gf-basis-empty"><BookOpen size={28}/><h2>先确定要依据什么判断</h2><p>选择现有版本，或上传客户发来的要求。系统不会把名称相同当成内容相同。</p></div>}
    {basis && <>
      {basis.source.origin === 'local' && <div className="gf-basis-import"><div><strong>{basis.source.state === 'failed' ? '要求提取失败' : basis.source.state === 'manual' ? '人工录入条款' : '文件已收到，尚未解析'}</strong><p>{basis.source.state === 'failed' ? '尚无提取结果。原件记录仍在，可人工录入或更换文件。' : '本原型不解析真实文件。请按原件录入条款，并保留出处。'}</p></div><label>版本<Input disabled={readonly} value={basis.source.version} onChange={event => change({...basis, source: {...basis.source, version: event.target.value}})}/></label>{!readonly && <Button variant="outline" onClick={addClause}><Plus size={16}/>录入条款</Button>}{!readonly && basis.source.state === 'unparsed' && <Button variant="ghost" onClick={() => change({...basis, source: {...basis.source, state: 'failed'}})}>模拟提取失败</Button>}</div>}
      {basis.conflict.active && <section className="gf-basis-conflict"><h2>{readonly ? '公差依据已确认' : '同一尺寸有两个公差定义'}</h2>{readonly ? <><p>本次采用：{basis.conflict.choice === 'internal' ? '内部模板 v1 · ±0.05 mm' : '客户 v2 · ±0.10 mm'}</p><p>依据：{basis.conflict.reason}</p></> : <><p>客户 v2：±0.10 mm；内部模板 v1：±0.05 mm。选择本次依据，不会修改任一原版本。</p><fieldset><legend>本次采用</legend>{([{id: 'pending', label: '待确认'}, {id: 'customer', label: '客户 v2 · ±0.10 mm'}, {id: 'internal', label: '内部 v1 · ±0.05 mm'}] as const).map(item => <label key={item.id}><input type="radio" name="conflict-basis" checked={basis.conflict.choice === item.id} onChange={() => change(resolveBasisConflict(basis, item.id))}/>{item.label}</label>)}</fieldset><label className="gf-basis-reason">裁定依据与理由<Textarea placeholder="如已确认的协议、要求适用范围及确认人" value={basis.conflict.reason} onChange={event => change({...basis, conflict: {...basis.conflict, reason: event.target.value}})}/></label></>}</section>}
      <div className={`gf-basis-layout gf-basis-layout--${layout}`}>
        {layout === 'guided' ? <><nav className="gf-basis-clause-nav" aria-label="条款列表">{basis.clauses.map(clause => <Button variant="ghost" key={clause.id} aria-current={current?.id === clause.id ? 'step' : undefined} onClick={() => setFocused(clause.id)}>{clause.id} · {clause.title || '新条款'}<span>{clause.checked && clause.applicability !== 'pending' ? '已核对' : '待核对'}</span></Button>)}</nav><div>{current && clauseRow(current)}{current && <div className="gf-basis-sequence"><span>第 {basis.clauses.indexOf(current) + 1} / {basis.clauses.length} 条</span><Button variant="outline" disabled={basis.clauses.indexOf(current) === basis.clauses.length - 1} onClick={() => setFocused(basis.clauses[basis.clauses.indexOf(current) + 1].id)}>下一条<ArrowRight size={16}/></Button></div>}</div></> : <><div>{basis.clauses.map(clauseRow)}</div>{layout === 'source' && current && <aside className="gf-basis-reference"><label>对照条款<select value={current.id} onChange={event => setFocused(event.target.value)}>{basis.clauses.map(clause => <option key={clause.id} value={clause.id}>{clause.id} · {clause.title || '新条款'}</option>)}</select></label>{sourceText(current)}</aside>}</>}
      </div>
      {!readonly && basis.source.origin === 'demo' && <Button variant="ghost" className="gf-basis-add" onClick={addClause}><Plus size={16}/>补充要求</Button>}
      <footer className="gf-basis-footer"><div><strong>{confirmed ? '已固定本次审核依据' : `${basis.clauses.filter(clause => clause.checked && clause.applicability !== 'pending' && (clause.applicability !== 'exclude' || clause.reason.trim())).length} / ${basis.clauses.length} 条已核对`}</strong>{issues.length ? <details><summary>还有 {issues.length} 项待处理</summary><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></details> : <p>确认的是检查范围，不是审核结论。</p>}</div>{readonly ? <Button variant="outline" onClick={onBack}>返回当前工作</Button> : <Button className="gf-primary" disabled={issues.length > 0 || upload.busy} onClick={() => {if (!upload.busy) onConfirm(confirmBasis(basis, scopeKey))}}>确认本次要求<ArrowRight size={16}/></Button>}</footer>
    </>}
    <Dialog open={sourceOpen} onOpenChange={setSourceOpen}><DialogContent className="gf-basis-dialog"><DialogHeader><DialogTitle>选择审核要求</DialogTitle><DialogDescription>{basis ? '使用新来源会替换当前未提交的要求草稿。取消则保留原内容。' : '选定来源后，再逐条确认本次适用性。'} 草稿版本不可选用。</DialogDescription></DialogHeader><div className="gf-basis-source-choices">{collections.flatMap(item => [...item.versions].reverse().map(version => <Button variant="outline" key={version.id} disabled={!scopeMatches(scope, version.scope)} onClick={() => setCandidate(copyForCase(item, version))} aria-pressed={candidate?.library?.versionId === version.id}><span><strong>{version.basis.source.title} · {version.basis.source.version}</strong><small>{scopeLabel(version.scope)} · {version.basis.clauses.length} 条{!scopeMatches(scope, version.scope) ? ' · 范围不符' : ''}</small></span></Button>))}{scopeMatches(scope, {customer: '北辰汽车', part: '支架 P-204', revision: 'B'}) && <Button variant="outline" onClick={() => setCandidate(demoBasis(true))} aria-pressed={candidate?.conflict.active === true}><span><strong>冲突示例：客户 v2 与内部模板 v1</strong><small>同一孔径公差不同，需裁定来源</small></span></Button>}</div>{!collections.some(item => item.versions.some(version => scopeMatches(scope, version.scope))) && <p>没有范围一致的已保存版本。可上传本次要求，或回工作台先核对并保存适用版本。</p>}{candidate && <p className="gf-basis-selection">将使用：{candidate.source.title} · {candidate.source.version || '版本待确认'}{candidate.conflict.active ? '，并核对内部模板冲突' : ''}</p>}<Button disabled={!candidate} className="gf-primary" onClick={() => {if (!candidate) return; change(candidate); setFocused(candidate.clauses[0]?.id ?? ''); setEditing(null); setSourceOpen(false)}}>使用此来源</Button></DialogContent></Dialog>
    <Sheet open={sourceClause !== null} onOpenChange={open => !open && setSourceClause(null)}><SheetContent className="gf-basis-sheet"><SheetHeader><SheetTitle>核对出处</SheetTitle><SheetDescription>对照条款与来源，不能把编辑后的说明当作原文。</SheetDescription></SheetHeader>{basis?.clauses.find(clause => clause.id === sourceClause) && sourceText(basis.clauses.find(clause => clause.id === sourceClause)!)}</SheetContent></Sheet>
    {import.meta.env.DEV && <aside className="gf-switcher" aria-label="要求页方案切换"><button aria-label="上一种要求布局" onClick={() => switchLayout(layouts[(layouts.findIndex(item => item.id === layout) + 2) % 3].id)}><ArrowLeft size={16}/></button><span>布局方案　{layouts.findIndex(item => item.id === layout) + 1} / 3 · {layouts.find(item => item.id === layout)?.name}</span><button aria-label="下一种要求布局" onClick={() => switchLayout(layouts[(layouts.findIndex(item => item.id === layout) + 1) % 3].id)}><ArrowRight size={16}/></button></aside>}
  </section>
}
