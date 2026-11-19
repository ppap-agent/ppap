/** Chapters of a delivered report come from its PDF outline: the cover and the ten chapters, each with its first page. */
export type ReportChapter = {title: string; page: number}

/** The P3 precheck contents before the PDF is read; the reader then lists the PDF's own outline. */
export const precheckReportChapters = ['封面', '一、结论摘要', '二、提交范围与文件集', '三、问题总览', '四、阻断项明细', '五、警告项明细', '六、状态登记', '七、整改建议汇总', '八、结论作用域与局限性声明', '九、附录 · 检查项全量结果', '十、附录 · 分则附加节'] as const

type OutlineItem = {title: string; dest: string | unknown[] | null}

/** Top-level outline entries with a resolvable page; an entry that cannot be resolved is left out, never guessed. */
export async function outlineChapters(outline: OutlineItem[] | null, pageOf: (dest: string | unknown[]) => Promise<number>): Promise<ReportChapter[]> {
  const chapters: ReportChapter[] = []
  for (const item of outline ?? []) {
    if (!item.dest) continue
    try {chapters.push({title: item.title, page: await pageOf(item.dest)})} catch {/* an unresolvable entry is not listed */}
  }
  return chapters
}

/** The chapter being read: the last one starting on or before the page. */
export function chapterAt(chapters: ReportChapter[], page: number): number {
  let current = -1
  chapters.forEach((chapter, index) => {if (chapter.page <= page) current = index})
  return current
}

/** Text search ignores the line breaks and spaces the layout adds. */
export function textIncludes(text: string, query: string): boolean {
  const needle = query.replace(/\s+/g, '')
  return needle.length > 0 && text.replace(/\s+/g, '').includes(needle)
}

/** Pages searched after the current one, wrapping around to it last. */
export function searchOrder(current: number, total: number): number[] {
  return Array.from({length: total}, (_, offset) => (current + offset) % total + 1)
}
