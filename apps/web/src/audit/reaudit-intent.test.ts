import assert from 'node:assert/strict'
import test from 'node:test'
import type {components} from '../generated/openapi.ts'
import {chooseReauditRecognition, reauditRecognition} from './reaudit-intent.ts'

type File = components['schemas']['AuditTaskFile']
const original = {file: {id: 'file-a'}, selection: 'selected', removal_reason: null, preferred_rule_version_id: 'old-rule', rule_options: [{id: 'old-rule', title: '原审核版本'}, {id: 'new-rule', title: '较新版本'}], replacement_of_file_version_id: 'original-a'} as File

test('re-audit choices retain applicability without offering old and new execution versions', () => {
  assert.equal(reauditRecognition(original), 'source')
  const rematch = chooseReauditRecognition(original, 'rematch')
  assert.equal(reauditRecognition(rematch), 'rematch')
  assert.equal(rematch.preferred_rule_version_id, undefined)
  assert.equal(rematch.file, original.file)
  assert.equal(rematch.rule_options, original.rule_options)
  assert.equal(rematch.replacement_of_file_version_id, 'original-a')
  assert.equal(chooseReauditRecognition(rematch, 'source').preferred_rule_version_id, 'old-rule')
  assert.equal(original.preferred_rule_version_id, 'old-rule')
})
test('an explicitly selected applicable rule survives readback without being changed to a descendant', () => {
  const selected = {...original, preferred_rule_version_id: 'different-applicable-rule'}
  assert.equal(reauditRecognition(selected), 'selected')
  assert.equal(chooseReauditRecognition(selected, 'selected'), selected)
  assert.equal(chooseReauditRecognition(selected, 'rematch').preferred_rule_version_id, undefined)
  assert.throws(() => chooseReauditRecognition({...original, rule_options: []}, 'source'))
})
