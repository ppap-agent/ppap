// Shows where in the frozen original a result points. Every byte comes from the audit's own
// file version through the host session; a work item's current attachment is never read here.
import { Banner, Button, Image, Space, Spin, Typography } from '@douyinfe/semi-ui';

const { Text } = Typography;
import { useEffect, useId, useRef, useState } from 'react';
import type { FileVersion } from './model';
import { bytes, call, PpapError, save, say, webBase } from './ppap';

type Source = { roundId: string; file: FileVersion };
const base = (source: Source) => `/rounds/${source.roundId}/files/${source.file.file_id}/versions/${source.file.id}`;
const extension = (name: string) => name.slice(name.lastIndexOf('.') + 1).toLowerCase();
const images = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];

export function OriginalView({ source, page = 1 }: { source: Source; page?: number }) {
  const kind = extension(source.file.original_filename);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState('');
  async function download() {
    setSaving(true);
    setFailure('');
    try {
      save(await bytes(`${base(source)}/download`), source.file.original_filename);
    } catch (cause) {
      setFailure(say(cause));
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="original" aria-label="原件">
      <header className="original-head">
        <Text strong ellipsis={{ showTooltip: true }}>{source.file.original_filename}</Text>
        <Button size="small" theme="borderless" loading={saving} onClick={() => void download()}>下载原件</Button>
      </header>
      {failure && <Banner type="warning" fullMode={false} closeIcon={null} description={failure} />}
      {kind === 'pdf' ? (
        <PdfView key={source.file.id + ':' + page} name={source.file.original_filename} load={() => bytes(`${base(source)}/download`)} page={page} />
      ) : images.includes(kind) ? (
        <Picture key={source.file.id} source={source} />
      ) : (
        <OfficeView key={source.file.id} source={source} />
      )}
    </section>
  );
}

// Office files -----------------------------------------------------------

// The same OnlyOffice view the PPAP web app uses: PPAP signs a read-only config for this one
// frozen version, and the document server renders the real layout.
type OfficeReply = { available: boolean; notice?: string; script_url?: string; config?: Record<string, unknown> };
type Editor = { destroyEditor: () => void };
type DocsApi = { DocEditor: new (id: string, config: Record<string, unknown>) => Editor };

let script: Promise<void> | undefined;
function loadEditor(src: string): Promise<void> {
  if (script) return script;
  script = new Promise<void>((resolve, reject) => {
    const tag = document.createElement('script');
    tag.src = src;
    tag.async = true;
    tag.referrerPolicy = 'no-referrer';
    const fail = () => {
      clearTimeout(timer);
      tag.remove();
      script = undefined;
      reject(new Error('office_unavailable'));
    };
    const timer = setTimeout(fail, 15000);
    tag.onload = () => {
      clearTimeout(timer);
      resolve();
    };
    tag.onerror = fail;
    document.head.append(tag);
  });
  return script;
}

function OfficeView({ source }: { source: Source }) {
  const id = 'office-' + useId().replace(/:/g, '');
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let live = true;
    let editor: Editor | undefined;
    const failed = (text: string) => {
      if (!live) return;
      setNotice(text);
      setState('failed');
    };
    void (async () => {
      try {
        const reply = await call<OfficeReply>(`/files/${source.file.file_id}/versions/${source.file.id}/office?round_id=${encodeURIComponent(source.roundId)}`);
        if (!live) return;
        if (!reply.available || !reply.script_url || !reply.config) return failed(reply.notice || '这份文件不能在这里显示，请下载原件核对。');
        await loadEditor(reply.script_url);
        const api = (window as { DocsAPI?: DocsApi }).DocsAPI;
        if (!live) return;
        if (!api) return failed('原件预览暂时不可用，请下载原件核对。');
        editor = new api.DocEditor(id, { ...reply.config, events: { onDocumentReady: () => live && setState('ready'), onError: () => failed('原件预览出错，请下载原件核对。') } });
      } catch (cause) {
        failed(cause instanceof PpapError ? say(cause) : '原件预览暂时不可用，请下载原件核对。');
      }
    })();
    return () => {
      live = false;
      editor?.destroyEditor();
    };
  }, [source.file.id, source.file.file_id, source.roundId, id]);
  if (state === 'failed') return <Text type="tertiary">{notice}</Text>;
  return (
    <div className="office">
      {state === 'loading' && <div className="office-wait" role="status"><Text type="tertiary"><Spin size="small" /> 正在打开原件…</Text></div>}
      <div id={id} />
    </div>
  );
}

// Pictures ----------------------------------------------------------------

