import assert from 'node:assert/strict'
import test from 'node:test'
import {pdfSelection} from './pdf-selection.ts'

const original = {width: 600, height: 800, convertToViewportPoint: (x: number, y: number) => [x - 20, 830 - y]}
test('PDF selection converts zoomed, rotated, cropped coordinates to unrotated top-left points', () => {
  // Display viewport is rotated 90 degrees and scaled 2x; CropBox starts at (20,30).
  const displayed = {convertToPdfPoint: (x: number, y: number) => [y / 2 + 20, x / 2 + 30]}
  assert.deepEqual(pdfSelection([200, 100], [800, 600], displayed, original), [50, 400, 300, 700])
  assert.deepEqual(pdfSelection([800, 600], [200, 100], displayed, original), [50, 400, 300, 700])
})
test('PDF selection clips to page, rejects empty or invalid geometry', () => {
  const identity = {convertToPdfPoint: (x: number, y: number) => [x + 20, 830 - y]}
  assert.deepEqual(pdfSelection([-10, -20], [900, 1000], identity, original), [0, 0, 600, 800])
  assert.equal(pdfSelection([10, 10], [10.1, 10.2], identity, original), null)
  assert.equal(pdfSelection([NaN, 0], [20, 40], identity, original), null)
})
