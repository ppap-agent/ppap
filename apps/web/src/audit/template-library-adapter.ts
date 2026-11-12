import {apiUrl, request, uploadLocalVersion, withResponseTrace} from '../api.ts'
import type {components} from '../generated/openapi.ts'
import type {TemplateLibraryAdapter} from './template-library.tsx'

type SubmissionFile = {file_id: string; version_id: string}

async function readBlob(path: string, failure: string) {
  const response = await fetch(`${apiUrl}${path}`, {method: 'GET', credentials: 'include'})
  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('ppap-session-expired'))
    throw withResponseTrace(new Error(failure), response)
  }
  return response.blob()
}

/** Transport only. `personalDriveUpload` is PublicConfig.personal_drive_upload (Feishu login active). */
export function createNativeTemplateLibraryAdapter(options: {personalDriveUpload?: boolean} = {}): TemplateLibraryAdapter {
  // A retried submission reuses the versions already received, so its request id still names the same files.
  const uploaded = new WeakMap<File, Promise<SubmissionFile>>()
  function uploadOnce(file: File) {
    const known = uploaded.get(file)
    if (known) return known
    const pending = uploadLocalVersion(file, crypto.randomUUID()).then(receipt => {
      if (receipt.state !== 'ready' || !receipt.file) throw new Error(`${file.name}：${receipt.reason || '文件未上传完成，请重试。'}`)
      return {file_id: receipt.file.file_id, version_id: receipt.file.id}
    })
    uploaded.set(file, pending)
    pending.catch(() => uploaded.delete(file))
    return pending
  }
  const customerPath = (customerId: string) => `/template-library/${encodeURIComponent(customerId)}`
  return {
    read: () => request('/template-library'),
    download: (customerId, templateId) => readBlob(`${customerPath(customerId)}/files/${encodeURIComponent(templateId)}`, '模板暂时无法下载，请重试。'),
    downloadAll: customerId => readBlob(`${customerPath(customerId)}/archive`, '模板 ZIP 暂时无法下载，请重试。'),
    ...options.personalDriveUpload ? {feishu: {
      drive: {
        rootFolder: () => request<components['schemas']['DriveFile']>('/integrations/feishu/root-folder'),
        list: ({folderToken, pageToken} = {}) => request(`/integrations/feishu/files?${new URLSearchParams({...folderToken ? {folder_token: folderToken} : {}, ...pageToken ? {page_token: pageToken} : {}})}`),
      },
      save: (customerId: string, folderToken: string, templateIds?: string[]) => request<components['schemas']['TemplateSaveResponse']>(`${customerPath(customerId)}/feishu`, 'POST', {folder_token: folderToken, ...templateIds ? {template_ids: templateIds} : {}}),
    }} : {},
    async submit({requestId, customer, files}) {
      const refs: SubmissionFile[] = []
      for (const file of files) refs.push(await uploadOnce(file))
      return request<components['schemas']['TemplateSubmissionReceipt']>('/template-submissions', 'POST', {request_id: requestId, customer, files: refs})
    },
  }
}
