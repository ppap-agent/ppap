import {isSafeReturnTo} from './helpers'
import RuleLibrary from './rules/RuleLibrary'
import RuleDetail from './rules/RuleDetail'
import RuleImport from './rules/RuleImport'

export default function RulesView() {
  const params = new URLSearchParams(location.search)
  const requestedReturn = params.get('return_to') || ''
  const returnTo = isSafeReturnTo(requestedReturn) ? requestedReturn : ''
  const returnLabel = returnTo.startsWith('/?round=') ? '返回审核结果' : '返回审核准备'
  const parseId = params.get('parse') || ''
  const predecessor = params.get('predecessor') || ''
  const ruleId = params.get('rule') || ''

  if (params.has('new') || parseId) {
    return (
      <RuleImport
        initialParseId={parseId}
        predecessor={predecessor}
        initialType={params.get('type') || ''}
        returnTo={returnTo}
      />
    )
  }

  if (ruleId) {
    return <RuleDetail ruleId={ruleId} returnTo={returnTo} returnLabel={returnLabel} saved={params.get('saved') === '1'} />
  }

  if (predecessor) {
    return <RuleImport initialParseId="" predecessor={predecessor} initialType={params.get('type') || ''} returnTo={returnTo} />
  }

  return <RuleLibrary returnTo={returnTo} returnLabel={returnLabel} />
}
