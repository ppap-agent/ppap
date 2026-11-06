import assert from 'node:assert/strict'
import test from 'node:test'
import {createElement, useContext} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {FactAnswersContext} from './fact-answers-context.ts'
import {nativeFactAnswersAdapter} from './fact-answers-contract.ts'
import {fixtureFactAnswers} from '../../preview/audit-fact-answers.ts'

const recipeFreeFactAnswers = fixtureFactAnswers('archive-ready')

test('fact transport defaults to native HTTP and preview explicitly provides its adapter', () => {
  const seen: unknown[] = []
  function Reader() {seen.push(useContext(FactAnswersContext)); return null}
  renderToStaticMarkup(createElement(Reader))
  renderToStaticMarkup(createElement(FactAnswersContext.Provider, {value: recipeFreeFactAnswers}, createElement(Reader)))
  assert.deepEqual(seen, [nativeFactAnswersAdapter, recipeFreeFactAnswers])
})

test('recipe-free task, archive and historical previews read successful round-scoped empty fact views', async () => {
  for (const [scene, roundId] of [['populated', 'preview-round-task-accept'], ['archive-ready', 'preview-round-preview-archive-task-A001-3-accept'], ['round-history', 'history-round']]) {
    assert.deepEqual(await fixtureFactAnswers(scene).read(roundId), {round_id: roundId, revision: 0, questions: [], answers: [], results: [], model_calls: 0})
  }
})
