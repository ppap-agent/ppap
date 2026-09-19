# 测试

测试保护当前行为，不维护第二套产品状态或“证明台账”。

## 原则

- **logic/unit**：完整覆盖稳定规则和数据不变量。
- **integration**：覆盖数据库、对象存储、Worker、授权等关键边界。
- **E2E / external**：只为少数真实用户路径或真实外部效果使用。
- 测试断言稳定性质，不冻结当前文件数量、目录 hash、服务清单或已经删除功能的“持续不存在”。
- Git 中的 fixture/oracle 由 Git 版本化，不再维护 hash manifest 或 oracle registry。

## 默认资产权限

`ppap-agent` GitHub 组织中的测试、样本、配置和我们可访问的远程环境默认可直接用于本项目测试，除非之后明确排除。不要因为“业务数据”“旧环境”或“来源仓”标签额外等待许可确认。

## 普通 CI

```sh
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
```

GitHub Actions 在 push / pull request 上直接运行本仓测试。终态 CI 不 checkout `ppap-agent`。

## Corpus / 检查能力迁移

#3 把仍有价值的 pytest、helper、fixture 和人工 expected 迁入本仓。可以按内聚测试域一次迁入，不需要人为拆成一个 case 一个 PR。

#6 以**尽快让本仓 corpus 回归可用**为目标迁实现：
- 已经工作的内聚 engine/package 可以整体迁入，如果这比逐条重写更快；
- 迁入后由本仓测试约束，再删除没有当前消费者的代码；
- 不为了迁移整洁度修改人工 expected；
- PDF/DOC/OCR/Vision 等只要属于客户实际路径且已有可用资产，就直接利用，不需要先证明“为什么可以用”。

## 本地/远程集成

本地 isolated-dev：

```sh
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

组织内现有可访问远程环境也可以直接用于相关 integration / E2E 验证。只保留真实安全边界和非幂等外部效果的必要 readback；不增加 PROVEN/NOT_PROVEN、evidence JSON 或额外 gate taxonomy。
