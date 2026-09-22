const HOST_TIMEOUT_MS = 31000
const OAUTH_SCOPE = [
  'offline_access',
  'auth:user.id:read',
  'tenant:tenant:readonly',
  'docs:doc',
  'drive:drive',
  'docx:document:readonly',
  'docx:document:write_only',
  'partner_ai:credential:write',
  'partner_ai:tenant_ai_item:read',
  'partner_ai:usage_record:write',
]

type HostWindow = Window & {
  h5sdk?: {ready?: (cb: () => void) => void; error?: (cb: (err: unknown) => void) => void}
  tt?: {requestAccess?: (options: {
    appID: string
    scopeList: string[]
    success: (result: {code?: string}) => void
    fail: (err: unknown) => void
  }) => void}
  __PPAP_FEISHU_HOST__?: {login: () => Promise<string>}
}

export function isFeishuHost(win: Window = window): boolean {
  const host = win as HostWindow
  if (host.__PPAP_FEISHU_HOST__) return true
  return Boolean(host.h5sdk && host.tt && typeof host.tt.requestAccess === 'function')
}

function hostUnavailable(): Error {
  return new Error('当前环境不是飞书宿主')
}

async function waitForSdk(win: HostWindow): Promise<void> {
  if (!win.h5sdk?.ready) return
  await new Promise<void>((resolve, reject) => {
    const timer = win.setTimeout(() => reject(new Error('飞书登录初始化超时，请稍后重试')), HOST_TIMEOUT_MS)
    try {
      win.h5sdk?.ready?.(() => { win.clearTimeout(timer); resolve() })
    } catch {
      win.clearTimeout(timer)
      reject(new Error('飞书登录初始化失败，请稍后重试'))
    }
  })
}

export async function requestHostCode(appId: string, win: Window = window): Promise<string> {
  const host = win as HostWindow
  if (host.__PPAP_FEISHU_HOST__ && typeof host.__PPAP_FEISHU_HOST__.login === 'function') {
    const code = String(await host.__PPAP_FEISHU_HOST__.login() || '').trim()
    if (!code) throw new Error('未获取到飞书授权码，请重试')
    return code
  }
  if (!appId.trim()) throw hostUnavailable()
  if (!host.tt?.requestAccess) throw hostUnavailable()
  await waitForSdk(host)
  return new Promise<string>((resolve, reject) => {
    try {
      host.tt?.requestAccess?.({
        appID: appId.trim(),
        scopeList: OAUTH_SCOPE,
        success(result) {
          const code = typeof result?.code === 'string' ? result.code.trim() : ''
          if (!code) { reject(new Error('未获取到飞书授权码，请重试')); return }
          resolve(code)
        },
        fail() { reject(new Error('飞书授权失败，请稍后重试')) },
      })
    } catch {
      reject(new Error('飞书授权失败，请稍后重试'))
    }
  })
}
