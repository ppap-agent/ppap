"""The paid gate itself must reject fake success without contacting a provider."""

import copy
import hashlib
import io
import json
import socket
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
        "PPAP_DEEP_MODEL_MAX_REQUESTS": "32",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("PPAP_APPROVED_SHA", "b" * 40),
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


def local_configuration():
    env = {key: value for key, value in configuration().items() if not key.startswith("GITHUB_")}
    env.update(PPAP_CHECKER_LIVE_MODE="local", PPAP_CHECKER_LIVE_LOCAL_ID="b" * 32, COMPOSE_PROJECT_NAME="ppap-checker-local-" + "b" * 32)
    env.update(LIVE_PG_DATABASE="ppap_checker_" + "b" * 32, LIVE_S3_BUCKET="checker-live-" + "b" * 32, LIVE_PG_PORT="41001", LIVE_S3_PORT="41002", LIVE_API_PORT="41003", LIVE_WEB_PORT="41004")
    return env


def test_local_frozen_inputs_use_runtime_library_without_disabling_revision_guard(monkeypatch):
    operation = {"operation_id": "old-operation", "tenant_id": "tenant", "frozen": {"executor_revision": "old-executor", "library_revision": "old-library"}}
    proof = {"operation_id": "old-operation", "tenant_id": "tenant", "executor_revision": "old-executor", "library_revision": "old-library", "inputs": {"case": "a" * 64}}
    calls = []

    def execute(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(proof).encode())

    monkeypatch.setattr(acceptance.subprocess, "run", execute)
    from audit import standard_suites

    monkeypatch.setattr(standard_suites, "library_revision", lambda: "new-driver-library")
    assert acceptance.frozen_input_digests(local_configuration(), None, operation) == {"case": "a" * 64}
    args, options = calls[0]
    assert args[:3] == ["docker", "exec", "-i"]
    assert "PGOPTIONS=-c default_transaction_read_only=on" in args
    assert "-worker-1" in args[-4]
    assert "materialize_request" in args[-1]
    assert json.loads(options["input"]) == {"operation_id": "old-operation", "tenant_id": "tenant"}
    for field, bad in (("operation_id", "other"), ("tenant_id", "other"), ("library_revision", "new-driver-library"), ("executor_revision", "other"), ("inputs", {"case": "not-a-digest"})):
        original = proof[field]
        proof[field] = bad
        with pytest.raises(acceptance.AcceptanceFailure, match="frozen_runtime_|invalid_frozen_runtime_inputs"):
            acceptance.frozen_input_digests(local_configuration(), None, operation)
        proof[field] = original


def test_guest_startup_uses_unchanged_production_limits_and_reports_receipt(monkeypatch):
    launches = []

    def launch(args, **kwargs):
        launches.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"1.5.0\n")

    monkeypatch.setattr(controller.subprocess, "run", launch)
    proof = controller.probe_guest_startup(SimpleNamespace(memory_mib=1536, max_processes=256))
    assert acceptance.verify_guest_startup({"guest_startup": proof}) == proof
    args, options = launches[0]
    assert args[:3] == ["/usr/bin/prlimit", "--as=1610612736", "--nproc=256"]
    assert "--nofile=128" in args and "--core=0" in args and "--fsize=268435456" in args
    assert "--reuid=10001" in args and "--no-new-privs" in args and "--bounding-set=-all" in args
    assert args[-2:] == ["/usr/local/bin/ppap-pi", "--version"]
    assert "API_KEY" not in json.dumps(options)
    for wrong in ({}, {**proof, "address_space_mib": 3072}, {**proof, "pi_version": "wrong"}):
        with pytest.raises(acceptance.AcceptanceFailure, match="guest_startup_not_proven"):
            acceptance.verify_guest_startup({"guest_startup": wrong})


@pytest.mark.parametrize("exit_code,version", [(-5, b""), (0, b"wrong")], ids=["guest-failure", "wrong-version"])
def test_guest_startup_rejects_failure_before_controller_ready(monkeypatch, exit_code, version):
    monkeypatch.setattr(controller.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=exit_code, stdout=version))
    with pytest.raises(ValueError, match="guest_startup_failed"):
        controller.probe_guest_startup(SimpleNamespace(memory_mib=1536, max_processes=256))


