"""The paid gate itself must reject fake success without contacting a provider."""

import copy
import hashlib
import io
import json
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import yaml
from openpyxl import load_workbook

from scripts import checker_live_controller as controller
from scripts import prepare_checker_live as setup
from scripts import run_checker_live as acceptance

ROOT = Path(__file__).resolve().parents[2]


def configuration():
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ATTEMPT": "1",
        "PPAP_BUILD_SHA": "a" * 40,
        "PPAP_APPROVED_SHA": "a" * 40,
        "CI_LLM_BASE_URL": "https://provider.example/v1",
        "CI_LLM_MODEL": "arbitrary-model",
        "CI_LLM_API_KEY": "synthetic-never-transmitted-key",
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "123",
        "COMPOSE_PROJECT_NAME": "ppap-checker-live-123-1",
        "PPAP_PERSONAL_MODEL_MAX_REQUESTS": "32",
        "PPAP_PERSONAL_MODEL_MAX_INPUT_TOKENS": "200000",
        "PPAP_PERSONAL_MODEL_MAX_OUTPUT_TOKENS": "32000",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("PPAP_APPROVED_SHA", "b" * 40),
        ("CI_LLM_API_KEY", ""),
        ("GITHUB_ACTIONS", "false"),
        ("GITHUB_RUN_ID", "not-a-run"),
        ("COMPOSE_PROJECT_NAME", "production"),
    ],
)
def test_live_entry_requires_manual_invocation_exact_source_and_isolated_services(key, value):
    env = configuration()
    env[key] = value
    with pytest.raises((acceptance.AcceptanceFailure, ValueError)):
        acceptance.live_configuration(env)


def test_provider_is_not_tied_to_a_brand():
    assert acceptance.live_configuration(configuration()) == ("https://provider.example/v1", "arbitrary-model")


def test_live_browser_explicitly_selects_the_full_sample_set_before_one_admission(tmp_path, monkeypatch):
    """Driver orchestration only; native browser behavior has its separate journey."""
    env = configuration()
    monkeypatch.setenv("CI_LLM_API_KEY", env["CI_LLM_API_KEY"])
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(expect=Mock()))
    _, samples = valid_receipt()
    report = {"journey": {}, "samples": samples}
    report_path = tmp_path / "report.json"
    configured = {"configured": True, "base_url": env["CI_LLM_BASE_URL"], "model": env["CI_LLM_MODEL"], "revision": "model-revision"}
    receipt = {"operation_id": "ui-generated-operation", "state": "queued"}
    selected = [next(iter(samples))]  # The product's current-row default.
    page, batch, select_all, start = Mock(), Mock(), Mock(), Mock()
    labels, captures, forwarded = {}, [], []
    page.get_by_role.side_effect = lambda role, *, name, exact: batch if role == "region" else start if name == "确认开始修复" else Mock()
    batch.get_by_role.side_effect = lambda role, *, name, exact: select_all if name == "选择全部已确认样本" else Mock()
    select_all.click.side_effect = lambda: selected.__setitem__(slice(None), list(samples))
    page.get_by_label.side_effect = lambda name, *, exact: labels.setdefault(name, Mock())
    page.expect_response.side_effect = [nullcontext(SimpleNamespace(value=SimpleNamespace(status=200, json=Mock(return_value=result)))) for result in (configured, receipt)]
    page.route.side_effect = lambda pattern, callback: captures.append(callback)

    def admit():
        body = {"operation_id": receipt["operation_id"], "expected_model_revision": configured["revision"], "feedback_ids": list(selected)}

        def forward():
            persisted = json.loads(report_path.read_text())
            assert persisted["stage"] == "admitting" and persisted["operation_id"] == body["operation_id"]
            labels[f"同意将本次资料发送至 {env['CI_LLM_BASE_URL']}，使用 {env['CI_LLM_MODEL']} 修复并验证这 8 条反馈"].check.assert_called_once()
            forwarded.append(body)

        captures[0](SimpleNamespace(request=SimpleNamespace(method="POST", post_data_json=body), continue_=forward))

    start.click.side_effect = admit
    journey = acceptance.BrowserJourney.__new__(acceptance.BrowserJourney)
    journey.page = page
    assert journey.configure_and_start({"round_id": "round", "file_version_id": "file"}, env["CI_LLM_BASE_URL"], env["CI_LLM_MODEL"], report, report_path) == (configured, receipt)
    select_all.click.assert_called_once()
    assert len(forwarded) == 1 and forwarded[0]["feedback_ids"] == list(samples)
    assert report["journey"] == {"browser_feedback_selection": True, "browser_personal_configuration": True}
    assert env["CI_LLM_API_KEY"] not in report_path.read_text()
    page.reload.assert_called_once()


