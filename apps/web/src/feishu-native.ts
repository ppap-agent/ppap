// Feishu client capabilities (JSAPI) for pages opened inside Feishu. Each call answers null when the
// capability is not there or fails, and the caller keeps its web version; nothing here is required.
import {request} from './api.ts'
import {isFeishuHost} from './hostLogin.ts'
import type {components} from './generated/openapi.ts'

type JsapiConfig = components['schemas']['FeishuJsapiConfig']
type DriveFile = components['schemas']['DriveFile']
type ShareTarget = components['schemas']['AuditShareTargetReference'] & {display_name: string}
type Callbacks<T> = {success?: (result: T) => void; fail?: (error: unknown) => void}
type FeishuWindow = Window & {
  h5sdk?: {ready?: (callback: () => void) => void; config?: (options: Record<string, unknown>) => void}
  tt?: {
    docsPicker?: (options: Callbacks<{fileList?: {filePath?: string; fileName?: string}[]}> & Record<string, unknown>) => void
    chooseContact?: (options: Callbacks<{data?: {openId?: string; name?: string}[]}> & Record<string, unknown>) => void
    chooseChat?: (options: Callbacks<{data?: {id?: string; name?: string; chatType?: number}[]}> & Record<string, unknown>) => void
    showToast?: (options: {title: string; icon?: string; duration?: number} & Callbacks<unknown>) => void
  }
}

const JSAPI_LIST = ['docsPicker', 'chooseContact', 'chooseChat', 'showToast']
let configured: {url: string; at: number; done: Promise<boolean>} | null = null

/** The page's Feishu authentication (h5sdk.config), signed by our server for this page address. */
function authenticated(win: FeishuWindow = window): Promise<boolean> {
  if (!isFeishuHost(win) || !win.h5sdk?.config) return Promise.resolve(false)
  const url = win.location.href.split('#')[0]
  // A signature lasts about 10 minutes and belongs to one address.
  if (configured && configured.url === url && Date.now() - configured.at < 8 * 60 * 1000) return configured.done
  const done = (async () => {
    try {
      const config = await request<JsapiConfig>(`/integrations/feishu/jsapi-config?url=${encodeURIComponent(url)}`)
      await new Promise<void>(resolve => win.h5sdk?.ready ? win.h5sdk.ready(resolve) : resolve())
      return await new Promise<boolean>(resolve => {
        const timer = win.setTimeout(() => resolve(false), 10000)
        win.h5sdk!.config!({appId: config.app_id, timestamp: config.timestamp, nonceStr: config.nonce_str, signature: config.signature, jsApiList: JSAPI_LIST,
          onSuccess: () => {win.clearTimeout(timer); resolve(true)}, onFail: () => {win.clearTimeout(timer); resolve(false)}})
      })
    } catch {
      return false
    }
  })()
  configured = {url, at: Date.now(), done}
  void done.then(ok => {if (!ok && configured?.done === done) configured = null})
  return done
}

/** null when the picker was closed or refused after it opened: the person changed their mind, so no web fallback. */
function call<T>(invoke: (callbacks: Required<Callbacks<T>>) => void): Promise<T | null> {
  return new Promise(resolve => {
    try {invoke({success: resolve, fail: () => resolve(null)})} catch {resolve(null)}
  })
}

/** What our import accepts, as in the web picker: Feishu documents and Excel, Word or PDF files. */
export const importableDriveFile = (file: DriveFile) => ['sheet', 'bitable', 'doc', 'docx'].includes(file.type) || file.type === 'file' && /\.(xlsx|xls|xlsm|doc|docx|pdf)$/i.test(file.name)

const driveTypes: Record<string, string> = {docx: 'docx', docs: 'doc', doc: 'doc', sheets: 'sheet', base: 'bitable', file: 'file'}

/** A cloud document address as the drive file our import accepts; knowledge-base pages have no file token. */
export function driveFileFromAddress(address: string, name: string): DriveFile | null {
  try {
    const [kind, token] = new URL(address).pathname.split('/').filter(Boolean)
    const type = driveTypes[kind]
    return type && token && /^[A-Za-z0-9_-]+$/.test(token) ? {token, type, name: name || token} : null
  } catch {
    return null
  }
}

/** Feishu's own cloud document picker. null: not available here (use the web picker); empty when closed. */
export async function pickFeishuDocuments(win: FeishuWindow = window): Promise<{files: DriveFile[]; skipped: string[]} | null> {
  if (!win.tt?.docsPicker || !await authenticated(win)) return null
  const result = await call<{fileList?: {filePath?: string; fileName?: string}[]}>(callbacks => win.tt!.docsPicker!({pickerTitle: '选择要审核的文件', pickerConfirm: '添加', ...callbacks}))
  const files: DriveFile[] = [], skipped: string[] = []
  for (const item of result?.fileList ?? []) {
    const file = item.filePath ? driveFileFromAddress(item.filePath, item.fileName ?? '') : null
    if (file && importableDriveFile(file)) files.push(file); else skipped.push(item.fileName || '未命名文件')
  }
  return {files, skipped}
}

/** Feishu's contact or group picker for sharing. null: not available here (use the web picker); empty when closed. */
export async function chooseFeishuTargets(kind: 'user' | 'group', win: FeishuWindow = window): Promise<ShareTarget[] | null> {
  if (!await authenticated(win)) return null
  if (kind === 'user') {
    if (!win.tt?.chooseContact) return null
    const result = await call<{data?: {openId?: string; name?: string}[]}>(callbacks => win.tt!.chooseContact!({multi: true, externalContact: false, enableChooseDepartment: false, ...callbacks}))
    return (result?.data ?? []).filter(row => row.openId).map(row => ({id: row.openId!, kind: 'user' as const, display_name: row.name || '成员'}))
  }
  if (!win.tt?.chooseChat) return null
  const result = await call<{data?: {id?: string; name?: string; chatType?: number}[]}>(callbacks => win.tt!.chooseChat!({multiSelect: true, selectType: 1, allowCreateGroup: false, externalChat: false, ...callbacks}))
  return (result?.data ?? []).filter(row => row.id && row.chatType === 1).map(row => ({id: row.id!, kind: 'group' as const, display_name: row.name || '群组'}))
}

/** Feishu's own toast; false when the page should show its own. */
export async function feishuToast(text: string, win: FeishuWindow = window): Promise<boolean> {
  if (!win.tt?.showToast || !await authenticated(win)) return false
  return Boolean(await call<unknown>(callbacks => win.tt!.showToast!({title: text, icon: 'success', duration: 2000, ...callbacks})))
}
