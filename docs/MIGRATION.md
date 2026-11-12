# 迁移与退出

本文件只在 legacy migration 期间存在。迁移 baseline 的具体 Git revision 只由 owning Issue 保存：
- engine/corpus：#3
- 飞书产品：#11
- PC UI：#57
- InsForge runtime：#58
- 本仓生产交付 / 客户入口切换：#15

#15 完成后，本文件应删除或缩减到仍存在的真实兼容边界；历史迁移设计由 Git history 保存。

## 原则

- 组织内代码、配置、文档、测试、样本和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。
- Engine 迁移保持 #3 baseline 行为，不要求修复旧系统本来存在的算法 failure。
- 飞书迁移保持 #11 定义的现有客户 capability 不减少；UI 可以重构，不要求像素一致。
- 来源仓可作为迁移输入；`ppap-agent` 迁入后仍是活的参考实现，不是一次性来源。
- 一个内聚模块整体迁入更快时可以整体迁入；迁后删除跨仓 import、checkout、旧 HTTP wrapper。不要为了“迁完”而删除仍有其它用户或算法验证价值的 `ppap-agent`。
- Issue 之间表达验收关系，不规定实施顺序。

## 当前试用期切换

当前客户为试用阶段，旧审核历史不是上线前置。文件和规则可以在新系统重新导入，因此不建设通用历史数据迁移框架。

未来某个具体客户明确要求旧历史时，再针对该客户和实际数据处理。

## 客户入口与 `ppap-agent`

当 #3/#7/#11 的客户能力成立，并且 #15 的单仓 build/deploy/operate 成立后，可以把**本仓客户入口**切到 current `ppap`。这不结束 `ppap-agent`：它继续服务其它用户，并作为新算法的验证基地和参考实现。不要 archive 或拆除该仓。

其它共享 Java/客户系统只有确有其它消费者的部分继续保留。

## Checker Core 历史身份（#255）

`python -m scripts.import_checker_history` 只处理本仓既有、终态、尚无 Core admission snapshot 的 Round。它不导入未关闭草稿、不创建 Round/Job、不执行代码或模型、不收费、不通知、不修改机器结果或人工复核，也不将今天的标准 Checker 冒充历史代码。生产运行属于 #218，不能随应用启动执行。

先应用编号 SQL migrations。平台标准规则源仍使用 `python -m product.seed_standard_suites`（默认只列出；显式 `--apply` 才安装），不生成 Oracle 验证或自动发布。客户历史按精确 tenant UUID 单独处理：

```sh
# migrator DB + 只读原件对象凭据；默认事务 READ ONLY，无对象写入
uv run --no-sync python -m scripts.import_checker_history --tenant-id TENANT_UUID
# 人工核对上一步的 complete/blocked、数量、源摘要后，显式确认该计划
uv run --no-sync python -m scripts.import_checker_history --tenant-id TENANT_UUID --apply --expect-digest PLAN_DIGEST
# API DB 身份，只按该用户当前的成员资格、Round 和所有原件权限读回
uv run --no-sync python -m scripts.import_checker_history --tenant-id TENANT_UUID --round-id ROUND_UUID --verify-user-id USER_UUID
```

可重复传 `--round-id` 限定集合。空租户不写任何内容；未知租户、错误/非历史 Round 选择拒绝。缺损记录单独输出 `blocked` 并返回退出码 2，不阻止其他完整 Round 导入。计划摘要绑定冻结 DB 来源和原件核对结果；来源改变后必须重新 dry-run。

历史身份追加在原 `round_inputs.checker_history`，不是新轮次台账，也不是可执行 `checker_execution_snapshots`。旧轮次没有可证实的历史代码字节，身份只保留来源摘要、文件摘要、规则摘要和执行来源，并明确 `code_identity=unavailable`、始终 `unverified`；不从最新版规则推断代码，不创建 Suite。缺失/损坏原件、非终态或规则缺失留为 `blocked`，不虚构绑定。历史 Deep Artifact 已随 Deep 数据删除（#322），不再导入。

历史读回沿原权限，复读原件字节并核对摘要；导入后原件丢失或被替换仍报错。每个 Round 的身份写入在单事务内提交。已完整导入的 Round 读回为 `verified`（这里只指导入身份一致，不是算法验证）。保留原 stdout 供人工核对；它不含规则正文、原件内容、密钥或对象 URL。修复缺失来源后重新 dry-run/apply；冲突或不确定结果先读回，不能清空身份重试。
