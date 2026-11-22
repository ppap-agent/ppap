# PPAP

把遗留的 PPAP 产品、渠道和审核能力收敛为一个客户可用、可持续维护的产品。

> 打开一次审核，就知道审了什么、结果是什么、原始依据在哪里、下一步做什么。

产品、架构、数据、安全、审核与规则流程、MCP、开发和发布都在 [AGENTS.md](AGENTS.md)。测试见 [tests/README](tests/README.md)，视觉设计见 [Design tokens](apps/web/tokens/README.md) 与 [design.md](design.md)，配置见 [.env.example](.env.example)。

```text
apps/web -> apps/api -> PostgreSQL / object storage -> apps/worker -> packages
```
