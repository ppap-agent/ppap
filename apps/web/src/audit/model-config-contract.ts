import {request} from '../api.ts'
import type {components} from '../generated/openapi'

export type PersonalModelConfig = components['schemas']['PersonalModelConfigResponse']

// These calls only store/read settings. They never contact the configured provider.
export const readPersonalModelConfig = (signal?: AbortSignal) => request<PersonalModelConfig>('/me/model-config', 'GET', undefined, signal)
export function savePersonalModelConfig(apiKey: string, baseUrl: string, model: string) {
  if (!apiKey.trim() || !baseUrl.trim() || !model.trim()) throw new Error('请完整填写 API Key、Base URL 和模型名称。')
  return request<PersonalModelConfig>('/me/model-config', 'PUT', {api_key: apiKey.trim(), base_url: baseUrl.trim(), model: model.trim()})
}
export const revokePersonalModelConfig = () => request<PersonalModelConfig>('/me/model-config', 'DELETE')

/** Never display server/provider text which could contain submitted credentials. */
export function modelConfigFailure(cause: unknown): string | null {
  const code = (cause as {code?: unknown})?.code
  const messages: Record<string, string> = {
    invalid_model_credential: 'API Key 格式不正确，请重新输入。',
    invalid_model_endpoint: 'Base URL 格式不正确，请填写不含账户、查询参数或片段的 HTTPS 地址。',
    model_endpoint_not_allowed: '此 Base URL 尚未获部署批准，请联系管理员配置允许的模型地址。',
    invalid_model_model: '模型名称格式不正确，请填写提供方支持的模型名称。',
    model_credential_encryption_unavailable: '私人配置加密暂不可用，请联系管理员。',
    access_denied: '当前账户无权修改这份私人配置。',
    unauthenticated: '请先登录后读取当前配置。',
  }
  return typeof code === 'string' ? messages[code] || null : null
}
