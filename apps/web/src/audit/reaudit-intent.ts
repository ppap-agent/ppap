import type {components} from '../generated/openapi.ts'

type ReauditFile = components['schemas']['AuditTaskFile']
type RuleOption = components['schemas']['MatchingCandidate']
export type RuleVersionChoice = 'newer' | 'original'

/** A re-audit file's rule options are the original audit's rule version, then a newer version when that rule has one. */
export function newerRuleOf(file: ReauditFile): RuleOption | undefined {
  return !file.reference_only && file.rule_options && file.rule_options.length > 1 ? file.rule_options[1] : undefined
}

/** Points a file with a newer rule version at the chosen version; other files keep their rule. */
export function withRuleVersion(file: ReauditFile, choice: RuleVersionChoice): ReauditFile {
  const newer = newerRuleOf(file)
  if (!newer) return file
  return {...file, preferred_rule_version_id: choice === 'newer' ? newer.id : file.rule_options![0].id}
}

/** Which version the selected files with a newer rule use; null when they differ (one was changed by hand) or there are none. */
export function ruleVersionChoice(files: ReauditFile[]): RuleVersionChoice | null {
  const versioned = files.filter(file => file.selection === 'selected' && newerRuleOf(file))
  if (!versioned.length) return null
  if (versioned.every(file => file.preferred_rule_version_id === newerRuleOf(file)!.id)) return 'newer'
  if (versioned.every(file => file.preferred_rule_version_id === file.rule_options![0].id)) return 'original'
  return null
}

/** The rule's own name: the option titles carry which version they are. */
export function ruleName(option: RuleOption): string {
  return option.title.replace(/ · (?:原审核版本|较新版本)$/, '')
}
