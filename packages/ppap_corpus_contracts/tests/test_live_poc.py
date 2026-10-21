"""The live runner fails before writes on the wrong candidate or charging policy."""

import json
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from ppap_corpus_contracts.live_poc import run_live
from ppap_corpus_contracts.poc_oracle import load_poc_cases


@pytest.mark.parametrize("changed", [{"git_sha": "other"}, {"usage_charge_enabled": True}])
def test_preflight_does_not_write_to_an_unapproved_candidate(tmp_path, changed):
    writes = []

    def respond(request):
        if request.method != "GET":
            writes.append(request.url.path)
        body = {"git_sha": "a" * 40, "profile": "isolated-dev", "usage_charge_enabled": False, **changed}
        return httpx.Response(200, json=body)

    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(respond)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, isolated_login=True)
    assert result["outcome"] == "NOT_PROVEN"
    assert writes == []
    assert json.loads((output / "report.json").read_text())["run"]["outcome"] == "NOT_PROVEN"


def test_existing_output_cannot_start_a_second_proof_run(tmp_path):
    with httpx.Client(base_url="http://test") as client:
        with pytest.raises(FileExistsError):
            run_live(client, candidate_sha="a" * 40, output=tmp_path)


class ProductAPI:
    """External HTTP fixture, never used by the real acceptance command."""

    def __init__(self, failure=""):
        self.failure = failure
        self.changed_round_id = None
        self.changed_file_name = None
        self.changed_rule_index = None
        self.files, self.parses, self.rules, self.drafts, self.rounds = {}, {}, {}, {}, {}
        self.cases = {case.file_name: case for case in load_poc_cases()}
        self.writes = []

    def __call__(self, request):
        path, method = request.url.path, request.method
        if method != "GET":
            self.writes.append((method, path))
        data = json.loads(request.content) if request.content and not path.endswith("/blob") else {}
        config = {"git_sha": "a" * 40, "profile": "isolated-dev", "usage_charge_enabled": False}
        if path in {"/public-config", "/version"}:
            return httpx.Response(200, json=config)
        if path == "/ready":
            return httpx.Response(200, json={**config, "status": "ready", "postgres": {"state": "ok"}, "object_storage": {"state": "ok"}})
        if path == "/isolated-dev/sessions":
            return httpx.Response(200, json={})
        if path == "/files":
            version = {**data, "file_id": str(uuid4()), "id": str(uuid4()), "status": "ready"}
            self.files[version["id"]] = version
            return httpx.Response(201, json=version)
        if path.startswith("/files/"):
            version = self.files[path.split("/")[4]]
            if path.endswith("/blob"):
                version["sha256"] = sha256(request.content).hexdigest()
                return httpx.Response(200, json={})
            if path.endswith("/download"):
                raise AssertionError("This fixture uses the file SHA readback, not raw downloads")
            if path.endswith("/rule-sources"):
                return httpx.Response(200, json={"units": [{"id": "source", "location": "表 1", "excerpt": "原始要求"}]})
            return httpx.Response(200, json=version)
        if path == "/rules/parse":
            counts = {"全功能检验报告": 11, "过程流程图": 9, "潜在过程失效模式及后果分析": 7, "控制计划": 11, "过程特殊特性清单": 2}
            body = {
                "kind": "engine",
                "type": data["document_type"],
                "rule_profile": "v3",
                "customers": [],
                "models": [],
                "parts": [],
                "rules": [{"id": str(index), "category": "要求", "rule_to_check": "原始要求", "priority": None, "type_of_rule": "text"} for index in range(1, counts[data["document_type"]] + 1)],
            }
            parsed = {"id": data["request_id"], "status": "succeeded", "result": {"title": "原始规则", "body": body, "source_file_version_id": data["source_file_version_id"]}}
            self.parses[parsed["id"]] = parsed
            return httpx.Response(202, json=parsed)
        if path.startswith("/rules/parse/"):
            return httpx.Response(200, json=self.parses[path.rsplit("/", 1)[1]])
        if path == "/rules":
            rule = {**data, "id": data["request_id"]}
            self.rules[rule["id"]] = rule
            return httpx.Response(201, json=rule)
        if path.startswith("/rules/"):
            return httpx.Response(200, json=self.rules[path.rsplit("/", 1)[1]])
        if path == "/drafts":
            draft = {"id": data["request_id"], "items": []}
            self.drafts[draft["id"]] = draft
            return httpx.Response(201, json=draft)
        if path.startswith("/drafts/"):
            draft = self.drafts[path.split("/")[2]]
            if path.endswith("/accept"):
                result = {
                    "id": str(uuid4()),
                    "source_draft_id": draft["id"],
                    "analysis_mode": "standard",
                    "execution": "completed",
                    "result_job_id": str(uuid4()),
                    "inputs": draft["items"],
                    "coverage": {"rule_items": []},
                    "findings": [],
                }
                for item in draft["items"]:
                    version = self.files[item["file_version_id"]]
                    item["rule"] = {"id": item["rule_version_id"], "body": self.rules[item["rule_version_id"]]["body"]}
                    for index, expected in self.cases[version["original_filename"]].expected.items():
                        key = f"{version['id']}:rule:{index}"
                        result["coverage"]["rule_items"].append(
                            {"id": str(index), "file_version_id": version["id"], "rule_version_id": item["rule_version_id"], "coverage": "mapped", "status": expected.status, "item_key": key}
                        )
                        result["findings"].append({"item_key": key, "status": expected.status, "suggestions": "真实结果的 HTTP 样例"})
                if self.failure == "duplicate":
                    result["coverage"]["rule_items"].append(result["coverage"]["rule_items"][0].copy())
                if self.failure == "duplicate_finding":
                    result["findings"].insert(0, {**result["findings"][0], "status": "ERR"})
                if self.failure == "wrong_coverage_binding":
                    result["coverage"]["rule_items"][0]["rule_version_id"] = "another-rule-version"
                if self.failure == "no_inline_rule_version":
                    for row in result["coverage"]["rule_items"]:
                        row.pop("rule_version_id")
                if self.failure == "wrong_binding":
                    result["inputs"][0]["rule_version_id"] = "different"
                if self.failure == "missing":
                    result["coverage"]["rule_items"].pop()
                if self.failure == "failed":
                    result["execution"] = "failed"
                if self.failure == "timeout":
                    result["execution"] = "queued"
                if (
                    self.failure
                    in {
                        "missing_frozen_file",
                        "extra_frozen_file",
                        "substituted_frozen_file",
                        "frozen_profile_drift",
                        "frozen_rule_text_drift",
                        "profile_drift_readback",
                        "reordered_inputs",
                        "one_status_mismatch",
                        "different_explanation",
                    }
                    and self.changed_round_id is None
                    and (self.failure != "reordered_inputs" or len(result["inputs"]) > 1)
                ):
                    self.changed_round_id = result["id"]
                    if self.failure == "missing_frozen_file":
                        result["inputs"] = result["inputs"][1:]
                    elif self.failure == "extra_frozen_file":
                        result["inputs"] = [*result["inputs"], {**result["inputs"][0], "file_version_id": str(uuid4())}]
                    elif self.failure == "substituted_frozen_file":
                        result["inputs"][0]["file_version_id"] = str(uuid4())
                    elif self.failure == "frozen_profile_drift":
                        body = result["inputs"][0]["rule"]["body"]
                        result["inputs"][0]["rule"]["body"] = {**body, "rule_profile": "v2"}
                    elif self.failure == "frozen_rule_text_drift":
                        body = result["inputs"][0]["rule"]["body"]
                        result["inputs"][0]["rule"]["body"] = {**body, "rules": [{**body["rules"][0], "rule_to_check": "已改变的冻结规则正文"}, *body["rules"][1:]]}
                    elif self.failure == "reordered_inputs":
                        result["inputs"] = list(reversed(result["inputs"]))
                    elif self.failure in {"one_status_mismatch", "different_explanation"}:
                        row = result["coverage"]["rule_items"][0]
                        finding = next(item for item in result["findings"] if item["item_key"] == row["item_key"])
                        self.changed_file_name = self.files[row["file_version_id"]]["original_filename"]
                        self.changed_rule_index = int(row["id"])
                        if self.failure == "one_status_mismatch":
                            row["status"] = finding["status"] = "NG" if row["status"] == "PASS" else "PASS"
                        else:
                            finding["suggestions"] = "说明文字已更新\n  审核结果未改变"
                self.rounds[result["id"]] = result
                draft["round"] = result
                if self.failure == "lost_accept_response":
                    raise httpx.ReadTimeout("private transport details must not enter reports", request=request)
                return httpx.Response(201, json=result)
            if method == "PUT":
                draft.update(data)
            return httpx.Response(200, json=draft.get("round") if path.endswith("/round") else draft)
        if path.startswith("/rounds/"):
            result = self.rounds[path.rsplit("/", 1)[1]]
            if self.failure == "profile_drift_readback" and result["id"] == self.changed_round_id:
                body = result["inputs"][0]["rule"]["body"]
                result["inputs"][0]["rule"]["body"] = {**body, "rule_profile": "v2"}
            return httpx.Response(200, json=result)
        raise AssertionError(f"Unexpected external API request: {method} {path}")


