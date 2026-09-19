# 迁移来源清单

本文件只服务当前迁移任务：说明旧能力大致在哪里，以及新产品是否仍需要它。正常产品开发不需要阅读本页。具体迁移范围由当前 Issue 决定。

| 能力 | 主要来源 | 当前处理原则 |
|---|---|---|
| 身份/租户/授权 | UAC、现有飞书入口 | 只保留 PPAP 所需身份和对象授权，不复制通用组织平台 |
| 文件与历史原件 | Java AuditFile、UAC source、Python audit_files、host documents | 收敛到产品 file/file_version；旧引用仅在迁移任务中映射 |
| 草稿/资料角色 | 旧 context_only/group 语义 | 迁移用户能力，不复制旧内部模型 |
| 规则与识别 | `ppap-agent` 规则和测试 | 由 #3 原生回归驱动；#6 只迁当前失败用例需要的最小实现 |
| 审核执行 | `ppap-agent` tasks/claim | 产品轮次与执行 attempt 分离；不保留永久旧 HTTP 产品层 |
| Web/飞书入口 | 旧 Web、UAC、hax 现场实现 | 进入同一 Product API 和产品状态 |
| 用量 | hax/host 历史记录 | 本地只记录实际事件，外部确认由当前 provider contract 决定 |
| 历史迁移 | UAC/Java/Agent/host 各自 ID | 使用有命名空间的来源映射，不按同名文件/邮箱猜合并 |
| 部署/恢复 | 各旧运行方式 | 只迁当前 PPAP 需要的运行能力 |
| lark_adaptor 群聊流程 | `ppap-agent/lark_adaptor` | 不进入当前主产品；仅在发现真实消费者时单独处理 |

## 使用方式

迁移代码前固定来源仓和 Git revision，先运行来源仓已有测试，得到真实失败后再迁最少代码。来源 commit、人工 expected 和当前 Issue 足以描述迁移输入，不再建立第二套 evidence/corpus registry。

旧仓或共享服务仍可能有其它消费者。新产品不再依赖某条旧路径，不等于授权关闭整个仓库、主机或客户实例。
