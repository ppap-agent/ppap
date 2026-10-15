// Stitch: projects/13261964020862199279/screens/1455a90bd66e4891a879ebc715e815a2.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, Check, ListFilter} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {Input} from '../components/ui/input'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {ScrollArea} from '../components/ui/scroll-area'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {ResizableHandle, ResizablePanel, ResizablePanelGroup} from '../components/ui/resizable'
import {JudgmentEditor} from './judgment'
import {decisionInputKey, judgmentComplete, requirementSatisfied, type Judgment} from './judgment-state'
import {DocumentPane} from './source-reader'
import {sourceKey, type DemoDocument, type SourceFile, type SourceFocus} from './source-documents'
import type {DecisionEvidence, DecisionInput} from './decision'
import {FileIcon, FileName} from './file-name'

const machineVariant = (row: DecisionEvidence) => row.result === '满足' ? 'secondary' as const : 'outline' as const
function sourceFile(input: DecisionInput, row?: Pick<DecisionEvidence, 'sources'>) {
  const evidence = row?.sources[0]
  const candidates = evidence ? input.files.filter(file => evidence.fileVersionId ? file.material?.file_version_id === evidence.fileVersionId : file.name === evidence.file) : input.files.length === 1 ? input.files : []
  return candidates.length === 1 ? candidates[0] : undefined
}
function routeOptions() {
  const params = new URLSearchParams(location.search)
  return {pane: params.get('reviewPane') === 'source' ? 'source' : 'review',
    filter: ['all', 'attention'].includes(params.get('reviewFilter') || '') ? params.get('reviewFilter')! : 'pending',
    query: (params.get('reviewQuery') || '').slice(0, 200),
    file: (params.get('reviewFile') || '').slice(0, 2000), location: (params.get('reviewLocation') || '').slice(0, 500)}
}
function writeOptions(values: Record<string, string>) {
  const url = new URL(location.href)
  for (const [key, value] of Object.entries(values)) {if (value) url.searchParams.set(key, value); else url.searchParams.delete(key)}
  history.replaceState(null, '', url)
}

