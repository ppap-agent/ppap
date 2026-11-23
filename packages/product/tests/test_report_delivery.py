"""A Feishu report document's delivery (#359): read-only for others, the frozen report PDFs attached and named at the top
with their fingerprints. Request shapes are checked against a fake Feishu; nothing here reaches the network."""

import hashlib
import json
import unittest
import zlib
from urllib.parse import parse_qs, unquote

import httpx
from product.feishu_writeback import READ_ONLY, attach_report_pdfs, make_read_only, pending_attachments, report_blocks
from product.store.store import _report_public, report_fingerprint

PDF = b"%PDF-1.7 frozen report"
SHA = hashlib.sha256(PDF).hexdigest()


def ready_round() -> dict:
    """A finished round with one audited file and one finding: enough for a complete report."""
    source = {"file_version_id": "v1", "filename": "audit.xlsx", "role": "target", "locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"}, "excerpt": "缺少签字"}
    semantic = {"rule_version_id": "rule1", "clause_id": "R1", "rule_text": "签字", "scope": {"total": 1, "checked": 1, "unknown": 0}, "sources": [source]}
    return {
        "execution": "completed",
        "inputs": [{"role": "target", "file_version_id": "v1", "original_filename": "audit.xlsx", "rule_version_id": "rule1", "rule": {"title": "客户规则"}}],
        "findings": [{"status": "NG", "item": "签字", "suggestions": "缺少签字", "locator": {"file_version_id": "v1", "rule_item_id": "R1", "semantic": semantic}}],
    }


def text_of(block: dict) -> str:
    return "".join(part["text_run"]["content"] for value in block.values() if isinstance(value, dict) for part in value["elements"])