@pytest.mark.parametrize(
    "failure, outcome",
    [
        ("", "PROVEN"),
        ("lost_accept_response", "PROVEN"),
        ("duplicate", "NOT_PROVEN"),
        ("wrong_binding", "NOT_PROVEN"),
        ("missing", "NOT_PROVEN"),
        ("failed", "NOT_PROVEN"),
        ("timeout", "NOT_PROVEN"),
        ("duplicate_finding", "NOT_PROVEN"),
        ("wrong_coverage_binding", "NOT_PROVEN"),
        ("no_inline_rule_version", "PROVEN"),
        ("reordered_inputs", "PROVEN"),
        ("different_explanation", "PROVEN"),
        ("one_status_mismatch", "NOT_PROVEN"),
    ],
)
def test_full_public_flow_keeps_all_cases_and_never_reaccepts(tmp_path, failure, outcome):
    api = ProductAPI(failure)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, isolated_login=True, timeout=0.001, poll_interval=0)
    assert result["outcome"] == outcome
    assert len([path for method, path in api.writes if path.endswith("/accept")]) == 8
    assert len([path for method, path in api.writes if path == "/rules/parse"]) == 5
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["selected_cases"] == 27
    assert report["summary"]["total_rules"] == 220
    assert "private transport" not in (output / "report.json").read_text()
    assert (output / "report.md").is_file()
    if failure == "reordered_inputs":
        changed = api.rounds[api.changed_round_id]
        original = api.drafts[changed["source_draft_id"]]["items"]
        assert len(changed["inputs"]) > 1
        assert changed["inputs"] != original
    if failure == "one_status_mismatch":
        assert result["errors"] == []
        assert result["attribution"] == "inconclusive"
        assert report["summary"]["mismatches"] == 1
        mismatches = [row for row in report["rule_results"] if row["kind"] != "match"]
        assert len(mismatches) == 1
        row = mismatches[0]
        assert row["round_id"] == api.changed_round_id
        assert row["file_name"] == api.changed_file_name
        assert row["rule_index"] == api.changed_rule_index
        expected = api.cases[api.changed_file_name].expected[api.changed_rule_index].status
        assert row["expected_status"] == expected
        assert row["actual_status"] == ("NG" if expected == "PASS" else "PASS")
        assert row["kind"] == "status_mismatch"
        assert row["failure_stage"] == "comparison"
    if failure == "different_explanation":
        assert report["summary"]["mismatches"] == 0
        assert all(row["kind"] == "match" for row in report["rule_results"])
        changed = [row for row in report["rule_results"] if row["round_id"] == api.changed_round_id and row["file_name"] == api.changed_file_name and row["rule_index"] == api.changed_rule_index]
        assert len(changed) == 1
        assert changed[0]["agent_suggestions"] == "说明文字已更新\n  审核结果未改变"


