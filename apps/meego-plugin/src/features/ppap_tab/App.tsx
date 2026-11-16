import { useCallback, useEffect, useMemo, useState } from 'react';
import './index.css';
import { call, newId, PpapError, startSession, taskLink, waitFor } from './ppap';

type Attachment = { uid: string; name: string; size: string; content_type: string };
type FileVersion = { id: string; file_id: string; original_filename: string };
type Job = { id: string; status: string };
type TaskFile = {
  file: FileVersion;
  selection: 'selected' | 'unselected' | 'removed';
  removal_reason: string | null;
  match: { state: string; document_type: string | null; rule: { title: string } | null } | null;
  audit?: { conclusion: 'PASS' | 'NG' | 'UNKNOWN' | null; execution: string; counts: { ng_count: number; unknown_count: number } } | null;
};
type Task = {
  id: string;
  state: 'matching' | 'pending_confirm' | 'reviewing' | 'completed';
  round_id: string | null;
  completion_reason: 'no_matching_rules' | 'processed' | null;
  estimated_points: number | null;
  input_revision?: number;
  confirmation_basis_digest: string | null;
  execution?: 'queued' | 'running' | 'completed' | 'failed';
  has_execution_error?: boolean;
  progress?: { completed: number; total: number };
  files: TaskFile[];
};
type Context = { spaceId: string; workObjectId: string; workItemId: number };
type Screen =
  | { kind: 'loading'; text: string }
  | { kind: 'connect'; url: string }
  | { kind: 'error'; text: string }
  | { kind: 'ready' };

// One vocabulary with the PPAP web app.
const conclusionLabel: Record<string, string> = { PASS: '符合', NG: '不符合', UNKNOWN: '尚有未确定项' };
const matchLabel: Record<string, string> = { matched: '已匹配规则', reference: '作为参考资料', unmatched: '没有可用规则', failed: '识别失败', pending: '识别中', matching: '识别中' };
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
  round_has_errors: '审核出现技术错误，不能写回，请在 PPAP 中查看。',
  wait_timeout: '处理时间较长，请稍后回来查看。',
};
const say = (failure: unknown) => (failure instanceof PpapError && errorText[failure.code]) || '操作没有完成，请稍后重试。';
const storageKey = (ctx: Context) => `ppap.task.${ctx.spaceId}.${ctx.workItemId}`;

