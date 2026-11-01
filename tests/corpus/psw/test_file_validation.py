"""P3 legacy Word checks share the PSW corpus, not a package-owned fixture copy."""

from pathlib import Path

from worker.intake import validate_file


def test_real_legacy_doc_without_converter_is_unknown_not_invalid(monkeypatch) -> None:
    monkeypatch.setenv("LEGACY_DOC_CONVERTER_EXECUTABLE", "/not-installed/ppap-test-libreoffice")
    fixture = Path(__file__).resolve().parents[1] / "fixtures/legacy_doc/psw.doc"

    assert validate_file("真实旧文档.doc", fixture.read_bytes()) == {
        "state": "error",
        "reasons": ["文件检查暂不可用，请稍后重试"],
    }