@pytest.mark.parametrize("base", ["http://provider.example/v1", "https://user:secret@provider.example/v1", "https://provider.example/v1?key=secret", "https://provider.example/v1#secret"])
def test_provider_address_cannot_carry_a_secret_or_use_plaintext(base):
    env = configuration()
    env["CI_LLM_BASE_URL"] = base
    with pytest.raises((acceptance.AcceptanceFailure, ValueError)):
        acceptance.live_configuration(env)


def test_ephemeral_setup_uses_private_unique_credentials_and_never_overwrites(tmp_path):
    path = tmp_path / "private.env"
    setup.prepare(configuration(), path)
    assert path.stat().st_mode & 0o777 == 0o600
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert len(values) == len(set(values.values())) == 7
    assert "synthetic-never-transmitted-key" not in path.read_text()
    with pytest.raises(FileExistsError):
        setup.prepare(configuration(), path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("PPAP_APPROVED_SHA", "b" * 40),
        ("CI_LLM_MODEL", ""),
        ("PPAP_PERSONAL_MODEL_MAX_REQUESTS", "33"),
        ("PPAP_PERSONAL_MODEL_MAX_INPUT_TOKENS", "200001"),
        ("PPAP_PERSONAL_MODEL_MAX_OUTPUT_TOKENS", "0"),
    ],
)
def test_setup_rejects_changed_source_or_excessive_budget(tmp_path, key, value):
    env = configuration()
    env[key] = value
    with pytest.raises(ValueError):
        setup.prepare(env, tmp_path / "private.env")
    assert not (tmp_path / "private.env").exists()


def valid_receipt():
    samples, cases = {}, []
    # Synthetic receipt-only test values are unrelated to any live run's hidden
    # inputs, which are chosen privately after the public commit is built.
    values = (*acceptance.TRAINING_SAMPLES[:3], (7, "NG"), *acceptance.TRAINING_SAMPLES[3:], (30, "PASS"))
    for ordinal, (value, expected) in enumerate(values, 1):
        ident = str(ordinal)
        baseline = "PASS" if value >= 0 else "NG"
        samples[ident] = {"ordinal": ordinal, "expected": expected, "baseline": baseline}
        cases.append(
            {
                "case_id": ident,
                "ordinal": ordinal,
                "split": "holdout" if ordinal % 4 == 0 else "training",
                "coverage": True,
                "error_code": None,
                "clauses": [{"clause_id": "R1", "expected": expected, "actual": expected, "baseline_actual": baseline, "evidence_locatable": True, "checks_performed": 1, "regression": False}],
            }
        )
    return {
        "state": "completed",
        "suite_revision_id": "base",
        "candidate_revision_id": "candidate",
        "usage": {"source": "provider", "uncertain": False, "input_tokens": 500, "output_tokens": 100, "model_requests": 2},
        "report": {"validation": {"verified": True, "total_samples": 8, "passed_samples": 8, "cases": cases}},
    }, samples


def test_gate_accepts_only_real_usage_and_independent_positive_negative_holdout():
    receipt, samples = valid_receipt()
    assert acceptance.validate_outcome(receipt, samples)["passed_samples"] == 8


