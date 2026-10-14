import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Same Stitch reader. Browser layout is a reading aid, not a Word pagination engine.
import {useEffect, useState} from 'react'
import {Minus, Plus} from 'lucide-react'
import {Button} from '../components/ui/button'
export function WordSource({blob, name}: {blob: Blob; name: string}) {
  const [html, setHTML] = useState(''), [text, setText] = useState(''), [error, setError] = useState(''), [zoom, setZoom] = useState(100)
  useEffect(() => {
    let current = true
    void Promise.all([import('docx-preview'), blob.arrayBuffer()]).then(async ([docx, bytes]) => {
      const inert = document.implementation.createHTMLDocument(''), body = inert.createElement('div'), styles = inert.createElement('div')
      await docx.renderAsync(bytes, body, styles, {inWrapper: false, ignoreWidth: true, ignoreHeight: true, ignoreFonts: true, breakPages: false, useBase64URL: true,
        renderChanges: true, renderComments: false, renderAltChunks: false, renderHeaders: true, renderFooters: true, renderFootnotes: true, renderEndnotes: true})
      // Display only library-generated content, inside a scriptless, networkless frame.
      for (const host of [body, styles]) {
        host.querySelectorAll('script,iframe,object,embed,form,input,button,link,meta,base').forEach(node => node.remove())
        host.querySelectorAll('*').forEach(node => [...node.attributes].forEach(attribute => {
          const key = attribute.name.toLowerCase()
          if (key.startsWith('on') || key === 'href' || key === 'xlink:href' || key === 'srcset' || key === 'src' && !attribute.value.startsWith('data:')) node.removeAttribute(attribute.name)
        }))
      }
      if (!current) return
      setHTML(styles.innerHTML + body.innerHTML)
      setText([...body.querySelectorAll('p')].map(node => node.textContent ?? '').join('\n'))
    }).catch(() => {if (current) setError('此 Word 文件无法排版，可能已加密、损坏或含不支持的内容。请下载原件核对。')})
    return () => {current = false}
  }, [blob])
  const source = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><style>html{background:white;color:#111;font-family:Arial,sans-serif}body{margin:0;padding:20px;zoom:${zoom / 100};overflow-wrap:anywhere}section.docx{padding:16px!important;min-height:0!important;max-width:100%;box-sizing:border-box}table{max-width:100%}img,svg{max-width:100%;height:auto}p{line-height:1.6}a{color:inherit;text-decoration:none}</style></head><body>${html}</body></html>`
  return <div className={"flex flex-col h-full min-h-105 min-w-0 max-md:min-h-0"}>
    <div className={"flex flex-wrap gap-1.5 items-center p-2 border-b shrink-0 text-sm [&>[role=combobox]]:w-auto [&>[role=combobox]]:max-w-full [&>[role=combobox]]:min-w-40 [&_form]:flex [&_form]:gap-1 [&_form]:items-center [&_form]:min-w-0 max-md:gap-2 max-md:[&>[role=combobox]]:flex-[1_0_calc(100%_-_116px)] max-md:[&>[role=combobox]]:min-w-0 max-md:[&>[role=combobox]]:w-0 max-md:[&_form]:order-[2]"}><span>Word 阅读视图</span><div className={"flex items-center gap-1 whitespace-nowrap ml-auto"}><Button variant="ghost" size="icon-sm" aria-label="缩小 Word" disabled={zoom <= 100} onClick={() => setZoom(value => value - 25)}><Minus/></Button><span>{zoom}%</span><Button variant="ghost" size="icon-sm" aria-label="放大 Word" disabled={zoom >= 200} onClick={() => setZoom(value => value + 25)}><Plus/></Button></div></div>
    <Collapsible className={"shrink-0 "}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">排版与页码可能不同于原件</Button></CollapsibleTrigger><CollapsibleContent><p>此视图辅助阅读正文和表格，分页、字体、批注、修订显示和嵌入对象可能不完整。需要精确页码、签章或修改记录时，请下载原件核对。</p></CollapsibleContent></Collapsible>
    {error ? <p className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"} role="alert">{error}</p> : !html ? <p className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"} role="status">正在排版 Word 内容…</p> : <iframe className={"w-full border-0 bg-background flex-1 min-h-75"} title={`${name} Word 阅读内容`} sandbox="" srcDoc={source}/>}
    {html && <Collapsible className={"max-h-45 overflow-auto"}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">正文文字</Button></CollapsibleTrigger><CollapsibleContent><pre>{text || '没有可提取的段落文字；请阅读页面内容或下载原件核对。'}</pre></CollapsibleContent></Collapsible>}
  </div>
}
