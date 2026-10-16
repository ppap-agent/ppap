"""P3 compatibility uses the existing legacy Excel corpus without changing it."""

from pathlib import Path

from ppap_application import validate_file


def test_real_legacy_excel_is_checked_through_the_shared_loader() -> None:
    fixture = Path(__file__).resolve().parents[3] / "assets/v2.0plus/fallback/5-模具验收报告-上海亚大.xls"

    assert validate_file("真实旧工作簿.xls", fixture.read_bytes()) == {"state": "valid", "reasons": []}