@pytest.mark.parametrize(
    "path,value",
    [
        (("state",), "unknown"),
        (("state",), "failed"),
        (("candidate_revision_id",), None),
        (("usage", "source"), "controlled"),
        (("usage", "uncertain"), True),
        (("usage", "input_tokens"), 0),
        (("usage", "model_requests"), 0),
        (("report", "validation", "verified"), False),
        (("report", "validation", "cases", 0, "clauses", 0, "actual"), "PASS"),
        (("report", "validation", "cases", 3, "clauses", 0, "actual"), "UNABLE"),
        (("report", "validation", "cases", 7, "clauses", 0, "actual"), "ERR"),
        (("report", "validation", "cases", 7, "clauses", 0, "actual"), "NG"),
        (("report", "validation", "cases", 3, "clauses", 0, "expected"), "PASS"),
        (("report", "validation", "cases", 0, "clauses", 0, "baseline_actual"), "NG"),
        (("report", "validation", "cases", 0, "clauses", 0, "checks_performed"), 0),
        (("report", "validation", "cases", 0, "clauses", 0, "evidence_locatable"), False),
        (("report", "validation", "cases", 3, "split"), "training"),
        (("report", "validation", "cases", 3, "ordinal"), 3),
        (("report", "validation", "cases", 3, "coverage"), False),
        (("report", "validation", "cases", 3, "case_id"), "1"),
    ],
)
def test_claimed_verified_receipt_cannot_hide_broken_independent_proof(path, value):
    receipt, samples = valid_receipt()
    target = receipt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance.validate_outcome(receipt, samples)


def test_live_hidden_values_and_label_order_are_private_fresh_and_independent(monkeypatch):
    entropy = iter([1, 2, 0, 3, 4, 1])
    monkeypatch.setattr(acceptance.secrets, "randbelow", lambda bound: next(entropy))
    first, second = acceptance.make_samples(), acceptance.make_samples()
    training = set(acceptance.TRAINING_SAMPLES)
    assert training == {(3, "NG"), (10, "PASS"), (20, "PASS"), (-5, "NG"), (9, "NG"), (12, "PASS")}
    assert [first[index][1] for index in (3, 7)] == ["NG", "PASS"]
    assert [second[index][1] for index in (3, 7)] == ["PASS", "NG"]
    assert {first[index][0] for index in (3, 7)}.isdisjoint(second[index][0] for index in (3, 7))
    for run in (first, second):
        for index, (value, expected) in enumerate(run):
            if index in (3, 7):
                assert value not in {item[0] for item in training}
            else:
                assert (value, expected) in training
            assert expected == ("NG" if value < 10 else "PASS")


def test_private_samples_round_trip_into_marked_workbooks():
    for ordinal, (value, expected) in enumerate(acceptance.make_samples(), 1):
        raw = acceptance.workbook(value, ordinal)
        book = load_workbook(io.BytesIO(raw), data_only=True)
        assert book.active["A1"].value == pytest.approx(value)
        assert ("NG" if book.active["A1"].value < 10 else "PASS") == expected
        assert controller.contains_holdout(raw) == (ordinal % 4 == 0)
        book.close()


def test_probe_rejects_holdout_at_author_boundary_before_runtime(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "source.bin").write_bytes(acceptance.workbook(7, 4))
    probe = controller.Probe(tmp_path / "evidence/controller.json")
    with pytest.raises(ValueError, match="live_holdout_boundary_violation"):
        probe.observe_job(SimpleNamespace(operation="adapt-checker"), inputs)
    assert json.loads(probe.path.read_text())["violation"] is True


def test_probe_checks_fixed_holdout_has_no_model_grant_and_allows_one_adaptation(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "source.bin").write_bytes(acceptance.workbook(3, 1))
    probe = controller.Probe(tmp_path / "evidence/controller.json")
    (probe.path.parent / "private-oracle.json").write_text(json.dumps({"hidden_case_ids": ["hidden-case"], "hidden_source_sha256": ["a" * 64]}))
    probe.observe_job(SimpleNamespace(operation="adapt-checker"), inputs)
    (inputs / "source.bin").write_bytes(acceptance.workbook(7, 4))
    probe.observe_job(SimpleNamespace(operation="execute-audit", allow_model_calls=False), inputs)
    assert probe.facts["holdout_fixed_trials"] == 1
    with pytest.raises(ValueError):
        probe.observe_job(SimpleNamespace(operation="execute-audit", allow_model_calls=True), inputs)
    with pytest.raises(ValueError):
        probe.observe_job(SimpleNamespace(operation="adapt-checker"), inputs)


def test_probe_rejects_provider_canary_without_logging_body(tmp_path):
    probe = controller.Probe(tmp_path / "evidence/controller.json")
    probe.facts["adaptations"] = 1
    probe.author_active = True
    probe.observe_provider({"input": "safe synthetic training", "model": "arbitrary-model"})
    with pytest.raises(ValueError):
        probe.observe_provider({"input": "checker-live-holdout-secret"})
    assert "secret" not in probe.path.read_text()
    assert probe.facts["provider_requests"] == 1


