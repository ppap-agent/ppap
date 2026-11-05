import {highlightParts} from '../lib/highlight'

export function HighlightText({text, query}: {text: string; query: string}) {
  return <>{highlightParts(text, query).map((part, index) => part.hit ? <mark key={index} className="rounded-sm bg-warning/20 text-inherit">{part.text}</mark> : part.text)}</>
}