function Picture({ source }: { source: Source }) {
  const [url, setUrl] = useState('');
  const [failure, setFailure] = useState('');
  useEffect(() => {
    let made = '';
    let live = true;
    bytes(`${base(source)}/download`).then(
      blob => {
        if (!live) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      },
      cause => live && setFailure(say(cause)),
    );
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [source]);
  if (failure) return <Text type="tertiary">{failure}</Text>;
  if (!url) return <Text type="tertiary" role="status"><Spin size="small" /> 正在打开原件…</Text>;
  return <Image src={url} alt={source.file.original_filename} width="100%" />;
}

// PDF -------------------------------------------------------------------

type PdfDocument = { numPages: number; getPage(page: number): Promise<any>; destroy(): Promise<void> };

async function openPdf(data: ArrayBuffer): Promise<PdfDocument> {
  // The same pdf.js the PPAP web app uses. The parser runs on the page's own thread, so the
  // plugin host needs no separate worker file.
  const [pdfjs, worker] = await Promise.all([import('pdfjs-dist/legacy/build/pdf.mjs'), import('pdfjs-dist/legacy/build/pdf.worker.mjs')]);
  (globalThis as { pdfjsWorker?: unknown }).pdfjsWorker = worker;
  const assets = (folder: string) => `${webBase()}/pdfjs/${folder}/`;
  return pdfjs.getDocument({ data, cMapUrl: assets('cmaps'), cMapPacked: true, standardFontDataUrl: assets('standard_fonts'), stopAtErrors: false }).promise as unknown as Promise<PdfDocument>;
}

export function PdfView({ load, page: wanted = 1, name }: { load: () => Promise<Blob>; page?: number; name: string }) {
  const [pdf, setPdf] = useState<PdfDocument | null>(null);
  const [page, setPage] = useState(wanted);
  const [width, setWidth] = useState(0);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(true);
  const frame = useRef<HTMLDivElement>(null);
  const paper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let current = true;
    let opened: PdfDocument | null = null;
    load()
      .then(blob => blob.arrayBuffer())
      .then(openPdf)
      .then(value => {
        opened = value;
        if (current) setPdf(value);
        else void value.destroy();
      })
      .catch(() => current && setFailure('这份 PDF 无法在这里显示，请下载后核对。'));
    return () => {
      current = false;
      void opened?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!frame.current) return;
    const observer = new ResizeObserver(entries => setWidth(Math.floor(entries[0].contentRect.width)));
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, []);
  const shown = pdf ? Math.min(Math.max(1, page), pdf.numPages) : page;
  useEffect(() => {
    if (!pdf || !width || !paper.current) return;
    let current = true;
    let task: { promise: Promise<void>; cancel(): void } | undefined;
    const host = paper.current;
    setBusy(true);
    setFailure('');
    pdf
      .getPage(shown)
      .then(async sheet => {
        if (!current) return;
        const natural = sheet.getViewport({ scale: 1 });
        const view = sheet.getViewport({ scale: Math.max(120, width - 2) / natural.width });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const canvas = document.createElement('canvas');
        canvas.width = Math.ceil(view.width * ratio);
        canvas.height = Math.ceil(view.height * ratio);
        canvas.style.width = `${view.width}px`;
        canvas.style.height = `${view.height}px`;
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', `${name} · 第 ${shown} 页`);
        task = sheet.render({ canvas, canvasContext: canvas.getContext('2d'), viewport: view, transform: [ratio, 0, 0, ratio, 0, 0] });
        await task!.promise;
        if (current) {
          host.replaceChildren(canvas);
          setBusy(false);
        }
      })
      .catch(() => {
        if (current) {
          setBusy(false);
          setFailure(`第 ${shown} 页无法显示，请下载后核对。`);
        }
      });
    return () => {
      current = false;
      task?.cancel();
    };
  }, [pdf, shown, width, name]);

  return (
    <div className="pdf" ref={frame}>
      {failure && <Banner type="warning" fullMode={false} closeIcon={null} description={failure} />}
      {pdf && pdf.numPages > 1 && (
        <Space className="pager">
          <Button size="small" disabled={shown <= 1} onClick={() => setPage(shown - 1)}>上一页</Button>
          <Text type="tertiary">第 {shown} / {pdf.numPages} 页</Text>
          <Button size="small" disabled={shown >= pdf.numPages} onClick={() => setPage(shown + 1)}>下一页</Button>
        </Space>
      )}
      {busy && !failure && <Text type="tertiary" role="status"><Spin size="small" /> 正在显示…</Text>}
      <div className="paper" ref={paper} />
    </div>
  );
}
