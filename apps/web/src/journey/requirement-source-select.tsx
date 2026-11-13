import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import type {RequirementBasis, RequirementClause} from './requirements-state'
import {citeRequirementSource} from './requirement-sources'

export function RequirementSourceSelect({basis, clause, disabled, onChange}: {basis: RequirementBasis; clause: RequirementClause; disabled: boolean; onChange: (clause: RequirementClause) => void}) {
  if (!basis.server) return null
  return <label className="grid gap-2">原文位置
    <Select disabled={disabled || !basis.server.sources?.units.length} value={clause.provenance?.id ?? ''} onValueChange={id => {
      const unit = basis.server?.sources?.units.find(item => item.id === id)
      if (unit) onChange({...citeRequirementSource(clause, unit, basis), checked: false})
    }}>
      <SelectTrigger className="w-full"><SelectValue placeholder="选择与条款对应的原文"/></SelectTrigger>
      <SelectContent>{basis.server.sources?.units.map(unit => <SelectItem key={unit.id} value={unit.id} description={unit.excerpt.slice(0, 80)}>{unit.location}</SelectItem>)}</SelectContent>
    </Select>
  </label>
}
