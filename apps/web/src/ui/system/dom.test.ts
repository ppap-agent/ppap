import assert from 'node:assert/strict'
import test from 'node:test'
import {publicProps} from './dom.ts'

test('publicProps strips style objects and system markers while keeping accessible attributes', () => {
  const passed = publicProps({
    title: '说明',
    style: {opacity: 0.2},
    className: 'x',
    color: 'red',
    'data-ui': 'hack',
    'data-variant': 'hack',
    'data-size': 'hack',
    'data-tone': 'hack',
    'data-kind': 'hack',
    'data-testid': 'keep',
    'aria-label': 'keep',
    type: 'text',
  })
  assert.deepEqual(passed, {
    title: '说明',
    'data-testid': 'keep',
    'aria-label': 'keep',
    type: 'text',
  })
})