def test_local_entry_is_explicit_and_never_impersonates_actions_or_live_stack(tmp_path):
    env = local_configuration()
    assert acceptance.live_configuration(env) == (env["CI_LLM_BASE_URL"], env["CI_LLM_MODEL"])
    setup.prepare(env, tmp_path / "local.env")
    for changes in ({"COMPOSE_PROJECT_NAME": "ppap-current"}, {"CI": "true"}, {"GITHUB_ACTIONS": "false"}, {"PPAP_CHECKER_LIVE_LOCAL_ID": ""}):
        with pytest.raises(ValueError, match="isolated_local_test_required"):
            setup.validate_invocation({**env, **changes})


def test_local_prepare_allocates_distinct_ports_and_unique_data_names(tmp_path):
    env = local_configuration()
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        occupied = existing.getsockname()[1]
        path = tmp_path / "private.env"
        setup.prepare(env, path)
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert acceptance.data_names({**env, **values}) == ("ppap_checker_" + "b" * 32, "checker-live-" + "b" * 32)
    ports = acceptance.service_ports({**env, **values})
    assert len(set(ports.values())) == 4 and occupied not in ports.values()
    assert values["LIVE_EVIDENCE_SOURCE"] == "checker-evidence"
    with pytest.raises(acceptance.AcceptanceFailure, match="local_data_names_not_prepared"):
        acceptance.data_names({**env, "LIVE_PG_DATABASE": "ppap"})
    with pytest.raises(acceptance.AcceptanceFailure, match="local_ports_not_prepared"):
        acceptance.service_ports({**env, "LIVE_PG_PORT": ""})


def test_three_local_scenarios_have_one_operation_and_explicit_runtime_identity():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for scenario in ("u08", "rule-threshold", "rule-wording"):
        env = {**local_configuration(), "PPAP_BUILD_SHA": sha, "PPAP_CHECKER_LIVE_SCENARIO": scenario, "PPAP_CHECKER_LIVE_RUNTIME_SHA": "c" * 40}
        plan = acceptance.describe_scenario(env)
        assert plan["harness_sha"] == sha and plan["runtime_sha"] == "c" * 40
        assert plan["optimization_operations"] == 1 and plan["automatic_retry"] is False
        assert plan["samples"] == 8 and plan["hidden_documents"] == 2
        assert plan["selected_clause_ids"] == (["U08:4"] if scenario == "u08" else ["R1", "R2"])
        assert plan["complete_u08"] is False
        assert env["CI_LLM_API_KEY"] not in json.dumps(plan)
    with pytest.raises(acceptance.AcceptanceFailure, match="unsupported_live_scenario"):
        acceptance.scenario_name({"PPAP_CHECKER_LIVE_SCENARIO": "all-production"})


def test_u08_projection_preserves_full_existing_checker_and_resource_bytes():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    original = acceptance.frozen_u08_files(sha)
    key, rules, scoped, clause = acceptance.scenario_source("u08", sha)
    assert key.startswith("live:UAES:U08:4:") and clause == "U08:4"
    assert rules["rules"] == [row for row in json.loads(original["rules.json"])["rules"] if row["id"] == clause]
    assert rules["checker_binding"] == {clause: "checker:create_audit"}
    assert scoped["checker.py"] == original["checker.py"] and scoped["resources.py"] == original["resources.py"]


def test_u08_private_derivatives_keep_real_layout_and_independent_mark_truth():
    original = acceptance.U08_ORIGINAL.read_bytes()
    samples = acceptance.u08_samples()
    assert samples[0]["raw"] == original and samples[0]["basis"] == "existing_human_oracle"
    assert hashlib.sha256(original).hexdigest() == acceptance.U08_ORIGINAL_SHA
    source = load_workbook(io.BytesIO(original))
    try:
        original_cells = {cell.coordinate: cell.value for row in source["Table 1"] for cell in row}
        merges = list(source["Table 1"].merged_cells.ranges)
        for ordinal, sample in enumerate(samples, 1):
            book = load_workbook(io.BytesIO(sample["raw"]))
            try:
                sheet = book["Table 1"]
                assert list(sheet.merged_cells.ranges) == merges
                assert {cell.coordinate: cell.value for row in sheet for cell in row if cell.coordinate not in acceptance.U08_MARK_CELLS} == {
                    key: value for key, value in original_cells.items() if key not in acceptance.U08_MARK_CELLS
                }
                has_mark = any(str(sheet[cell].value or "").strip() for cell in acceptance.U08_MARK_CELLS)
                assert sample["expected"] == ("PASS" if has_mark else "NG")
                assert controller.contains_holdout(sample["raw"]) == (ordinal % 4 == 0)
            finally:
                book.close()
    finally:
        source.close()
    assert {samples[index]["expected"] for index in (3, 7)} == {"PASS", "NG"}
    assert len({hashlib.sha256(row["raw"]).hexdigest() for row in samples}) == 8
    assert acceptance.U08_ORIGINAL.read_bytes() == original