def test_http_write_with_unknown_outcome_is_never_retried():
    calls = []

    def fail(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("synthetic uncertainty")

    client = acceptance.Client("synthetic-session")
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(fail), base_url="http://api:8080", follow_redirects=False)
    with pytest.raises(httpx.ReadTimeout):
        client.request("POST", "/suite-revisions/base/optimizations", payload={"operation_id": "frozen-id"})
    assert calls == ["POST"]
    client.http.close()


def checker_workflow():
    return yaml.load((ROOT / ".github/workflows/checker-optimization-live.yml").read_text(), Loader=yaml.BaseLoader)


def test_workflow_is_manual_only_bounded_and_not_in_default_gate():
    workflow = checker_workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {"expected_sha", "max_model_requests", "max_input_tokens", "max_output_tokens"}
    job = workflow["jobs"]["live"]
    assert "if" not in job
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert "checker-optimization-live" not in (ROOT / ".github/workflows/tests.yml").read_text()
    assert "CI_LLM_API_KEY" not in json.dumps(job["env"])
    steps_with_key = [step for step in job["steps"] if "CI_LLM_API_KEY" in step.get("env", {})]
    assert len(steps_with_key) == 1
    assert "uv run --no-sync python -m pytest tests/repository/test_checker_live_acceptance.py" in json.dumps(workflow)
    assert job["env"]["COMPOSE_PROJECT_NAME"].endswith("${{ github.run_id }}-${{ github.run_attempt }}")
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["name"].endswith("${{ github.run_id }}-${{ github.run_attempt }}")


@pytest.mark.parametrize("event,attempt,matched_sha", [("workflow_dispatch", "1", True), ("workflow_dispatch", "2", True), ("push", "1", True), ("workflow_dispatch", "1", False)])
def test_first_step_rejects_invalid_invocations_without_skipping(tmp_path, event, attempt, matched_sha):
    job = checker_workflow()["jobs"]["live"]
    # Exercise the actual first shell step, before any model-key consuming step.
    assert "runner." not in json.dumps(job["env"])
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    github_env = tmp_path / "github env"
    env = configuration()
    env.update(RUNNER_TEMP=str(runner_temp), GITHUB_ENV=str(github_env), GITHUB_EVENT_NAME=event, GITHUB_RUN_ATTEMPT=attempt)
    if not matched_sha:
        env["PPAP_APPROVED_SHA"] = "b" * 40
    result = subprocess.run(["/bin/bash", "-c", job["steps"][0]["run"]], env=env, capture_output=True, text=True)
    valid = event == "workflow_dispatch" and matched_sha
    assert (result.returncode == 0) is valid
    if valid:
        exported = dict(line.split("=", 1) for line in github_env.read_text().splitlines())
        assert exported == {"PPAP_CHECKER_LIVE_ENV": str(runner_temp / "checker-live.env"), "PPAP_CHECKER_LIVE_OUTPUT": str(runner_temp / "checker-live-output")}
    else:
        assert not github_env.exists()


def test_stack_keeps_oracle_outside_guest_and_disables_fixture_login():
    config = yaml.safe_load((ROOT / "deploy/isolated-dev/checker-live.yaml").read_text())
    for name, service in config["services"].items():
        assert not service.get("env_file")
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
        assert not service.get("networks")  # Only this project's fresh default network.
        assert not service.get("restart")
        if name in {"api", "worker", "deep"}:
            assert service["environment"]["PPAP_PROFILE"] == "remote-dev"
            assert service["environment"]["LLM_API_KEY"] == ""
            assert "CI_LLM_API_KEY" not in service["environment"]
            assert service["environment"]["PPAP_USAGE_CHARGE_ENABLED"] == "false"
        assert "run_checker_live.py" not in json.dumps(service)
    mounts = config["services"]["deep"]["volumes"]
    assert [entry for entry in mounts if "scripts/" in entry] == ["../../scripts/checker_live_controller.py:/live/checker_controller.py:ro"]
    assert not any("docker.sock" in entry for service in config["services"].values() for entry in service.get("volumes", []))


def test_probe_forbids_unplanned_provider_consumption_before_optimization(tmp_path):
    probe = controller.Probe(tmp_path / "evidence/controller.json")
    with pytest.raises(ValueError):
        probe.observe_provider({"input": "unexpected baseline metadata request"})
    assert probe.facts["provider_requests"] == 0


