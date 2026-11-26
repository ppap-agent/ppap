import {openedInFeishu} from './hostLogin.ts'

/**
 * What the place the page is opened in can do, known at first render (apps/web/README「窄屏不另起一套」).
 * Inside the Feishu phone client there is no local file picker worth a PPAP batch and no browser download:
 * files come from Feishu, and reports are read in the page, written to a Feishu document or shared in Feishu.
 * Layout never depends on this; it only hides the entries the host cannot carry.
 */
export type HostCapabilities = {localFiles: boolean; browserDownload: boolean}

export function hostCapabilities(win: Window = window): HostCapabilities {
  const phone = openedInFeishu(win) && /\b(?:Android|iPhone|iPad|iPod)\b|Mobile\//.test(win.navigator.userAgent)
  return {localFiles: !phone, browserDownload: !phone}
}


/** This page's host, fixed for the page's life. */
export const host = typeof window === 'undefined' ? {localFiles: true, browserDownload: true} : hostCapabilities()