def test_rule_scenarios_preserve_unchanged_clause_and_wording_does_not_invent_repair():
    for name in ("rule-threshold", "rule-wording"):
        _, rules, files, _ = acceptance.scenario_source(name, "a" * 40)
        assert files["checker.py"].decode() == acceptance.BASELINE
        assert "大于等于 0" in rules["rules"][0]["rule_to_check"]
        assert [row["id"] for row in rules["rules"]] == ["R1", "R2"]
        samples = acceptance.scenario_samples(name)
        assert len(samples) == 8
        assert {samples[index]["expected"] for index in (3, 7)} == {"PASS", "NG"}
        if name == "rule-wording":
            assert all(sample["expected"] == ("PASS" if sample["value"] >= 0 else "NG") for sample in samples)


def test_local_image_check_rejects_missing_or_mixed_source_without_pull(monkeypatch):
    calls = []
    env = {**local_configuration(), "PPAP_CHECKER_LIVE_RUNTIME_SHA": "c" * 40}

    def inspect(command, **kwargs):
        calls.append(command)
        metadata = {"Id": "sha256:" + "d" * 64, "Architecture": "arm64" if "deep" in command[3] else "amd64", "Os": "linux", "Config": {"Labels": {"org.opencontainers.image.revision": "c" * 40}}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(metadata))

    monkeypatch.setattr(acceptance.subprocess, "run", inspect)
    identities = acceptance.verify_local_images(env)
    assert set(identities) == {"api", "worker", "deep", "web"}
    assert identities["deep"]["architecture"] == "arm64" and identities["api"]["architecture"] == "amd64"
    assert all(image["os"] == "linux" for image in identities.values())
    assert all(command[:3] == ["docker", "image", "inspect"] for command in calls)
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    with pytest.raises(acceptance.AcceptanceFailure, match="local_runtime_image_missing"):
        acceptance.verify_local_images(env)


def test_local_running_containers_and_http_sha_must_match_this_project(monkeypatch):
    env = local_configuration()
    images = {name: {"reference": name, "image_id": "sha256:" + name, "source_sha": env["PPAP_BUILD_SHA"]} for name in ("api", "worker", "deep", "web")}
    containers = [
        {
            "Id": name,
            "Image": "sha256:" + name,
            "Config": {"Labels": {"com.docker.compose.project": env["COMPOSE_PROJECT_NAME"], "com.docker.compose.service": name}},
            "State": {"Running": True},
            "NetworkSettings": {"Ports": {}},
        }
        for name in ("api", "worker", "deep", "web", "postgres", "silo")
    ]
    next(item for item in containers if item["Id"] == "deep")["Mounts"] = [{"Destination": "/evidence", "Type": "volume", "Name": env["COMPOSE_PROJECT_NAME"] + "_checker-evidence"}]
    for name, internal in (("api", "8080"), ("web", "4173"), ("postgres", "5432"), ("silo", "9000")):
        external = str(acceptance.service_ports(env)[name])
        next(item for item in containers if item["Id"] == name)["NetworkSettings"]["Ports"][internal + "/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": external}]
    monkeypatch.setattr(
        acceptance.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="\n".join(item["Id"] for item in containers) if command[:2] == ["docker", "ps"] else json.dumps(containers)),
    )

    class Http:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url):
            urls = acceptance.service_urls(env)
            assert url in {urls["api"] + "/version", urls["web"] + "/build-identity.json"}
            return SimpleNamespace(status_code=200, json=lambda: {"git_sha": env["PPAP_BUILD_SHA"]})

    monkeypatch.setattr(acceptance.httpx, "Client", Http)
    assert set(acceptance.verify_local_runtime(env, images)) == set(images)
    evidence = next(item for item in containers if item["Id"] == "deep")["Mounts"][0]
    evidence["Type"] = "bind"
    with pytest.raises(acceptance.AcceptanceFailure, match="local_evidence_requires_linux_volume"):
        acceptance.verify_local_runtime(env, images)
    evidence["Type"] = "volume"
    containers[0]["Image"] = "wrong-image"
    with pytest.raises(acceptance.AcceptanceFailure, match="running_image_identity_mismatch"):
        acceptance.verify_local_runtime(env, images)
    containers[0]["Image"] = images["api"]["image_id"]
    containers[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostIp"] = "0.0.0.0"
    with pytest.raises(acceptance.AcceptanceFailure, match="local_loopback_service_mismatch"):
        acceptance.verify_local_runtime(env, images)


def test_local_harness_rejects_wrong_or_dirty_source_before_business_writes(monkeypatch):
    env = local_configuration()
    responses = iter([SimpleNamespace(returncode=0, stdout=env["PPAP_BUILD_SHA"]), SimpleNamespace(returncode=1)])
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: next(responses))
    with pytest.raises(acceptance.AcceptanceFailure, match="local_harness_has_uncommitted_changes"):
        acceptance.verify_local_source(env)
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="wrong-source"))
    with pytest.raises(acceptance.AcceptanceFailure, match="local_harness_source_mismatch"):
        acceptance.verify_local_source(env)


