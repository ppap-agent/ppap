"""The live runner fails before writes on the wrong candidate or charging policy."""

import json
import sys
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from audit.audits import EXECUTOR_DEEP, EXECUTOR_STANDARD
from corpus_contracts.live_poc import RULE_DOCS, main, run_live
from corpus_contracts.poc_oracle import build_bundle_map, load_poc_cases
from product.standard_policy import StandardExecutionPolicy


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
        self.policy = StandardExecutionPolicy(app_revision="a" * 40, model="fixture-model", model_config_revision="c" * 64, tool_config_revision="d" * 64)
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
                    "analysis_mode": draft["analysis_mode"],
                    "executor": EXECUTOR_DEEP if draft["analysis_mode"] == "deep" else EXECUTOR_STANDARD,
                    "executor_revision": "b" * 64 if draft["analysis_mode"] == "deep" else self.policy.engine_revision(),
                    "execution": "completed",
                    "result_job_id": str(uuid4()),
                    "inputs": draft["items"],
                    "coverage": {"rule_items": []},
                    "findings": [],
                }
                if draft["analysis_mode"] == "standard":
                    result["execution_policy"] = self.policy.model_dump(mode="json")
                for item in draft["items"]:
                    version = self.files[item["file_version_id"]]
                    item["rule"] = {"id": item["rule_version_id"], "body": self.rules[item["rule_version_id"]]["body"]}
                    for index, expected in self.cases[version["original_filename"]].expected.items():
                        key = f"{version['id']}:rule:{index}"
                        result["coverage"]["rule_items"].append(
                            {
                                "id": str(index),
                                "file_version_id": version["id"],
                                "rule_version_id": item["rule_version_id"],
                                "coverage": "executed" if draft["analysis_mode"] == "deep" else "mapped",
                                "status": expected.status,
                                "item_key": key,
                            }
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
                if self.failure.startswith(("accepted_", "completed_")) or self.failure in {"execution_error", "extra_rule_output", "extra_finding", "swapped_coverage_mode"}:
                    if self.changed_round_id is None:
                        self.changed_round_id = result["id"]
                        if self.failure == "accepted_mode":
                            result["analysis_mode"] = "standard" if draft["analysis_mode"] == "deep" else "deep"
                        elif self.failure == "accepted_executor":
                            result["executor"] = EXECUTOR_STANDARD if draft["analysis_mode"] == "deep" else EXECUTOR_DEEP
                        elif self.failure == "accepted_revision":
                            result["executor_revision"] = "ppap-deep-v2" if draft["analysis_mode"] == "deep" else "another-standard-revision"
                        elif self.failure == "accepted_missing_policy":
                            result.pop("execution_policy")
                        elif self.failure == "accepted_invalid_policy":
                            result["execution_policy"]["model_config_revision"] = "invalid-digest"
                        elif self.failure == "accepted_extra_policy_field":
                            result["execution_policy"]["secret"] = "private-policy-value"
                        elif self.failure == "accepted_policy_tampering":
                            result["execution_policy"]["model"] = "other-model"
                        elif self.failure == "accepted_wrong_build":
                            changed = self.policy.model_copy(update={"app_revision": "e" * 40})
                            result.update(execution_policy=changed.model_dump(mode="json"), executor_revision=changed.engine_revision())
                        elif self.failure == "accepted_legacy_revision":
                            result["executor_revision"] = "in-repo-engine-v1"
                        elif self.failure == "execution_error":
                            result["coverage"]["rule_items"][0].update(coverage="execution_error", status="ERR")
                            result["findings"][0]["status"] = "ERR"
                        elif self.failure == "extra_rule_output":
                            extra = {**result["coverage"]["rule_items"][0], "id": "unexpected-rule", "item_key": "unexpected-item"}
                            result["coverage"]["rule_items"].append(extra)
                            result["findings"].append({"item_key": extra["item_key"], "status": "PASS"})
                        elif self.failure == "extra_finding":
                            result["findings"].append({"item_key": "unexpected-item", "status": "PASS"})
                        elif self.failure == "swapped_coverage_mode":
                            for row in result["coverage"]["rule_items"]:
                                row["coverage"] = "mapped" if draft["analysis_mode"] == "deep" else "executed"
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
            if result["id"] == self.changed_round_id:
                if self.failure == "completed_mode":
                    result["analysis_mode"] = "standard" if result["analysis_mode"] == "deep" else "deep"
                elif self.failure == "completed_executor":
                    result["executor"] = "another-executor"
                elif self.failure == "completed_revision":
                    result["executor_revision"] = "c" * 64 if result["analysis_mode"] == "deep" else "another-standard-revision"
                elif self.failure == "completed_missing_policy":
                    result.pop("execution_policy")
                elif self.failure == "completed_policy_tampering":
                    result["execution_policy"]["model"] = "other-model"
                elif self.failure == "completed_policy_drift":
                    changed = self.policy.model_copy(update={"model": "other-model"})
                    result.update(execution_policy=changed.model_dump(mode="json"), executor_revision=changed.engine_revision())
                elif self.failure == "completed_policy_reordered" and result.get("execution_policy"):
                    result["execution_policy"] = dict(reversed(result["execution_policy"].items()))
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
        ("completed_policy_reordered", "PROVEN"),
    ],
)
@pytest.mark.parametrize("analysis_mode", ["standard", "deep"])
def test_full_public_flow_keeps_all_cases_and_never_reaccepts(tmp_path, failure, outcome, analysis_mode):
    api = ProductAPI(failure)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, analysis_mode=analysis_mode, isolated_login=True, timeout=0.001, poll_interval=0)
    assert result["outcome"] == outcome
    assert len([path for method, path in api.writes if path.endswith("/accept")]) == 8
    assert len([path for method, path in api.writes if path == "/rules/parse"]) == 5
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["selected_cases"] == 27
    assert report["summary"]["total_rules"] == 220
    assert result["analysis_mode"] == report["run"]["analysis_mode"] == analysis_mode
    assert {draft["analysis_mode"] for draft in api.drafts.values()} == {analysis_mode}
    accepted = [operation for operation in result["operations"] if operation["stage"] == "accept"]
    assert {operation["analysis_mode"] for operation in accepted} == {analysis_mode}
    frozen = [operation for operation in accepted if "executor" in operation]
    assert all(operation["executor"] == (EXECUTOR_DEEP if analysis_mode == "deep" else EXECUTOR_STANDARD) for operation in frozen)
    assert all(operation["executor_revision"] == ("b" * 64 if analysis_mode == "deep" else api.policy.engine_revision()) for operation in frozen)
    if analysis_mode == "standard":
        assert all(operation["execution_policy"] == api.policy.model_dump(mode="json") for operation in frozen)
    if outcome == "PROVEN":
        assert len(frozen) == 8
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
@pytest.mark.parametrize("analysis_mode", ["standard", "deep"])
def test_changed_frozen_inputs_reject_only_the_affected_group_without_reaccepting(tmp_path, failure, stage, kind, analysis_mode):
    api = ProductAPI(failure)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, analysis_mode=analysis_mode, isolated_login=True, timeout=0.001, poll_interval=0)

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


