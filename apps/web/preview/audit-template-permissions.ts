import type {AuditTemplatePermissionAdapter, PermissionFolder, PermissionReceipt, TemplatePermissions} from '../src/audit/template-permission-contract'
import {sameSnapshot} from '../src/audit/result-projection'
import {targetKey} from '../src/audit/sharing-contract'

/** Service-owned choices for UI design only. No Drive ACL endpoints are called. */
export function fixtureTemplatePermissions(scene: string): AuditTemplatePermissionAdapter {
  const states = new Map<string, {revision: number; members: TemplatePermissions['members']}>(), jobs = new Map<string, PermissionReceipt>()
  function snapshot(folder: PermissionFolder, query: string): TemplatePermissions {
    const key = JSON.stringify(folder)
    if (!states.has(key)) states.set(key, {revision: 1, members: [{target: {kind: 'user', id: 'creator'}, display_name: '示例创建人', role: '所有者', source: 'owner'}, {target: {kind: 'user', id: 'wang'}, display_name: '示例王工', role: '可编辑', source: 'direct'}, {target: {kind: 'group', id: 'quality'}, display_name: '示例质量群', role: '可查看', source: 'inherited'}]})
    const state = states.get(key)!, revision = `preview-acl-${state.revision}`, allowed = scene !== 'templates-permissions-denied' && !(['templates-permissions-revoked', 'templates-permissions-restore-denied'].includes(scene) && state.revision > 1)
    const members = state.members.filter(member => member.display_name.includes(query))
    const actions: TemplatePermissions['actions'] = allowed ? members.filter(member => member.source === 'direct').flatMap(member => [{id: `${revision}:${member.target.id}:change`, target: member.target, target_name: member.display_name, before: member.role, after: member.role === '可编辑' ? '可查看' : '可编辑', label: member.role === '可编辑' ? '改为可查看' : '改为可编辑'}, {id: `${revision}:${member.target.id}:remove`, target: member.target, target_name: member.display_name, before: member.role, after: null, label: '移除协作者'}]) : []
    if (allowed && query && '示例陈工'.includes(query) && !state.members.some(member => member.target.id === 'chen')) actions.push({id: `${revision}:chen:add`, target: {kind: 'user', id: 'chen'}, target_name: '示例陈工', before: null, after: '可查看', label: '添加为可查看'})
    return {folder: structuredClone(folder), permission_revision: revision, query, creator_name: '示例创建人', manage_access: {state: allowed ? 'allowed' : 'denied', reason: allowed ? null : '管理权限已撤销。'}, members: structuredClone(members), actions}
  }
  return {
    readPermissions: async (folder, query) => snapshot(folder, query),
    apply: async request => {
      const existing = jobs.get(request.request_id)
      if (existing) return structuredClone(existing)
      const current = snapshot(request.folder, ''), state = states.get(JSON.stringify(request.folder))!
      const authorized = snapshot(request.folder, request.action.target_name).actions.some(action => sameSnapshot(action, request.action))
      let receipt: PermissionReceipt = {request: structuredClone(request), state: 'applied', result_revision: null, reason: null}
      if (request.permission_revision !== current.permission_revision || scene === 'templates-permissions-conflict') {state.revision += 1; receipt = {...receipt, state: 'conflict', reason: '其他管理员已修改权限，请重新核对。'}}
      else if (!authorized || scene === 'templates-permissions-revoked') {state.revision += 1; receipt = {...receipt, state: 'rejected', reason: '无权修改此文件夹的权限。'}}
      else {
        state.members = state.members.filter(member => targetKey(member.target) !== targetKey(request.action.target))
        if (request.action.after) state.members.push({target: structuredClone(request.action.target), display_name: request.action.target_name, role: request.action.after, source: 'direct'})
        state.revision += 1; receipt.result_revision = `preview-acl-${state.revision}`
      }
      jobs.set(request.request_id, receipt)
      if (['templates-permissions-unknown', 'templates-permissions-restore-denied'].includes(scene)) throw new Error('preview_permission_receipt_lost')
      return structuredClone(receipt)
    },
    readChange: async request => {
      const receipt = jobs.get(request.request_id)
      if (!receipt || !sameSnapshot(receipt.request, request)) throw new Error('unknown_permission_request')
      return structuredClone(receipt)
    },
  }
}
