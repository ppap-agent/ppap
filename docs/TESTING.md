# 测试

测试保护当前行为，不维护第二套产品状态。

迁移 baseline 的具体 Git revision 和当前观察结果只由 owning Issue 保存：
- engine/corpus parity：[#3](https://github.com/ppap-agent/ppap/issues/3)
- 飞书 capability parity：[#11](https://github.com/ppap-agent/ppap/issues/11)

迁移完成后，本仓测试成为 current authority，不继续复制 legacy baseline 到长期文档。

## 测试层级

- **logic / unit**：稳定规则和数据不变量。
- **corpus regression**：完整 engine parity；baseline 原有 failure 不属于 migration regression。
- **integration**：数据库、对象存储、Worker、授权和必要外部接口。
- **E2E**：少量完整客户路径。#7 覆盖 Web 审核闭环；#11 覆盖飞书来源进入同一产品闭环。

规则排列组合留在低层，不复制到 E2E。

## 当前可运行入口

```sh
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v

python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

随着 #3/#7/#11/#15 实现，canonical corpus、integration、E2E、quality checks 和 production build 命令在本文件补充真实入口。没有实际命令前不预写“已支持”的测试流程。

组织内测试资产和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。