class Feishu:
    """A fake Feishu that records every request; `answers` maps (method, path suffix) to responses, in order."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answers: dict[tuple[str, str], list] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for (method, suffix), queue in self.answers.items():
            if request.method == method and request.url.path.endswith(suffix) and queue:
                answer = queue.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unexpected {request.method} {request.url.path}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def ok(data: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data or {}})


CREATED = ok({"children": [{"block_id": "viewA", "block_type": 33, "children": ["fileA"]}]})


class FingerprintTests(unittest.TestCase):
    def test_the_fingerprint_is_the_first_twelve_hex_of_the_pdf_sha256(self):
        self.assertEqual(report_fingerprint(SHA), SHA[:12])
        for broken in (None, "", "abc", SHA.upper(), SHA[:-1]):
            self.assertIsNone(report_fingerprint(broken))

    def test_the_top_names_each_pdf_with_its_fingerprint_and_links_to_its_report_page(self):
        body = ready_round()
        original = {"filename": "audit.xlsx-审核报告.pdf", "fingerprint": report_fingerprint(SHA), "url": "https://ppap.example/tasks/t1?report=v1"}
        blocks = report_blocks(body, customer="UAES", originals=[original])
        self.assertEqual(text_of(blocks[1]), f"以 PPAP 原件为准 · 查看原件 · 指纹 {SHA[:12]}")
        link = blocks[1]["text"]["elements"][1]["text_run"]
        self.assertEqual((link["content"], unquote(link["text_element_style"]["link"]["url"])), ("查看原件", original["url"]))
        # Feishu takes the link URL-encoded.
        self.assertNotIn("/", link["text_element_style"]["link"]["url"])
        # The two lines that were at the top stay, right after it.
        self.assertEqual([text_of(block) for block in blocks[2:4]], ["机器初审结果，以人工确认和签发为准。", "交付时未签发。"])
        self.assertTrue(text_of(blocks[4]).startswith("客户：联电科技/UAES · 标准模式"))
        # The PDFs go at the end, under their own heading.
        self.assertEqual(text_of(blocks[-2]), "报告原件（PDF）")
        # A round with several files names each line's report.
        two = report_blocks(body, originals=[original, {**original, "filename": "other-审核报告.pdf", "fingerprint": "0" * 12}])
        self.assertEqual(
            [text_of(block) for block in two[1:3]], [f"以 PPAP 原件为准 · audit.xlsx-审核报告.pdf · 查看原件 · 指纹 {SHA[:12]}", "以 PPAP 原件为准 · other-审核报告.pdf · 查看原件 · 指纹 000000000000"]
        )


class ReadOnlyTests(unittest.TestCase):
    def test_read_only_for_others_is_one_patch_of_the_public_setting(self):
        feishu = Feishu()
        feishu.answers = {("PATCH", "/public"): [ok({"permission_public": READ_ONLY})]}
        with feishu.client() as http:
            self.assertEqual(make_read_only(http, "test-token", "doc123"), ("set", None))
        (request,) = feishu.requests
        self.assertEqual(str(request.url).split("?")[0], "https://open.feishu.cn/open-apis/drive/v2/permissions/doc123/public")
        self.assertEqual(parse_qs(request.url.query.decode()), {"type": ["docx"]})
        self.assertEqual(json.loads(request.content), {"external_access_entity": "closed", "link_share_entity": "tenant_readable"})
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")

    def test_a_rejection_is_recorded_with_its_public_code_and_not_retried(self):
        feishu = Feishu()
        feishu.answers = {("PATCH", "/public"): [httpx.Response(400, json={"code": 99991679, "msg": "private provider details"})]}
        with feishu.client() as http:
            self.assertEqual(make_read_only(http, "test-token", "doc123"), ("failed", "feishu_report_permission_rejected:99991679"))
        self.assertEqual(len(feishu.requests), 1)

    def test_an_unknown_outcome_is_read_back(self):
        for public, expected in ((READ_ONLY, ("set", None)), ({**READ_ONLY, "link_share_entity": "tenant_editable"}, ("failed", "feishu_report_permission_unknown"))):
            feishu = Feishu()
            feishu.answers = {("PATCH", "/public"): [httpx.ConnectError("lost")], ("GET", "/public"): [ok({"permission_public": public})]}
            with feishu.client() as http, self.subTest(public=public):
                self.assertEqual(make_read_only(http, "test-token", "doc123"), expected)
            self.assertEqual([request.method for request in feishu.requests], ["PATCH", "GET"])
        unreadable = Feishu()
        unreadable.answers = {("PATCH", "/public"): [httpx.ConnectError("lost")], ("GET", "/public"): [httpx.ConnectError("lost")]}
        with unreadable.client() as http:
            self.assertEqual(make_read_only(http, "test-token", "doc123"), ("unknown", "feishu_report_permission_unknown"))

    def test_a_failure_is_shown_with_a_retry_and_the_document_still_delivered(self):
        items = pending_attachments([{"report_id": "r1", "filename": "a-审核报告.pdf", "fingerprint": SHA[:12]}])
        attached = [{**items[0], "state": "attached", "block_id": "fileA", "file_token": "boxA"}]
        row = ("id1", "ready", "doc123", None, "failed", "feishu_report_permission_rejected:99991679", attached, False)
        report = _report_public(row)
        self.assertEqual((report["status"], report["document_id"]), ("ready", "doc123"))
        self.assertEqual(report["read_only"], {"state": "failed", "error_code": "feishu_report_permission_rejected:99991679"})
        self.assertEqual(report["retry"], ["read_only"])
        # The person never granted it: signing in again for the report asks for it.
        self.assertTrue(report["reauthorize"])
        self.assertEqual(report["attachments"], [{"filename": "a-审核报告.pdf", "fingerprint": SHA[:12], "state": "attached", "error_code": None}])
        # Nothing to retry while another request is finishing it, and nothing internal shown.
        busy = _report_public(("id1", "ready", "doc123", None, None, None, items, True))
        self.assertEqual((busy["read_only"]["state"], busy["attachments"][0]["state"], busy["retry"]), ("working", "working", []))
        self.assertNotIn("block_id", json.dumps(busy))
        stopped = _report_public(("id1", "ready", "doc123", None, "set", None, items, False))
        self.assertEqual((stopped["attachments"][0]["state"], stopped["retry"], stopped["reauthorize"]), ("failed", ["attachments"], False))
        # Delivered before #359: as it was.
        self.assertEqual(_report_public(("id1", "ready", "doc123", None, None, None, None, False)), {"id": "id1", "status": "ready", "document_id": "doc123", "error_code": None})


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.saved: list[list[dict]] = []

    def persist(self, items):
        self.saved.append(json.loads(json.dumps(items)))
        return True

    def attach(self, feishu, items):
        with feishu.client() as http:
            return attach_report_pdfs(http, "test-token", "doc123", items, lambda report_id: PDF, self.persist, pause=lambda _seconds: None)

    def test_a_pdf_is_a_file_block_then_uploaded_into_it_then_bound(self):
        feishu = Feishu()
        feishu.answers = {("POST", "/children"): [CREATED], ("POST", "/medias/upload_all"): [ok({"file_token": "boxA"})], ("PATCH", "/blocks/fileA"): [ok()]}
        items = self.attach(feishu, pending_attachments([{"report_id": "r1", "filename": "a-审核报告.pdf", "fingerprint": SHA[:12]}]))
        self.assertEqual((items[0]["state"], items[0]["block_id"], items[0]["file_token"]), ("attached", "fileA", "boxA"))
        create, upload, bind = feishu.requests
        self.assertEqual(create.url.path, "/open-apis/docx/v1/documents/doc123/blocks/doc123/children")
        self.assertEqual(json.loads(create.content), {"index": -1, "children": [{"block_type": 23, "file": {"token": ""}}]})
        self.assertEqual(upload.url.path, "/open-apis/drive/v1/medias/upload_all")
        self.assertTrue(upload.headers["content-type"].startswith("multipart/form-data"))
        form = upload.content.decode("utf-8", "replace")
        for name, value in (
            ("file_name", "a-审核报告.pdf"),
            ("parent_type", "docx_file"),
            ("parent_node", "fileA"),
            ("size", str(len(PDF))),
            ("checksum", str(zlib.adler32(PDF))),
            ("extra", json.dumps({"drive_route_token": "doc123"})),
        ):
            self.assertIn(f'name="{name}"\r\n\r\n{value}\r\n', form)
        self.assertIn(PDF.decode(), form)
        self.assertEqual((bind.method, bind.url.path, json.loads(bind.content)), ("PATCH", "/open-apis/docx/v1/documents/doc123/blocks/fileA", {"replace_file": {"token": "boxA"}}))
        # The boundary is recorded before the block is created.
        self.assertEqual(self.saved[0][0]["state"], "writing")
        self.assertIsNone(self.saved[0][0]["block_id"])

    def test_an_unknown_creation_is_read_back_and_never_made_twice(self):
        first = Feishu()
        first.answers = {("POST", "/children"): [httpx.ReadTimeout("lost")]}
        items = self.attach(
            first, pending_attachments([{"report_id": "r1", "filename": "a-审核报告.pdf", "fingerprint": SHA[:12]}, {"report_id": "r2", "filename": "b-审核报告.pdf", "fingerprint": "1" * 12}])
        )
        self.assertEqual([(item["state"], item["block_id"]) for item in items], [("unknown", None), ("pending", None)])
        # It stops there: the second PDF is not tried while the first one's block may exist.
        self.assertEqual(len(first.requests), 1)
        # Retried: the block it made is there, empty; it is used, and only the second PDF gets a new block.
        listing = ok({"items": [{"block_id": "doc123", "block_type": 1}, {"block_id": "fileA", "block_type": 23, "file": {"token": ""}}], "has_more": False})
        second = Feishu()
        second.answers = {
            ("GET", "/documents/doc123/blocks"): [listing],
            ("POST", "/children"): [ok({"children": [{"block_id": "viewB", "block_type": 33, "children": ["fileB"]}]})],
            ("POST", "/medias/upload_all"): [ok({"file_token": "boxA"}), ok({"file_token": "boxB"})],
            ("PATCH", "/blocks/fileA"): [ok()],
            ("PATCH", "/blocks/fileB"): [ok()],
        }
        items = self.attach(second, items)
        self.assertEqual([(item["state"], item["block_id"]) for item in items], [("attached", "fileA"), ("attached", "fileB")])
        self.assertEqual(
            [request.method + " " + request.url.path.rsplit("/", 1)[-1] for request in second.requests],
            ["GET blocks", "POST upload_all", "PATCH fileA", "POST children", "POST upload_all", "PATCH fileB"],
        )
        # Nothing to do once everything is attached.
        done = Feishu()
        self.assertEqual(self.attach(done, items), items)
        self.assertEqual(done.requests, [])

    def test_an_unknown_creation_that_did_not_land_is_made_once_and_several_unknown_blocks_are_not_guessed(self):
        items = [{**pending_attachments([{"report_id": "r1", "filename": "a-审核报告.pdf", "fingerprint": SHA[:12]}])[0], "state": "unknown"}]
        none = Feishu()
        none.answers = {
            ("GET", "/blocks"): [ok({"items": [], "has_more": False})],
            ("POST", "/children"): [CREATED],
            ("POST", "/medias/upload_all"): [ok({"file_token": "boxA"})],
            ("PATCH", "/blocks/fileA"): [ok()],
        }
        self.assertEqual(self.attach(none, items)[0]["state"], "attached")
        two = ok({"items": [{"block_id": "fileX", "block_type": 23, "file": {"token": ""}}, {"block_id": "fileY", "block_type": 23, "file": {"token": ""}}], "has_more": False})
        ambiguous = Feishu()
        ambiguous.answers = {("GET", "/blocks"): [two]}
        result = self.attach(ambiguous, items)
        self.assertEqual((result[0]["state"], result[0]["block_id"]), ("unknown", None))
        self.assertEqual([request.method for request in ambiguous.requests], ["GET"])

    def test_a_rejected_upload_keeps_its_block_for_the_retry(self):
        feishu = Feishu()
        feishu.answers = {("POST", "/children"): [CREATED], ("POST", "/medias/upload_all"): [httpx.Response(400, json={"code": 99991679})]}
        items = self.attach(feishu, pending_attachments([{"report_id": "r1", "filename": "a-审核报告.pdf", "fingerprint": SHA[:12]}]))
        self.assertEqual((items[0]["state"], items[0]["block_id"], items[0]["error_code"]), ("failed", "fileA", "feishu_report_attach_rejected:99991679"))
        again = Feishu()
        again.answers = {("POST", "/medias/upload_all"): [ok({"file_token": "boxA"})], ("PATCH", "/blocks/fileA"): [ok()]}
        self.assertEqual(self.attach(again, items)[0]["state"], "attached")
        self.assertEqual([request.url.path.rsplit("/", 1)[-1] for request in again.requests], ["upload_all", "fileA"])

    def test_a_pdf_that_cannot_be_read_is_not_attached_and_says_so(self):
        feishu = Feishu()
        items = pending_attachments([{"report_id": "r1", "filename": "a-审核报告.pdf", "fingerprint": SHA[:12]}])

        def missing(_report_id):
            raise ValueError("report_artifact_conflict")

        with feishu.client() as http:
            result = attach_report_pdfs(http, "test-token", "doc123", items, missing, self.persist, pause=lambda _seconds: None)
        self.assertEqual((result[0]["state"], result[0]["error_code"]), ("failed", "report_pdf_unavailable"))
        self.assertEqual(feishu.requests, [])


if __name__ == "__main__":
    unittest.main()
