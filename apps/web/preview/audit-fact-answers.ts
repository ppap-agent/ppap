import type {FactAnswersAdapter} from '../src/audit/fact-answers-contract.ts'

// audit.tsx creates recipe-free sample rounds with no saved original-fact answers.
// Match product.store.fact_answers authorization before its successful empty _view.
// The dedicated fact-questions preview keeps its own controlled HTTP boundary.
export function fixtureFactAnswers(scene: string): FactAnswersAdapter {
  function authorize() {
    if (scene === 'archive-original-denied') throw Object.assign(new Error('当前账号无权查看原件。'), {status: 403})
  }
  return {
    async read(roundId) {
      authorize()
      return {round_id: roundId, revision: 0, questions: [], answers: [], results: [], model_calls: 0}
    },
    async apply() {authorize(); throw new Error('This preview round has no original-fact question to answer.')},
    async readOperation() {authorize(); return null},
  }
}
