"""Original PSW appendix scope without live model observations."""

import fitz
from audit.contracts import AuditInput


def test_original_native_appendices_do_not_create_unread_visual_scope_or_resolve_guidance():
    import hashlib
    from pathlib import Path

    from audit.engine.checkers.psw_scope import _Objects

    path = Path(__file__).resolve().parents[3] / "assets/li/samples/模拟测试文件V2-01/16-零件提交保证书/16-零件提交保证书-上海亚大.pdf"
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    assert digest == "24133e74c1dac80339a694863930121b9525d4b0d887f7e020b9ed82f3014c0b"
    scope = _Objects(AuditInput(audit_id="frozen-native-appendices", file_url=str(path)), digest, {})
    assert scope.authorization == [7]
    assert scope.unresolved_pages == set()
    identities = [args[0] for args, _kwargs in scope.sources if args[0].get("coverage") == "native_document_heading"]
    assert [item["page"] for item in identities] == [1, 6]
    with fitz.open(path) as document:
        for item in identities:
            assert item["source_sha256"] == digest
            assert document[item["page"] - 1].get_textbox(fitz.Rect(item["bbox"])).strip() in {"生产件批准交付物文件清单", "PPAP文件无法提交声明"}
    assert path.read_bytes() == original