def test_default_mode_stays_standard_and_invalid_modes_never_make_requests(tmp_path):
    api = ProductAPI()
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=tmp_path / "default")
    assert result["outcome"] == "PROVEN"
    assert result["analysis_mode"] == "standard"
    assert result["scope"] == "full"
    assert result["denominators"] == {"full": {"bundles": 8, "files": 27, "rules": 220}, "selected": {"bundles": 8, "files": 27, "rules": 220}}
    assert {draft["analysis_mode"] for draft in api.drafts.values()} == {"standard"}

    def unexpected_request(_request):
        raise AssertionError("invalid mode reached the HTTP boundary")

    output = tmp_path / "invalid"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(unexpected_request)) as client, pytest.raises(ValueError, match="invalid_analysis_mode"):
        run_live(client, candidate_sha="a" * 40, output=output, analysis_mode="automatic")
    assert not output.exists()


@pytest.mark.parametrize("analysis_mode", ["standard", "deep"])
@pytest.mark.parametrize("bundle_ids", [["Aries Spoiler"], ["POC测试样本", "Aries Spoiler"]])
def test_selected_whole_bundles_publish_only_required_rules_and_never_claim_full_proof(tmp_path, analysis_mode, bundle_ids):
    api = ProductAPI()
    output = tmp_path / "subset"
    bundles = build_bundle_map(api.cases.values())
    selected = [case for bundle in bundle_ids for case in bundles[bundle]]
    required_seqs = {case.file_seq for case in selected}
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, analysis_mode=analysis_mode, bundle_ids=bundle_ids, isolated_login=True)
    assert result["outcome"] == "SUBSET_PROVEN"
    assert result["scope"] == "subset"
    assert result["selected_bundles"] == sorted(bundle_ids)
    assert result["denominators"] == {
        "full": {"bundles": 8, "files": 27, "rules": 220},
        "selected": {"bundles": len(bundle_ids), "files": len(selected), "rules": sum(len(case.expected) for case in selected)},
    }
    assert {draft["analysis_mode"] for draft in api.drafts.values()} == {analysis_mode}
    assert {version["original_filename"] for version in api.files.values()} == {case.file_name for case in selected} | {RULE_DOCS[seq] for seq in required_seqs}
    assert len(api.rules) == len(api.parses) == len(required_seqs)
    accepts = [operation for operation in result["operations"] if operation["stage"] == "accept"]
    assert [operation["bundle"] for operation in accepts] == sorted(bundle_ids)
    assert len({path for _method, path in api.writes if path.endswith("/accept")}) == len(bundle_ids)
    assert all(operation["analysis_mode"] == analysis_mode and operation["executor_revision"] == ("b" * 64 if analysis_mode == "deep" else api.policy.engine_revision()) for operation in accepts)
    for draft in api.drafts.values():
        names = {api.files[item["file_version_id"]]["original_filename"] for item in draft["items"]}
        bundle = next(bundle for bundle in bundle_ids if names & {case.file_name for case in bundles[bundle]})
        assert names == {case.file_name for case in bundles[bundle]}
    report = json.loads((output / "report.json").read_text())
    assert report["run"] == result
    assert set(report["by_bundle"]) == set(bundle_ids)
    assert report["summary"]["selected_cases"] == len(selected)
    assert report["summary"]["total_rules"] == len(report["rule_results"]) == result["denominators"]["selected"]["rules"]
    markdown = (output / "report.md").read_text()
    assert "SUBSET_PROVEN" in markdown and "subset" in markdown and "denominators" in markdown and "220" in markdown