def test_real_guest_permission_probe_is_executable_python():
    compile(controller.PRIVATE_READ_PROBE, "guest-private-read-probe", "exec")


@pytest.mark.parametrize("boundary", ["author", "provider"])
def test_hidden_oracle_labels_are_rejected_without_a_workbook_canary(tmp_path, boundary):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "manifest.json").write_text("{}")
    probe = controller.Probe(tmp_path / "evidence/controller.json")
    (probe.path.parent / "private-oracle.json").write_text(json.dumps({"hidden_case_ids": ["independent-hidden-case"], "hidden_source_sha256": ["a" * 64]}))
    leaked = {"training": [{"case_id": "independent-hidden-case", "expected": {"R1": "NG"}, "actual": {"R1": "PASS"}}]}
    if boundary == "author":
        (inputs / "manifest.json").write_text(json.dumps(leaked))
        with pytest.raises(ValueError):
            probe.observe_job(SimpleNamespace(operation="adapt-checker"), inputs)
    else:
        probe.observe_job(SimpleNamespace(operation="adapt-checker"), inputs)
        probe.author_active = True
        with pytest.raises(ValueError):
            probe.observe_provider(leaked)
    assert probe.facts["violation"] is True
    assert "independent-hidden-case" not in probe.path.read_text()


def test_unknown_null_report_retains_authoritative_state_and_known_usage():
    report = {"operation_id": "requested-id", "usage": {"input_tokens": 17, "model_requests": 1}}
    acceptance.retain_receipt(report, {"operation_id": "requested-id", "state": "unknown", "report": None, "usage": {"uncertain": True}, "failure": "execution_outcome_unknown"})
    assert report["state"] == "unknown" and report["product_failure"] == "execution_outcome_unknown"
    assert report["operation_id"] == report["returned_operation_id"] == "requested-id"
    assert report["usage"] == {"input_tokens": 17, "model_requests": 1, "uncertain": True}
    assert report["validation"] == {}


def identity_case():
    receipt, _ = valid_receipt()
    validation = receipt["report"]["validation"]
    receipt.update(operation_id="operation", suite_revision_id="base")
    validation.update(suite_revision_id="candidate", suite_digest="candidate-digest", oracle_revision="oracle-digest", library_revision="library")
    digests = {}
    for row in validation["cases"]:
        row["input_digest"] = hashlib.sha256(row["case_id"].encode()).hexdigest()
        digests[row["case_id"]] = row["input_digest"]
    return receipt, {
        "operation_id": "operation",
        "base_revision_id": "base",
        "detail": {"id": "candidate", "resolved_digest": "candidate-digest"},
        "frozen": {"oracle_revision": "oracle-digest", "library_revision": "library"},
        "input_digests": digests,
    }


@pytest.mark.parametrize(
    "path",
    [
        ("operation_id",),
        ("suite_revision_id",),
        ("candidate_revision_id",),
        ("report", "validation", "suite_revision_id"),
        ("report", "validation", "oracle_revision"),
        ("report", "validation", "suite_digest"),
        ("report", "validation", "library_revision"),
        ("report", "validation", "cases", 0, "input_digest"),
    ],
)
def test_wrong_operation_candidate_or_frozen_oracle_cannot_pass(path):
    receipt, context = identity_case()
    acceptance.validate_bindings(receipt, **context)
    target = receipt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "wrong-identity"
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance.validate_bindings(receipt, **context)


@pytest.mark.parametrize("field", ["evidence", "clause_id", "suggestions", "snapshot"])
def test_history_same_status_does_not_hide_changed_evidence_or_frozen_source(field):
    result = {"result_attempt": 1, "findings": [{"status": "PASS", "clause_id": "R1", "evidence": {"locator": "A1"}, "suggestions": "original"}]}
    snapshot = {"targets": [{"file_version_id": "original", "suite_revision_id": "base"}]}
    before = acceptance.history_fingerprint(result, snapshot)
    changed = copy.deepcopy(result)
    if field == "snapshot":
        snapshot["targets"][0]["file_version_id"] = "other"
    else:
        changed["findings"][0][field] = "changed"
    assert acceptance.history_fingerprint(changed, snapshot) != before


