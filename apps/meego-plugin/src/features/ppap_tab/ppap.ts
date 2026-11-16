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

export async function call<T>(path: string, init: { method?: string; body?: unknown } = {}, retried = false): Promise<T> {
  const response = await fetch(`${PPAP_API_URL}${path}`, {
    method: init.method || 'GET',
    headers: { Authorization: `Bearer ${token}`, ...(init.body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (response.status === 401 && !retried) {
    const renewed = await startSession();
    if (renewed.state !== 'connected') throw new PpapError('connect_required', 401);
    return call<T>(path, init, true);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new PpapError(String((body as { error?: string }).error || 'request_failed'), response.status);
  return body as T;
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
