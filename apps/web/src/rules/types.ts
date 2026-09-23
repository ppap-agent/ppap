import type {Rule} from '../api'

export interface RuleItem {
  id: string
  category: string
  priority: string | null
  rule_to_check: string
  type_of_rule: string
}

export interface RuleBody {
  kind: string
  type: string
  rule_profile: string
  customers: string[]
  models: string[]
  parts: string[]
  rules: RuleItem[]
}

export interface RuleDetail extends Rule {
  body: RuleBody
  source_file_id: string
  source_file_version_id: string
}

export interface RuleCandidate {
  title: string
  body: RuleBody
  source_file_version_id: string
}
