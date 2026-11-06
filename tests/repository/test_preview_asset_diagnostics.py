"""Failure-only preview timing observes progress without changing browser budgets."""

from urllib.parse import urlsplit


def test_pending_assets_and_slow_completions_keep_elapsed_time(preview_asset_timing_factory):
    now = [1.0]
    timing = preview_asset_timing_factory(clock=lambda: now[0])
    timing.start("entry", "/preview/audit.tsx")
    timing.start("style", "/src/index.css")
    now[0] = 1.1
    timing.finish("style")
    now[0] = 6.0
    assert timing.snapshot() == {
        "started": 2,
        "finished": 1,
        "pending_count": 1,
        "pending": [{"asset": "/preview/audit.tsx", "elapsed_ms": 5000}],
        "slow_completed": [],
    }
    timing.finish("entry")
    assert timing.snapshot()["pending"] == []
    assert timing.snapshot()["slow_completed"] == [{"asset": "/preview/audit.tsx", "elapsed_ms": 5000}]


def test_diagnostic_storage_and_output_remain_bounded_without_losing_counts(preview_asset_timing_factory):
    now = [0.0]
    timing = preview_asset_timing_factory(clock=lambda: now[0])
    for index in range(200):
        timing.start(index, f"/src/module-{index}.tsx")
    assert len(timing.pending) == 128
    now[0] = 2.0
    assert timing.snapshot()["pending_count"] == 200
    assert len(timing.snapshot()["pending"]) == 12
    for index in range(200):
        timing.finish(index)
    assert timing.snapshot()["pending_count"] == 0
    assert len(timing.snapshot()["slow_completed"]) == 12


def test_asset_paths_exclude_queries_cross_origin_and_business_requests(preview_asset_path):
    origin = urlsplit("http://127.0.0.1:4185")
    assert preview_asset_path("http://127.0.0.1:4185/src/main.tsx?input=private#fragment", origin) == "/src/main.tsx"
    assert preview_asset_path("https://other.example/src/main.tsx?secret=hidden", origin) is None
    assert preview_asset_path("http://127.0.0.1:4185/api/rules?token=hidden", origin) is None
    assert preview_asset_path("http://127.0.0.1:4185/files/private", origin) is None
