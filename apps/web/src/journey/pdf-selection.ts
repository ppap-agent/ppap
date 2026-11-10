/** Convert a drag in the displayed canvas to the original, unrotated page's top-left points. */
export function pdfSelection(start: [number, number], end: [number, number], displayed: {convertToPdfPoint: (x: number, y: number) => number[]}, original: {width: number; height: number; convertToViewportPoint: (x: number, y: number) => number[]}): number[] | null {
  const points = [start, end].map(([x, y]) => {
    const point = displayed.convertToPdfPoint(x, y)
    return original.convertToViewportPoint(point[0], point[1])
  })
  const [a, b] = points
  if (![...a, ...b, original.width, original.height].every(Number.isFinite)) return null
  const region = [Math.max(0, Math.min(original.width, Math.min(a[0], b[0]))), Math.max(0, Math.min(original.height, Math.min(a[1], b[1]))), Math.max(0, Math.min(original.width, Math.max(a[0], b[0]))), Math.max(0, Math.min(original.height, Math.max(a[1], b[1])))]
  return region[2] - region[0] >= 1 && region[3] - region[1] >= 1 ? region.map(value => Math.round(value * 100) / 100) : null
}
