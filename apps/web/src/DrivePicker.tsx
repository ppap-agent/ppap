import {useEffect, useRef, useState} from 'react'
import {request, type Material} from './api'
import Modal from './Modal'
import {Button, Checkbox, Field, FilterBar, Input, Select, Stack, Text} from './ui'

interface DriveFile {token: string; name: string; type: string}

export default function DrivePicker({disabled, onImport, onBusy}: {disabled: boolean; onImport: (material: Material) => Promise<void>; onBusy: (value: boolean) => void}) {
  const [opened, setOpened] = useState(false)
  const [folder, setFolder] = useState('')
  const [files, setFiles] = useState<DriveFile[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [role, setRole] = useState<Material['role']>('target')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  const browseController = useRef<AbortController | undefined>(undefined)
  useEffect(() => () => browseController.current?.abort(), [])

  const key = (file: DriveFile) => `${file.type}:${file.token}`
  const supported = (file: DriveFile) => ['file', 'doc', 'docx', 'sheet', 'bitable'].includes(file.type)
  function close() {
    if (importing) return
    setOpened(false)
    browseController.current?.abort()
  }
  async function action(work: () => Promise<void>) {
    if (busy || disabled) return
    setBusy(true)
    onBusy(true)
    try { await work() }
    catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) setMessage(e instanceof Error ? e.message : '飞书操作失败')
    }
    finally { setBusy(false); onBusy(false) }
  }
  async function browse(reset = true, tokenValue = folder) {
    await action(async () => {
      let token = tokenValue.trim()
      if (token.includes('://')) {
        const url = new URL(token)
        const match = url.pathname.match(/^\/drive\/folder\/([A-Za-z0-9_-]+)\/?$/)
        if (url.protocol !== 'https:' || !url.hostname.endsWith('.feishu.cn') || !match) throw new Error('请输入飞书文件夹链接或 token。')
        token = match[1]
      }
      const query = new URLSearchParams({folder_token: token})
      if (!reset && next) query.set('page_token', next)
      browseController.current = new AbortController()
      setMessage('正在读取云空间…')
      const result = await request<{files: DriveFile[]; next_page_token?: string}>(`/integrations/feishu/files?${query}`, 'GET', undefined, browseController.current.signal)
      if (reset) setSelected([])
      setFiles(current => {
        const nextFiles = reset ? result.files : current.concat(result.files)
        setMessage(`${nextFiles.length} 个目录项`)
        return nextFiles
      })
      setFolder(token)
      setNext(result.next_page_token || null)
    })
  }
  async function importFiles() {
    await action(async () => {
      setImporting(true)
      try {
        const chosen = files.filter(file => selected.includes(key(file)))
        for (const file of chosen) {
          setMessage(`正在导入 ${file.name}`)
          const version = await request<{file_id: string; id: string; original_filename: string}>('/integrations/feishu/files/import', 'POST', {file_token: file.token, doc_type: file.type})
          setSelected(current => current.filter(id => id !== key(file)))
          await onImport({file_id: version.file_id, file_version_id: version.id, original_filename: version.original_filename, role, rule_version_id: null})
        }
        setMessage('导入完成。')
        setOpened(false)
      } finally { setImporting(false) }
    })
  }

  return (
    <>
      <Button type="button" disabled={disabled} onClick={() => setOpened(true)}>从飞书导入</Button>
      {opened && (
        <Modal title="从飞书导入" busy={importing} onClose={close}>
          <Stack gap="md">
            <FilterBar onSubmit={() => { void browse(true, folder) }}>
              <Input value={folder} onChange={event => setFolder(event.target.value)} aria-label="飞书文件夹链接" placeholder="粘贴飞书文件夹链接" inputMode="url" enterKeyHint="go" autoCapitalize="none" spellCheck={false} disabled={disabled || busy} />
              <Button type="submit" disabled={disabled || busy}>打开</Button>
              <Button type="button" disabled={disabled || busy} onClick={() => { setFolder(''); void browse(true, '') }}>我的云空间</Button>
            </FilterBar>
            {message && <Text>{message}</Text>}
            <Stack gap="sm">
              {files.map(file => (
                file.type === 'folder'
                  ? <Button key={key(file)} type="button" variant="ghost" disabled={disabled || busy} onClick={() => { setFolder(file.token); void browse(true, file.token) }}>文件夹 · {file.name} →</Button>
                  : <Checkbox key={key(file)} label={`${file.name}${supported(file) ? '' : '（不支持导入）'}`} checked={selected.includes(key(file))} disabled={!supported(file) || disabled || busy} onChange={event => setSelected(current => event.target.checked ? current.concat(key(file)) : current.filter(id => id !== key(file)))} />
              ))}
            </Stack>
            {next && <Button type="button" disabled={disabled || busy} onClick={() => void browse(false)}>加载更多</Button>}
            <Field label="资料用途">
              <Select value={role} disabled={disabled || busy} onChange={event => setRole(event.target.value as Material['role'])}>
                <option value="target">待审文件</option>
                <option value="reference">参考资料</option>
              </Select>
            </Field>
            <Button variant="primary" type="button" disabled={!selected.length || disabled || busy} onClick={() => void importFiles()}>导入{selected.length ? ` ${selected.length} 份资料` : ''}</Button>
          </Stack>
        </Modal>
      )}
    </>
  )
}
