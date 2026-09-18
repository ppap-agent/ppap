# Live / dependency 验收入口

产品浏览器 Gate：`python tools/live.py --help`。LLM dependency readiness：`python tools/llm_probe.py --help`。实际配置见 [开发指南](../../docs/DEVELOPMENT.md)，可证明和未覆盖的范围见 [TESTING](../../docs/TESTING.md)。

LLM P01 使用 isolated-dev 的 Responses-compatible defaults，也可由 `LLM_BASE_URL / LLM_MODEL` 覆盖；只需要 `LLM_API_KEY` Secret。它只证明一次 endpoint/model 调用，不证明审核质量。

这里的产品 oracle 是占位示例，必须换成有审阅来源的真实样本期望。不要把当前执行结果自动保存成 oracle。正常真实依赖费用已授权；缺环境或产品路径时保留 NOT_PROVEN。

[本轮实际验证记录](../../docs/evidence/context-2026-09-18.md) 区分工具自测与未证明的 Live 结果。[工具 CI](../../.github/workflows/verification-tools.yml) 只运行验证器自测；[独立旧引擎 preflight](../../.github/workflows/legacy-preflight.yml) 只执行真实版本读取。普通代码提交不重复触发付费 LLM probe。

迁移期需要直接验证旧引擎时，使用 [旧引擎契约验证](engine.md)；不要把其结果算成新产品 Story。
