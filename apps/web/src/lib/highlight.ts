// Splits text around case-insensitive hits of a search term; odd positions are the hits.
export function highlightParts(text: string, query: string): {text: string; hit: boolean}[] {
  const term = query.trim()
  if (!term) return [{text, hit: false}]
  return text.split(new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')).map((part, index) => ({text: part, hit: index % 2 === 1})).filter(part => part.text)
}
