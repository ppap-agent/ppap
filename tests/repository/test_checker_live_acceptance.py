"""The paid gate itself must reject fake success without contacting a provider."""

import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

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
        "PPAP_CHECKER_LIVE_APPROVED": "true",
        "GITHUB_RUN_ATTEMPT": "1",
        "PPAP_BUILD_SHA": "a" * 40,
        "PPAP_APPROVED_SHA": "a" * 40,
        "CI_LLM_BASE_URL": "https://provider.example/v1",
        "PPAP_APPROVED_BASE_URL": "https://provider.example/v1",
        "CI_LLM_MODEL": "arbitrary-model",
        "PPAP_APPROVED_MODEL": "arbitrary-model",
        "CI_LLM_API_KEY": "synthetic-never-transmitted-key",
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "123",
        "COMPOSE_PROJECT_NAME": "ppap-checker-live-123",
        "PPAP_PERSONAL_MODEL_MAX_REQUESTS": "32",
        "PPAP_PERSONAL_MODEL_MAX_INPUT_TOKENS": "200000",
        "PPAP_PERSONAL_MODEL_MAX_OUTPUT_TOKENS": "32000",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("PPAP_CHECKER_LIVE_APPROVED", "false"),
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("PPAP_APPROVED_BASE_URL", "https://other.example/v1"),
        ("PPAP_APPROVED_MODEL", "another-model"),
        ("CI_LLM_API_KEY", ""),
        ("GITHUB_ACTIONS", "false"),
        ("GITHUB_RUN_ID", "not-a-run"),
        ("COMPOSE_PROJECT_NAME", "production"),
    ],
)
def test_live_entry_requires_exact_explicit_approval_and_isolated_services(key, value):
    env = configuration()
    env[key] = value
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance.approved_configuration(env)


def test_provider_is_not_tied_to_a_brand():
    assert acceptance.approved_configuration(configuration()) == ("https://provider.example/v1", "arbitrary-model")


@pytest.mark.parametrize("base", ["http://provider.example/v1", "https://user:secret@provider.example/v1", "https://provider.example/v1?key=secret", "https://provider.example/v1#secret"])
def test_provider_address_cannot_carry_a_secret_or_use_plaintext(base):
    env = configuration()
    env["CI_LLM_BASE_URL"] = env["PPAP_APPROVED_BASE_URL"] = base
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance.approved_configuration(env)


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
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("PPAP_APPROVED_SHA", "b" * 40),
        ("PPAP_APPROVED_MODEL", "other"),
        ("PPAP_PERSONAL_MODEL_MAX_REQUESTS", "33"),
        ("PPAP_PERSONAL_MODEL_MAX_INPUT_TOKENS", "200001"),
        ("PPAP_PERSONAL_MODEL_MAX_OUTPUT_TOKENS", "0"),
    ],
)
def test_setup_rejects_changed_approval_or_excessive_budget(tmp_path, key, value):
    env = configuration()
    env[key] = value
    with pytest.raises(ValueError):
        setup.prepare(env, tmp_path / "private.env")
    assert not (tmp_path / "private.env").exists()


def valid_receipt():
    samples, cases = {}, []
    for ordinal, (value, expected) in enumerate(acceptance.SAMPLES, 1):
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


def test_expectations_are_literal_and_not_a_model_or_checker_output():
    assert acceptance.SAMPLES == ((3, "NG"), (10, "PASS"), (20, "PASS"), (7, "NG"), (-5, "NG"), (9, "NG"), (12, "PASS"), (30, "PASS"))
    assert not any(str(acceptance.SAMPLES) in text for text in (acceptance.BASELINE, (ROOT / "scripts/checker_live_controller.py").read_text()))
    for ordinal, (value, _expected) in enumerate(acceptance.SAMPLES, 1):
        raw = acceptance.workbook(value, ordinal)
        book = load_workbook(io.BytesIO(raw), data_only=True)
        assert book.active["A1"].value == value
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
    probe.observe_provider({"input": "safe synthetic training"})
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


def test_workflow_is_manual_only_bounded_and_not_in_default_gate():
    workflow = yaml.load((ROOT / ".github/workflows/checker-optimization-live.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["approve_paid"]["default"] == "false"
    assert workflow["jobs"]["live"]["if"] == "${{ inputs.approve_paid && github.run_attempt == 1 }}"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert "checker-optimization-live" not in (ROOT / ".github/workflows/tests.yml").read_text()
    assert "CI_LLM_API_KEY" not in json.dumps(workflow["jobs"]["live"]["env"])
    steps_with_key = [step for step in workflow["jobs"]["live"]["steps"] if "CI_LLM_API_KEY" in step.get("env", {})]
    assert len(steps_with_key) == 1


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
    assert report["state"] == "unknown" and report["failure"] == "execution_outcome_unknown"
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
