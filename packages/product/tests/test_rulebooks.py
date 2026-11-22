"""Rulebook revisions are read by directory convention, for any customer, and never claimed to be in audit by guess."""

import json
from pathlib import Path

from product.rulebooks import list_revisions, problems, read_revision

ROOT = Path(__file__).resolve().parents[3] / "assets"


def write(root: Path, customer: str, version: str, revision: str, rules: dict[str, list[tuple[str, str]]], table: str | None = None) -> None:
    for code, clauses in rules.items():
        path = root / customer / "rulebook" / version / revision / "rules" / f"{code}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"title": f"{code} 文件（示例规则库 {version}）", "rules": [{"id": cid, "category": f"ACME-{code}-要点", "rule_to_check": text} for cid, text in clauses]}
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    if table:
        (root / customer / "rulebook" / version / f"v0.0-m0 到 {version}-{revision} 对照.md").write_text(table, encoding="utf-8")


def test_any_customer_revisions_with_their_changes_in_the_rulebook_order(tmp_path):
    write(tmp_path, "acme", "v0.0", "m0", {"A": [("A:1", "旧条款")]})
    table = "# 对照\n\n|新条款|线上条款|对照|差别|问卷卡片|\n|---|---|---|---|---|\n|B:1|—|新增|要求签字|—|\n|A:1|A:1|意思变了（待复核）|改了阈值|A:1#1|\n"
    write(tmp_path, "acme", "v1.0", "m0", {"A": [("A:1", "新条款")], "B": [("B:1", "签字")]}, table)
    (tmp_path / "acme" / "rulebook" / "v1.0" / "draft").mkdir()  # not a revision directory
    assert [(row["version"], row["revision"], row["clause_count"], row["base"], row["changes"]) for row in list_revisions(tmp_path, "acme")] == [
        ("v0.0", 0, 1, None, None),
        ("v1.0", 0, 2, {"version": "v0.0", "revision": 0}, {"same": 0, "changed": 1, "new": 1}),
    ]
    view = read_revision(tmp_path, "acme", "v1.0", 0)
    # Document types follow the comparison table; titles drop the rulebook name, categories their code prefix.
    assert [(document["title"], [(clause["id"], clause["category"], clause["change"]) for clause in document["clauses"]]) for document in view["documents"]] == [
        ("B 文件", [("B:1", "要点", {"kind": "new", "note": "要求签字"})]),
        ("A 文件", [("A:1", "要点", {"kind": "changed", "note": "改了阈值"})]),
    ]
    assert view["in_audit"] is False


def test_unknown_customers_and_revisions_read_nothing(tmp_path):
    write(tmp_path, "acme", "v1.0", "m0", {"A": [("A:1", "条款")]})
    assert list_revisions(tmp_path, "nobody") == [] and list_revisions(tmp_path / "missing", "acme") == []
    for customer, version, revision in (("acme", "v1.0", 1), ("acme", "v9.0", 0), ("../acme", "v1.0", 0), ("acme", "../v1.0", 0)):
        assert read_revision(tmp_path, customer, version, revision) is None


def test_uaes_v1_is_under_review_not_in_audit():
    (row,) = [row for row in list_revisions(ROOT, "uaes") if (row["version"], row["revision"]) == ("v1.0", 0)]
    assert (row["in_audit"], row["clause_count"], row["changes"]) == (False, 63, {"same": 6, "changed": 46, "new": 11})
    view = read_revision(ROOT, "uaes", "v1.0", 0)
    assert len(view["documents"]) == 19 and view["documents"][0]["title"] == "包装方案"
    assert all(clause["change"] for document in view["documents"] for clause in document["clauses"])


def test_a_revision_is_in_audit_only_when_its_clauses_are_the_presets_audits_run(tmp_path):
    from audit.presets import customer_presets

    presets = [preset for preset in customer_presets() if preset.customer == "UAES"]
    write(tmp_path, "uaes", "v0.0", "m1", {preset.entry_id: [(clause.id, clause.text) for clause in preset.clauses] for preset in presets})
    write(tmp_path, "uaes", "v0.0", "m2", {preset.entry_id: [(clause.id, clause.text + "。") for clause in preset.clauses] for preset in presets})
    assert [(row["revision"], row["in_audit"]) for row in list_revisions(tmp_path, "uaes")] == [(1, True), (2, False)]


def test_every_shipped_rulebook_names_its_clauses_by_document_type_and_number():
    customers = [path.name for path in ROOT.iterdir() if (path / "rulebook").is_dir()]
    assert "uaes" in customers
    assert {customer: problems(ROOT, customer) for customer in customers} == {customer: [] for customer in customers}


def test_a_name_off_its_file_repeated_or_changed_within_a_version_is_found(tmp_path):
    write(tmp_path, "acme", "v1.0", "m0", {"A": [("A:1", "条款"), ("A:2", "条款")], "B": [("B:1", "条款")]})
    write(tmp_path, "acme", "v1.0", "m1", {"A": [("A:1", "改"), ("A:1", "重复"), ("B:3", "放错文件")], "B": [("B:1", "条款")]})
    write(tmp_path, "acme", "v2.0", "m0", {"A": [("A:9", "新版本可以改编号")]})
    assert problems(tmp_path, "acme") == [
        "v1.0 修订 1 A.json：B:3 不是「A:编号」",
        "v1.0 修订 1：A:1 出现 2 次",
        "v1.0 修订 1 的条款编号与修订 0 不同（多 B:3，少 A:2）；编号变了应升版本",
    ]
