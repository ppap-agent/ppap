# Live 验收入口

运行 `python tools/live.py --help`。实际配置见 [开发指南](../../docs/DEVELOPMENT.md)，可证明和未覆盖的范围见 [TESTING](../../docs/TESTING.md)。

这里的 oracle 是占位示例，必须换成有审阅来源的真实样本期望。不要把当前执行结果自动保存成 oracle。正常真实依赖费用已授权；缺环境或产品路径时保留 NOT_PROVEN。

[本轮实际验证记录](../../docs/evidence/context-2026-09-18.md) 区分工具自测与未证明的 Live 结果。[工具 CI](../../.github/workflows/verification-tools.yml) 只运行验证器自测；[独立旧引擎 preflight](../../.github/workflows/legacy-preflight.yml) 只执行真实版本读取。前者通过不证明外部依赖可用，后者通过不证明产品审核成立。普通代码提交不重复触发旧引擎探针。

迁移期需要直接验证旧引擎时，使用 [旧引擎契约验证](engine.md)；不要把其结果算成新产品 Story。
