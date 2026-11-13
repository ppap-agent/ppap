# Packages

`packages/` 只放当前 PPAP 实际使用的可复用库。包是库，不是隐藏微服务；依赖与成员以各自 `pyproject.toml` 和根 `uv.lock` 为准。

| Package | Owns |
|---|---|
| `product` | 产品状态与编排：文件、规则、轮次、配置；`product.store` 是 API / Worker 共用的 PostgreSQL persistence |
| `audit` | 审核内核：规则、逻辑文档、证据、求值；检查程序目录叶子 `audit/checkers/<公司>/` 与读取实现 `audit/checker_lib/<公司>/` |
| `agent` | Deep 执行协议、UDS client 与 Controller（`python -m agent`）；Worker 只连 UDS |
| `documents` | 物理文档读取、定位和局部呈现（Excel、Word、PDF、图像、OCR、PDF 转换），不判定审核结果 |

Corpus regression 的契约包在 `tests/corpus/contracts`，仅用于开发/测试，不是产品 runtime API。Worker 执行 job 中冻结的 Checker Core 与 Suite 快照，见 [ARCHITECTURE](../docs/ARCHITECTURE.md#执行)。