export default function App() {
  const [screen, setScreen] = useState<Screen>({ kind: 'loading', text: '正在连接 PPAP…' });
  const [user, setUser] = useState('');
  const [ctx, setCtx] = useState<Context | null>(null);
  const [files, setFiles] = useState<Attachment[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [task, setTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');

  const connect = useCallback(async () => {
    setScreen({ kind: 'loading', text: '正在连接 PPAP…' });
    try {
      const reply = await startSession();
      if (reply.state === 'connect_required') return setScreen({ kind: 'connect', url: reply.connect_url });
      setUser(reply.display_name);
      const context = (await window.JSSDK.tab.getContext()) as Context;
      setCtx(context);
      setScreen({ kind: 'ready' });
    } catch (failure) {
      setScreen({ kind: 'error', text: say(failure) });
    }
  }, []);

  useEffect(() => void connect(), [connect]);

  const item = useMemo(
    () => (ctx ? { project_key: ctx.spaceId, work_item_type_key: ctx.workObjectId, work_item_id: String(ctx.workItemId) } : null),
    [ctx],
  );

  const watchTask = useCallback(async (taskId: string) => {
    const done = (t: Task) => t.state === 'pending_confirm' || t.state === 'completed';
    const read = () => call<Task>(`/audit-tasks/${taskId}`);
    setTask(await waitFor(read, done, setTask));
  }, []);

  useEffect(() => {
    if (!ctx || !item) return;
    (async () => {
      try {
        const listed = await call<{ files: Attachment[] }>(`/integrations/meego/work-items/${item.project_key}/${item.work_item_type_key}/${item.work_item_id}/files`);
        setFiles(listed.files);
        setChosen(new Set(listed.files.map(f => f.uid)));
        const saved = await window.JSSDK.storage.getItem(storageKey(ctx)).catch(() => null);
        if (saved) {
          setBusy('正在读取上次的审核…');
          await watchTask(saved).catch(() => window.JSSDK.storage.removeItem(storageKey(ctx)));
        }
      } catch (failure) {
        setNotice(say(failure));
      } finally {
        setBusy('');
      }
    })();
  }, [ctx, item, watchTask]);

  async function start() {
    if (!ctx || !item || !files) return;
    setNotice('');
    try {
      const picked = files.filter(f => chosen.has(f.uid));
      const inputs: { file_version_id: string; validation_id: string; selection: 'selected'; removal_reason: null }[] = [];
      for (const [index, attachment] of picked.entries()) {
        setBusy(`正在读取附件 ${index + 1}/${picked.length}…`);
        const version = await call<FileVersion>('/integrations/meego/files/import', { method: 'POST', body: { request_id: newId(), ...item, uid: attachment.uid } });
        const job = await call<Job>('/file-validations', { method: 'POST', body: { request_id: newId(), source_file_id: version.file_id, source_file_version_id: version.id } });
        const checked = await waitFor(() => call<Job>(`/file-validations/${job.id}`), j => !['queued', 'leased', 'running'].includes(j.status));
        if (checked.status !== 'succeeded') throw new PpapError('validation_failed', 0);
        inputs.push({ file_version_id: version.id, validation_id: checked.id, selection: 'selected', removal_reason: null });
      }
      setBusy('正在识别文件类型和规则…');
      const taskId = newId();
      await call<Task>('/audit-tasks', { method: 'POST', body: { request_id: taskId, customer_preference: null, files: inputs } });
      await call(`/integrations/meego/tasks/${taskId}/work-item`, { method: 'PUT', body: item });
      await window.JSSDK.storage.setItem(storageKey(ctx), taskId);
      await watchTask(taskId);
    } catch (failure) {
      setNotice(failure instanceof PpapError && failure.code === 'validation_failed' ? '有附件无法读取，请检查文件后重试。' : say(failure));
    } finally {
      setBusy('');
    }
  }

  async function confirm() {
    if (!task || !task.confirmation_basis_digest || task.input_revision === undefined) return;
    setNotice('');
    try {
      setBusy('正在提交确认…');
      await call(`/audit-tasks/${task.id}/confirm`, {
        method: 'POST',
        body: { request_id: newId(), expected_revision: task.input_revision, expected_confirmation_basis_digest: task.confirmation_basis_digest },
      });
      setBusy('审核进行中…');
      const finished = (t: Task) => t.state === 'completed' && (t.execution === 'completed' || t.execution === 'failed' || t.completion_reason === 'no_matching_rules');
      setTask(await waitFor(() => call<Task>(`/audit-tasks/${task.id}`), finished, setTask));
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  }

  async function writeBack() {
    if (!task?.round_id) return;
    setNotice('');
    try {
      setBusy('正在写回工作项…');
      const result = await call<{ state: string }>(`/integrations/meego/rounds/${task.round_id}/writeback`, { method: 'POST' });
      setNotice(result.state === 'already_written' ? '这一轮的结论之前已经写回过评论区。' : '已把结论写入工作项评论区。');
    } catch (failure) {
      setNotice(say(failure));
    } finally {
      setBusy('');
    }
  }

  async function restart() {
    if (ctx) await window.JSSDK.storage.removeItem(storageKey(ctx)).catch(() => undefined);
    setTask(null);
    setNotice('');
  }

  async function disconnect() {
    try {
      await call('/integrations/meego/connection', { method: 'DELETE' });
    } finally {
      await connect();
    }
  }

  if (screen.kind === 'loading') return <p className="muted" role="status">{screen.text}</p>;
  if (screen.kind === 'error') return <section className="panel"><p>{screen.text}</p><button className="secondary" onClick={() => void connect()}>重试</button></section>;
  if (screen.kind === 'connect')
    return (
      <section className="panel">
        <h2>连接 PPAP</h2>
        <p>首次使用需要连接你的 PPAP 账号。审核会按你在 PPAP 的权限和额度执行。</p>
        <div className="actions">
          <button className="primary" onClick={() => window.JSSDK.navigation.open(screen.url, '_blank')}>连接 PPAP</button>
          <button className="secondary" onClick={() => void connect()}>我已完成连接</button>
        </div>
      </section>
    );

  const running = Boolean(busy);
  return (
    <section className="panel">
      <header className="header">
        <span className="muted">已连接：<span title={user}>{user}</span></span>
        <button className="link" onClick={() => void disconnect()}>断开连接</button>
      </header>
      {busy && <p role="status" className="muted">{busy}</p>}
      {notice && <p role="alert" className="notice">{notice}</p>}

      {!task && (
        <>
          <h2>选择要审核的附件</h2>
          {files === null && <p className="muted">正在读取附件…</p>}
          {files?.length === 0 && <p className="muted">这个工作项还没有附件。请先上传 PPAP 文件。</p>}
          {files && files.length > 0 && (
            <ul className="list">
              {files.map(f => (
                <li key={f.uid}>
                  <label title={f.name}>
                    <input type="checkbox" checked={chosen.has(f.uid)} disabled={running}
                      onChange={e => setChosen(prev => { const next = new Set(prev); if (e.target.checked) next.add(f.uid); else next.delete(f.uid); return next; })} />
                    <span className="name">{f.name}</span>
                    <span className="muted">{f.size}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <button className="primary" disabled={running || chosen.size === 0} onClick={() => void start()}>开始识别（{chosen.size} 份）</button>
        </>
      )}

      {task && task.state === 'matching' && <p className="muted" role="status">正在识别文件类型和规则…</p>}

      {task && task.state === 'pending_confirm' && (
        <>
          <h2>核对后开始审核</h2>
          <ul className="list">
            {task.files.map(f => (
              <li key={f.file.id}>
                <span className="name" title={f.file.original_filename}>{f.file.original_filename}</span>
                <span className="muted">
                  {f.selection === 'removed' ? `不审核：${f.removal_reason ?? ''}` : f.match?.rule ? `规则：${f.match.rule.title}` : matchLabel[f.match?.state ?? 'pending']}
                </span>
              </li>
            ))}
          </ul>
          <p className="muted">预计用量：{task.estimated_points === null ? '无法预估' : `${task.estimated_points} 点`}</p>
          <div className="actions">
            {task.confirmation_basis_digest
              ? <button className="primary" disabled={running} onClick={() => void confirm()}>确认并开始审核</button>
              : <a className="link" onClick={() => window.JSSDK.navigation.open(taskLink(task.id), '_blank')}>在 PPAP 中核对</a>}
            <button className="secondary" disabled={running} onClick={() => void restart()}>重新选择附件</button>
          </div>
        </>
      )}

      {task && (task.state === 'reviewing' || task.state === 'completed') && (
        <>
          <h2>{task.state === 'reviewing' ? '审核进行中' : '审核结果'}</h2>
          {task.progress && task.state === 'reviewing' && <p className="muted">已完成 {task.progress.completed}/{task.progress.total}</p>}
          {task.completion_reason === 'no_matching_rules' && <p>没有可用的审核规则，本次未审核、未扣点。</p>}
          {task.has_execution_error && <p className="notice">审核出现技术错误，请在 PPAP 中查看。</p>}
          <ul className="list">
            {task.files.filter(f => f.selection === 'selected').map(f => (
              <li key={f.file.id}>
                <span className="name" title={f.file.original_filename}>{f.file.original_filename}</span>
                <span className={`badge ${f.audit?.conclusion ?? 'pending'}`}>
                  {f.audit?.conclusion ? conclusionLabel[f.audit.conclusion] : f.audit?.execution === 'review_failed' ? '审核异常' : '审核中'}
                </span>
              </li>
            ))}
          </ul>
          <div className="actions">
            {task.state === 'completed' && task.round_id && !task.has_execution_error && (
              <button className="primary" disabled={running} onClick={() => void writeBack()}>写回工作项</button>
            )}
            <a className="link" onClick={() => window.JSSDK.navigation.open(taskLink(task.id), '_blank')}>在 PPAP 中查看详情</a>
            {task.state === 'completed' && <button className="secondary" disabled={running} onClick={() => void restart()}>重新审核</button>}
          </div>
        </>
      )}
    </section>
  );
}