def test_post_generation_reaudit_cannot_start_an_extra_paid_request(tmp_path):
    probe = controller.Probe(tmp_path / "evidence/controller.json")
    probe.facts["adaptations"] = 1
    with pytest.raises(ValueError):
        probe.observe_provider({"input": "unexpected post-generation model request"})
    assert probe.facts["provider_requests"] == 0


def test_host_fixture_settings_use_explicit_random_credentials_and_distinct_loopback_ports():
    from cryptography.fernet import Fernet

    env = configuration()
    env.update(
        LIVE_PG_MIGRATOR_PASSWORD="synthetic-non-default-password",
        LIVE_S3_ACCESS_KEY="synthetic-access",
        LIVE_S3_SECRET_KEY="synthetic-storage-password",
        LIVE_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    settings = acceptance.local_settings(env)
    assert settings.profile == "remote-dev" and settings.feishu_app_id == "checker-live"
    assert settings.pg_host == "127.0.0.1" and settings.pg_port == 15432
    assert settings.s3_endpoint == "http://127.0.0.1:19000"
    assert settings.personal_model_max_requests == 32 and settings.personal_model_max_input_tokens == 200000 and settings.personal_model_max_output_tokens == 32000
    config = yaml.safe_load((ROOT / "deploy/isolated-dev/checker-live.yaml").read_text())
    assert config["services"]["api"]["ports"] == ["127.0.0.1:18080:8080"]
    assert config["services"]["web"]["ports"] == ["127.0.0.1:14173:4173"]


def test_final_snapshot_preserves_gate_failure_when_product_has_no_failure(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"accepted": False, "operation_id": "original", "gate_failure": "independent_answer_mismatch", "usage": {"model_requests": 2}}))
    (tmp_path / "private-client.json").write_text(json.dumps({"session": "synthetic"}))
    calls = []

    class ReadOnlyClient:
        def __init__(self, session):
            self.http = SimpleNamespace(timeout=None, close=lambda: None)

        def request(self, method, target):
            calls.append((method, target))
            return {"operation_id": "original", "state": "completed", "failure": None, "report": None, "usage": {}}

    monkeypatch.setattr(acceptance, "Client", ReadOnlyClient)
    acceptance.final_snapshot(path)
    saved = json.loads(path.read_text())
    assert saved["gate_failure"] == "independent_answer_mismatch" and saved["product_failure"] is None and saved["accepted"] is False
    assert saved["usage"]["model_requests"] == 2
    assert calls == [("GET", "/checker-optimizations/original")]


