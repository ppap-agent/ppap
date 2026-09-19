# Tests

Canonical 测试策略见 [docs/TESTING.md](../docs/TESTING.md)。本目录只记录当前可执行入口。

## Unit

```sh
PYTHONPATH=packages:packages/ppap_docops/src:packages/ppap_excel_audit_engine/src:packages/ppap_ocr_adapter/src:packages/ppap_pdf_to_excel/src:packages/ppap_vision/src:services/api:services/worker \
  python -m unittest discover -s tests/unit -v
```

## Corpus

```sh
python -m pytest tests/corpus/v2_assets --ftype excel -q
python -m pytest tests/corpus/v2_assets --ftype pdf -q
python -m pytest tests/corpus/v2_assets --ftype scanned -q
python -m pytest tests/corpus/v3_poc -q
python -m pytest tests/corpus/test_psw_cross_format_regression.py tests/corpus/test_psw_cross_format_audit.py -q
```

## Local integration

```sh
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

#7/#11 的 E2E 入口在真实实现合入后补到这里。
