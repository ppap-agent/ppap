import assert from 'node:assert/strict'
import test from 'node:test'
import {color, contrastPairs} from './tokens.ts'

function channel(hex: string, shift: number) {
  const value = (parseInt(hex.slice(1), 16) >> shift) & 255
  const srgb = value / 255
  return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string) {
  return 0.2126 * channel(hex, 16) + 0.7152 * channel(hex, 8) + 0.0722 * channel(hex, 0)
}

function ratio(foreground: string, background: string) {
  const [hi, lo] = [luminance(foreground), luminance(background)].sort((left, right) => right - left)
  return (hi + 0.05) / (lo + 0.05)
}

function tokenColor(path: string) {
  const [group, name] = path.split('.').slice(1)
  return color[group as keyof typeof color][name as never] as string
}

test('text and background tokens stay at WCAG AA', () => {
  for (const [foreground, background] of contrastPairs) {
    const score = ratio(tokenColor(foreground), tokenColor(background))
    assert.ok(score >= 4.5, `${foreground} on ${background} is ${score.toFixed(2)}`)
  }
})
