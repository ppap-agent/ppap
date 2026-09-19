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
PYTHONPATH=packages:packages/ppap_docops/src:packages/ppap_excel_audit_engine/src:packages/ppap_ocr_adapter/src:packages/ppap_pdf_to_excel/src:packages/ppap_vision/src:services/api:services/worker \
  python -m unittest discover -s tests/unit -v

python -m pytest tests/corpus/v2_assets --ftype excel -q
python -m pytest tests/corpus/v2_assets --ftype pdf -q
python -m pytest tests/corpus/v2_assets --ftype scanned -q
python -m pytest tests/corpus/v3_poc -q
python -m pytest tests/corpus/test_psw_cross_format_regression.py tests/corpus/test_psw_cross_format_audit.py -q

python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

PSW 的 `.doc` 用例需要本机 LibreOffice，以及 Linux 上可执行的 `ppap-local-converter-sandbox`（由 `packages/ppap_docops/native/legacy_doc_sandbox.c` 编译）。需要 Paddle 的 PDF 转换优先使用 `tests/fixtures/ocr_cache`；未命中时再读 `PADDLE_OCR_URL`。人工 expected 以 corpus 内 CSV/XLSX 为准，不要改它来迎合实现。

随着 #7/#11/#15 实现，integration、E2E、quality checks 和 production build 命令在本文件补充真实入口。没有实际命令前不预写“已支持”的测试流程。

组织内测试资产和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。
