const analysisNotices: Record<string, string | null> = {
  photo_content_requires_manual_confirmation: '照片格式已核实。照片中的零件、标识和内容仍需人工确认。',
  empty_document_text: '未读取到文件文字，请核对原件后重新识别。',
  document_text_unavailable: '无法读取文件文字，请核对原件后重新识别。',
  metadata_uses_excerpt: null,
  metadata_blank_customer: null,
  metadata_blank_model: null,
  metadata_blank_project: null,
  metadata_blank_part: null,
  metadata_blank_part_number: null,
  checker_suite_unavailable: null,
  metadata_extraction_failed: '业务信息识别失败，请人工核对。',
  document_classification_failed: '无法确定文件类型，请核对原件后重新识别。',
  rule_matching_failed: '自动匹配失败，请返回检查文件后重新识别。',
  no_available_rules: '暂无可用规则，请查看模板或通过模板文档联系我们。',
  matching_document_type_required: '尚未确定文件类型。请返回核对原件，重新识别；不会仅凭现有规则推断文件类型。',
  matching_document_type_rules_missing: '没有此类文件可用的规则。检查程序由平台离线准备，如需准备请联系我们；准备好后返回本任务重新识别。',
  matching_customer_rules_missing: '没有适用于该客户和文件类型的可用规则。请核对客户；检查程序由平台离线准备，如需准备请联系我们，准备好后重新识别。',
  matching_rules_customer_unassigned: '此类规则尚未确认客户范围。请指定客户或明确确认“不限定客户”，保存新版本后重新识别。旧版本不变。',
  ambiguous_available_rules: '有多份生效规则符合条件，未自动选择。请核对候选规则的适用范围与版本，再返回本任务重新识别。',
  matching_snapshot_invalid: '匹配依据不完整，请返回检查文件后重新识别。',
  matching_result_unknown: '尚未取得完整匹配结果，请读取识别结果。',
}

export function analysisNotice(notice: string): string | null {
  if (Object.hasOwn(analysisNotices, notice)) return analysisNotices[notice]
  return /\p{Script=Han}/u.test(notice) ? notice : '识别信息需要核对，请读取识别结果；若仍有异常，请联系支持。'
}

/** One way to show a moment everywhere: to the minute, 24-hour, and without the year when it is this year. */
export function displayTime(value: string | number | Date): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return typeof value === 'string' ? value : ''
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return date.toLocaleString('zh-CN', {...(sameYear ? {} : {year: 'numeric'}), month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false})
}
