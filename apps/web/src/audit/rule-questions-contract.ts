import type {components} from '../generated/openapi'

export type QuestionnaireSummary = components['schemas']['RuleQuestionnaireSummary']
export type QuestionnaireList = components['schemas']['RuleQuestionnaireList']
export type QuestionnaireView = components['schemas']['RuleQuestionnaireView']
export type Question = components['schemas']['RuleQuestion']
export type QuestionAnswer = components['schemas']['RuleQuestionAnswer']
export type AnswerReceipt = components['schemas']['RuleAnswerReceipt']
export type AnswerRequest = {operation_id: string; question_key: string; expected_revision: number; choice: string | null; text: string | null; note: string | null}

/** Card groups in the order the customer answers them: one answer to a common question settles many clauses. */
export const questionGroups: {key: Question['group']; label: string}[] = [
  {key: 'common', label: '共性问题'},
  {key: 'conclusion', label: '交客户补结论'},
  {key: 'general', label: '通用判定说明'},
  {key: 'clause', label: '逐条问题'},
]

/** The choice that confirms an answer already relayed to us; it is offered only on a card that carries one. */
export const RELAYED = 'relayed'

export function choiceLabel(question: Pick<Question, 'options' | 'relayed'>, choice: string | null | undefined): string {
  if (!choice) return ''
  if (choice === RELAYED) return question.relayed?.applied ? '确认按共性答复处理' : '确认已转达的答复'
  return question.options.find(option => option.key === choice)?.label ?? ''
}

export function groupQuestions(questions: Question[]) {
  return questionGroups.map(group => {
    const rows = questions.filter(question => question.group === group.key)
    return {...group, questions: rows, answered: rows.filter(question => question.answers.length > 0).length}
  }).filter(group => group.questions.length > 0)
}

/** A saved answer, applied to the view as the server recorded it: the answerer's earlier answer becomes history. */
export function applyReceipt(view: QuestionnaireView, receipt: AnswerReceipt): QuestionnaireView {
  if (receipt.questionnaire_id !== view.id) return view
  let answered = view.answered, mine = view.mine
  const questions = view.questions.map(question => {
    if (question.key !== receipt.question_key) return question
    const own = question.answers.find(answer => answer.mine)
    if (own && own.revision >= receipt.revision) return question
    const current = {revision: receipt.revision, choice: receipt.choice, text: receipt.text, note: receipt.note, answered_at: receipt.answered_at}
    if (!question.answers.length) answered += 1
    if (!own) mine += 1
    const answers = own
      ? question.answers.map(answer => answer.mine ? {...answer, ...current, history: [...answer.history, {revision: answer.revision, choice: answer.choice, text: answer.text, note: answer.note, answered_at: answer.answered_at}]} : answer)
      : [...question.answers, {...current, answerer: receipt.answerer, mine: true, first_answered_at: receipt.answered_at, history: []}]
    return {...question, answers}
  })
  return {...view, questions, answered, mine}
}

/** The receipt must be the one this request asked for; anything else is not proof the answer was saved. */
export function checkedReceipt(receipt: AnswerReceipt | null | undefined, questionnaireId: string, input: AnswerRequest): AnswerReceipt {
  if (!receipt || receipt.operation_id !== input.operation_id || receipt.questionnaire_id !== questionnaireId || receipt.question_key !== input.question_key || !receipt.mine) throw new Error('保存结果与这次答复对不上，请重新读取后核对。')
  return receipt
}
