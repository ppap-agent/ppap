// Stitch: projects/13261964020862199279/screens/644365d1a08f43248b5e9390cd9189ba.
// Same task/state, three structures via libraryLayout=list|focus|changes on the journey route.
import {useEffect, useRef, useState, type Dispatch, type SetStateAction} from 'react'
import {ArrowLeft, ArrowRight, BookOpen, Plus, UploadCloud} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Checkbox} from '../src/components/ui/checkbox'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../src/components/ui/dialog'
import {localBasis, type RequirementClause} from './requirements-state'
import {clauseContent, libraryDraftIssues, scopeFields, scopeLabel, type RequirementCollection, type RequirementDraft, type RequirementUse, type RequirementVersion} from './requirement-library-state'
import './requirement-library.css'
import {fileAccept, useLocalUploads} from './local-files'
import {RequirementOriginal} from './requirements'

const layouts = [{id: 'list', name: '来源列表'}, {id: 'focus', name: '单份聚焦'}, {id: 'changes', name: '变更对照'}] as const
const routeLayout = () => layouts.find(layout => layout.id === new URLSearchParams(location.search).get('libraryLayout'))?.id ?? 'focus'
const routeSelection = () => ({id: new URLSearchParams(location.search).get('requirement') ?? '', version: new URLSearchParams(location.search).get('reqVersion') ?? ''})

function ClauseDifference({before, after, label}: {before?: RequirementClause; after: RequirementClause; label: string}) {
  return <article><strong>{after.id} · {after.title || '新条款'}</strong><div className="gf-library-diff">
    <div><span>之前</span><p>{before?.text ?? '无此条款'}</p>{before && <small>{before.title} · {before.source} · {before.location}</small>}</div>
    <div><span>{label}</span><p>{after.text || '尚未录入'}</p><small>{after.title || '名称待补充'} · {after.source} · {after.location || '出处待补充'}</small></div>
  </div>{(before?.original ?? '') !== after.original && <details><summary>来源摘录也有变化</summary><div className="gf-library-diff"><div><span>之前</span><p>{before?.original || '没有摘录'}</p></div><div><span>{label}</span><p>{after.original || '没有摘录'}</p></div></div></details>}</article>
}