@pytest.mark.parametrize("bundle_ids", [[], ["unknown"], ["Aries Spoiler", "Aries Spoiler"], ["Aries Spoiler", "unknown"], ["Aries Spoiler "]])
def test_invalid_or_duplicate_bundle_selection_fails_before_any_http_request(tmp_path, bundle_ids):
    calls = []

    def unexpected_request(request):
        calls.append((request.method, request.url.path))
        raise AssertionError("invalid bundle selection reached the HTTP boundary")

    output = tmp_path / "invalid"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(unexpected_request)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, bundle_ids=bundle_ids, isolated_login=True)
    assert result["outcome"] == "NOT_PROVEN"
    assert result["attribution"] == "inconclusive"
    assert result["errors"] == [{"stage": "preflight", "code": "invalid_bundle_selection" if not bundle_ids or len(set(bundle_ids)) != len(bundle_ids) else "unknown_bundle_selection"}]
    assert result["operations"] == calls == []


def test_explicitly_selecting_all_bundles_is_the_same_full_scope(tmp_path):
    api = ProductAPI()
    bundles = sorted(build_bundle_map(api.cases.values()), reverse=True)
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=tmp_path / "all", bundle_ids=bundles)
    assert result["outcome"] == "PROVEN"
    assert result["scope"] == "full"
    assert result["selected_bundles"] == sorted(bundles)
    assert result["denominators"]["full"] == result["denominators"]["selected"] == {"bundles": 8, "files": 27, "rules": 220}
    assert len(api.drafts) == 8 and len(api.rules) == 5


@pytest.mark.parametrize("failure", ["lost_accept_response", "one_status_mismatch", "extra_rule_output", "frozen_rule_text_drift", "execution_error"])
def test_subset_keeps_selected_denominator_identity_and_read_only_unknown_recovery(tmp_path, failure):
    api = ProductAPI(failure)
    bundle = "Cetus Spoiler-有特殊特性清单"
    output = tmp_path / "subset"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, analysis_mode="deep", bundle_ids=[bundle], timeout=0.001, poll_interval=0)
    assert result["outcome"] == ("SUBSET_PROVEN" if failure == "lost_accept_response" else "NOT_PROVEN")
    assert result["scope"] == "subset"
    assert result["denominators"]["selected"] == {"bundles": 1, "files": 5, "rules": 31}
    assert len([path for _method, path in api.writes if path.endswith("/accept")]) == 1
    assert len(api.drafts) == len(api.rounds) == 1
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["selected_cases"] == 5
    assert report["summary"]["total_rules"] == len(report["rule_results"]) == 31
    assert {row["bundle"] for row in report["rule_results"]} == {bundle}
    if failure == "execution_error":
        failed = [row for row in report["rule_results"] if row["kind"] != "match"]
        assert len(failed) == 1 and failed[0]["kind"] == "execution_failure" and failed[0]["actual_status"] == "ERR"


def test_cli_repeated_bundle_selection_returns_success_only_as_subset_proof(tmp_path, monkeypatch, capsys):
    api = ProductAPI()
    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(api))
    monkeypatch.setattr("corpus_contracts.live_poc.httpx.Client", lambda **_kwargs: client)
    monkeypatch.setattr(sys, "argv", ["live_poc", "--api", "http://test", "--candidate-sha", "a" * 40, "--output", str(tmp_path / "cli"), "--bundle", "Aries Spoiler", "--bundle", "POC测试样本"])
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "SUBSET_PROVEN"
    assert len(api.drafts) == 2