def test_forwarded_pg_cluster_is_rejected_before_identity_or_s3_writes(tmp_path, monkeypatch):
    import product
    from product import db, storage

    env = local_configuration()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CI", raising=False)
    for key in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(acceptance, "verify_local_source", lambda environment: None)
    monkeypatch.setattr(acceptance, "verify_local_images", lambda environment: {})
    monkeypatch.setattr(acceptance, "verify_local_runtime", lambda environment, images: {})
    monkeypatch.setattr(acceptance, "local_settings", lambda environment: SimpleNamespace())
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="local-cluster|" + env["LIVE_PG_DATABASE"]))
    queries = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def transaction(self):
            return nullcontext()

        def execute(self, query):
            queries.append(query)
            return SimpleNamespace(fetchone=lambda: ("forwarded-cluster", env["LIVE_PG_DATABASE"]))

    monkeypatch.setattr(db, "connect", lambda *args: Connection())
    monkeypatch.setattr(storage, "s3_client", lambda *args: pytest.fail("Wrong PG identity must stop before S3"))
    monkeypatch.setattr(product, "build_product_store", lambda *args, **kwargs: pytest.fail("Wrong PG identity must stop before creating users or sessions"))
    with pytest.raises(acceptance.AcceptanceFailure, match="local_pg_cluster_identity_mismatch"):
        acceptance.run(tmp_path / "report.json")
    assert queries[0] == "SET TRANSACTION READ ONLY"


def test_private_database_nonce_and_authenticated_bucket_are_both_required(monkeypatch):
    from botocore.exceptions import ClientError
    from product import db, storage

    env = local_configuration()
    monkeypatch.setattr(acceptance, "local_settings", lambda environment: SimpleNamespace())
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="local-cluster|" + env["LIVE_PG_DATABASE"]))

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def transaction(self):
            return nullcontext()

        def execute(self, query):
            return SimpleNamespace(fetchone=lambda: ("local-cluster", env["LIVE_PG_DATABASE"]))

    monkeypatch.setattr(db, "connect", lambda *args: Connection())
    reads = []

    def head(**kwargs):
        reads.append(kwargs)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    monkeypatch.setattr(storage, "s3_client", lambda *args: SimpleNamespace(head_bucket=head))
    proof = acceptance.verify_local_data_services(env)
    assert proof["pg_system_identifier"] == "local-cluster" and proof["database"] == env["LIVE_PG_DATABASE"]
    assert reads == [{"Bucket": env["LIVE_S3_BUCKET"]}]

    def unauthorized(**kwargs):
        raise ClientError({"Error": {"Code": "InvalidAccessKeyId"}, "ResponseMetadata": {"HTTPStatusCode": 403}}, "HeadBucket")

    monkeypatch.setattr(storage, "s3_client", lambda *args: SimpleNamespace(head_bucket=unauthorized))
    with pytest.raises(acceptance.AcceptanceFailure, match="local_s3_bucket_unverified_InvalidAccessKeyId"):
        acceptance.verify_local_data_services(env)


def test_local_oracle_transport_preserves_bytes_without_putting_private_input_in_arguments(tmp_path, monkeypatch):
    path = tmp_path / "private-oracle.json"
    raw = b'{"hidden_case_ids":["private-oracle-canary"],"hidden_source_sha256":["hidden-source"]}'
    path.write_bytes(raw)
    calls = []

    def transfer(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs["input"] == raw and b"private-oracle-canary" not in repr(command).encode()
        return SimpleNamespace(returncode=0, stdout=(hashlib.sha256(raw).hexdigest() + "\n").encode())

    monkeypatch.setattr(acceptance.subprocess, "run", transfer)
    acceptance.transfer_private_oracle(path, local_configuration())
    assert len(calls) == 1
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"wrong-digest"))
    with pytest.raises(acceptance.AcceptanceFailure, match="private_oracle_transport_identity_mismatch"):
        acceptance.transfer_private_oracle(path, local_configuration())


