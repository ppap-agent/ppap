import type {AuditSupportAdapter} from '../src/audit/support'

/** Explicit UI samples only: no real contact, service QR, or Feishu request. */
export function fixtureSupport(scene: string): AuditSupportAdapter | undefined {
  if (!['support-ready', 'support-empty', 'support-error', 'support-qr-error', 'support-loading'].includes(scene)) return undefined
  const version = 'preview-support-config-v1'
  let reads = 0
  return {
    read: async () => {
      reads += 1
      if (scene === 'support-error' && reads === 1) throw new Error('preview_support_read_failed')
      return {state: 'configured', config_version: version, phone: scene === 'support-empty' ? null : '示例电话（不可拨打）', email: scene === 'support-empty' ? null : 'support@example.invalid', service_hours: scene === 'support-empty' ? null : '服务时间待确认', qr_available: scene !== 'support-empty'}
    },
    readQr: async configVersion => {
      if (configVersion !== version || scene === 'support-empty') throw new Error('preview_support_config_mismatch')
      if (scene === 'support-qr-error') throw new Error('preview_support_image_failed')
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 256
      const context = canvas.getContext('2d')
      if (!context) throw new Error('preview_image_unavailable')
      context.fillStyle = '#fff'; context.fillRect(0, 0, 256, 256)
      context.strokeStyle = '#000'; context.strokeRect(8, 8, 240, 240)
      context.fillStyle = '#000'; context.textAlign = 'center'; context.font = '20px sans-serif'
      context.fillText('交互示例图片', 128, 112)
      context.fillText('非服务二维码', 128, 146)
      return new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('preview_image_unavailable')), 'image/png'))
    },
  }
}
