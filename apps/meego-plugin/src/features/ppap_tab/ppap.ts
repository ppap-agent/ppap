// PPAP Product API client for the Meego plugin. The host session token lives in
// memory only and is exchanged again from a fresh Meego auth code when it expires.
import { PPAP_API_URL } from './config';

export type SessionReply =
  | { state: 'connected'; token: string; expires_at: string; display_name: string; meego_name: string; web_url: string }
  | { state: 'connect_required'; connect_url: string; expires_at: string; meego_name: string };

export class PpapError extends Error {
  constructor(public code: string, public status: number) {
    super(code);
  }
}

let token = '';
let webUrl = '';

export function webBase(): string {
  return webUrl;
}

export async function startSession(): Promise<SessionReply> {
  const { code } = await window.JSSDK.utils.getAuthCode();
  const response = await fetch(`${PPAP_API_URL}/integrations/meego/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new PpapError(String(body.error || 'session_failed'), response.status);
  if (body.state === 'connected') {
    token = body.token;
    webUrl = body.web_url;
  }
  return body as SessionReply;
}

async function send(path: string, init: { method?: string; body?: unknown }, retried = false): Promise<Response> {
  const response = await fetch(`${PPAP_API_URL}${path}`, {
    method: init.method || 'GET',
    headers: { Authorization: `Bearer ${token}`, ...(init.body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (response.status === 401 && !retried) {
    const renewed = await startSession();
    if (renewed.state !== 'connected') throw new PpapError('connect_required', 401);
    return send(path, init, true);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new PpapError(String((body as { error?: string }).error || 'request_failed'), response.status);
  }
  return response;
}

export async function call<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await send(path, init);
  return (await response.json().catch(() => ({}))) as T;
}

/** Frozen originals and reports are read as bytes with the host session, never through a cookie link. */
export async function bytes(path: string): Promise<Blob> {
  return (await send(path, {})).blob();
}

export function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function newId(): string {
  return crypto.randomUUID();
}

export async function waitFor<T>(read: () => Promise<T>, done: (value: T) => boolean, onTick?: (value: T) => void, limitMs = 30 * 60 * 1000): Promise<T> {
  const started = Date.now();
  let delay = 1500;
  for (;;) {
    const value = await read();
    onTick?.(value);
    if (done(value)) return value;
    if (Date.now() - started > limitMs) throw new PpapError('wait_timeout', 0);
    await new Promise(resolve => setTimeout(resolve, delay));
    delay = Math.min(delay * 1.5, 10000);
  }
}

export function taskLink(taskId: string): string {
  const target = `${webUrl}/tasks/${encodeURIComponent(taskId)}`;
  return `${webUrl}/enter-current?return_to=${encodeURIComponent(target)}`;
}

const errorText: Record<string, string> = {
  meego_tenant_not_mapped: '这个飞书项目空间尚未开通 PPAP，请联系管理员。',
  tenant_not_allowed: '你的企业尚未开通 PPAP 新版，请联系管理员。',
  meego_disabled: 'PPAP 暂未开放飞书项目接入。',
  meego_auth_failed: '无法确认你的飞书项目身份，请刷新页面后重试。',
  meego_access_denied: '你没有这个工作项的访问权限。',
  file_too_large: '附件超过 200 MB，无法导入。',
  connect_required: '连接已失效，请重新连接 PPAP。',
  writeback_in_progress: '正在写回，请稍后刷新查看。',
  meego_writeback_unknown: '写回结果暂时无法确认，请先在评论区查看，不要重复写回。',
  round_incomplete: '审核尚未全部完成，暂时不能写回。',
  round_has_errors: '审核出现技术错误，不能写回。',
  wait_timeout: '处理时间较长，请稍后回来查看。',
  writeback_target_missing: '这一次审核不是从这个工作项发起的，不能写回。',
  access_denied: '你没有权限查看这项审核。',
  review_conflict: '这条复核刚被更新过，已为你载入最新内容，请核对后再保存。',
  invalid_review: '复核内容不完整或过长，请检查后再保存。',
  validation_failed: '有附件无法读取，请检查文件后重试。',
  task_revision_conflict: '审核内容刚发生变化，请核对后再确认。',
};

export const say = (failure: unknown) => (failure instanceof PpapError && errorText[failure.code]) || '操作没有完成，请稍后重试。';
