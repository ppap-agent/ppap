# 迁移来源清单

本文件只服务当前迁移任务：说明旧能力大致在哪里，以及哪些内容还需要一次性迁入本仓。正常产品开发不需要阅读本页。

| 能力 | 主要来源 | 当前处理原则 |
|---|---|---|
| 身份/租户/授权 | UAC、现有飞书入口 | 只保留 PPAP 所需身份和对象授权，不复制通用组织平台 |
| 文件与历史原件 | Java AuditFile、UAC source、Python audit_files、host documents | 收敛到产品 file/file_version；旧引用仅在迁移任务中映射 |
| 草稿/资料角色 | 旧 context_only/group 语义 | 迁移用户能力，不复制旧内部模型 |
| 规则、测试与识别 | `ppap-agent` | #3 先把长期需要的 pytest、fixture、oracle/helper 迁入本仓；#6 再由本仓失败测试驱动最小实现迁入 |
| 审核执行 | `ppap-agent` tasks/claim | 迁当前需要的执行语义；终态不保留旧 HTTP、源码 checkout 或跨仓 import |
| Web/飞书入口 | 旧 Web、UAC、hax 现场实现 | 进入同一 Product API 和产品状态 |
| 用量 | hax/host 历史记录 | 本地只记录实际事件，外部确认由当前 provider contract 决定 |
| 历史迁移 | UAC/Java/Agent/host 各自 ID | 使用有命名空间的来源映射，不按同名文件/邮箱猜合并 |
| 部署/恢复 | 各旧运行方式 | 只迁当前 PPAP 需要的运行能力 |
| lark_adaptor 群聊流程 | `ppap-agent/lark_adaptor` | 不进入当前主产品；只有发现真实消费者时才单独处理 |

## 使用方式

迁移一个能力时，只读取该能力直接相关的来源。把仍有长期价值的资产迁入 `ppap` 后，测试和开发立即切到本仓，不维持来源仓镜像、wrapper 或长期 checkout。

`ppap-agent` 是明确的 sunset 目标：其 PPAP 代码、测试、fixture 和 oracle 的当前 ownership 最终都应归 `ppap`；历史 commit 只保留来源追溯作用。

其它旧仓或共享服务可能有非 PPAP 消费者。新产品不再依赖某条旧路径，不等于授权关闭所有共享仓库、主机或客户实例。
