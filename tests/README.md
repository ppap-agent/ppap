# Tests

Canonical 测试策略见 [docs/TESTING.md](../docs/TESTING.md)。本目录只记录当前可执行入口。

## Unit

```sh
python -m pip install -e ".[dev]"
git show --format= --check HEAD -- . ':!assets' ':!tests/fixtures'
python -m compileall -q packages services tests
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

## Schema / deploy / E2E

#7 / #11 浏览器 E2E：`python -m unittest tests.e2e.test_web_audit -v`（需 Playwright 与 `docker compose -f deploy/isolated-dev/compose.yaml up -d --wait`）。

Schema upgrade：`docker compose -f deploy/isolated-dev/compose.yaml up -d postgres init-db --wait && python -m unittest tests.schema.test_upgrade -v`。

Production compose：`PPAP_BUILD_SHA="$(git rev-parse HEAD)" docker buildx bake && docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait && python -m unittest tests.deploy.test_runtime tests.deploy.test_backup -v && docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans`。
