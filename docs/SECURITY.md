# 安全与外部效果

安全保护实际边界，不通过增加审批层代替实现。

API、worker、迁移管理员分别使用最小权限数据库角色。业务关联检查租户与对象权限；外部身份使用 provider/app/tenant/subject 绑定，不按未经验证的邮箱自动合并。

对象 key 不覆盖，下载能力按对象和短期有效范围授予。用户可影响的 URL 下载需要 SSRF 边界、大小/时间限制、重定向检查和内容验证；只检查扩展名不足。未受信文档内容不得成为模型的工具指令或读取无关凭据的授权。

## Secret 与配置

Secret 是泄漏后真正跨越信任边界的值；Cursor Cloud 把普通配置也存入 Runtime Secret 并不造成问题，**存储机制不改变安全分类**。

当前 Core LLM：

- `LLM_API_KEY`：Secret，isolated-dev 唯一必须外部注入的 core secret。
- `LLM_BASE_URL`：普通配置，isolated-dev 默认 `https://api.trustedtunnel.app/v1`，可覆盖。
- `LLM_MODEL`：普通配置，isolated-dev 默认 `gpt-5.6-luna`，可覆盖。
- 协议固定为 Responses-compatible；endpoint 由 `<LLM_BASE_URL>/responses` 派生，不增加可漂移的 `LLM_PROTOCOL`。

`FEISHU_APP_ID` 是普通配置，`FEISHU_APP_SECRET` 是飞书 capability Secret。用户 access/refresh token、session/cookie 是运行时身份数据，不是开发机静态 Secret。

隔离开发 PostgreSQL、S3-compatible storage 和内部开发角色允许仓库固定 dev defaults；这些值只绑定隔离开发网络，生产不得继承。生产数据库/对象存储凭据由部署 Secret 注入并执行最小权限。

LLM readiness 探针只发送固定无业务内容，禁止 HTTP redirect，不记录 API key、raw provider output 或 response id。它只证明一次依赖可用；不得被当作模型质量、审核准确性或用户 Story 证明。

可选 integration Secret 缺失只禁用对应 capability，不得迫使 Core PPAP 使用假凭据或启动失败。LLM key 缺失时审核 capability 明确 unavailable；不得静默切换到另一个 endpoint/model。

密钥只进入 gitignored 本地 `.env`、Cloud Runtime Secret 或部署 Secret。不要复述历史泄漏值。发现旧明文凭据记录变量和轮换需求，本次不主动轮换共享生产 key。

隔离测试可使用真实付费依赖，授权不等于共享外部租户的所有数据都变成测试数据。用测试对象验证，不发无关客户通知。保留最小脱敏证据，不提交 cookie、session、signed URL 或客户原文。

保留 `.doc` 能力时，转换器无网络、资源受限、通过受控 IPC。测试在隔离环境不能取消产品运行时的文档隔离。

公开发布前另核对源码许可证、模型/文档库分发条件、规则及样本权利。私有接管仓并未自动获得开源许可。
