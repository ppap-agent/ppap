# Live 验收入口

运行 `python tools/live.py --help`。实际配置见 [开发指南](../../docs/DEVELOPMENT.md)，可证明和未覆盖的范围见 [TESTING](../../docs/TESTING.md)。

这里的 oracle 是占位示例，必须换成有审阅来源的真实样本期望。不要把当前执行结果自动保存成 oracle。正常真实依赖费用已授权；缺环境或产品路径时保留 NOT_PROVEN。

[本轮实际验证记录](../../docs/evidence/context-2026-09-18.md) 区分工具自测与未证明的 Live 结果。[CI](../../.github/workflows/verification-tools.yml) 仅运行工具自测，不宣称产品验收通过。
