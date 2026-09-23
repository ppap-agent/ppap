import {parseProductRoute, returnLabel} from './navigation/route'
import RuleLibrary from './rules/RuleLibrary'
import RuleDetail from './rules/RuleDetail'
import RuleImport from './rules/RuleImport'

export default function RulesView() {
  const route = parseProductRoute(location.pathname, location.search)

  if (route.page === 'rule-import') {
    return (
      <RuleImport
        initialParseId={route.parseId || ''}
        predecessor={route.predecessorId || ''}
        initialType={route.documentType || ''}
        returnTo={route.returnTo}
      />
    )
  }

  if (route.page === 'rule-detail') {
    return <RuleDetail ruleId={route.ruleId} returnTo={route.returnTo} returnLabel={returnLabel(route.returnTo)} saved={!!route.saved} />
  }

  const libraryReturn = route.page === 'rule-library' ? route.returnTo : undefined
  return <RuleLibrary returnTo={libraryReturn} returnLabel={returnLabel(libraryReturn)} />
}