def test_local_controller_transport_rejects_corruption_without_overwriting_known_receipt(tmp_path, monkeypatch):
    path = tmp_path / "controller.json"
    raw = b'{"guest_cannot_read_evidence":true,"provider_requests":0}'
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=hashlib.sha256(raw).hexdigest().encode() + b"\n" + raw))
    assert acceptance.controller_evidence(path, local_configuration())["provider_requests"] == 0
    assert path.read_bytes() == raw
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"wrong\n" + raw))
    with pytest.raises(acceptance.AcceptanceFailure, match="controller_evidence_transport_identity_mismatch"):
        acceptance.controller_evidence(path, local_configuration())
    assert path.read_bytes() == raw
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: pytest.fail("Linux workflow must keep its existing bind path"))
    acceptance.transfer_private_oracle(path, configuration())
    assert acceptance.controller_evidence(path, configuration())["provider_requests"] == 0


def test_live_browser_explicitly_selects_the_full_sample_set_before_one_admission(tmp_path, monkeypatch):
    """Driver orchestration only; native browser behavior has its separate journey."""
    env = configuration()
    monkeypatch.setenv("CI_LLM_API_KEY", env["CI_LLM_API_KEY"])
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(expect=Mock()))
    _, samples = valid_receipt()
    report = {"journey": {}, "samples": samples}
    report_path = tmp_path / "report.json"
    receipt = {"operation_id": "ui-generated-operation", "state": "queued"}
    selected = [next(iter(samples))]  # The product's current-row default.
    page, batch, select_all, start = Mock(), Mock(), Mock(), Mock()
    labels, captures, forwarded = {}, [], []
    page.get_by_role.side_effect = lambda role, *, name, exact: batch if role == "region" else start if name == "确认开始修复" else Mock()
    batch.get_by_role.side_effect = lambda role, *, name, exact: select_all if name == "选择全部已确认样本" else Mock()
    select_all.click.side_effect = lambda: selected.__setitem__(slice(None), list(samples))
    page.get_by_label.side_effect = lambda name, *, exact: labels.setdefault(name, Mock())
    page.expect_response.side_effect = [nullcontext(SimpleNamespace(value=SimpleNamespace(status=200, json=Mock(return_value=receipt))))]
    page.route.side_effect = lambda pattern, callback: captures.append(callback)

    def admit():
        body = {"operation_id": receipt["operation_id"], "consume_model": True, "feedback_ids": list(selected)}

        def forward():
            persisted = json.loads(report_path.read_text())
            assert persisted["stage"] == "admitting" and persisted["operation_id"] == body["operation_id"]
            labels["同意由 AI 修复并验证这 8 条反馈"].check.assert_called_once()
            forwarded.append(body)

        captures[0](SimpleNamespace(request=SimpleNamespace(method="POST", post_data_json=body), continue_=forward))

    start.click.side_effect = admit
    journey = acceptance.BrowserJourney.__new__(acceptance.BrowserJourney)
    journey.page = page
    assert journey.start({"round_id": "round", "file_version_id": "file"}, report, report_path) == receipt
    select_all.click.assert_called_once()
    assert len(forwarded) == 1 and forwarded[0]["feedback_ids"] == list(samples)
    assert report["journey"] == {"browser_feedback_selection": True}
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
        ("PPAP_DEEP_MODEL_MAX_REQUESTS", "33"),
        ("PPAP_DEEP_MODEL_MAX_REQUESTS", "0"),
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
        "usage": {"source": "provider", "input_tokens": 500, "output_tokens": 100, "model_requests": 2},
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


