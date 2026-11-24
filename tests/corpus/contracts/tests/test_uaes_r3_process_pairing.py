"""Revision 3 cross-file identity, using the candidate entry point and real source cells. Only image transcription is substituted."""

import hashlib
import json
from pathlib import Path

import fitz
import pytest
from agent.execution.audit_runtime import _load_program, _restore_modules
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_suites import _load_leaf
from openpyxl import Workbook, load_workbook

UAES = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = UAES / "rulebook/v1.0/candidates"
V040 = next((UAES / "samples").glob("*V040*")) / "测试文件"


def original(folder, section):
    return next(f for f in folder.glob(f"{section}*/*") if f.is_file())


@pytest.fixture
def candidate(tmp_path):
    loaded = []

    def open_audit(kind, target, peers):
        folder = LEAVES / "uaes" / kind
        modules, saved = _load_program(_load_leaf(LEAVES, "uaes", folder).program_files, tmp_path / f"program-{len(loaded)}")
        loaded.append(saved)
        checker = modules["checker"]

        def file(path):
            return AuditInput(audit_id=path.name, file_url=str(path), file_metadata={"file_version_id": path.name})

        types = {kind: [file(path)] for kind, path in peers.items()}
        group = GroupContextInput(by_doc_type=types, by_audit_id={f.audit_id: f for ff in types.values() for f in ff})
        rule = RuleInput.of(json.loads((folder / "rules.json").read_text()), rule_id=kind)
        return checker.create_audit(file(target), rule, group), checker

    yield open_audit
    for saved in reversed(loaded):
        _restore_modules(saved)


# Checked against the seven different dimension callouts cropped from the actual magnetic-cover originals.
# Bind each transcription to its crop pixels, not to a control-plan row, result, file name, or class mapping.
DIMENSIONS = [
    ("35", "+0.1", "-0.1", False, False),
    ("13.8", "+0.1", "-0.05", True, True),
    ("18.8", "0", "-0.2", True, True),
    ("46.4", "+0.2", "-0.1", False, False),
    ("31.4", "+0.05", "-0.05", False, False),
    ("12.2", "+0.05", "-0.1", False, False),
    ("25.3", "+0.05", "-0.05", False, False),
]


def image_reader(files):
    def pixels(pix):
        return pix.width, pix.height, hashlib.sha256(pix.samples).digest()

    facts = {}
    for file, calls in files:
        with fitz.open(file) as doc:
            for page in doc:
                for picture in page.get_image_info(xrefs=True):
                    if picture["xref"] not in calls:
                        continue
                    area = fitz.Rect(picture["bbox"]) + (-2, -2, 2, 2)
                    pix = page.get_pixmap(clip=area, dpi=min(600, int(1200 * 72 / max(area.width, 1.0))))
                    idx, frame = calls[picture["xref"]]
                    nominal, upper, lower, diameter, envelope = DIMENSIONS[idx]
                    facts[pixels(pix)] = dict(readable=True, name="", nominal=nominal, upper=upper, lower=lower, diameter=diameter, envelope=envelope, frame=str(frame))
    return lambda image, *_: facts.get(pixels(fitz.Pixmap(image)), dict.fromkeys(("name", "nominal", "upper", "lower", "diameter", "envelope", "frame"), None) | {"readable": False})


def test_magnetic_cover_quantitative_spec_is_not_no_dimensions(candidate):
    audit, _ = candidate("U08", original(V040, "10"), {})
    try:
        r = audit.evaluate("CP:5")
        assert r.status == "UNABLE"
        assert "11个加工" in r.suggestions and "46.4" in r.suggestions and "第46行" in r.suggestions
    finally:
        audit.close()


def test_list_dimensions_remain_distinct_from_names(candidate):
    f = original(V040, "11")
    audit, _ = candidate("U07", original(V040, "09"), {"过程特殊特性清单": f})
    try:
        audit._implementation().transcribe = image_reader([(f, {30: (0, 1), 31: (1, 1), 32: (2, 1), 33: (3, 2), 34: (4, 2), 35: (5, 1), 36: (6, 2)})])
        r = audit.evaluate("PFMEA:3")
        assert r.status == "NG" and "第1页" in r.suggestions and "没有独立特性名" in r.suggestions and "46.4" in r.suggestions
        assert "标识不一致" not in r.suggestions
    finally:
        audit.close()


def test_control_plan_compares_a_mark_only_after_pairing_both_keys(candidate):
    flow, fmea = original(V040, "08"), original(V040, "09")
    audit, _ = candidate("U08", original(V040, "10"), {"过程流程图": flow, "潜在过程失效模式及后果分析": fmea})
    try:
        audit._implementation().transcribe = image_reader(
            [
                (flow, {32: (0, 1), 33: (4, 1), 34: (1, 1), 35: (2, 1), 36: (3, 2), 37: (4, 2), 38: (6, 2), 39: (5, 1)}),
                (fmea, {33: (0, 1), 34: (3, 2), 37: (6, 2), 38: (4, 2), 41: (1, 1), 42: (2, 1), 43: (5, 1)}),
            ]
        )
        r = audit.evaluate("CP:8")
        assert r.status == "NG" and "35±0.1" in r.suggestions and "无独立特性名" in r.suggestions, r.suggestions
        assert "配对成功但特殊特性标识不一致" not in r.suggestions
    finally:
        audit.close()


@pytest.mark.parametrize("part", ["776", "A40"])
def test_process_analysis_retains_evidence_for_local_names(candidate, part):
    folder = next((UAES / "samples").glob(f"*{part}*")) / "测试文件"
    audit, _ = candidate("U07", original(folder, "09"), {"过程流程图": original(folder, "08")})
    try:
        r = audit.evaluate("PFMEA:5")
        assert r.status == "UNABLE", r.suggestions
        assert all(text in r.suggestions for text in ["色标", "仓存", "标签", "GP12", "外观检查", "漏检", "喷砂", "喷丸"])
    finally:
        audit.close()


def test_same_step_with_identified_stock_and_inspection_objects(candidate, tmp_path):
    # A short flow containing the three operations, exactly as written in 776.
    folder = next((UAES / "samples").glob("*776*")) / "测试文件"
    source = load_workbook(original(folder, "08"), read_only=True, data_only=True)
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    try:
        for i, row in enumerate(source.worksheets[0].values, 1):
            if i in (10, 11, 13, 25, 26):
                sheet.append(list(row))
    finally:
        source.close()
    flow = tmp_path / "flow.xlsx"
    book.save(flow)
    audit, _ = candidate("U07", original(folder, "09"), {"过程流程图": flow})
    try:
        r = audit.evaluate("PFMEA:5")
        assert r.status == "PASS" and "全部3道" in r.suggestions, r.suggestions
        assert all(x in r.suggestions for x in ["色标", "标签", "外观检查"])
    finally:
        audit.close()


def test_wrapped_bilingual_operations_keep_the_same_chinese_name(candidate):
    audit, _ = candidate("U07", original(V040, "09"), {"过程流程图": original(V040, "08")})
    try:
        result = audit.evaluate("PFMEA:5")
        assert result.status == "UNABLE" and "已确认7道主工序" in result.suggestions, result.suggestions
        # 同名的 2、9、10 原件可以定位，不能用这三条的改进替其他异名工序放行。
        assert all(f"工序{step}（" in result.suggestions for step in (1, 3, 8))
    finally:
        audit.close()
