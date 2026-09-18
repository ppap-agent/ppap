# PPAP 工作入口

本仓是最终产品运行仓。先读 [Northstar](docs/NORTHSTAR.md) 和当前 Issue，再按改动读取 [架构](docs/ARCHITECTURE.md)、局部代码及对应测试。不要默认重读公司历史。

## 权威与自主决策

- 产品应做什么：Northstar 与已接受的产品决定。现在实现了什么：确切源码与测试。某环境实际怎样运行：对应时间、版本、路径的运行证据。三者不能互相替代。
- 当前调查基线和未知项在 [CURRENT](docs/CURRENT.md)；迁移来源在 [INVENTORY](docs/INVENTORY.md)。来源仓只读；本仓可自主实现和改进。
- 可以重新选择语言、框架和内部结构；记录实质取舍。不要为迁就实现而降低验收标准，也不要把未经确认的新业务政策藏进代码。
- 一个事实只在其拥有者处维护，其它文档链接它。实施状态在 Issues；测试输出不是另一套台账。

## 必须保持

产品轮次与执行尝试分离；受理和入队原子化；输入版本与规则绑定可追溯；机器结果与人工复核分别保存；所有数据访问执行租户与对象授权。详细语义只在 Northstar 和 DATA_MODEL 定义。

## 配置边界

Core LLM 固定走 Responses-compatible API。canonical 配置是 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；isolated-dev 的 Base URL/Model 默认是 `https://api.trustedtunnel.app/v1` / `gpt-5.6-luna`，因此日常开发只需注入 API key。Cursor 把 Base URL/Model 也存为 Secret 可接受；这不改变它们是普通配置的语义。

不要添加第二套 `OPENAI_*` authority，也不要增加 `LLM_PROTOCOL`。真实 LLM readiness 用 `python tools/llm_probe.py` 做一次最小 `/responses` 请求；P01 通过不等于审核质量通过。Feishu、Partner AI、legacy engine 等是可选 capability，缺配置不能阻止 core stack 启动。详细规则见 [DEVELOPMENT](docs/DEVELOPMENT.md)。

## 开发与交付

先验证当前 Issue/PR，避免重复工作。一次提交一个可审查结果，验证后 commit & push；不得覆盖并发提交。更新直接相关文档，不增加说明旧说明的包装层。

Live 验证使用授权的隔离环境和真实依赖，正常调用费用已获许可。隔离环境中的外部账号仍须属于测试范围。凭据只进 gitignored 本地配置或 Runtime Secret，日志不保留 token 或客户内容。失败如实报告，不能重跑业务操作抹掉失败。

目标尚无应用运行代码，不要编造启动命令或声称 E2E 已通过。工具、应用与发布各自报告验证范围；后续新增真实命令时同步对应目录说明。
