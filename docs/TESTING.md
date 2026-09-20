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
- **E2E**：少量完整客户路径。#7 覆盖 Web 审核闭环；#11 覆盖飞书来源进入同一产品闭环。飞书 Drive 导入与文档关联文件的确定性回归在 `tests/unit/test_feishu_drive.py` 与 `tests/unit/test_feishu_doc_links.py`（mocked Open API）。docs-addon 宿主登录到普通 round 的确定性回归在 `tests/unit/test_feishu_docs_addon.py`（mocked host code + Open API）。客户路径 `/addon` 与 `/audit-files` 的静态入口在 `tests/unit/test_web_server.py`。

规则排列组合留在低层，不复制到 E2E。

## 当前可运行入口

```sh
python tools/dev.py install
python tools/dev.py quality
python tools/dev.py test
python tools/dev.py test pytest tests/corpus/v2_assets --ftype excel -q
python tools/dev.py test pytest tests/corpus/v2_assets --ftype pdf -q
python tools/dev.py test pytest tests/corpus/v2_assets --ftype scanned -q
python tools/dev.py test pytest tests/corpus/v3_poc -q
python tools/dev.py test pytest tests/corpus/test_psw_cross_format_regression.py tests/corpus/test_psw_cross_format_audit.py -q
python tools/dev.py test schema
python tools/dev.py test deploy
python tools/dev.py test backup

python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py verify-audit
python tools/stack.py down
```

PSW 的 `.doc` 用例需要本机 LibreOffice，以及 Linux 上可执行的 `ppap-local-converter-sandbox`（由 `packages/ppap_docops/native/legacy_doc_sandbox.c` 编译）。`.doc` 转换是默认行为，没有开关变量；转换器或沙箱缺失时该项记 ERR，不跳过格式。需要 Paddle 的 PDF 转换优先使用 `tests/fixtures/ocr_cache`；未命中时再读 `PADDLE_OCR_URL`。人工 expected 以 corpus 内 CSV/XLSX 为准，不要改它来迎合实现。

#7 的代表性浏览器 E2E 覆盖待审+参考、提交、离开后回访、打开当前 round 冻结原件（或看到 locator-gap）、复核和显式重审：

```sh
python tools/dev.py install
python -m pip install -e ".[e2e]"
python -m playwright install chromium
python tools/stack.py up
python tools/dev.py test e2e
python tools/stack.py down
```

组织内测试资产和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。
