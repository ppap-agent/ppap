import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {newerRuleOf, ruleName, ruleVersionChoice, withRuleVersion} from './reaudit-intent.ts'

type File = components['schemas']['AuditTaskFile']
const versioned = {file: {id: 'file-a'}, selection: 'selected', removal_reason: null, preferred_rule_version_id: 'old-rule', rule_options: [{id: 'old-rule', title: '控制计划审核规则 · 原审核版本'}, {id: 'new-rule', title: '控制计划审核规则 · 较新版本'}], replacement_of_file_version_id: 'original-a'} as File
const unchanged = {...versioned, file: {id: 'file-b'}, preferred_rule_version_id: 'same-rule', rule_options: [{id: 'same-rule', title: 'PFD 审核规则 · 原审核版本'}]} as File

test('a newer rule version is chosen for every file that has one, and can be switched back', () => {
  const newer = [versioned, unchanged].map(file => withRuleVersion(file, 'newer'))
  assert.deepEqual(newer.map(file => file.preferred_rule_version_id), ['new-rule', 'same-rule'])
  assert.equal(ruleVersionChoice(newer), 'newer')
  assert.equal(newer[0].replacement_of_file_version_id, 'original-a')
  const original = newer.map(file => withRuleVersion(file, 'original'))
  assert.deepEqual(original.map(file => file.preferred_rule_version_id), ['old-rule', 'same-rule'])
  assert.equal(ruleVersionChoice(original), 'original')
  assert.equal(versioned.preferred_rule_version_id, 'old-rule')
})

test('without a newer version, or with a rule changed by hand, there is no version choice', () => {
  assert.equal(ruleVersionChoice([unchanged]), null)
  assert.equal(newerRuleOf({...versioned, reference_only: true}), undefined)
  assert.equal(ruleVersionChoice([{...versioned, preferred_rule_version_id: 'other-rule'}]), null)
  assert.equal(ruleName(versioned.rule_options![1]), '控制计划审核规则')
})
