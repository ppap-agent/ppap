import assert from 'node:assert/strict'
import test from 'node:test'
import {chooseFeishuTargets, driveFileFromAddress, importableDriveFile, pickFeishuDocuments} from './feishu-native.ts'

test('a picked cloud document address becomes the drive file our import accepts', () => {
  assert.deepEqual(driveFileFromAddress('https://example.feishu.cn/docx/doxcnAbc123?from=picker', '控制计划'), {token: 'doxcnAbc123', type: 'docx', name: '控制计划'})
  assert.deepEqual(driveFileFromAddress('https://example.feishu.cn/sheets/shtcnX_9', ''), {token: 'shtcnX_9', type: 'sheet', name: 'shtcnX_9'})
  assert.equal(driveFileFromAddress('https://example.feishu.cn/base/bascnQ', 'B')?.type, 'bitable')
  assert.equal(driveFileFromAddress('https://example.feishu.cn/file/boxcnQ', 'a.pdf')?.type, 'file')
  // Knowledge-base pages carry a node token, not a file token.
  assert.equal(driveFileFromAddress('https://example.feishu.cn/wiki/wikcnQ', 'Wiki'), null)
  assert.equal(driveFileFromAddress('not a url', 'x'), null)
})

test('only Feishu documents and Excel, Word or PDF files are importable', () => {
  assert.equal(importableDriveFile({token: 't', type: 'docx', name: 'a'}), true)
  assert.equal(importableDriveFile({token: 't', type: 'file', name: 'a.XLSX'}), true)
  assert.equal(importableDriveFile({token: 't', type: 'file', name: 'a.png'}), false)
  assert.equal(importableDriveFile({token: 't', type: 'mindnote', name: 'a'}), false)
})

test('outside Feishu nothing is called and the web pickers stay in charge', async () => {
  const browser = {location: {href: 'https://ppap.example.com/tasks/new'}} as unknown as Window
  assert.equal(await pickFeishuDocuments(browser), null)
  assert.equal(await chooseFeishuTargets('user', browser), null)
})
