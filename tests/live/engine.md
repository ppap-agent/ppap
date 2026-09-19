# 迁移期旧引擎验证

这个工具只验证旧引擎对固定测试输入的分析、受理和规则结果，不证明新 Product API、Web 或整个 Story。目标产品入口仍是 [tools/live.py](../../tools/live.py)，聚合规则仍由 [TESTING](../../docs/TESTING.md) 拥有。

```sh
python -m unittest discover -s tests/unit -v
python tools/legacy_engine_gate.py --case .local/engine.json
```

配置在本地环境：`PPAP_LEGACY_ENGINE_URL` 是授权引擎的 `/api/v1`；`PPAP_LEGACY_VERSION_URL` 是同一实例的 `/version`；`PPAP_LEGACY_ENGINE_TOKEN` 为合法开发凭据；`PPAP_LEGACY_PROJECT_ID` 必须拥有被测规则。每份原件 URL 从场景指定的环境变量读取，例如 `PPAP_LIVE_SOURCE_URL`。这些变量不进浏览器。

复制 [示例](engine.example.json) 到 `.local/engine.json`，填实际完整构建 SHA、原件 SHA-256、规则绑定及审阅过的独立 oracle。示例不是可用测试数据。多文件只覆盖各自独立审核的目标，不声称覆盖参考文件/contextOnly 或全部跨文件规则。

请求/响应依据：[audits.py](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/ppap_agent/app/schemas/audits.py)、[tasks.py](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/ppap_agent/app/schemas/tasks.py)、[evidence](https://github.com/ppap-agent/ppap-agent/blob/53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9/packages/ppap_core/src/ppap_core/contracts.py)。它们是旧协议，不是新产品必须复制的接口。

真实操作顺序：核对版本；GET 原件核对摘要；一次数组 `/audits/analysis`；确认规则未被替换；一次数组 `/tasks`；GET 轮询本次 taskId；完整比较 oracle。网络失败不重发 POST，批次部分成功不能算整体通过。出现未知提交结果，用 `.local/engine-live/<run>/reconcile.local.json` 的私有 ID 读回/对账；不要重新运行来覆盖失败。清理按该实例的实际支持接口执行，不猜测删除 API。

原件读取上限 64 MB、响应上限 8 MB，默认等待 300 秒且可在场景中调整至 3600 秒：这是本工具的 proof budget，不是产品格式/SLA 限制。签名有效期须覆盖本次执行，URL 对应对象须不可变；调用端读到了正确字节不独自证明远端使用的字节。`require_locator` 只检查 locator 存在，不证明定位内容正确；真实位置语义仍需 corpus/产品审阅。

报告只输出构建/场景/工具摘要、阶段、计数和错误码；不输出原文、签名 URL、token、用户身份或异常正文。退出码 0 表示 `legacy.engine.oracle` 这个 scope 成立，2 表示 NOT_PROVEN；`product_story_outcome` 固定为 NOT_PROVEN，绝不能汇总成新产品通过。

27 项工具自测覆盖空/漏/重复项、错误构建/身份、规则变化、源摘要不符、超时不得重发和异常脱敏。它们使用替身测试验证器本身，不是业务验收。本轮真实命令只到配置检查：缺授权引擎配置，分析/任务调用均为 0，结果 NOT_PROVEN。实际安全记录见 [本轮运行](../../docs/evidence/engine-gate-2026-09-18.json)。

下一步由授权的 `PPAP_CORPUS_ROOT` 与写入 catalog 的人工审阅记录提供真实 oracle 后再跑；比较命令不变。新引擎内迁后可删除旧 HTTP 验证路径，不能让这个迁移工具变成产品运行依赖，也不能把它的输出写回 oracle。