export function RequirementLibraryPanel({collections, onChange, uses, onOpen, saveError}: {
  collections: RequirementCollection[]; onChange: Dispatch<SetStateAction<RequirementCollection[]>>
  uses: RequirementUse[]; onOpen: (id: string) => void; saveError: string
}) {
  const [selection, setSelection] = useState(routeSelection)
  const [layout, setLayout] = useState(routeLayout)
  const [query, setQuery] = useState('')
  const [message, setMessage] = useState('')
  const [ack, setAck] = useState(false)
  const [discard, setDiscard] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const collection = selection.id ? collections.find(item => item.id === selection.id) : collections[0]
  const draftSelected = Boolean(collection?.draft && (!selection.version || selection.version === 'draft'))
  const current = draftSelected ? collection!.draft! : selection.version ? collection?.versions.find(version => version.id === selection.version) : collection?.versions.at(-1)
  const base = current?.baseId ? collection?.versions.find(version => version.id === current.baseId) : undefined
  const issues = collection ? libraryDraftIssues(collection) : []
  const related = uses.filter(use => collection?.versions.some(version => use.basis.source.id === version.id || use.basis.library?.versionId === version.id))
  const shown = collections.filter(item => {
    const content = item.draft ?? item.versions.at(-1)
    return content && `${content.basis.source.title} ${scopeLabel(content.scope)}`.toLowerCase().includes(query.trim().toLowerCase())
  })
  function select(id: string, version = '') {
    setSelection({id, version}); setAck(false); setMessage('')
    const url = new URL(location.href); url.searchParams.set('requirement', id)
    if (version) url.searchParams.set('reqVersion', version); else url.searchParams.delete('reqVersion')
    history.replaceState(null, '', url)
  }
  function switchLayout(next: typeof layout) {
    setLayout(next)
    const url = new URL(location.href); url.searchParams.set('libraryLayout', next); history.replaceState(null, '', url)
  }
  useEffect(() => {
    const sync = () => {setSelection(routeSelection()); setLayout(routeLayout()); setAck(false)}
    const key = (event: KeyboardEvent) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || (event.target as HTMLElement)?.closest('input,textarea,select,button,[contenteditable],[role="dialog"]')) return
      switchLayout(layouts[(layouts.findIndex(item => item.id === layout) + (event.key === 'ArrowRight' ? 1 : 2)) % 3].id)
    }
    window.addEventListener('popstate', sync); window.addEventListener('keydown', key)
    return () => {window.removeEventListener('popstate', sync); window.removeEventListener('keydown', key)}
  }, [layout])
  function edit(update: (draft: RequirementDraft) => RequirementDraft) {
    if (!collection?.draft || !draftSelected) return
    setAck(false); setMessage('')
    onChange(items => items.map(item => item.id === collection.id && item.draft ? {...item, draft: update(item.draft)} : item))
  }
  function patchClause(id: string, patch: Partial<RequirementClause>) {
    edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.map(clause => clause.id === id ? {...clause, checked: false, ...patch} : clause)}}))
  }
  function startVersion() {
    if (!collection || !current) return
    if (collection.draft) {select(collection.id, 'draft'); return}
    const id = crypto.randomUUID()
    const draft: RequirementDraft = {id, baseId: current.id, scope: {...current.scope}, reason: '', basis: {...structuredClone(current.basis), library: undefined, confirmed: null, source: {...current.basis.source, id, version: ''}, clauses: current.basis.clauses.map(clause => ({...clause, checked: false, applicability: 'pending', reason: ''}))}}
    onChange(items => items.map(item => item.id === collection.id ? {...item, draft} : item)); select(collection.id, 'draft')
  }
  function save() {
    if (!collection?.draft || libraryDraftIssues(collection).length || !ack || saveError) return
    const version: RequirementVersion = {...structuredClone(collection.draft), at: new Date().toISOString()}
    version.basis.source.version = version.basis.source.version.trim()
    onChange(items => items.map(item => item.id === collection.id ? {...item, versions: [...item.versions, version], draft: null} : item))
    select(collection.id, version.id); setMessage(`已保存 ${version.basis.source.version}，可供后续事项选择。已有事项没有改变。`)
  }
  const upload = useLocalUploads(([entry]) => {
    const basis = localBasis(entry.file.name, entry.ref), id = crypto.randomUUID()
    onChange(items => [...items, {id, versions: [], draft: {id: basis.source.id, basis, scope: {customer: '', part: '', revision: ''}, reason: '', baseId: null}}])
    setQuery(''); select(id, 'draft')
  }, 'requirement-library')
  const sourceList = <div className="gf-library-sources"><Input aria-label="搜索审核要求" placeholder="搜索客户、零件或要求" value={query} onChange={event => setQuery(event.target.value)}/><nav aria-label="要求来源">{shown.map(item => {
    const latest = item.draft ?? item.versions.at(-1)!
    return <Button key={item.id} variant="ghost" aria-current={collection?.id === item.id ? 'page' : undefined} onClick={() => select(item.id)}><BookOpen size={17}/><span><strong>{latest.basis.source.title || '待命名要求'}</strong><small>{latest.scope.customer || '客户待核对'} · {latest.scope.part || '零件待核对'}</small></span><small>{item.draft ? '草稿' : latest.basis.source.version}</small></Button>
  })}</nav>{!shown.length && <p>没有匹配的要求。<Button variant="ghost" onClick={() => setQuery('')}>清除搜索</Button></p>}</div>
  const changed = current?.basis.clauses.filter(clause => !base || clauseContent(clause) !== clauseContent(base.basis.clauses.find(old => old.id === clause.id) ?? {...clause, text: ''})) ?? []
  const removed = base?.basis.clauses.filter(old => !current?.basis.clauses.some(clause => clause.id === old.id)) ?? []
  const comparison = current && <section className="gf-library-comparison" aria-label="版本差异">
    <h3>{base ? `相对 ${base.basis.source.version} 的差异` : '首个版本'}</h3>
    {base && <p>{changed.length} 条新增或修改，{removed.length} 条移除。比较内容与出处，不推断规则等价性。</p>}
    {base && scopeLabel(base.scope) !== scopeLabel(current.scope) && <p>适用范围：{scopeLabel(base.scope)} → {scopeLabel(current.scope)}</p>}
    {base && base.basis.source.title !== current.basis.source.title && <p>要求名称：{base.basis.source.title} → {current.basis.source.title}</p>}
    {changed.map(clause => <ClauseDifference key={clause.id} before={base?.basis.clauses.find(old => old.id === clause.id)} after={clause} label={draftSelected ? '草稿' : current.basis.source.version}/>)}
    {removed.map(clause => <div key={clause.id}><strong>移除：{clause.id} · {clause.title}</strong><p>{clause.text}</p><small>{clause.source} · {clause.location}</small></div>)}
    {!changed.length && !removed.length && <p>{base ? '条款内容与出处没有变化。' : '尚无条款，请先按来源录入。'}</p>}
  </section>
  const clauses = current && <section className="gf-library-clauses" aria-label="要求条款">{current.basis.clauses.map(clause => <article key={clause.id}>
    <header><h3>{clause.id} · {clause.title || '新条款'}</h3><span>{clause.checked ? '来源已核对' : '来源待核对'}</span></header>
    {draftSelected ? <><div className="gf-library-fields"><label>条款名称<Input value={clause.title} onChange={event => patchClause(clause.id, {title: event.target.value})}/></label><label>出处位置<Input value={clause.location} onChange={event => patchClause(clause.id, {location: event.target.value})}/></label><label className="gf-library-wide">来源文件<Input value={clause.source} onChange={event => patchClause(clause.id, {source: event.target.value})}/></label></div><div className="gf-library-diff"><label>来源摘录<Textarea value={clause.original} placeholder="可从原件录入；没有摘录时不能声称已提取原文" onChange={event => patchClause(clause.id, {original: event.target.value})}/></label><label>要执行的要求<Textarea value={clause.text} onChange={event => patchClause(clause.id, {text: event.target.value})}/></label></div><div className="gf-library-clause-actions"><label><Checkbox aria-label={`${clause.id} 已核对来源`} checked={clause.checked} onCheckedChange={value => patchClause(clause.id, {checked: value === true})}/>已按原件核对内容与出处</label><Button variant="ghost" onClick={() => edit(draft => ({...draft, basis: {...draft.basis, clauses: draft.basis.clauses.filter(item => item.id !== clause.id)}}))}>移除此草稿条款</Button></div></> : <><p>{clause.text}</p><small>{clause.source} · {clause.location}</small><details><summary>来源摘录</summary><blockquote>{clause.original || '未保存摘录；请按上述位置查阅原件。'}</blockquote></details></>}
  </article>)}{draftSelected && <Button variant="outline" onClick={() => edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, state: 'manual'}, clauses: [...draft.basis.clauses, {id: `M-${crypto.randomUUID().slice(0, 6)}`, title: '', text: '', original: '', source: draft.basis.source.filename, location: '', applicability: 'pending', checked: false, reason: ''}]}}))}><Plus size={16}/>录入条款</Button>}</section>
  return <section className={`gf-library gf-library--${layout}`}>
    <header className="gf-task-heading"><h1>审核要求</h1><Button className="gf-primary" onClick={() => picker.current?.click()}><UploadCloud/>上传要求</Button></header>
    <input className="sr-only" aria-label="上传共用要求文件" ref={picker} type="file" accept={fileAccept} disabled={upload.busy} onChange={event => {void upload.upload(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    {upload.busy && <p role="status">正在保存要求原件…</p>}{upload.error && <p role="alert">{upload.error}</p>}{saveError && <p role="alert" className="gf-basis-warning">{saveError}</p>}{message && !saveError && <p role="status" className="gf-library-message">{message}</p>}
    <div className="gf-library-body">{layout === 'list' ? sourceList : <details className="gf-library-source-picker"><summary>选择要求来源 · {collections.length} 份</summary>{sourceList}</details>}
      {current && collection ? <div className="gf-library-detail"><header className="gf-library-title"><div><h2>{current.basis.source.title || '核对新要求'}</h2><p>{scopeLabel(current.scope)}</p><RequirementOriginal basis={current.basis}/></div>{!draftSelected && <Button variant="outline" onClick={startVersion}>{collection.draft ? '继续版本草稿' : '创建新版本'}</Button>}</header>
        <nav className="gf-library-versions" aria-label="要求版本">{collection.versions.map(version => <Button variant="ghost" key={version.id} aria-current={!draftSelected && current.id === version.id ? 'page' : undefined} onClick={() => select(collection.id, version.id)}>{version.basis.source.version} · 已保存</Button>)}{collection.draft && <Button variant="ghost" aria-current={draftSelected ? 'page' : undefined} onClick={() => select(collection.id, 'draft')}>{collection.draft.basis.source.version || '新版本'} · 草稿</Button>}</nav>
        {draftSelected ? <>
          <p className="gf-library-note">草稿保存在本机。确认版本后，仍需在事项中核对适用性。</p>
          <div className="gf-library-fields">
            {(['title', 'version'] as const).map(key => <label key={key}>{key === 'title' ? '要求名称' : '版本号'}<Input value={current.basis.source[key]} onChange={event => edit(draft => ({...draft, basis: {...draft.basis, source: {...draft.basis.source, [key]: event.target.value}}}))}/></label>)}
            {scopeFields.map(({key, flag, label}) => <div className="gf-library-scope" key={key}>
              <label>{key === 'revision' ? label : `适用${label}`}<Input disabled={current.scope[flag] === true} value={current.scope[key]} onChange={event => edit(draft => ({...draft, scope: {...draft.scope, [key]: event.target.value}}))}/></label>
              <label><Checkbox checked={current.scope[flag] === true} onCheckedChange={value => edit(draft => ({...draft, scope: {...draft.scope, [flag]: value === true}}))}/>不限定{label}</label>
            </div>)}
            <label className="gf-library-wide">来源说明 / 变更原因<Textarea value={current.reason} onChange={event => edit(draft => ({...draft, reason: event.target.value}))}/></label>
          </div>
          {current.basis.source.origin === 'local' && <p className="gf-basis-warning">尚未自动识别条款。请打开要求原件，核对后录入正文和出处。</p>}
        </> : <p className="gf-library-note">{current.reason}　{'at' in current && current.at ? `保存于 ${new Date(current.at).toLocaleString('zh-CN')}` : '示例来源'} · 只读</p>}
        {layout === 'changes' && current.basis.clauses.length > 0 ? <>{comparison}<details className="gf-library-all" open={draftSelected}><summary>查看全部 {current.basis.clauses.length} 条要求</summary>{clauses}</details></> : <>{clauses}<details className="gf-library-all"><summary>查看版本差异</summary>{comparison}</details></>}
        {draftSelected && <footer className="gf-library-save"><div>{issues.length > 0 && <details><summary>还有 {issues.length} 项待核对</summary><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></details>}<label><Checkbox checked={ack} onCheckedChange={value => setAck(value === true)}/>我已核对来源、范围和变更；保存不改变既有事项</label></div><div><Button variant="ghost" onClick={() => setDiscard(true)}>放弃草稿</Button><Button className="gf-primary" disabled={issues.length > 0 || !ack || Boolean(saveError)} onClick={save}>确认并保存版本</Button></div></footer>}
        <section className="gf-library-uses"><h3>已有事项的依据</h3><p>每个事项保留自己选择的版本。这里不自动升级，也不推断哪些问题已解决。</p>{related.length ? related.map(use => <div key={use.id}><span><strong>{use.title}</strong><small>{use.basis.source.version} · {use.submitted ? '已登记，依据固定' : '未登记，可在事项中重新选择'}</small></span><Button variant="ghost" onClick={() => onOpen(use.id)}>查看事项<ArrowRight size={16}/></Button></div>) : <p>此浏览器还没有引用该来源的事项。</p>}</section>
      </div> : <div className="gf-task-empty"><BookOpen/><h2>{selection.id ? '暂时无法打开此要求版本' : '从要求来源开始'}</h2><p>{selection.id ? '此浏览器没有对应记录。没有改选其他版本，请返回列表选择。' : '上传客户提供的要求，再核对范围与条款。'}</p>{selection.id && <Button variant="outline" onClick={() => select('')}>返回要求列表</Button>}</div>}
    </div>
    <Dialog open={discard} onOpenChange={setDiscard}><DialogContent><DialogHeader><DialogTitle>放弃这个版本草稿？</DialogTitle><DialogDescription>只移除未确认的草稿。已保存版本和已有事项不变。</DialogDescription></DialogHeader><Button variant="outline" onClick={() => setDiscard(false)}>继续编辑</Button><Button onClick={() => {if (!collection) return; onChange(items => items.flatMap(item => item.id !== collection.id ? [item] : item.versions.length ? [{...item, draft: null}] : [])); select(collection.versions.length ? collection.id : '', collection.versions.at(-1)?.id ?? ''); setDiscard(false)}}>放弃此草稿</Button></DialogContent></Dialog>
    {import.meta.env.DEV && <aside className="gf-switcher" aria-label="要求库布局方案"><button aria-label="上一种要求库布局" onClick={() => switchLayout(layouts[(layouts.findIndex(item => item.id === layout) + 2) % 3].id)}><ArrowLeft size={16}/></button><span>{layouts.find(item => item.id === layout)?.name}</span><button aria-label="下一种要求库布局" onClick={() => switchLayout(layouts[(layouts.findIndex(item => item.id === layout) + 1) % 3].id)}><ArrowRight size={16}/></button></aside>}
  </section>
}
