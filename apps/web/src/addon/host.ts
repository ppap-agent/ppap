// The Feishu document the panel lives in. Everything a person can do natively in the
// document (messages, the document's own blocks, its theme and permissions) goes through
// here; a capability the host lacks is reported as absent, never faked.
import {BlockitClient as PackagedBlockitClient, type BlockSnapshot, type DocMiniApp, type TextualBlockData} from '@lark-opdev/block-docs-addon-api'

export type Notice = 'info' | 'success' | 'warning' | 'error'
export type AddonHost = {
  login: () => Promise<unknown>
  documentToken: () => Promise<string | undefined>
  notify?: (type: Notice, message: string) => void
  watchDarkMode?: (onChange: (dark: boolean) => void) => void
  canEdit?: () => Promise<boolean>
  documentText?: () => Promise<string>
  insertMarkdown?: (markdown: string) => Promise<boolean>
  reveal?: (heading: string) => Promise<boolean>
  onDocumentChange?: (onChange: () => void) => () => void
  open?: (url: string) => void
}

declare global {
  interface Window {
    __PPAP_DOCS_ADDON_HOST__?: AddonHost
    BlockitClient?: new () => {initAPI: () => DocMiniApp}
  }
}

export function hostUnavailable() {
  const error = new Error('请在飞书云文档中打开 PPAP Audit。')
  ;(error as Error & {reason?: string}).reason = 'host-unavailable'
  return error
}

export async function resolveHost(): Promise<AddonHost> {
  if (window.__PPAP_DOCS_ADDON_HOST__) return window.__PPAP_DOCS_ADDON_HOST__
  let framed = false
  try { framed = window.self !== window.top } catch { framed = true }
  if (!framed) throw hostUnavailable()
  const Client = window.BlockitClient || PackagedBlockitClient
  if (typeof Client !== 'function') throw hostUnavailable()
  const api = new Client().initAPI()
  const reference = () => api.getActiveDocumentRef()
  const heading = async (text: string): Promise<BlockSnapshot | undefined> => {
    const root = await api.Document.getRootBlock(await reference())
    return (root.childSnapshots || []).find(block => {
      try { return api.Block.TextualBlock.getPlainText(block.data as TextualBlockData).includes(text) } catch { return false }
    })
  }
  return {
    login: () => api.Service.User.login(),
    documentToken: async () => (await reference())?.docToken,
    notify: (type, message) => { void api.View.Action.showMessage({type, message, duration: type === 'error' ? 6000 : 3000}).catch(() => undefined) },
    watchDarkMode: onChange => {
      void api.Env.DarkMode.getIsDarkMode().then(onChange, () => undefined)
      void api.Env.DarkMode.onDarkModeChange(onChange).catch(() => undefined)
    },
    canEdit: async () => (await api.Service.Permission.getDocumentPermission(await reference())).editable,
    documentText: () => api.Document.getDocAsPlainText(),
    insertMarkdown: async markdown => {
      const root = await api.Document.getRootBlock(await reference())
      return Boolean(await api.Document.insertBlocksByMarkdown(markdown, root.ref))
    },
    reveal: async text => {
      const block = await heading(text)
      if (!block) return false
      await api.Viewport.scrollToBlock(block.ref, true)
      return true
    },
    onDocumentChange: onChange => {
      let timer = 0
      const handler = () => { window.clearTimeout(timer); timer = window.setTimeout(onChange, 1500) }
      void reference().then(ref => api.Events.onDocumentChange(ref, handler)).catch(() => undefined)
      return () => { window.clearTimeout(timer); void reference().then(ref => api.Events.offDocumentChange(ref, handler)).catch(() => undefined) }
    },
    open: url => { void api.Service.Hyperlink.open(url).catch(() => window.open(url, '_blank', 'noopener')) },
  }
}
