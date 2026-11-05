from pathlib import Path

import pytest
from corpus_contracts.poc_oracle import PocCase, load_poc_cases


def test_poc_case_rejects_empty_expected(tmp_path: Path) -> None:
    path = tmp_path / "empty.xlsx"
    path.write_bytes(b"x")
    with pytest.raises(AssertionError, match=r"V3 oracle incomplete: file=empty.xlsx seq=8 has 0 PASS/NG"):
        PocCase(
            file_name="empty.xlsx",
            file_seq="8",
            file_path=path,
            bundle_id="bundle",
            expected={},
        )


def test_default_v3_assets_load_at_least_one_case() -> None:
    assert load_poc_cases()
