"""A revision prepared offline is checked against the customer's original before it can be imported."""

import json

import pytest
from product import rulebook_import


def revision(tmp_path, text):
    rules = tmp_path / "uaes" / "rulebook" / "v9.0" / "m0" / "rules"
    rules.mkdir(parents=True)
    (rules / "A.json").write_text(json.dumps({"title": "A 文件", "rules": [{"id": "A:1", "category": "签字", "rule_to_check": text}]}), encoding="utf-8")
    original = tmp_path / "原件.md"
    original.write_text("## 适用范围\n\n本节适用于全部 A 文件。\n\n### 规则 1\n\n批准栏须**已签名**并填写日期，D\\<10 不通过。\n", encoding="utf-8")
    return rules.parent, original


def test_markdown_layout_is_ignored_and_a_shared_scope_may_lead_the_clause(tmp_path):
    directory, original = revision(tmp_path, "本节适用于全部 A 文件。批准栏须已签名并填写日期，D<10 不通过。")
    bundle = rulebook_import.checked(directory, original)
    assert (bundle["customer_id"], bundle["version"], bundle["revision"], bundle["origin"]) == ("uaes", "v9.0", 0, "local")
    assert bundle["documents"][0]["clauses"][0]["id"] == "A:1"


def test_a_reworded_sentence_is_refused(tmp_path):
    directory, original = revision(tmp_path, "批准栏须已签名（电子签名也可）并填写日期，D<10 不通过。")
    with pytest.raises(SystemExit, match="A:1"):
        rulebook_import.checked(directory, original)


def test_the_bundle_is_the_body_the_revision_endpoint_takes(tmp_path, capsys):
    directory, original = revision(tmp_path, "本节适用于全部 A 文件。批准栏须已签名并填写日期，D<10 不通过。")
    assert rulebook_import.main(["bundle", str(directory), "--original", str(original)]) == 0
    out = capsys.readouterr()
    assert set(json.loads(out.out)) == {"version", "revision", "base", "documents", "changes", "origin"}
    assert out.err.strip() == "POST /rulebooks/uaes/revisions"