export function ReviewWorkspace({input, documents, judgments, frozen, taskActive = true, onChange, onRequest, replies = {}}: {
  input: DecisionInput; documents: DemoDocument[]; judgments: Record<string, Judgment>; frozen: boolean
  taskActive?: boolean
  onChange: (row: DecisionEvidence, judgment: Judgment) => void; onRequest?: (row: DecisionEvidence) => void
  replies?: Record<string, {title: string; question: string; text: string}[]>
}) {
  const first = input.evidence.find(row => row.id === new URLSearchParams(location.search).get('clause'))
    ?? input.evidence.find(row => row.result !== '不适用' && !judgmentComplete(input, row, judgments[row.id])) ?? input.evidence[0]
  const [active, setActive] = useState(first?.id ?? ''), [directory, setDirectory] = useState(false)
  const [wide, setWide] = useState(() => window.matchMedia('(min-width: 64rem)').matches)
  useEffect(() => {const media = window.matchMedia('(min-width: 64rem)'); const update = () => setWide(media.matches); media.addEventListener('change', update); return () => media.removeEventListener('change', update)}, [])
  const [query, setQuery] = useState(() => routeOptions().query), [filter, setFilter] = useState(() => routeOptions().filter), [pane, setPane] = useState(() => routeOptions().pane)
  const restoredSource = input.files.find(file => sourceKey(file) === routeOptions().file)
  const initialFile = restoredSource ?? sourceFile(input, first)
  const initialLocation = restoredSource ? routeOptions().location : first?.sources[0]?.location
  const [selectedFile, setSelectedFile] = useState(() => initialFile ? sourceKey(initialFile) : '')
  const [focus, setFocus] = useState<(SourceFocus & {fileKey: string; clauseId: string}) | null>(() => first && initialFile && initialLocation && initialLocation !== '位置待核对' ? {fileKey: sourceKey(initialFile), clauseId: first.id, location: initialLocation, requestId: 1} : null)
  const navigation = useRef(0), focusSequence = useRef(1), reviewStart = useRef<HTMLHeadingElement>(null), sourceStart = useRef<HTMLDivElement>(null)
  const currentTaskActive = useRef(taskActive); currentTaskActive.current = taskActive
  useEffect(() => {navigation.current++}, [taskActive])
  const context = decisionInputKey(input), currentContext = useRef(context); currentContext.current = context
  const row = input.evidence.find(item => item.id === active) ?? first
  const judgment = row ? judgments[row.id] ?? {value: 'pending' as const, reason: ''} : null
  const file = input.files.find(item => sourceKey(item) === selectedFile)
  const applicable = input.evidence.filter(item => item.result !== '不适用')
  const reviewed = applicable.filter(item => judgmentComplete(input, item, judgments[item.id])).length
  const unsatisfied = applicable.filter(item => !requirementSatisfied(input, item, judgments[item.id])).length
  const filtered = input.evidence.filter(item => `${item.displayId || item.id} ${item.title} ${item.requirement}`.toLowerCase().includes(query.trim().toLowerCase())
    && (filter === 'all' || filter === 'attention' ? filter === 'all' || ['不满足', '无法判断', '未覆盖', '执行失败'].includes(item.result) : item.result !== '不适用' && !judgmentComplete(input, item, judgments[item.id])))
  const index = row ? input.evidence.indexOf(row) : -1
  const previous = input.evidence[index - 1], following = input.evidence[index + 1]
  const next = row ? applicable.slice(applicable.indexOf(row) + 1).concat(applicable.slice(0, applicable.indexOf(row))).find(item => !judgmentComplete(input, item, judgments[item.id])) : undefined
  const forward = following ?? next
  const forwardLabel = next && next.id === forward?.id ? '下一待复核' : '下一条'
  const nextPending = useRef(next); nextPending.current = next
  function select(nextRow: DecisionEvidence, persist = true) {
    navigation.current++
    setActive(nextRow.id); setPane('review'); setDirectory(false)
    const source = sourceFile(input, nextRow), key = source ? sourceKey(source) : ''
    const location = nextRow.sources[0]?.location
    setSelectedFile(key)
    setFocus(source && location && location !== '位置待核对' ? {fileKey: key, clauseId: nextRow.id, location, requestId: ++focusSequence.current} : null)
    if (persist) writeOptions({clause: nextRow.id, reviewPane: '', reviewFile: '', reviewLocation: ''})
    requestAnimationFrame(() => {reviewStart.current?.scrollIntoView({block: 'nearest'}); reviewStart.current?.focus({preventScroll: true})})
  }
  useEffect(() => {
    const sync = () => {
      const params = new URLSearchParams(location.search), requested = input.evidence.find(item => item.id === params.get('clause')) ?? first
      if (requested) select(requested, false)
      const options = routeOptions(); setQuery(options.query); setFilter(options.filter); setPane(options.pane)
      if (requested && input.files.some(file => sourceKey(file) === options.file)) {
        setSelectedFile(options.file)
        setFocus(options.location ? {fileKey: options.file, clauseId: requested.id, location: options.location, requestId: ++focusSequence.current} : null)
      }
    }
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [input.evidence])
  function showPane(nextPane: string) {
    navigation.current++; setPane(nextPane); writeOptions({reviewPane: nextPane === 'source' ? 'source' : ''})
  }
  function read(original?: SourceFile, location?: string) {
    const key = original ? sourceKey(original) : selectedFile
    if (original) setSelectedFile(key)
    setFocus(key && row && location && location !== '位置待核对' ? {fileKey: key, clauseId: row.id, location, requestId: ++focusSequence.current} : null)
    writeOptions({reviewFile: key, reviewLocation: location && location !== '位置待核对' ? location : ''})
    showPane('source')
    if (!wide) requestAnimationFrame(() => sourceStart.current?.scrollIntoView({block: 'start'}))
  }
  function openSource(source: DecisionEvidence['sources'][number]) {
    const file = sourceFile(input, {sources: [source]})
    if (file) read(file, source.location)
  }
  function returnToJudgment() {
    showPane('review')
    requestAnimationFrame(() => {reviewStart.current?.scrollIntoView({block: 'start'}); reviewStart.current?.focus({preventScroll: true})})
  }
  function clauseHref(id: string) {const url = new URL(location.href); url.searchParams.set('clause', id); for (const key of ['reviewPane', 'reviewFile', 'reviewLocation']) url.searchParams.delete(key); return url.pathname + url.search}
  const epoch = navigation.current
  const continueAfterSave = next ? () => {if (currentTaskActive.current && navigation.current === epoch && currentContext.current === context && nextPending.current) select(nextPending.current)} : undefined
  return <Tabs value={pane} onValueChange={showPane} asChild><section aria-label="逐条依据与复核" className="min-w-0 space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
      <div className="flex flex-wrap items-center gap-3"><Sheet open={directory} onOpenChange={open => {navigation.current++; setDirectory(open)}}><SheetTrigger asChild><Button variant="outline" size="sm"><ListFilter/>条款目录 · {index + 1}/{input.evidence.length}</Button></SheetTrigger><SheetContent side="left" className="data-[side=left]:w-full data-[side=left]:max-w-full sm:data-[side=left]:max-w-md"><SheetHeader><SheetTitle>审核条款</SheetTitle><SheetDescription>人工已保存 {reviewed}/{applicable.length} 项。切换条款不会提交未保存的判断。</SheetDescription></SheetHeader>
        <Input type="search" maxLength={200} aria-label="搜索审核条款" placeholder="搜索编号或条款" value={query} onChange={event => {setQuery(event.target.value); writeOptions({reviewQuery: event.target.value})}}/>
        <RadioGroup value={filter} onValueChange={value => {setFilter(value); writeOptions({reviewFilter: value === 'pending' ? '' : value})}} aria-label="筛选审核条款"><div className="flex flex-wrap gap-4">{[['pending', '待复核'], ['attention', '需要关注'], ['all', '全部']].map(([value, label]) => <label key={value} className="flex min-h-9 items-center gap-2 text-sm max-md:min-h-11"><RadioGroupItem value={value}/>{label}</label>)}</div></RadioGroup>
        <ScrollArea className="min-h-0 flex-1"><nav aria-label="审核条款" className="space-y-1 pr-3">{filtered.map(item => <Button key={item.id} asChild variant={row?.id === item.id ? 'secondary' : 'ghost'} className="h-auto w-full justify-start whitespace-normal text-left"><a href={clauseHref(item.id)} aria-current={row?.id === item.id ? 'page' : undefined} onClick={event => {if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); select(item)}}><span className="flex min-w-0 flex-1 flex-col gap-1"><span className="break-words">{item.displayId || item.id} · {item.title}</span><span className={['flex flex-wrap items-center gap-2 text-xs', row?.id === item.id ? 'text-secondary-foreground' : 'text-muted-foreground'].join(' ')}><span>机器：{item.result}</span><span>{judgmentComplete(input, item, judgments[item.id]) ? '人工已保存' : judgments[item.id]?.value && judgments[item.id].value !== 'pending' ? '未保存草稿' : '待复核'}</span></span></span>{judgmentComplete(input, item, judgments[item.id]) && <Check className="size-4 shrink-0"/>}</a></Button>)}{!filtered.length && <p className="py-4 text-sm text-muted-foreground">{query ? '没有匹配的条款' : filter === 'pending' ? '当前没有待复核条款，可切换到全部查看。' : '当前没有这类条款'}</p>}</nav></ScrollArea>
      </SheetContent></Sheet><span className="text-sm text-muted-foreground max-sm:hidden">人工已保存 <strong className="font-medium text-foreground tabular-nums">{reviewed}/{applicable.length}</strong></span></div>
      <div className="flex flex-wrap items-center gap-2"><Button variant="ghost" size="sm" disabled={!previous} onClick={() => previous && select(previous)} title="上一条"><ArrowLeft/><span className="max-sm:sr-only">上一条</span></Button><Button variant="ghost" size="sm" disabled={!forward} onClick={() => forward && select(forward)} title={forwardLabel}><span className="max-sm:sr-only">{forwardLabel}</span><ArrowRight/></Button>{next && next.id !== forward?.id && <Button variant="outline" size="sm" onClick={() => select(next)}>下一待复核<ArrowRight/></Button>}</div>
    </div>
    {applicable.length > 0 && reviewed === applicable.length && <p role="status" className="text-sm text-muted-foreground">本轮人工复核已全部保存。{unsatisfied ? `仍有 ${unsatisfied} 条未确认满足要求。` : '这不代表已获正式批准。'}</p>}
    <TabsList className="lg:hidden" aria-label="复核工作区"><TabsTrigger value="review">条款与判断</TabsTrigger><TabsTrigger value="source">原件</TabsTrigger></TabsList>
    <div className="min-w-0 lg:h-[calc(100dvh-18rem)] lg:min-h-[32rem]"><ResizablePanelGroup role="group" orientation="horizontal" disabled={!wide} className="max-lg:h-auto! max-lg:[&>[data-mobile-active=false]]:hidden!" aria-label="复核与原件工作区">
      <TabsContent value="review" forceMount asChild {...(wide ? {role: 'region', 'aria-labelledby': undefined} : {})}><ResizablePanel aria-label="条款与判断" defaultSize="42%" minSize={wide ? '320px' : '0%'} data-mobile-active={pane === 'review'} className="min-w-0">
        <div className="min-w-0 space-y-5 lg:h-full lg:overflow-y-auto lg:pr-4">
          {row && judgment ? <article key={row.id} className="min-w-0 space-y-5 pb-4" aria-label={`当前条款 ${row.displayId || row.id}`}>
            <header className="space-y-3"><div className="flex flex-wrap items-start justify-between gap-2"><h2 ref={reviewStart} tabIndex={-1} className="min-w-0 break-words text-base font-semibold outline-none">{row.displayId || row.id} · {row.title}</h2><Badge variant={machineVariant(row)}>机器：{row.result}</Badge></div>{row.requirement !== row.title && <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{row.requirement}</p>}</header>
            <section className="space-y-3" aria-label="机器检查依据">
              {row.explanation && <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{row.explanation}</p>}
              {row.sources.length > 0 ? <div className="space-y-3">{row.sources.map((source, index) => {
                const file = sourceFile(input, {sources: [source]})
                return <div key={index} className="space-y-2">{file ? <Button variant="outline" size="sm" className="h-auto max-w-full justify-start whitespace-normal text-left" onClick={() => openSource(source)}><FileIcon name={source.file}/><span className="min-w-0 [overflow-wrap:anywhere]">{source.location === '位置待核对' ? source.file : `${source.file} · ${source.location}`}</span></Button> : <><FileName name={source.file} className="text-sm"/><p className="text-sm text-muted-foreground">此来源无法唯一对应本轮原件，请选择原件人工核对。</p></>}{source.excerpt && <blockquote className="whitespace-pre-wrap break-words border-l-2 pl-3 text-sm">{source.excerpt}</blockquote>}</div>
              })}</div> : <p className="text-sm text-muted-foreground">{row.result === '未覆盖' ? '本条没有机器检查结果，可依据原件人工核对。' : '没有可定位的机器证据。'}</p>}
            </section>
            {replies[row.id]?.map((reply, index) => <section key={index} className="space-y-1 border-l-2 pl-3 text-sm"><strong>{reply.title}</strong><p>{reply.question}</p><p className="whitespace-pre-wrap break-words">{reply.text}</p></section>)}
            {row.result !== '不适用' && <JudgmentEditor key={context} input={input} row={row} judgment={judgment} frozen={frozen} documents={documents} onRead={read} afterSave={continueAfterSave} onChange={nextJudgment => onChange(row, nextJudgment)}/>}
            {onRequest && row.result !== '不适用' && !requirementSatisfied(input, row, judgment) && <Button variant="ghost" disabled={frozen} onClick={() => onRequest(row)}>补正与回件<ArrowRight/></Button>}
          </article> : <p>本轮没有可复核条款。</p>}
        </div>
      </ResizablePanel></TabsContent>
      {wide && <ResizableHandle withHandle className="mx-4"/>}
      <TabsContent value="source" forceMount asChild {...(wide ? {role: 'region', 'aria-labelledby': undefined} : {})}><ResizablePanel aria-label="原件阅读" defaultSize="58%" minSize={wide ? '360px' : '0%'} data-mobile-active={pane === 'source'} className="min-w-0">
        <section className="flex h-[max(32rem,calc(100dvh-13rem))] min-w-0 flex-col gap-3 lg:h-full" aria-label="当前原件"><div ref={sourceStart} className="flex scroll-mt-4 flex-wrap items-center justify-between gap-2 lg:hidden"><p className="min-w-0 flex-1 break-words text-sm font-medium">{row?.displayId || row?.id} · {row?.title}</p><Button variant="outline" size="sm" onClick={returnToJudgment}><ArrowLeft/>返回判断</Button></div>{input.files.length === 1 && file ? <FileName name={file.name} className="text-sm font-medium"/> : <Select value={selectedFile} onValueChange={value => {navigation.current++; setSelectedFile(value); setFocus(null); writeOptions({reviewFile: value, reviewLocation: ''})}}><SelectTrigger className="w-full min-w-0" aria-label="复核原件"><SelectValue placeholder="选择原件" className="min-w-0"/></SelectTrigger><SelectContent className="max-w-[calc(100vw-2rem)]">{input.files.map((item: SourceFile) => <SelectItem key={sourceKey(item)} value={sourceKey(item)} textValue={item.name}><FileIcon name={item.name}/><span className="min-w-0 whitespace-normal [overflow-wrap:anywhere]">{item.name}{input.files.filter(file => file.name === item.name).length > 1 && item.material ? ` · 上传版本 ${item.material.file_version_id.slice(0, 8)}` : ''}</span></SelectItem>)}</SelectContent></Select>}<DocumentPane key={file ? sourceKey(file) : 'none'} file={file} row={row} documents={documents} current={input.files} side="复核" focus={focus?.fileKey === selectedFile && focus.clauseId === row?.id ? focus : undefined} onClose={returnToJudgment}/></section>
      </ResizablePanel></TabsContent>
    </ResizablePanelGroup></div>
  </section></Tabs>
}
