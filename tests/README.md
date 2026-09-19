# Tests

Canonical 测试策略见 [docs/TESTING.md](../docs/TESTING.md)。本目录只记录当前可执行入口。

## Unit

```sh
python tools/dev.py install
python tools/dev.py quality
python tools/dev.py test
```

## Corpus

```sh
python tools/dev.py test pytest tests/corpus/v2_assets --ftype excel -q
python tools/dev.py test pytest tests/corpus/v2_assets --ftype pdf -q
python tools/dev.py test pytest tests/corpus/v2_assets --ftype scanned -q
python tools/dev.py test pytest tests/corpus/v3_poc -q
python tools/dev.py test pytest tests/corpus/test_psw_cross_format_regression.py tests/corpus/test_psw_cross_format_audit.py -q
```

## Local integration

```sh
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py verify-audit
python tools/stack.py down
```

#7 / #11 浏览器 E2E：`python tools/dev.py test e2e`（需 Playwright 与 `python tools/stack.py up`）。

Schema upgrade：`python tools/stack.py migrate && python tools/dev.py migrate && python tools/dev.py test schema`。

Production compose：`PPAP_BUILD_IMAGES=1 python tools/dev.py deploy && python tools/dev.py test deploy && python tools/dev.py undeploy`。