@pytest.mark.parametrize("snapshot", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_both_entrypoints_propagate_final_readback_verdict(tmp_path, monkeypatch, capsys, snapshot, changed):
    receipt, samples = valid_receipt()
    receipt["operation_id"] = "original"
    report = {**copy.deepcopy(acceptance.receipt_snapshot(receipt)), "accepted": True, "operation_id": "original", "samples": samples}
    path = tmp_path / "report.json"
    if snapshot:
        path.write_text(json.dumps(report))
    (tmp_path / "private-client.json").write_text(json.dumps({"session": "synthetic"}))
    if changed:
        receipt["operation_id"] = "different"
    calls = []

    class ReadOnlyClient:
        def __init__(self, session):
            self.http = SimpleNamespace(timeout=None, close=lambda: None)

        def request(self, method, target):
            calls.append((method, target))
            return receipt

    def run_with_final_readback(output):
        acceptance.final_readback(report, receipt)
        acceptance.write_report(output, report)

    monkeypatch.setenv("PPAP_CHECKER_LIVE_OUTPUT", str(tmp_path))
    monkeypatch.setattr(acceptance.sys, "argv", ["run_checker_live"] + (["--snapshot"] if snapshot else []))
    monkeypatch.setattr(acceptance, "Client", ReadOnlyClient)
    monkeypatch.setattr(acceptance, "run", run_with_final_readback)
    assert acceptance.main() == (1 if changed else 0)
    saved = json.loads(path.read_text())
    assert saved["accepted"] is not changed
    if changed:
        assert saved["gate_failure"] == "final_receipt_identity_or_proof_changed"
        assert "acceptance passed" not in capsys.readouterr().out
    assert calls == ([("GET", "/checker-optimizations/original")] if snapshot else [])


@pytest.mark.parametrize("mutation", ["operation", "state", "proof", "base", "usage"])
def test_final_readback_revokes_acceptance_if_identity_or_sealed_proof_changes(mutation):
    receipt, samples = valid_receipt()
    receipt["operation_id"] = "original"
    report = {**copy.deepcopy(acceptance.receipt_snapshot(receipt)), "accepted": True, "operation_id": "original", "samples": samples}
    if mutation == "operation":
        receipt["operation_id"] = "different"
    elif mutation == "state":
        receipt.update(state="unknown", report=None)
    elif mutation == "base":
        receipt["suite_revision_id"] = "another-base"
    elif mutation == "usage":
        receipt["usage"]["model_requests"] += 1
    else:
        receipt["report"]["validation"]["cases"][0]["clauses"][0]["actual"] = "PASS"
    acceptance.final_readback(report, receipt)
    assert report["accepted"] is False and report["gate_failure"] == "final_receipt_identity_or_proof_changed"
    assert report["operation_id"] == "original"


@pytest.mark.parametrize(
    "rendered",
    [
        {"round_id": "old", "attempt": "1", "status": "符合"},
        {"round_id": "new", "attempt": "1", "status": "符合"},
        {"round_id": "old", "attempt": "1", "status": "不符合"},
        {"round_id": "new", "attempt": "2", "status": "不符合"},
    ],
)
def test_old_pass_page_cannot_prove_a_new_ng_reaudit(rendered):
    expected = {"id": "new", "result_attempt": 1, "findings": [{"status": "NG"}]}
    acceptance.validate_rendered_round({"round_id": "new", "attempt": "1", "status": "不符合"}, expected)
    with pytest.raises(acceptance.AcceptanceFailure, match="browser_round_identity_or_result_mismatch"):
        acceptance.validate_rendered_round(rendered, expected)


def test_dom_identity_hooks_use_rendered_product_state_not_requested_url():
    reader = (ROOT / "apps/web/src/audit/round-reader.tsx").read_text()
    result = (ROOT / "apps/web/src/audit/checker-feedback.tsx").read_text()
    assert "data-round-id={round?.id === roundId ? round.id : undefined}" in reader
    assert "data-result-attempt={round?.id === roundId ? round.result_attempt : undefined}" in reader
    assert "data-operation-id={operation.operation_id}" in result
    assert "data-candidate-revision={operation.candidate_revision_id}" in result
    assert "data-case-id={row.case_id}" in result


@pytest.mark.parametrize("run_id,attempt", [("123", "2"), ("124", "1")])
def test_manual_rerun_and_new_dispatch_prepare_fresh_isolated_tests(tmp_path, run_id, attempt):
    first = tmp_path / "first.env"
    setup.prepare(configuration(), first)
    env = configuration()
    env.update(GITHUB_RUN_ID=run_id, GITHUB_RUN_ATTEMPT=attempt, COMPOSE_PROJECT_NAME=f"ppap-checker-live-{run_id}-{attempt}")
    second = tmp_path / "second.env"
    setup.prepare(env, second)
    assert acceptance.live_configuration(env) == (env["CI_LLM_BASE_URL"], env["CI_LLM_MODEL"])
    assert first.read_text() != second.read_text()


def test_existing_attempt_cannot_be_reinvoked_or_overwrite_its_operation(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    original = {"accepted": False, "stage": "waiting", "operation_id": "uncertain-original", "usage": {"uncertain": True}}
    path.write_text(json.dumps(original))
    for key, value in configuration().items():
        monkeypatch.setenv(key, value)
    with pytest.raises(acceptance.TestAlreadyStarted):
        acceptance.run(path)
    assert json.loads(path.read_text()) == original


def test_actions_model_configuration_is_the_only_source_for_all_stack_consumers():
    env = checker_workflow()["jobs"]["live"]["env"]
    assert env["CI_LLM_MODEL"] == "${{ secrets.CI_LLM_MODEL }}"
    assert env["CI_LLM_BASE_URL"] == "${{ secrets.CI_LLM_BASE_URL }}"
    config = yaml.safe_load((ROOT / "deploy/isolated-dev/checker-live.yaml").read_text())
    for service in ("api", "worker", "deep"):
        values = config["services"][service]["environment"]
        assert values["LLM_MODEL"] == "${CI_LLM_MODEL:?}"
        assert values["LLM_BASE_URL"] == values["PPAP_PERSONAL_MODEL_ALLOWED_BASE_URLS"] == "${CI_LLM_BASE_URL:?}"


def test_requested_model_records_the_gateway_selection_without_copying_private_content(tmp_path):
    from uuid import uuid4

    from agent.execution.gateway import Grants

    probe = controller.Probe(tmp_path / "evidence/controller.json")
    probe.author_active = True
    probe.facts["adaptations"] = 1
    model = "provider/arbitrary-model-alias"
    grants = Grants(model)
    body = {"model": "cli-internal-alias", "input": "private-synthetic-content"}
    grants.authorize(grants.issue(uuid4()), body, 100, select_model=True)
    probe.observe_provider(body)
    saved = json.loads(probe.path.read_text())
    assert saved["requested_models"] == [model]
    assert "private-synthetic-content" not in probe.path.read_text()
    assert "cli-internal-alias" not in probe.path.read_text()


def test_partial_progress_retains_only_verified_steps_and_known_usage(tmp_path):
    path = tmp_path / "report.json"
    report = {"accepted": False, "journey": {}, "usage": {"input_tokens": 17}}
    acceptance.checkpoint(path, report, "publishing", browser_optimize=True, reconnect_readback=True)
    acceptance.checkpoint(path, report, "reauditing", browser_publish=True)
    saved = json.loads(path.read_text())
    assert saved["stage"] == "reauditing" and saved["accepted"] is False
    assert saved["journey"] == {"browser_optimize": True, "reconnect_readback": True, "browser_publish": True}
    assert saved["usage"] == {"input_tokens": 17}


@pytest.mark.parametrize("accepted,job_status", [(True, "success"), (False, "failure"), (True, "failure")])
def test_summary_requires_real_completion_and_does_not_infer_failure_cause(tmp_path, accepted, job_status):
    report_path = tmp_path / "checker-live-output/report.json"
    acceptance.write_report(report_path, {"accepted": accepted, "stage": "complete" if accepted else "reauditing", "journey": {"browser_publish": True}, "usage": {"input_tokens": 17}})
    summary = tmp_path / "summary.md"
    env = configuration()
    env.update(RUNNER_TEMP=str(tmp_path), GITHUB_STEP_SUMMARY=str(summary), CHECKER_JOB_STATUS=job_status, GITHUB_SHA="a" * 40)
    setup.summarize(env)
    rendered = summary.read_text()
    facts = json.loads(rendered.split("```json\n")[1].split("\n```")[0])
    assert facts["outcome"] == ("PROVEN" if accepted and job_status == "success" else "NOT_PROVEN")
    assert facts["verified_steps"] == ["browser_publish"]
    assert facts["usage"] == {"input_tokens": 17}
    assert facts["provider_returned_model"].startswith("unknown")
    if facts["outcome"] == "NOT_PROVEN":
        assert facts["failure_attribution"] == "inconclusive"
    assert env["CI_LLM_API_KEY"] not in rendered


def test_summary_does_not_turn_an_unexecuted_test_into_success(tmp_path):
    summary = tmp_path / "summary.md"
    env = configuration()
    env.update(RUNNER_TEMP=str(tmp_path), GITHUB_STEP_SUMMARY=str(summary), CHECKER_JOB_STATUS="success", GITHUB_SHA="a" * 40)
    setup.summarize(env)
    facts = json.loads(summary.read_text().split("```json\n")[1].split("\n```")[0])
    assert facts["outcome"] == "NOT_PROVEN"
    assert facts["stage"] == "setup_or_build"
    assert facts["usage"] == "unknown"


@pytest.mark.parametrize("accepted", [False, True])
def test_cli_reentry_rejects_without_rewriting_original_verdict_or_usage(tmp_path, monkeypatch, accepted):
    path = tmp_path / "report.json"
    original = json.dumps({"accepted": accepted, "operation_id": "original", "usage": {"input_tokens": 17}, "gate_failure": "original-failure"})
    path.write_text(original)
    monkeypatch.setenv("PPAP_CHECKER_LIVE_OUTPUT", str(tmp_path))
    monkeypatch.setattr(acceptance.sys, "argv", ["run_checker_live"])
    monkeypatch.setattr(acceptance, "run", lambda _: pytest.fail("A second operation must not start"))
    assert acceptance.main() == 1
    assert path.read_text() == original