@pytest.mark.parametrize(
    "failure, stage, kind",
    [
        ("missing_frozen_file", "accept", "setup_failure"),
        ("extra_frozen_file", "accept", "setup_failure"),
        ("substituted_frozen_file", "accept", "setup_failure"),
        ("frozen_profile_drift", "accept", "setup_failure"),
        ("frozen_rule_text_drift", "accept", "setup_failure"),
        ("profile_drift_readback", "audit", "execution_failure"),
    ],
)
def test_changed_frozen_inputs_reject_only_the_affected_group_without_reaccepting(tmp_path, failure, stage, kind):
    api = ProductAPI(failure)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, isolated_login=True, timeout=0.001, poll_interval=0)

    assert result["outcome"] == "NOT_PROVEN"
    assert result["attribution"] == "inconclusive"
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error["stage"] == stage
    assert error["code"] == "frozen_input_mismatch"
    assert error["round_id"] == api.changed_round_id
    accept_paths = [path for method, path in api.writes if path.endswith("/accept")]
    assert len(accept_paths) == len(set(accept_paths)) == 8
    assert sum(path == "/rules/parse" for method, path in api.writes) == 5
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["selected_cases"] == 27
    assert report["summary"]["total_rules"] == 220
    affected = [row for row in report["rule_results"] if row["round_id"] == api.changed_round_id]
    assert {row["bundle"] for row in affected} == {error["bundle"]}
    assert {row["failure_stage"] for row in affected} == {stage}
    assert {row["kind"] for row in affected} == {kind}
    assert all(row["kind"] == "match" for row in report["rule_results"] if row["round_id"] != api.changed_round_id)
