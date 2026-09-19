# 安全边界

安全文档只描述真正的信任边界，不复制配置默认值或测试状态。

## 授权

API、Worker 和迁移操作使用各自需要的最小权限。业务访问始终检查租户和对象权限；外部身份使用 provider/app/tenant/subject 绑定，不按未经验证的邮箱自动合并。

登录不等于对象权限，也不等于人工复核权限。

## 文件与不可信输入

每个产品文件版本关联自己的存储对象/版本引用；后续上传不能重定向已引用历史版本。

用户可影响的 URL 导入必须限制协议、目标网络、重定向、响应大小和时间，避免 SSRF。文件扩展名不能作为内容安全判断。

文档内容是数据，不是授权。模型或解析器不能因为文档中的文本而获得工具、网络或无关凭据访问。继续支持 `.doc` 时，保留当前需求所需的受限转换边界。

## Secret

Secret 是泄漏后会跨越信任边界的值，例如 API key、app secret、verification token、session/cookie 和生产数据库/对象存储凭据。

普通 endpoint、model、应用 ID 等是否为 Secret 由其实际安全性质决定，不由存储它的产品 UI 决定。canonical 配置字段和 defaults 见 typed Settings / `.env.example`，不在这里重复。

密钥只进入 gitignored 本地配置、GitHub Secrets 或部署 Secret；日志不输出 token、cookie、signed URL 或客户原文。

## 外部效果

只有路径真正产生外部效果时才需要额外考虑幂等、未知结果和 readback。未知的非幂等结果不能盲目重试。

LLM、飞书、Partner AI 等可选 integration 的可用性不应阻塞无关 Core 路径。它们的 readiness workflow 只诊断对应 integration，不代表产品质量。
