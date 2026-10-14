import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch reference: projects/13261964020862199279/screens/bdfb4223cfbf4e768ecfc41ecccd3cad.
// Three structures on the intake route: clauses first, source comparison, and one clause at a time.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, Check, FileText, Plus, UploadCloud} from 'lucide-react'
import {Button} from '../components/ui/button'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Checkbox} from '../components/ui/checkbox'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle} from '../components/ui/sheet'
import {basisConfirmed, basisIssues, confirmBasis, demoBasis, localBasis, resolveBasisConflict, type RequirementBasis, type RequirementClause} from './requirements-state'
import {fileAccept, useLocalUploads} from './local-files'
import {SourceReaderButton} from './source-reader'
import {copyForCase, scopeMatches, scopeLabel, sourceContent, type RequirementCollection, type RequirementScope} from './requirement-library-state'
import {receiveRequirement, RuleExtraction} from './rule-extraction'
import {publishBasis} from './product'
import {isPreview, useAccess} from './access'
import {message} from './remote'

const layouts = [{id: 'clauses', name: '条款优先'}, {id: 'source', name: '原文对照'}, {id: 'guided', name: '逐项核对'}] as const
type Layout = typeof layouts[number]['id']
const currentLayout = (): Layout => layouts.find(item => item.id === new URLSearchParams(location.search).get('basisLayout'))?.id ?? 'clauses'
const applicability = {pending: '待确认', apply: '适用', exclude: '不适用'} as const

export function RequirementOriginal({basis}: {basis: RequirementBasis}) {
  if (!basis.source.localFile && !basis.server) return null
  const file = {name: basis.source.filename, revision: basis.source.version || '待核对', sourceVersion: 1, localFile: basis.source.localFile, material: basis.server?.material}
  return <SourceReaderButton file={file} label="阅读要求原件" input={{receiptId: basis.source.version || '要求草稿', scope: basis.source.title, files: [file], evidence: [], clarification: null}}/>
}