@pytest.mark.parametrize("analysis_mode", ["standard", "deep"])
@pytest.mark.parametrize("failure", ["accepted_mode", "accepted_executor", "accepted_revision", "completed_mode", "completed_executor", "completed_revision", "extra_rule_output", "extra_finding"])
def test_mode_policy_and_exact_rule_output_are_frozen_without_reaccepting(tmp_path, analysis_mode, failure):
    api = ProductAPI(failure)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, analysis_mode=analysis_mode, timeout=0.001, poll_interval=0)

    assert result["outcome"] == "NOT_PROVEN"
    assert result["attribution"] == "inconclusive"
    assert len(result["errors"]) == 1
    expected_stage = "accept" if failure.startswith("accepted_") else "audit"
    assert result["errors"][0]["stage"] == expected_stage
    assert result["errors"][0]["code"] == (
        "round_identity_mismatch" if failure.endswith("_mode") else "rule_result_identity_mismatch" if failure in {"extra_rule_output", "extra_finding"} else "frozen_executor_mismatch"
    )
    accepts = [path for _method, path in api.writes if path.endswith("/accept")]
    assert len(accepts) == len(set(accepts)) == 8
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["selected_cases"] == 27
    assert report["summary"]["total_rules"] == 220
    assert all(row["kind"] == "match" for row in report["rule_results"] if row["round_id"] != api.changed_round_id)


@pytest.mark.parametrize(
    "failure",
    [
        "accepted_missing_policy",
        "accepted_invalid_policy",
        "accepted_extra_policy_field",
        "accepted_policy_tampering",
        "accepted_wrong_build",
        "accepted_legacy_revision",
        "completed_missing_policy",
        "completed_policy_tampering",
        "completed_policy_drift",
    ],
)
def test_standard_policy_rejects_tampering_and_terminal_drift_without_reaccepting(tmp_path, failure):
    api = ProductAPI(failure)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, timeout=0.001, poll_interval=0)

    assert result["outcome"] == "NOT_PROVEN"
    stage = "accept" if failure.startswith("accepted_") else "audit"
    assert result["errors"] == [{"stage": stage, "bundle": "Aries Spoiler", "code": "frozen_executor_mismatch", "round_id": api.changed_round_id}]
    accepts = [path for _method, path in api.writes if path.endswith("/accept")]
    assert len(accepts) == len(set(accepts)) == 8
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["selected_cases"] == 27
    assert report["summary"]["total_rules"] == 220
    affected = [row for row in report["rule_results"] if row["round_id"] == api.changed_round_id]
    assert affected
    assert {row["kind"] for row in affected} == {"setup_failure" if stage == "accept" else "execution_failure"}
    assert all(row["kind"] == "match" for row in report["rule_results"] if row["round_id"] != api.changed_round_id)
    assert "private-policy-value" not in (output / "report.json").read_text()
    if stage == "audit":
        frozen = next(operation for operation in result["operations"] if operation.get("round_id") == api.changed_round_id)
        assert frozen["execution_policy"] == api.policy.model_dump(mode="json")
        assert frozen["executor_revision"] == api.policy.engine_revision()


@pytest.mark.parametrize("analysis_mode", ["standard", "deep"])
@pytest.mark.parametrize("failure", ["execution_error", "swapped_coverage_mode"])
def test_mode_coverage_keeps_execution_errors_distinct_from_missing_rules(tmp_path, analysis_mode, failure):
    api = ProductAPI(failure)
    output = tmp_path / "run"
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(api)) as client:
        result = run_live(client, candidate_sha="a" * 40, output=output, analysis_mode=analysis_mode, timeout=0.001, poll_interval=0)

    assert result["outcome"] == "NOT_PROVEN"
    assert result["errors"] == []
    assert len([path for _method, path in api.writes if path.endswith("/accept")]) == 8
    report = json.loads((output / "report.json").read_text())
    assert report["summary"]["total_rules"] == 220
    affected = [row for row in report["rule_results"] if row["kind"] != "match"]
    assert {row["round_id"] for row in affected} == {api.changed_round_id}
    if failure == "execution_error":
        assert len(affected) == 1
        assert affected[0]["actual_status"] == "ERR"
        assert affected[0]["kind"] == "execution_failure"
        assert report["summary"]["execution_failed_rules"] == 1
        assert report["summary"]["parity_failed_rules"] == 0
        assert "execution_failure" in (output / "report.md").read_text()
    else:
        assert {row["kind"] for row in affected} == {"missing_rule"}
