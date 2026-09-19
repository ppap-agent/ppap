"""Tool tests only. These tests do not prove live product behavior."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.live import (
    NotProven,
    check_findings,
    classify_difference,
    evaluate_corpus,
    human_oracle_authority,
    load_corpus_catalog,
    origin,
    probe,
    required,
    same_origin,
    normalized_findings,
    NoRedirect,
)

ROOT = Path(__file__).resolve().parents[2]


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.oracle = {"source_sha256": "a" * 64, "findings": {"R1": {"status": "NG", "locator": "Sheet1!B2"}}}
        self.actual = [{"id": "R1", "status": "NG", "evidence_state": "available", "locator": "Sheet1!B2", "source_sha256": "a" * 64}]

    def test_valid_semantics(self):
        check_findings(self.actual, self.oracle)

    def test_empty_result_is_not_success(self):
        with self.assertRaises(NotProven): check_findings([], self.oracle)

    def test_missing_oracle_is_not_success(self):
        with self.assertRaises(NotProven): check_findings(self.actual, {})

    def test_duplicate_rule_is_rejected(self):
        with self.assertRaises(NotProven): check_findings(self.actual * 2, self.oracle)

    def test_missing_rule_is_rejected(self):
        self.oracle["findings"]["R2"] = {"status": "PASS", "locator": None}
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_extra_rule_is_rejected_for_selected_case(self):
        other = dict(self.actual[0], id="R2")
        with self.assertRaises(NotProven): check_findings(self.actual + [other], self.oracle)

    def test_execution_completed_is_not_pass(self):
        self.actual[0]["status"] = "completed"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_silent_pass_mapping_rejected(self):
        self.actual[0]["status"] = "PASS"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_wrong_source_rejected(self):
        self.actual[0]["source_sha256"] = "b" * 64
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_wrong_locator_rejected(self):
        self.actual[0]["locator"] = "Sheet1!B3"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_unavailable_evidence_rejected(self):
        self.actual[0]["evidence_state"] = "missing"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_known_locator_gap_must_be_disclosed(self):
        self.oracle["findings"]["R1"]["locator"] = None
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)
        self.actual[0]["evidence_state"] = "missing"
        check_findings(self.actual, self.oracle)

    def test_unspecified_evidence_is_not_disclosed_gap(self):
        del self.oracle["findings"]["R1"]["locator"]
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_row_order_not_a_contract(self):
        self.oracle["findings"]["R2"] = {"status": "ERR", "locator": None}
        other = {"id": "R2", "status": "ERR", "evidence_state": "missing"}
        check_findings([other] + self.actual, self.oracle)
        check_findings(self.actual + [other], self.oracle)

    def test_history_comparison_ignores_order(self):
        rows = [{"id": "B", "status": "PASS"}, {"id": "A", "status": "NG"}]
        self.assertEqual(normalized_findings(rows), normalized_findings(rows[::-1]))

    def test_probe_does_not_follow_redirect(self):
        with self.assertRaises(NotProven):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://else.test")

    def test_implicit_https_port_is_equivalent(self):
        same_origin("https://example.test:443/round", "https://example.test")

    def test_cross_origin_rejected(self):
        with self.assertRaises(NotProven): same_origin("https://else.test", "https://example.test")

    def test_credential_in_url_rejected(self):
        with self.assertRaises(NotProven): origin("https://user:secret@example.test")

    def test_local_file_url_rejected(self):
        with self.assertRaises(NotProven): origin("file:///tmp/private")

    def test_missing_configuration_not_success(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(NotProven): required("PPAP_WEB_URL")

    def test_cli_missing_target_nonzero_secret_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {k: v for k, v in os.environ.items() if not k.startswith("PPAP_")}
            result = subprocess.run([sys.executable, "tools/live.py", "audit", "--output-dir", directory], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            records = list(Path(directory).glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text())
            self.assertEqual(record["outcome"], "NOT_PROVEN")
            self.assertEqual(record["kind"], "product_child_proof")
            self.assertNotIn("submit_invocations", record["evidence"])
            self.assertEqual(record["reason"], "missing_configuration:PPAP_WEB_URL")


class CorpusTests(unittest.TestCase):
    def test_catalog_keeps_promised_formats_and_public_hashes(self):
        catalog = load_corpus_catalog()
        report = evaluate_corpus(catalog)
        self.assertEqual(report["public_fixtures"], "PROVEN")
        self.assertEqual(report["business_oracle"], "NOT_PROVEN")
        self.assertEqual(report["controlled_corpus"], "NOT_PROVEN")
        self.assertEqual(report["docling_comparison"], "NOT_PROVEN")
        formats = [row["format"] for row in report["matrix"]]
        self.assertEqual(formats, ["xlsx", "pdf", "docx", "doc", "scanned", "cross_file"])
        by_format = {row["format"]: row for row in report["matrix"]}
        self.assertEqual(by_format["xlsx"]["public_fixture"], "present")
        self.assertEqual(by_format["pdf"]["public_fixture"], "present")
        self.assertEqual(by_format["docx"]["public_fixture"], "present")
        self.assertEqual(by_format["doc"]["public_fixture"], "absent")
        self.assertEqual(by_format["scanned"]["human_oracle"], "NOT_PROVEN")
        self.assertEqual(by_format["cross_file"]["replay"], "NOT_PROVEN")
        self.assertEqual(catalog["source_commit"], "53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9")
        self.assertTrue(catalog["oracle_revisions"])
        self.assertTrue(all(row.get("reason") for row in catalog["oracle_revisions"]))
        self.assertTrue(all(suite.get("rule_version") for suite in catalog["controlled_suites"]))
        self.assertTrue(human_oracle_authority("human-tables:ppap-agent@53a5bd2b"))
        self.assertFalse(human_oracle_authority("EXAMPLE_ONLY_replace"))
        self.assertFalse(human_oracle_authority("legacy_engine_batch"))
        self.assertFalse(human_oracle_authority("engine_output"))
        self.assertFalse(human_oracle_authority("NOT_A_BUSINESS_ORACLE"))

    def test_shrinking_promised_formats_is_rejected(self):
        catalog = load_corpus_catalog()
        catalog["promised_formats"] = ["xlsx", "pdf"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(catalog))
            with self.assertRaises(NotProven) as ctx:
                load_corpus_catalog(path)
            self.assertEqual(ctx.exception.code, "corpus_promise_changed")

    def test_engine_output_is_not_an_oracle(self):
        self.assertFalse(human_oracle_authority("legacy_engine"))
        catalog = load_corpus_catalog()
        catalog["public_fixtures"][0]["oracle_authority"] = "reviewed-record-2026-09-19"
        with self.assertRaises(NotProven) as ctx:
            evaluate_corpus(catalog)
        self.assertEqual(ctx.exception.code, "public_fixture_cannot_be_business_oracle")

    def test_difference_classes(self):
        base = {"key_fields": {"part": "A", "weight": "1"}, "statuses": ["PASS", "PASS"]}
        self.assertEqual(classify_difference(base, dict(base)), "equivalent")
        lost = {"key_fields": {"part": "A", "weight": ""}, "statuses": ["NG", "NG"], "conversion_diagnostics": ["structure.insufficient"]}
        self.assertEqual(classify_difference(base, lost), "conversion_loss")
        allowed = {"key_fields": dict(base["key_fields"]), "statuses": ["PASS", "NG"], "allowed_format_rule_differences": [2]}
        self.assertEqual(classify_difference(base, allowed), "approved_format_difference")
        regression = {"key_fields": dict(base["key_fields"]), "statuses": ["NG", "PASS"]}
        self.assertEqual(classify_difference(base, regression), "rule_logic_defect")
        coverage = {"key_fields": dict(base["key_fields"]), "statuses": ["PASS"]}
        self.assertEqual(classify_difference(base, coverage), "rule_logic_defect")

    def test_empty_corpus_root_does_not_scan_cwd(self):
        catalog = load_corpus_catalog()
        with tempfile.TemporaryDirectory() as directory:
            decoy = Path(directory)
            (decoy / "assets" / "v2.0plus").mkdir(parents=True)
            previous = os.getcwd()
            try:
                os.chdir(decoy)
                with patch.dict(os.environ, {"PPAP_CORPUS_ROOT": ""}, clear=False):
                    report = evaluate_corpus(catalog)
            finally:
                os.chdir(previous)
            self.assertEqual(report["controlled_corpus"], "NOT_PROVEN")
            self.assertEqual(report["matrix"][0]["controlled_sample"], "NOT_PROVEN")

    def test_empty_dataset_dirs_are_not_samples(self):
        catalog = load_corpus_catalog()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suite in catalog["controlled_suites"]:
                (root / suite["dataset_root"]).mkdir(parents=True, exist_ok=True)
            report = evaluate_corpus(catalog, corpus_root=str(root))
        self.assertEqual(report["controlled_corpus"], "NOT_PROVEN")
        self.assertEqual(report["business_oracle"], "NOT_PROVEN")
        for row in report["matrix"]:
            self.assertEqual(row["controlled_sample"], "absent")
            self.assertEqual(row["human_oracle"], "NOT_PROVEN")
            self.assertEqual(row["replay"], "NOT_PROVEN")

    def test_named_human_tables_without_bytes_are_not_proven(self):
        catalog = load_corpus_catalog()
        self.assertTrue(human_oracle_authority(catalog["controlled_suites"][0]["oracle_authority"]))
        report = evaluate_corpus(catalog, corpus_root=None)
        self.assertEqual(report["business_oracle"], "NOT_PROVEN")
        self.assertEqual(report["controlled_corpus"], "NOT_PROVEN")

    def test_authorized_checkout_with_sample_bytes_is_replay_ready(self):
        catalog = load_corpus_catalog()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets" / "v2.0plus" / "target.xlsx").parent.mkdir(parents=True)
            (root / "assets" / "v2.0plus" / "target.xlsx").write_bytes(b"PK\x03\x04")
            (root / "assets" / "v2.0plus" / "target.pdf").write_bytes(b"%PDF-1.4\n")
            csv_path = root / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
            csv_path.write_text("id,status\n", encoding="utf-8")
            poc = root / "assets" / "poc测试" / "POC测试样本" / "bundle"
            poc.mkdir(parents=True)
            (poc / "target.xlsx").write_bytes(b"PK\x03\x04")
            stats = root / "assets" / "poc测试" / "poc-测试数据统计表.xlsx"
            stats.write_bytes(b"PK\x03\x04")
            doc = root / "ppap_agent" / "tests" / "fixtures" / "legacy_doc" / "psw.doc"
            doc.parent.mkdir(parents=True)
            doc.write_bytes(b"DOC")
            report = evaluate_corpus(catalog, corpus_root=str(root))
        self.assertEqual(report["controlled_corpus"], "PROVEN")
        self.assertEqual(report["business_oracle"], "PROVEN")
        for row in report["matrix"]:
            self.assertEqual(row["controlled_sample"], "present")
            self.assertEqual(row["human_oracle"], "reviewed")
            self.assertEqual(row["replay"], "ready")

    def test_oracle_revision_requires_reason(self):
        catalog = load_corpus_catalog()
        catalog["oracle_revisions"] = [{"reviewer": "unspecified"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(catalog))
            with self.assertRaises(NotProven) as ctx:
                load_corpus_catalog(path)
            self.assertEqual(ctx.exception.code, "oracle_revision_missing_reason")

    def test_controlled_suite_cannot_use_engine_oracle(self):
        catalog = load_corpus_catalog()
        catalog["controlled_suites"][0]["oracle_kind"] = "legacy_engine"
        with self.assertRaises(NotProven) as ctx:
            evaluate_corpus(catalog)
        self.assertEqual(ctx.exception.code, "controlled_suite_oracle_must_be_human")

    def test_corpus_cli_reports_matrix_without_claiming_business_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "tools/live.py", "corpus", "--output-dir", directory],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 1)
            records = list(Path(directory).glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text())
            self.assertEqual(record["gate"], "P07")
            self.assertEqual(record["kind"], "prerequisite")
            self.assertEqual(record["outcome"], "NOT_PROVEN")
            self.assertEqual(record["reason"], "business_oracle_not_proven")
            self.assertEqual(record["cleanup"], "not_applicable")
            self.assertEqual(record["evidence"]["public_fixtures"], "PROVEN")
            self.assertEqual(len(record["evidence"]["matrix"]), 6)


if __name__ == "__main__": unittest.main()
