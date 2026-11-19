import {useEffect, useState} from 'react'
import {request, type RulesList} from '../api'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {message} from '../journey/remote'
import {useAccess} from '../journey/access'
import {auditAddressChanged} from './routes'
import {customerRules} from './rule-templates'
import {CustomerRulesBrowser, RuleDetail} from './rule-templates-view'

/** The customer rule library, read only: customers on the left, their file types and current rules on the right. */
export function AuditRules({active}: {active: boolean; onPublished?: () => void; headerActions?: HTMLElement | null}) {
  const principal = useAccess()?.principal
  return <RulesWorkspace key={principal ? `${principal.tenant_id}:${principal.user_id}` : 'preview'} active={active}/>
}

const routeParams = () => {
  const url = new URL(location.href)
  return {customer: url.searchParams.get('template') ?? '', rule: url.searchParams.get('systemRule') ?? ''}
}

function RulesWorkspace({active}: {active: boolean}) {
  const [read, setRead] = useState<{loading: boolean; error: string; customers: components['schemas']['AuditCustomer'][]; rules: RulesList['rules']}>({loading: true, error: '', customers: [], rules: []})
  const [revision, setRevision] = useState(0)
  const [route, setRoute] = useState(routeParams)
  useEffect(() => {
    const restore = () => setRoute(routeParams())
    window.addEventListener('popstate', restore); window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setRead(previous => ({...previous, loading: true, error: ''}))
    void Promise.all([
      request<components['schemas']['AuditCustomerCatalog']>('/audit-customers', 'GET', undefined, controller.signal),
      request<RulesList>('/rules', 'GET', undefined, controller.signal),
    ]).then(([catalog, rules]) => {
      if (!controller.signal.aborted) setRead({loading: false, error: '', customers: catalog.customers, rules: rules.rules})
    }).catch(cause => {if (!controller.signal.aborted) setRead({loading: false, error: message(cause), customers: [], rules: []})})
    return () => controller.abort()
  }, [revision, active])
  const entries = customerRules(read.customers, read.rules)
  if (read.error) return <Alert variant="destructive"><AlertTitle>客户规则未能读取</AlertTitle><AlertDescription><p>{read.error}</p><Button variant="outline" size="sm" onClick={() => setRevision(value => value + 1)}>重新读取</Button></AlertDescription></Alert>
  if (route.rule) {
    const owner = entries.find(entry => entry.types?.some(type => type.rules.some(rule => rule.id === route.rule)))?.customer
    return <RuleDetail ruleId={route.rule} customer={owner && {id: owner.customer_id, name: owner.full_name}}/>
  }
  return <section aria-label="审核规则库" className="flex min-h-0 flex-1 flex-col">
    <CustomerRulesBrowser entries={entries} selectedId={route.customer} loading={read.loading}/>
  </section>
}