export function BasisSummary({basis}: {basis: RequirementBasis | null | undefined}) {
  if (!basis) return <p className={"text-foreground leading-relaxed"}>此历史演示没有已确认的条款快照。不能据此声称要求已完整覆盖。</p>
  return <Collapsible className="my-4 min-w-0 max-w-full"><CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="max-w-full"><BookOpen size={17}/><span className="min-w-0 truncate" title={`${basis.source.title} · ${basis.source.version}`}>{basis.source.title} · {basis.source.version}{basis.library && basis.library.content !== sourceContent(basis) ? ' · 本次有调整' : ''}</span><span className="shrink-0">{basis.clauses.filter(item => item.applicability === 'apply').length} 条适用要求</span></Button></CollapsibleTrigger><CollapsibleContent>
    <div><RequirementOriginal basis={basis}/>{basis.confirmed && <p>确认人：{basis.confirmed.by} · {new Date(basis.confirmed.at).toLocaleString('zh-CN')}</p>}{basis.clauses.map(clause => <article key={clause.id}><strong>{clause.id} · {clause.title} · {applicability[clause.applicability]}</strong><p>{clause.text}</p><small>{clause.source} · {clause.location}</small>{clause.reason && <p>理由：{clause.reason}</p>}</article>)}{basis.conflict.active && <p>冲突裁定：{basis.conflict.choice === 'internal' ? '内部模板 v1' : basis.conflict.choice === 'customer' ? '客户要求 v2' : '待确认'} · {basis.conflict.reason || '未记录理由'}</p>}</div>
  </CollapsibleContent></Collapsible>
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
  const access = useAccess(), [publishing, setPublishing] = useState(false), [remoteError, setRemoteError] = useState('')
  const upload = useLocalUploads(async ([entry]) => {const next = await receiveRequirement(entry, onChange); change(next); setFocused('')}, scopeKey)
  const picker = useRef<HTMLInputElement>(null)
  const current = basis?.clauses.find(item => item.id === focused) ?? basis?.clauses[0]
  const issues = basisIssues(basis)
  const confirmed = basisConfirmed(basis, scopeKey)
  const collection = collections.find(item => item.id === basis?.library?.collectionId || item.versions.some(version => version.id === basis?.source.id || version.id === basis?.library?.versionId))
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
    const key = (event: KeyboardEvent) => {if (!isPreview() || !import.meta.env.DEV) return;
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
  async function confirm() {
    if (!basis || publishing) return
    setPublishing(true); setRemoteError('')
    try {
      const published = isPreview() ? basis : await publishBasis(basis)
      onConfirm(confirmBasis(published, scopeKey, access?.principal?.display_name))
    } catch (cause) {setRemoteError(message(cause))}
    finally {setPublishing(false)}
  }
  function sourceText(clause: RequirementClause) {
    return <div className={"break-all py-5.5 px-0 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:my-2.5 [&_h3]:mx-0 [&_blockquote]:my-4 [&_blockquote]:mx-0 [&_blockquote]:pl-4 [&_blockquote]:border-l [&_blockquote]:leading-relaxed [&_small]:text-muted-foreground"}><p className={"flex gap-2 items-start text-muted-foreground text-sm [&_svg]:shrink-0 [&_svg]:mt-0.5"}><FileText size={17}/>{clause.source} · {clause.location || '位置待补充'}</p><h3>{clause.title || '待录入条款'}</h3>{clause.original ? <blockquote>{clause.original}</blockquote> : <p>未解析原文。请在自己的原件中核对，再录入条款及页码或单元格位置。</p>}{basis?.source.origin === 'demo' && <small>示例来源片段；不是已上传文件的解析结果。</small>}{clause.original && clause.text !== clause.original && <p className={"text-foreground leading-relaxed"}>当前条款与来源片段不同。请核对修改是否改变含义。</p>}</div>
  }
  function clauseRow(clause: RequirementClause) {
    return <article className={"min-w-0 py-6 px-0 border-b [&_header]:flex [&_header]:items-center [&_header]:gap-2.5 [&_header_>_div]:flex [&_header_>_div]:items-center [&_header_>_div]:gap-2.5 [&_header]:justify-between [&_header_>_div]:flex-wrap [&_header_>_div]:min-w-0 [&_h2]:m-0 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:break-all [&_fieldset]:flex [&_fieldset]:flex-wrap [&_fieldset]:gap-y-2 [&_fieldset]:gap-x-5 [&_fieldset]:p-0 [&_fieldset]:border-0 [&_fieldset_label]:inline-flex [&_fieldset_label]:items-center [&_fieldset_label]:min-h-9 [&_fieldset_label]:gap-2 [&_fieldset_label]:cursor-pointer max-md:[&_fieldset_label]:min-h-11"} key={clause.id}>
      <header><div><span className={"text-muted-foreground text-sm tabular-nums"}>{clause.id}</span><h2>{clause.title || '待录入条款'}</h2><span className={"text-muted-foreground text-sm"}>{applicability[clause.applicability]}</span></div><Button variant="ghost" onClick={() => {setFocused(clause.id); setSourceClause(clause.id)}}><BookOpen size={16}/>出处</Button></header>
      {editing === clause.id && !readonly ? <div className={"grid gap-3 mt-3.5 [&_label]:grid [&_label]:gap-1.5 [&_label]:text-muted-foreground [&_label]:text-sm"}><label>条款名称<Input value={clause.title} onChange={event => patch(clause.id, {title: event.target.value, checked: false})}/></label><label>本次核对的条款<Textarea value={clause.text} onChange={event => patch(clause.id, {text: event.target.value, checked: false})}/></label><label>来源文件<Input value={clause.source} onChange={event => patch(clause.id, {source: event.target.value, checked: false})}/></label><label>页码或单元格位置<Input value={clause.location} placeholder="如第 3 页 §2，或工作表 B4" onChange={event => patch(clause.id, {location: event.target.value, checked: false})}/></label><Button variant="outline" onClick={() => setEditing(null)}>收起编辑</Button></div> : <><p className={"mt-3 mr-0 mb-2 ml-0 leading-relaxed break-all"}>{clause.text || '尚未录入条款正文。'}</p><div className={"flex gap-2.5 items-center justify-between text-muted-foreground text-sm break-all"}><span>{clause.source} · {clause.location || '出处待补充'}</span>{!readonly && <Button variant="ghost" onClick={() => setEditing(clause.id)}>编辑条款</Button>}</div></>}
      {!readonly && <div className={"flex flex-wrap items-center gap-y-3.5 gap-x-6 mt-4"}><RadioGroup value={clause.applicability} onValueChange={value => patch(clause.id, {applicability: value as RequirementClause['applicability']})} aria-label={`${clause.id} 本次适用性`} className="flex flex-wrap"><div className="flex flex-wrap gap-4">{Object.entries(applicability).map(([value, label]) => <label className="flex items-center gap-2 text-sm" key={value}><RadioGroupItem value={value}/>{label}</label>)}</div></RadioGroup><label className={"inline-flex items-center min-h-9 gap-2 cursor-pointer max-md:min-h-11"}><Checkbox aria-label={`${clause.id} 已核对条款与出处`} checked={clause.checked} onCheckedChange={value => patch(clause.id, {checked: value === true})}/>已核对条款与出处</label></div>}
      {clause.applicability === 'exclude' && (readonly ? <p>不适用理由：{clause.reason}</p> : <label className={"grid gap-1.5 text-muted-foreground text-sm mt-3"}>不适用理由<Textarea value={clause.reason} onChange={event => patch(clause.id, {reason: event.target.value})} placeholder="说明本事项为何不适用，并提供依据"/></label>)}
      {!readonly && clause.id.startsWith('M-') && <Button variant="ghost" onClick={() => {if (basis) change({...basis, clauses: basis.clauses.filter(item => item.id !== clause.id)})}}>移除此未提交条款</Button>}
    </article>
  }
  return <section className={"min-w-0 pb-14 text-foreground text-sm leading-relaxed"}>
    <Button variant="ghost" className={""} onClick={onBack}><ArrowLeft size={16}/>{backLabel ?? (readonly ? '返回当前工作' : '资料归属')}</Button>
    <header className={"flex justify-between gap-4.5 items-start mt-4.5 mr-0 mb-6.5 ml-0 [&_h1]:m-0 [&_h1]:text-3xl [&_h1]:font-bold [&_h1]:tracking-tight [&_p]:text-muted-foreground [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 max-md:[&_h1]:text-2xl"}><div><h1>{readonly ? '本次审核依据' : '确认审核依据'}</h1><p>{context}</p></div>{confirmed && <span className={"inline-flex gap-1.5 items-center text-foreground whitespace-nowrap"}><Check size={17}/>已确认</span>}</header>
    <div className={"flex gap-5 items-center justify-between py-4.5 px-0 border-y [&_>_div:first-child]:min-w-0 [&_>_div:first-child]:grid [&_>_div:first-child]:gap-1 [&_>_div:first-child]:break-all [&_span]:m-0 [&_span]:text-muted-foreground [&_span]:text-sm [&_p]:m-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_strong]:text-base max-xl:items-start max-xl:flex-col"}><div><span>要求来源</span><strong>{basis ? `${basis.source.title} · ${basis.source.version || '版本待确认'}` : '尚未选择'}</strong>{basis && <p>{basis.source.filename}</p>}{basis && <RequirementOriginal basis={basis}/>}</div>{!readonly && <div className={"flex flex-wrap gap-2 shrink-0"}><Button variant="outline" disabled={upload.busy} onClick={() => {setCandidate(null); setSourceOpen(true)}}>选择已有版本</Button><Button variant="outline" disabled={upload.busy} onClick={() => picker.current?.click()}><UploadCloud size={16}/>上传要求文件</Button></div>}</div>
    {!readonly && <input ref={picker} type="file" className={"sr-only"} aria-label="要求文件选择器" accept={fileAccept} disabled={upload.busy} onChange={event => {void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>}
    {upload.busy && <p role="status">正在保存要求原件…</p>}{upload.error && <p role="alert" className={"text-foreground leading-relaxed"}>{upload.error}</p>}
    {basis?.library && basis.library.content !== sourceContent(basis) && <p className={"text-foreground leading-relaxed"}>本次条款已调整，不等同于来源版本。此调整仅保存在当前事项。</p>}
    {basis && latest && latest.id !== basis.source.id && <p className={"text-foreground leading-relaxed"}>已有新版本 {latest.basis.source.version}。本次仍使用 {basis.source.version}，未自动替换。{readonly ? '本次依据已固定；新要求的影响仍需另行评估。' : '如需更换，请选择已有版本，并重新确认每条要求。'}</p>}
    {!basis && <div className={"py-15 px-0 max-w-140 [&_svg]:text-muted-foreground [&_h2]:mt-4 [&_h2]:mr-0 [&_h2]:mb-2 [&_h2]:ml-0 [&_h2]:text-xl [&_h2]:font-semibold [&_p]:text-muted-foreground"}><BookOpen size={28}/><h2>先确定要依据什么判断</h2><p>选择现有版本，或上传客户发来的要求。系统不会把名称相同当成内容相同。</p></div>}
    {basis && <>
      <RuleExtraction basis={basis} readonly={readonly} onChange={change}/>{remoteError && <p role="alert" className={"text-sm text-destructive"}>{remoteError}</p>}
      {basis.source.origin === 'local' && !basis.server && <div className={"[&_label]:grid [&_label]:gap-1.5 [&_label]:text-muted-foreground [&_label]:text-sm flex flex-wrap gap-4 items-end py-5.5 px-0 border-b [&_>_div]:flex-[1_1_100%] [&_p]:text-muted-foreground [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_label]:w-55 [&_label]:max-w-full"}><div><strong>{basis.source.state === 'failed' ? '要求提取失败' : basis.source.state === 'manual' ? '人工录入条款' : '文件已收到，尚未解析'}</strong><p>{basis.source.state === 'failed' ? '尚无提取结果。原件记录仍在，可人工录入或更换文件。' : '本原型不解析真实文件。请按原件录入条款，并保留出处。'}</p></div><label>版本<Input disabled={readonly} value={basis.source.version} onChange={event => change({...basis, source: {...basis.source, version: event.target.value}})}/></label>{!readonly && <Button variant="outline" onClick={addClause}><Plus size={16}/>录入条款</Button>}{!readonly && basis.source.state === 'unparsed' && <Button variant="ghost" onClick={() => change({...basis, source: {...basis.source, state: 'failed'}})}>模拟提取失败</Button>}</div>}
      {basis.conflict.active && <section className={"[&_fieldset]:flex [&_fieldset]:flex-wrap [&_fieldset]:gap-y-2 [&_fieldset]:gap-x-5 [&_fieldset]:p-0 [&_fieldset]:border-0 [&_fieldset_label]:inline-flex [&_fieldset_label]:items-center [&_fieldset_label]:min-h-9 [&_fieldset_label]:gap-2 [&_fieldset_label]:cursor-pointer p-5 my-6 mx-0 border-l bg-muted [&_h2]:text-lg [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1.5 [&_h2]:ml-0 [&_h2]:font-semibold [&_p]:mt-0 [&_p]:mr-0 [&_p]:mb-3 [&_p]:ml-0 [&_legend]:mb-2 [&_legend]:font-semibold max-md:p-4 max-md:[&_fieldset_label]:min-h-11"}><h2>{readonly ? '公差依据已确认' : '同一尺寸有两个公差定义'}</h2>{readonly ? <><p>本次采用：{basis.conflict.choice === 'internal' ? '内部模板 v1 · ±0.05 mm' : '客户 v2 · ±0.10 mm'}</p><p>依据：{basis.conflict.reason}</p></> : <><p>客户 v2：±0.10 mm；内部模板 v1：±0.05 mm。选择本次依据，不会修改任一原版本。</p><RadioGroup value={basis.conflict.choice} onValueChange={value => change(resolveBasisConflict(basis, value as RequirementBasis['conflict']['choice']))} aria-label="本次采用的要求"><div className="flex flex-wrap gap-4">{([{id: 'pending', label: '待确认'}, {id: 'customer', label: '客户 v2 · ±0.10 mm'}, {id: 'internal', label: '内部 v1 · ±0.05 mm'}] as const).map(item => <label key={item.id}><RadioGroupItem value={item.id}/>{item.label}</label>)}</div></RadioGroup><label className={"grid gap-1.5 text-muted-foreground text-sm mt-3"}>裁定依据与理由<Textarea placeholder="如已确认的协议、要求适用范围及确认人" value={basis.conflict.reason} onChange={event => change({...basis, conflict: {...basis.conflict, reason: event.target.value}})}/></label></>}</section>}
      <div className={["grid min-w-0 mt-3.5",({"source":"grid-cols-[minmax(0,_1.25fr)_minmax(260px,_.8fr)] gap-7.5 items-start max-xl:grid-cols-[minmax(0,_1fr)]","guided":"grid-cols-[210px_minmax(0,_1fr)] gap-7 items-start max-md:grid-cols-[minmax(0,_1fr)] max-md:gap-2"} as Record<string,string>)[String(layout)] ?? "",""].filter(Boolean).join("")}>
        {layout === 'guided' ? <><nav className={"grid gap-1 pt-3.5 max-md:flex max-md:overflow-x-auto"} aria-label="条款列表">{basis.clauses.map(clause => <Button variant="ghost" key={clause.id} aria-current={current?.id === clause.id ? 'step' : undefined} onClick={() => setFocused(clause.id)}>{clause.id} · {clause.title || '新条款'}<span>{clause.checked && clause.applicability !== 'pending' ? '已核对' : '待核对'}</span></Button>)}</nav><div>{current && clauseRow(current)}{current && <div className={"flex justify-between items-center mt-4 text-muted-foreground"}><span>第 {basis.clauses.indexOf(current) + 1} / {basis.clauses.length} 条</span><Button variant="outline" disabled={basis.clauses.indexOf(current) === basis.clauses.length - 1} onClick={() => setFocused(basis.clauses[basis.clauses.indexOf(current) + 1].id)}>下一条<ArrowRight size={16}/></Button></div>}</div></> : <><div>{basis.clauses.map(clauseRow)}</div>{layout === 'source' && current && <aside className={"sticky top-5 border-l pt-5 pr-0 pb-5 pl-6 [&_label]:grid [&_label]:gap-2 [&_label]:text-sm [&_label]:text-muted-foreground max-xl:static max-xl:border-l-0 max-xl:border-t max-xl:py-5 max-xl:px-0"}><label>对照条款<NativeSelect value={current.id} onChange={event => setFocused(event.target.value)}>{basis.clauses.map(clause => <NativeSelectOption key={clause.id} value={clause.id}>{clause.id} · {clause.title || '新条款'}</NativeSelectOption>)}</NativeSelect></label>{sourceText(current)}</aside>}</>}
      </div>
      {!readonly && (basis.source.origin === 'demo' || basis.server) && <Button variant="ghost" className={""} onClick={addClause}><Plus size={16}/>补充要求</Button>}
      <footer className={"flex items-start justify-between gap-5 py-6 px-0 [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0 [&_p]:text-muted-foreground [&_p]:text-sm [&_ul]:pl-4.5 [&_ul]:mt-2.5 [&_ul]:text-sm max-md:flex-col"}><div><strong>{confirmed ? '已固定本次审核依据' : `${basis.clauses.filter(clause => clause.checked && clause.applicability !== 'pending' && (clause.applicability !== 'exclude' || clause.reason.trim())).length} / ${basis.clauses.length} 条已核对`}</strong>{issues.length ? <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">还有 {issues.length} 项待处理</Button></CollapsibleTrigger><CollapsibleContent><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></CollapsibleContent></Collapsible> : <p>确认的是检查范围，不是审核结论。</p>}</div>{readonly ? <Button variant="outline" onClick={onBack}>返回当前工作</Button> : <Button className={""} disabled={issues.length > 0 || upload.busy || publishing} onClick={() => void confirm()}>确认本次要求<ArrowRight size={16}/></Button>}</footer>
    </>}
    <Dialog open={sourceOpen} onOpenChange={setSourceOpen}><DialogContent className={"max-h-[calc(100dvh_-_40px)] overflow-y-auto w-[min(580px,_calc(100vw_-_32px))]"}><DialogHeader><DialogTitle>选择审核要求</DialogTitle><DialogDescription>{basis ? '使用新来源会替换当前未提交的要求草稿。取消则保留原内容。' : '选定来源后，再逐条确认本次适用性。'} 草稿版本不可选用。</DialogDescription></DialogHeader><div className={"grid gap-2.5 [&_span]:grid [&_span]:gap-1.5 [&_small]:text-foreground [&_small]:font-normal"}>{collections.flatMap(item => [...item.versions].reverse().map(version => <Button variant="outline" key={version.id} disabled={!scopeMatches(scope, version.scope)} onClick={() => setCandidate(copyForCase(item, version))} aria-pressed={candidate?.library?.versionId === version.id}><span><strong>{version.basis.source.title} · {version.basis.source.version}</strong><small>{scopeLabel(version.scope)} · {version.basis.clauses.length} 条{!scopeMatches(scope, version.scope) ? ' · 范围不符' : ''}</small></span></Button>))}{scopeMatches(scope, {customer: '北辰汽车', part: '支架 P-204', revision: 'B'}) && isPreview() && <Button variant="outline" onClick={() => setCandidate(demoBasis(true))} aria-pressed={candidate?.conflict.active === true}><span><strong>冲突示例：客户 v2 与内部模板 v1</strong><small>同一孔径公差不同，需裁定来源</small></span></Button>}</div>{!collections.some(item => item.versions.some(version => scopeMatches(scope, version.scope))) && <p>没有范围一致的已保存版本。可上传本次要求，或回工作台先核对并保存适用版本。</p>}{candidate && <p className={"text-sm break-all"}>将使用：{candidate.source.title} · {candidate.source.version || '版本待确认'}{candidate.conflict.active ? '，并核对内部模板冲突' : ''}</p>}<Button disabled={!candidate} className={""} onClick={() => {if (!candidate) return; change(candidate); setFocused(candidate.clauses[0]?.id ?? ''); setEditing(null); setSourceOpen(false)}}>使用此来源</Button></DialogContent></Dialog>
    <Sheet open={sourceClause !== null} onOpenChange={open => !open && setSourceClause(null)}><SheetContent className={"w-[min(500px,_92vw)] max-w-125 overflow-y-auto"}><SheetHeader><SheetTitle>核对出处</SheetTitle><SheetDescription>对照条款与来源，不能把编辑后的说明当作原文。</SheetDescription></SheetHeader>{basis?.clauses.find(clause => clause.id === sourceClause) && sourceText(basis.clauses.find(clause => clause.id === sourceClause)!)}</SheetContent></Sheet>
    {isPreview() && import.meta.env.DEV && <aside className={"fixed z-10 bottom-5 left-[50%] flex items-center gap-2.5 p-1.5 border rounded-lg bg-muted shadow-sm text-foreground text-xs whitespace-nowrap max-sm:bottom-3.5"} aria-label="要求页方案切换"><Button variant="ghost" aria-label="上一种要求布局" onClick={() => switchLayout(layouts[(layouts.findIndex(item => item.id === layout) + 2) % 3].id)}><ArrowLeft size={16}/></Button><span>布局方案　{layouts.findIndex(item => item.id === layout) + 1} / 3 · {layouts.find(item => item.id === layout)?.name}</span><Button variant="ghost" aria-label="下一种要求布局" onClick={() => switchLayout(layouts[(layouts.findIndex(item => item.id === layout) + 1) % 3].id)}><ArrowRight size={16}/></Button></aside>}
  </section>
}