def test_wording_can_validate_unchanged_code_but_u08_must_resolve_a_real_unknown():
    receipt, samples = valid_receipt()
    for case in receipt["report"]["validation"]["cases"]:
        ident = case["case_id"]
        samples[ident]["baseline"] = samples[ident]["expected"]
        case["clauses"][0]["baseline_actual"] = samples[ident]["expected"]
    acceptance.validate_outcome(receipt, samples, scenario="rule-wording")
    with pytest.raises(acceptance.AcceptanceFailure, match="repair_or_holdout_coverage_missing"):
        acceptance.validate_outcome(receipt, samples, scenario="u08")
    for case in receipt["report"]["validation"]["cases"]:
        ident = case["case_id"]
        samples[ident].update(expected_by_clause={"U08:4": samples[ident]["expected"]}, baseline_by_clause={"U08:4": "UNABLE"})
        case["clauses"][0].update(clause_id="U08:4", baseline_actual="UNABLE")
    acceptance.validate_outcome(receipt, samples, scenario="u08")
    for case in receipt["report"]["validation"]["cases"]:
        samples[case["case_id"]]["baseline_by_clause"]["U08:4"] = "ERR"
        case["clauses"][0]["baseline_actual"] = "ERR"
    with pytest.raises(acceptance.AcceptanceFailure, match="repair_or_holdout_coverage_missing"):
        acceptance.validate_outcome(receipt, samples, scenario="u08")


def test_rule_change_live_cannot_omit_the_unchanged_clause_regression():
    receipt, samples = valid_receipt()
    for case in receipt["report"]["validation"]["cases"]:
        ident = case["case_id"]
        samples[ident].update(expected_by_clause={"R1": samples[ident]["expected"], "R2": "PASS"}, baseline_by_clause={"R1": samples[ident]["baseline"], "R2": "PASS"})
    with pytest.raises(acceptance.AcceptanceFailure, match="clause_coverage_failed"):
        acceptance.validate_outcome(receipt, samples, scenario="rule-threshold")
    for case in receipt["report"]["validation"]["cases"]:
        case["clauses"].append({**case["clauses"][0], "clause_id": "R2", "expected": "PASS", "actual": "PASS", "baseline_actual": "PASS"})
    acceptance.validate_outcome(receipt, samples, scenario="rule-threshold")


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
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {"expected_sha", "max_model_requests"}
    job = workflow["jobs"]["live"]
    assert "if" not in job
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert "checker-optimization-live" not in (ROOT / ".github/workflows/tests.yml").read_text()
    assert "CI_LLM_API_KEY" not in json.dumps(job["env"])
    # Only the step that starts the stack gives the platform key to its services.
    steps_with_key = [step for step in job["steps"] if "CI_LLM_API_KEY" in step.get("env", {})]
    assert [step["name"] for step in steps_with_key] == ["Start only the independent candidate stack"]
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
            assert service["environment"]["LLM_API_KEY"] == "${CI_LLM_API_KEY:-}"
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


def test_new_rule_operation_binds_the_reconfirmed_revision_not_old_adoption():
    receipt, context = identity_case()
    receipt["suite_revision_id"] = "new-rule-revision"
    acceptance.validate_bindings(receipt, **{**context, "base_revision_id": "new-rule-revision"})
    with pytest.raises(acceptance.AcceptanceFailure, match="operation_identity_mismatch"):
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
    assert settings.deep_model_max_requests == 32
    config = yaml.safe_load((ROOT / "deploy/isolated-dev/checker-live.yaml").read_text())
    assert config["services"]["api"]["ports"] == ["127.0.0.1:${LIVE_API_PORT:-18080}:8080"]
    assert config["services"]["web"]["ports"] == ["127.0.0.1:${LIVE_WEB_PORT:-14173}:4173"]
    local = acceptance.local_settings({**env, **local_configuration()})
    assert local.pg_port == 41001 and local.pg_database == "ppap_checker_" + "b" * 32
    assert local.s3_endpoint == "http://127.0.0.1:41002" and local.s3_bucket == "checker-live-" + "b" * 32
    assert local.web_url == "http://127.0.0.1:41004"


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
    expected = {"id": "new", "result_attempt": 1, "findings": [{"item_key": "R1", "status": "NG"}]}
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
        assert values["LLM_BASE_URL"] == values["PPAP_TENANT_MODEL_ALLOWED_BASE_URLS"] == "${CI_LLM_BASE_URL:?}"


def test_requested_model_records_the_gateway_selection_without_copying_private_content(tmp_path):
    from uuid import uuid4

    from agent.execution.gateway import Grants

    probe = controller.Probe(tmp_path / "evidence/controller.json")
    probe.author_active = True
    probe.facts["adaptations"] = 1
    model = "provider/arbitrary-model-alias"
    grants = Grants(model)
    body = {"model": "cli-internal-alias", "input": "private-synthetic-content"}
    grants.authorize(grants.issue(uuid4()), body, 100)
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
