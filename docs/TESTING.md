# 测试

测试保护当前行为，不维护第二套产品状态或“证明台账”。

## Engine migration baseline

Canonical source baseline：

`ppap-agent@53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9`

使用该 revision 的 V2 4.8、POC grouped、PSW cross-format corpus 与人工 expected。已观察结果：

| Suite | Baseline |
|---|---|
| V2 Excel | 78 passed |
| V2 PDF | 66 passed / 19 failed |
| V2 scanned | 1 failed |
| POC grouped | 27 passed |
| PSW | 已运行部分 5 passed；DOC 在有 LibreOffice 的环境按同 revision 结果补齐 baseline |

#3 的迁移验收是**逐 case / 逐规则结果与 baseline 一致，不新增 migration regression**。baseline 原本失败的 PDF/scanned case 可以继续失败；算法优化以后单独进行，不属于迁移完成条件。

迁入后，本仓的 pytest / fixture / oracle 成为 canonical regression，CI 不再 checkout `ppap-agent`。

## Feishu parity baseline

#11 以以下固定提交的客户可见行为为迁移下限：

- `ppap-web@557a2aeab3e5f5e68d8e8cf47593ca172da733d7`
- `ppap-uac@fbe912faf1894fe018ea88c20e46a57842468623`
- `ppap-biz@7570d966f178cde9c841600be485c43109d92019`
- `ppap-host@c32b46a0a0d4f252a1677e8abce53a210b3c6d28`
- `hax@0923f5acea380ddd09813621703777153a8c7971`

验收看业务 capability parity，不要求像素级 UI 相同。

## 测试层级

- **logic/unit**：稳定规则和数据不变量。
- **corpus regression**：完整 engine parity。
- **integration**：数据库、对象存储、Worker、授权和必要外部接口。
- **E2E**：少量完整客户路径。#7 至少一条完整 Web 审核闭环；#11 至少一条飞书来源进入同一审核闭环。

规则排列组合留在低层，不复制到 E2E。

## 当前 CI

```sh
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
```

随着 #3/#15 推进，CI 增加 canonical corpus、integration、E2E、lint/typecheck 和 production build。它们都是普通测试/构建步骤，不增加 Pxx/Gxx、PROVEN/NOT_PROVEN 或 evidence JSON。

组织内现有测试、样本、配置和可访问远程环境默认可以直接用于测试。
