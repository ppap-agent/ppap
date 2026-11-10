"""Native Word bytes and published profiles through Product API/Worker/PG.

Object storage and visual-provider responses are controlled. Word parsing,
rule binding, engine decisions and public result projection are not replaced.
These tests do not prove real visual recognition or PDF generation.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from io import BytesIO
from unittest.mock import patch

import pytest
from api import create_app
from api import web_contract as wc
from audit.suite_migration import migrated_builtin_suite
from docx import Document, oxml
from fastapi.testclient import TestClient
from PIL import Image
from product import build_product_store
from product.settings import Settings
from pydantic import TypeAdapter
from worker import process_claimed


@pytest.fixture
def native_docx_system(auth_store_factory, core_system):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "OCR_", "MODEL_NAME", "FEISHU_"))}
    with patch.dict(os.environ, keep, clear=True):
        os.environ.update(OCR_MODEL_NAME="controlled-ocr", OCR_OPENAI_API_KEY="controlled", OCR_OPENAI_BASE_URL="https://ocr.invalid/v1")
        settings = Settings(usage_charge_enabled=False, git_sha="a" * 40, OCR_MODEL_NAME="controlled-ocr", OCR_OPENAI_API_KEY="controlled", OCR_OPENAI_BASE_URL="https://ocr.invalid/v1")
        store = auth_store_factory(settings)
        store.seed_isolated_dev()
        core_system.install(store)
        worker = build_product_store(settings, objects=store.objects, role="worker")
        client = TestClient(create_app(settings, auth_store=store))
        assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
        try:
            yield client, worker
        finally:
            client.close()


def upload(client, filename, content):
    created = client.post("/files", json={"original_filename": filename, "byte_size": len(content)})
    assert created.status_code == 201, created.text
    version = created.json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=content).status_code == 200
    finished = client.post(path + "/finalize")
    assert finished.status_code == 200, finished.text
    return finished.json()


def parse_publish_and_accept(client, worker, filename, content, rule_body, core_system, checker_source):
    source_bytes = json.dumps(rule_body, ensure_ascii=False).encode()
    source = upload(client, "已确认规则.json", source_bytes)
    parse_id = str(uuid.uuid4())
    queued = client.post("/rules/parse", json={"request_id": parse_id, "source_file_id": source["file_id"], "source_file_version_id": source["id"]})
    assert queued.status_code == 202, queued.text
    parsed_job = worker.claim_job("word-rule-parse")
    assert parsed_job is not None and parsed_job.id == parse_id and parsed_job.kind == "rule_parse"
    process_claimed(worker, parsed_job, "word-rule-parse")
    parsed = client.get("/rules/parse/" + parse_id).json()
    assert parsed["status"] == "succeeded", parsed
    candidate = parsed["result"]
    assert candidate["body"] == rule_body
    assert candidate["source_file_version_id"] == source["id"]
    published = client.post("/rules", json=candidate)
    assert published.status_code == 201, published.text
    rule = published.json()
    assert rule["source_file_version_id"] == source["id"]
    assert client.get("/rules/" + rule["id"]).json()["body"] == rule["body"]
    assert {key: rule["body"][key] for key in rule_body} == rule_body
    assert set(checker_source.entrypoints) == {row["id"] for row in rule_body["rules"]}
    core_system.adopt(worker, rule["id"], checker_source.files["checker.py"].decode(), dict(checker_source.entrypoints))
    target = upload(client, filename, content)
    draft = client.post("/drafts").json()["id"]
    saved = client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": rule["id"]}]})
    assert saved.status_code == 200, saved.text
    key = "native-word-" + str(uuid.uuid4())
    accepted = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": key})
    assert accepted.status_code == 201, accepted.text
    frozen = accepted.json()
    replayed = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": key})
    assert replayed.status_code == 201 and replayed.json() == frozen
    assert len(frozen["inputs"]) == 1
    assert frozen["inputs"][0]["file_version_id"] == target["id"]
    assert frozen["inputs"][0]["rule_version_id"] == rule["id"]
    frozen_body = frozen["inputs"][0]["rule"]["body"]
    assert {key: frozen_body[key] for key in rule_body} == rule_body
    assert frozen_body["checker_binding"] == dict(checker_source.entrypoints)
    return target, rule, frozen


def assert_completed_word(client, worker, target, rule, frozen, content, expected):
    path = "/rounds/" + frozen["id"]
    response = client.get(path, params={"read_view": "true"})
    assert response.status_code == 200, response.text
    loaded = response.json()
    TypeAdapter(wc.Round).validate_python(loaded)
    assert loaded["execution"] == "completed"
    assert loaded["inputs"] == frozen["inputs"]
    assert loaded["failure"] is None
    assert [row["locator"]["rule_item_id"] for row in loaded["findings"]] == [str(index) for index in range(1, len(expected) + 1)]
    assert [row["status"] for row in loaded["findings"]] == expected, loaded["findings"]
    assert all(row["locator"]["file_version_id"] == target["id"] for row in loaded["findings"])
    assert [row["coverage"] for row in loaded["coverage"]["rule_items"]] == ["executed"] * len(expected)
    assert [row["status"] for row in loaded["coverage"]["rule_items"]] == expected
    assert loaded["coverage"]["unchecked"] == []
    assert loaded["read_view"]["unassigned_findings"] == []
    files = loaded["read_view"]["files"]
    assert len(files) == 1
    assert files[0]["file"]["id"] == target["id"]
    assert files[0]["file"]["sha256"] == hashlib.sha256(content).hexdigest()
    audit = files[0]["audit"]
    assert audit["execution"] == "completed"
    assert [row["finding"]["status"] for row in audit["checks"]] == expected
    assert audit["counts"]["pass_count"] == expected.count("PASS")
    assert audit["counts"]["ng_count"] == expected.count("NG")
    assert audit["counts"]["error_count"] == 0
    assert audit["conclusion"] == ("NG" if "NG" in expected else "PASS")
    original = client.get(f"{path}/files/{target['file_id']}/versions/{target['id']}/download")
    assert original.status_code == 200 and original.content == content
    assert client.get("/rules/" + rule["id"]).json()["body"] == rule["body"]
    for _ in range(2):
        assert client.get(path, params={"read_view": "true"}).json() == loaded
    assert [row["id"] for row in client.get("/rounds").json()["rounds"]] == [frozen["id"]]
    assert worker.claim_job("no-word-replay") is None


def equipment_docx_bytes():
    document = Document()
    document.add_paragraph("量检具、检测试验设备清单")
    table = document.add_table(rows=4, cols=11)
    table.cell(0, 0).merge(table.cell(0, 10)).text = "设备开发计划、验收清单"
    table.cell(1, 0).merge(table.cell(2, 0)).text = "产线/工序"
    table.cell(1, 1).merge(table.cell(1, 7)).text = "设备开发"
    table.cell(1, 8).merge(table.cell(1, 10)).text = "设备验收"
    headers = ("设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "验收结果", "整改措施", "整改计划")
    for column, header in enumerate(headers, 1):
        table.cell(2, column).text = header
    for column, value in enumerate(("焊接工序", "量检具", "供应商A", "EQ-01", "0", "1", "专用", "2026-09-01", "通过", "", "")):
        table.cell(3, column).text = value
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_equipment_docx_upload_parse_publish_and_worker_result_keep_v2_frozen(native_docx_system, core_system):
    client, worker = native_docx_system
    content = equipment_docx_bytes()
    body = {
        "kind": "engine",
        "type": "设备清单",
        "rule_profile": "v2",
        "customers": [],
        "models": [],
        "parts": [],
        "rules": [
            {"id": "1", "category": "设备开发", "priority": "P1", "rule_to_check": "核对设备开发清单。", "type_of_rule": "table"},
            {"id": "2", "category": "设备验收", "priority": "P1", "rule_to_check": "核对设备验收清单。", "type_of_rule": "table"},
        ],
    }
    target, rule, frozen = parse_publish_and_accept(client, worker, "量检具、检测试验设备清单.docx", content, body, core_system, migrated_builtin_suite("v2", "设备清单"))
    job = worker.claim_job("equipment-word-audit")
    assert job is not None and job.kind == "audit" and job.round_id == frozen["id"]
    process_claimed(worker, job, "equipment-word-audit")
    assert_completed_word(client, worker, target, rule, frozen, content, ["PASS", "PASS"])


def psw_docx_bytes(*, include_images):
    document = Document()
    delivery = document.add_table(rows=3, cols=2)
    delivery.cell(0, 0).merge(delivery.cell(0, 1)).text = "生产件批准交付物文件清单"
    delivery.cell(1, 0).text, delivery.cell(1, 1).text = "项目", "附件"
    delivery.cell(2, 0).text, delivery.cell(2, 1).text = "尺寸报告", "尺寸报告.xlsx"
    main = document.add_table(rows=60, cols=6)
    main.cell(0, 0).merge(main.cell(0, 5)).text = "零件提交保证书 PSW"
    main.cell(1, 0).text, main.cell(1, 3).text = "供应商名称/供应商代码", "顾客名称"
    main.cell(2, 0).text, main.cell(2, 3).text = "示例总公司/S001", "理想汽车"
    for row, label, value in (
        (3, "重量(kg)", "1.2345"),
        (4, "零件名称", "扰流板"),
        (5, "理想汽车零件号", "L123"),
        (6, "供方料号", "S123"),
        (7, "图纸号", "D123"),
        (8, "模具穴号", "1"),
        (9, "工程更改版本", "A"),
        (10, "版本日期", "2026-08-01"),
        (11, "街道地址", "示例路1号"),
        (12, "城市", "上海"),
        (13, "省", "上海"),
        (14, "国家", "中国"),
        (15, "顾客名称", "理想汽车"),
        (16, "设计开发", "张三"),
        (17, "采购工程", "李四"),
        (18, "PTC", "王五"),
        (19, "经授权的供方代表签字", "赵六"),
    ):
        main.cell(row, 0).text, main.cell(row, 1).text = label, value
    main.cell(31, 0).text = "提交原因"
    nested = main.cell(32, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).paragraphs[0]._p.append(
        oxml.parse_xml("""<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
          xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">
          <w:sdtPr><w:alias w:val="首次提交"/><w14:checkbox><w14:checked w14:val="1"/></w14:checkbox></w:sdtPr>
          <w:sdtContent><w:p><w:r><w:t>☒ 首次提交</w:t></w:r></w:p></w:sdtContent>
        </w:sdt>""")
    )
    main.cell(44, 0).text = "认可情况"
    for column, text in enumerate(("☒ 批准", "☐ 条件批准", "☐ 拒绝"), 1):
        main.cell(44, column).text = text
    for row, label in enumerate(("尺寸测量", "外观标准", "材料和功能试验", "统计过程数据"), 49):
        main.cell(row, 0).text = "☒ " + label
    main.cell(54, 0).text, main.cell(54, 1).text = "这些结果均满足所有图纸和规范要求", "☒ 是"
    if include_images:
        for row, size, color in ((56, (48, 48), (220, 0, 0)), (57, (32, 40), (0, 0, 180))):
            with Image.new("RGB", size, color) as image:
                stream = BytesIO()
                image.save(stream, format="PNG")
            stream.seek(0)
            main.cell(row, 4).paragraphs[0].add_run().add_picture(stream)
    content = BytesIO()
    document.save(content)
    return content.getvalue()


def visual_provider_response(request):
    images = []
    for payload in request["images"]:
        with Image.open(BytesIO(payload)) as image:
            images.append((image.size, image.getextrema()))
    assert images == [((48, 48), ((220, 220), (0, 0), (0, 0))), ((32, 40), ((0, 0), (0, 0), (180, 180)))]
    assert "word/document.xml" in request["prompt"]
    facts = {
        "psw_stamp_ending_text": "公司",
        "psw_stamp_ends_with_company": True,
        "authorization_table_present": False,
        "authorization_company_stamp_present": False,
        "authorized_signature_present": True,
        "psw_stamp_present": True,
    }
    result = {}
    for field, schema in request["response_schema"]["schema"]["properties"].items():
        result[field] = [
            {
                "scope": scope,
                "value": facts.get(field),
                "sources": [{"image_index": 0, "bbox": [0, 0, 1, 1]}],
                "covered_image_indices": list(range(len(images))),
                "coverage": "complete",
                ("reason" if field == "psw_stamp_ends_with_company" else "excerpt"): "受控提供方观察",
            }
            for scope in schema["items"]["properties"]["scope"]["enum"]
        ]
    return result


@pytest.mark.parametrize("include_images", [True, False], ids=["complete-visual-evidence", "missing-required-images"])
def test_psw_docx_upload_parse_publish_and_worker_result_keep_v3_frozen(native_docx_system, core_system, monkeypatch, include_images):
    client, worker = native_docx_system
    content = psw_docx_bytes(include_images=include_images)
    descriptions = (
        "生产件批准交付物文件清单附件检查",
        "重量字段格式检查",
        "印章及授权声明检查",
        "供应商名称格式检查",
        "签字与盖章完整性检查",
        "关键字段非空检查",
        "提交原因选项检查",
        "认可情况及结果勾选检查",
        "条件批准日期时效检查",
        "条件批准补充评论检查",
    )
    body = {
        "kind": "engine",
        "type": "零件提交保证书",
        "rule_profile": "v3",
        "customers": [],
        "models": [],
        "parts": [],
        "rules": [{"id": str(index), "category": text, "priority": "P0", "rule_to_check": text, "type_of_rule": "image" if index in {3, 5} else "table"} for index, text in enumerate(descriptions, 1)],
    }
    calls = []

    def visual(**request):
        calls.append(request)
        return visual_provider_response(request)

    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", visual)
    core_system.install(worker, controlled_model=True)
    target, rule, frozen = parse_publish_and_accept(client, worker, "零件提交保证书.docx", content, body, core_system, migrated_builtin_suite("v3", "零件提交保证书"))
    job = worker.claim_job("psw-word-audit")
    assert job is not None and job.kind == "audit" and job.round_id == frozen["id"]
    process_claimed(worker, job, "psw-word-audit")
    assert core_system.audit_jobs[-1].allow_model_calls is True
    expected = ["PASS"] * 10 if include_images else ["PASS", "PASS", "NG", "PASS", "NG", "PASS", "PASS", "PASS", "PASS", "PASS"]
    calls_after_audit = len(calls)
    assert calls_after_audit == (2 if include_images else 0)
    assert_completed_word(client, worker, target, rule, frozen, content, expected)
    assert len(calls) == calls_after_audit
