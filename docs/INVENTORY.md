# 迁移来源清单

`ppap-agent` GitHub 组织中的仓库、配置、文档、测试数据和我们可访问的远程环境，默认都属于本项目可直接利用的迁移资产，除非之后明确排除。

本页只帮助快速找到现成能力，不是权限审批清单。

| 能力 | 可利用来源 | 迁移方向 |
|---|---|---|
| 身份/租户/授权 | UAC、现有飞书应用、相关配置和环境 | 迁入 PPAP 所需身份和对象授权 |
| 文件与历史原件 | Java AuditFile、UAC source、Python audit_files、host documents、可访问存储/环境 | 收敛到 product file/file_version |
| 草稿/资料角色 | 旧 context_only/group 语义、现有 UI/接口 | 迁用户能力，不复制无消费者内部模型 |
| 规则、测试、识别 | `ppap-agent` tests/assets/packages | pytest/fixture/oracle 先归本仓；现成内聚实现可按速度整体迁入 |
| 审核执行 | `ppap-agent` tasks/claim、相关 worker/config | 迁可工作的执行能力；终态删除旧 HTTP/跨仓依赖 |
| Web/飞书入口 | ppap-web、UAC、host、hax、飞书相关环境 | 收敛到同一 Product API / Web |
| 用量/外部权益 | hax/host、Partner AI 相关配置和可访问环境 | 迁实际业务事件和当前外部确认路径 |
| 历史迁移 | UAC/Java/Agent/host 数据与可访问环境 | 直接利用现有数据源完成映射和迁移 |
| 部署/恢复 | 各旧运行方式、配置、远程环境 | 复用可工作的部署知识，收敛到本仓 |
| lark_adaptor | `ppap-agent/lark_adaptor` | 仅当客户路径实际需要时迁相关能力 |

## 使用方式

迁移一个能力时，先找现成可工作的资产并直接复用。不要先假设某份配置、样本或远程环境不能用，也不要为了“重新证明”而延迟客户路径。

`ppap-agent` 是明确 sunset 目标；其它组织内资产可以作为迁移来源使用。迁入后本仓拥有当前实现和测试。
