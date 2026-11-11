"""Manual paid Checker TDD test and regression guard, outside default pytest.

A private independent Oracle freezes answers before optimization. Synthetic
rule-change cases and a bounded real U08 workbook case share the same runtime.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from openpyxl import Workbook, load_workbook

from scripts.prepare_checker_live import validate_invocation

TRAINING_SAMPLES = ((3, "NG"), (10, "PASS"), (20, "PASS"), (-5, "NG"), (9, "NG"), (12, "PASS"))
HOLDOUT_MARKER = "checker-live-holdout-"
SCENARIOS = {"synthetic", "u08", "rule-threshold", "rule-wording"}
ROOT = Path(__file__).resolve().parents[1]
U08_ORIGINAL = ROOT / "assets/corpus/documents/uaes/通用文件/表单模板/测试文件/10 控制计划/控制计划.xlsx"
U08_ORIGINAL_SHA = "777dc22645a73dc11ce5ed5aa1b2883b32709469a4c3d9aea1df4f3ffadbf54f"
U08_MARK_CELLS = ("G20", "G21", "G22", "G34", "G35", "G36")
BASELINE = """import json
from types import SimpleNamespace
from openpyxl import load_workbook
from audit.contracts import AuditResultItem, AuditEvidence, EvidenceSource

def create_audit(audit_file, rule, group_context=None):
    book = load_workbook(audit_file.file_url, data_only=True)
    try:
        value = book.active["A1"].value
    finally:
        book.close()
    def check(clause):
        locator = json.dumps({"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1",
                              "file_version_id": audit_file.file_metadata["file_version_id"]})
        return AuditResultItem(classification="规则审核", item="阈值", priority="P0",
            status="PASS" if value >= 0 else "NG", suggestions="已核对 A1。", clause_id=clause, checks_performed=1,
            evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                sources=(EvidenceSource(kind="structure", locator=locator),)))
    return SimpleNamespace(checks=[SimpleNamespace(rule_item=SimpleNamespace(id=row["id"]),
        check=lambda clause=row["id"]: check(clause)) for row in rule.rules])
"""


class AcceptanceFailure(Exception):
    """Only fixed, non-secret failure codes may be emitted."""


class TestAlreadyStarted(AcceptanceFailure):
    """A second invocation must leave the original evidence untouched."""


def require(condition, code):
    if not condition:
        raise AcceptanceFailure(code)


def live_configuration(environment):
    """Use Actions configuration, preserving the product's endpoint safety rules."""
    from product.model_endpoint import normalize_base_url, valid_model

    validate_invocation(environment)
    base = normalize_base_url(environment.get("CI_LLM_BASE_URL", ""))
    # The provider key is given only to the stack's services, never to this runner.
    model = valid_model(environment.get("CI_LLM_MODEL", ""))
    return base, model


def workbook(value, ordinal):
    book = Workbook()
    book.active.append([value, HOLDOUT_MARKER + uuid4().hex if ordinal % 4 == 0 else "synthetic-training-" + uuid4().hex])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def make_samples():
    # This repository is public. Neither hidden values nor their filename/label
    # mapping may be recoverable from its source or a previous run's artifacts.
    # The odd numerator makes the negative case non-integral and disjoint from
    # training; the positive case is also outside every training value.
    hidden_values = [(2 * secrets.randbelow(1 << 39) + 1) * 10 / (1 << 40), 21 + secrets.randbelow(1 << 40)]
    if secrets.randbelow(2):
        hidden_values.reverse()
    # This independent, trusted Oracle implements the user-approved threshold;
    # it never executes candidate code or asks the provider for an expectation.
    hidden = [(value, "PASS" if value >= 10 else "NG") for value in hidden_values]
    return (*TRAINING_SAMPLES[:3], hidden[0], *TRAINING_SAMPLES[3:], hidden[1])


def scenario_name(environment):
    name = environment.get("PPAP_CHECKER_LIVE_SCENARIO", "synthetic")
    require(name in SCENARIOS, "unsupported_live_scenario")
    return name


def service_ports(environment):
    defaults = {"postgres": 15432, "silo": 19000, "api": 18080, "web": 14173}
    if environment.get("PPAP_CHECKER_LIVE_MODE") != "local":
        return defaults
    names = {"postgres": "LIVE_PG_PORT", "silo": "LIVE_S3_PORT", "api": "LIVE_API_PORT", "web": "LIVE_WEB_PORT"}
    raw = {component: environment.get(name, "") for component, name in names.items()}
    require(all(value.isdecimal() and 1024 <= int(value) <= 65535 for value in raw.values()), "local_ports_not_prepared")
    ports = {component: int(value) for component, value in raw.items()}
    require(len(set(ports.values())) == 4, "local_ports_not_distinct")
    return ports


def service_urls(environment):
    return {component: f"http://127.0.0.1:{port}" for component, port in service_ports(environment).items() if component != "postgres"}


def data_names(environment):
    if environment.get("PPAP_CHECKER_LIVE_MODE") != "local":
        return "ppap", "checker-live"
    ident = environment["PPAP_CHECKER_LIVE_LOCAL_ID"]
    database, bucket = "ppap_checker_" + ident, "checker-live-" + ident
    require(environment.get("LIVE_PG_DATABASE") == database and environment.get("LIVE_S3_BUCKET") == bucket, "local_data_names_not_prepared")
    return database, bucket


def runtime_images(environment):
    return {component: environment.get("LIVE_" + component.upper() + "_IMAGE") or f"ppap-{component}:{environment['PPAP_BUILD_SHA']}" for component in ("api", "worker", "deep", "web")}


def verify_local_images(environment):
    """Read existing images only. This driver never builds or pulls images."""
    expected = environment.get("PPAP_CHECKER_LIVE_RUNTIME_SHA", environment["PPAP_BUILD_SHA"])
    require(len(expected) == 40 and all(char in "0123456789abcdef" for char in expected), "invalid_runtime_source_sha")
    images = {}
    for component, reference in runtime_images(environment).items():
        inspected = subprocess.run(["docker", "image", "inspect", reference, "--format", "{{json .}}"], capture_output=True, text=True, timeout=10)
        require(inspected.returncode == 0, "local_runtime_image_missing")
        metadata = json.loads(inspected.stdout)
        actual = (metadata.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision")
        require(actual == expected, "runtime_image_source_mismatch")
        images[component] = {"reference": reference, "image_id": metadata["Id"], "source_sha": actual, "architecture": metadata["Architecture"], "os": metadata["Os"]}
    return images


def verify_local_source(environment):
    actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
    require(actual.returncode == 0 and actual.stdout.strip() == environment["PPAP_BUILD_SHA"], "local_harness_source_mismatch")
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, capture_output=True, timeout=10)
    require(clean.returncode == 0, "local_harness_has_uncommitted_changes")


def verify_local_runtime(environment, images):
    """Bind the loopback services to this disposable Compose project and images."""
    project = environment["COMPOSE_PROJECT_NAME"]
    listed = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project=" + project, "--format", "{{.ID}}"], capture_output=True, text=True, timeout=10)
    identifiers = listed.stdout.split()
    require(listed.returncode == 0 and bool(identifiers), "local_stack_not_running")
    inspected = subprocess.run(["docker", "inspect", *identifiers], capture_output=True, text=True, timeout=10)
    require(inspected.returncode == 0, "local_container_identity_unavailable")
    services = {}
    for container in json.loads(inspected.stdout):
        labels = container["Config"]["Labels"]
        require(labels.get("com.docker.compose.project") == project and container["State"]["Running"], "local_stack_identity_mismatch")
        service = labels.get("com.docker.compose.service")
        require(service not in services, "local_stack_service_ambiguous")
        services[service] = container
    for component, image in images.items():
        require(component in services and services[component]["Image"] == image["image_id"], "running_image_identity_mismatch")
    require(
        any(mount.get("Destination") == "/evidence" and mount.get("Type") == "volume" and mount.get("Name", "").startswith(project + "_") for mount in services["deep"].get("Mounts", [])),
        "local_evidence_requires_linux_volume",
    )
    ports, urls = service_ports(environment), service_urls(environment)
    for component, internal in (("api", "8080"), ("web", "4173"), ("postgres", "5432"), ("silo", "9000")):
        external = str(ports[component])
        require(component in services and services[component]["NetworkSettings"]["Ports"].get(internal + "/tcp") == [{"HostIp": "127.0.0.1", "HostPort": external}], "local_loopback_service_mismatch")
    expected = environment.get("PPAP_CHECKER_LIVE_RUNTIME_SHA", environment["PPAP_BUILD_SHA"])
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as http:
        api = http.get(urls["api"] + "/version")
        web = http.get(urls["web"] + "/build-identity.json")
        require(api.status_code == web.status_code == 200 and api.json().get("git_sha") == web.json().get("git_sha") == expected, "runtime_http_source_mismatch")
    return {component: {"container_id": services[component]["Id"], **image} for component, image in images.items()}


def verify_local_data_services(environment):
    """Prove the operator's actual PG/S3 targets before any business mutation.

    Published port metadata alone is insufficient on Desktop: a host-native
    port-forward can still own that address. The DB nonce and actual cluster ID
    must match the private container, and fresh S3 credentials must read its bucket.
    """
    from botocore.exceptions import ClientError
    from product.db import connect
    from product.storage import s3_client

    database, bucket = data_names(environment)
    settings = local_settings(environment)
    query = "SELECT system_identifier::text,current_database() FROM pg_control_system()"
    direct = subprocess.run(
        ["docker", "exec", environment["COMPOSE_PROJECT_NAME"] + "-postgres-1", "psql", "-U", "ppap", "-d", database, "-tA", "-c", query], capture_output=True, text=True, timeout=15
    )
    require(direct.returncode == 0, "local_pg_container_identity_unavailable")
    expected = direct.stdout.strip().split("|")
    require(len(expected) == 2 and expected[1] == database, "local_pg_database_nonce_mismatch")
    try:
        with connect(settings, "migrator") as conn, conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            actual = conn.execute(query).fetchone()
            require(actual is not None and list(actual) == expected, "local_pg_cluster_identity_mismatch")
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure("local_pg_operator_target_unverified") from None
    client = s3_client(settings)
    try:
        response = client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        safe_code = code if code.isalnum() else "unknown"
        raise AcceptanceFailure("local_s3_bucket_unverified_" + safe_code) from None
    require(response["ResponseMetadata"]["HTTPStatusCode"] == 200, "local_s3_bucket_unverified")
    return {"database": database, "pg_system_identifier": expected[0], "bucket": bucket, "authenticated_bucket_read": True}


def transfer_private_oracle(path, environment):
    if environment.get("PPAP_CHECKER_LIVE_MODE") != "local":
        return
    raw = path.read_bytes()
    code = (
        "import hashlib,sys,os; from pathlib import Path; raw=sys.stdin.buffer.read(); p=Path('/evidence/private-oracle.tmp'); "
        "p.write_bytes(raw); p.chmod(0o600); os.replace(p,'/evidence/private-oracle.json'); "
        "print(hashlib.sha256(Path('/evidence/private-oracle.json').read_bytes()).hexdigest())"
    )
    result = subprocess.run(["docker", "exec", "-i", environment["COMPOSE_PROJECT_NAME"] + "-deep-1", "python", "-c", code], input=raw, capture_output=True, timeout=15)
    require(result.returncode == 0 and result.stdout.decode().strip() == hashlib.sha256(raw).hexdigest(), "private_oracle_transport_identity_mismatch")


def controller_evidence(path, environment):
    if environment.get("PPAP_CHECKER_LIVE_MODE") == "local":
        code = "import hashlib,sys; from pathlib import Path; raw=Path('/evidence/controller.json').read_bytes(); sys.stdout.buffer.write(hashlib.sha256(raw).hexdigest().encode()+b'\\n'+raw)"
        result = subprocess.run(["docker", "exec", environment["COMPOSE_PROJECT_NAME"] + "-deep-1", "python", "-c", code], capture_output=True, timeout=15)
        require(result.returncode == 0, "controller_evidence_readback_failed")
        claimed, separator, raw = result.stdout.partition(b"\n")
        require(bool(separator) and claimed.decode() == hashlib.sha256(raw).hexdigest(), "controller_evidence_transport_identity_mismatch")
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.chmod(0o600)
        temporary.replace(path)
    return json.loads(path.read_text())


def verify_guest_startup(probe):
    expected = {"address_space_mib": 1536, "max_processes": 256, "pi_version": "1.5.0"}
    require(probe.get("guest_startup") == expected, "guest_startup_not_proven")
    return expected


def frozen_input_digests(environment, store, operation):
    """Rebuild evidence with the actual runtime library, never the newer driver."""
    if environment.get("PPAP_CHECKER_LIVE_MODE") != "local":
        from product.store.checker_development import CheckerDevelopmentStore
        from worker.checker_optimizer import materialize_request

        request, _ = materialize_request(CheckerDevelopmentStore(store), operation)
        return {sample.case_id: sample.inputs.digest for sample in request.oracle}
    code = """import json,sys
from psycopg.rows import dict_row
from product.settings import load_settings
from product.store.store import AuthStore
from product.store.checker_development import CheckerDevelopmentStore
from worker.checker_optimizer import materialize_request
identity=json.load(sys.stdin)
store=AuthStore(load_settings(),role='worker')
with store._conn() as conn:
 with conn.cursor(row_factory=dict_row) as cursor:
  row=cursor.execute('SELECT * FROM product.checker_optimizations WHERE operation_id=%s AND tenant_id=%s',(identity['operation_id'],identity['tenant_id'])).fetchone()
if row is None: raise ValueError('frozen_operation_unavailable')
request,_=materialize_request(CheckerDevelopmentStore(store),row)
proof={key:str(row[key]) for key in ('operation_id','tenant_id')}
proof.update({key:row['frozen'][key] for key in ('executor_revision','library_revision')})
proof['inputs']={sample.case_id:sample.inputs.digest for sample in request.oracle}
print(json.dumps(proof))
"""
    identity = {key: str(operation[key]) for key in ("operation_id", "tenant_id")}
    result = subprocess.run(
        ["docker", "exec", "-i", "-e", "PGOPTIONS=-c default_transaction_read_only=on", environment["COMPOSE_PROJECT_NAME"] + "-worker-1", "python", "-c", code],
        input=json.dumps(identity).encode(),
        capture_output=True,
        timeout=30,
    )
    require(result.returncode == 0 and len(result.stdout) <= 262144, "frozen_runtime_materialization_failed")
    try:
        proof = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise AcceptanceFailure("invalid_frozen_runtime_proof") from None
    frozen = operation["frozen"]
    require(all(proof.get(key) == value for key, value in identity.items()), "frozen_runtime_operation_mismatch")
    require(all(proof.get(key) == frozen[key] for key in ("executor_revision", "library_revision")), "frozen_runtime_revision_mismatch")
    inputs = proof.get("inputs")
    require(
        isinstance(inputs, dict) and bool(inputs) and all(isinstance(key, str) and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for key, value in inputs.items()),
        "invalid_frozen_runtime_inputs",
    )
    return inputs


def snapshot_controller(path, report):
    if os.environ.get("PPAP_CHECKER_LIVE_MODE") == "local":
        try:
            controller_evidence(path.parent / "controller.json", os.environ)
            report["controller_readback"] = "read_only_completed"
        except Exception:
            report["controller_readback"] = "unavailable"


def describe_scenario(environment):
    """A free, read-only plan. It does not create operations, samples or secrets."""
    name = scenario_name(environment)
    source_sha = environment.get("PPAP_CHECKER_LIVE_BASE_SHA", environment["PPAP_BUILD_SHA"])
    _, rules, files, clause_id = scenario_source(name, source_sha)
    return {
        "scenario": name,
        "harness_sha": environment["PPAP_BUILD_SHA"],
        "runtime_sha": environment.get("PPAP_CHECKER_LIVE_RUNTIME_SHA", environment["PPAP_BUILD_SHA"]),
        "runtime_images": runtime_images(environment),
        "baseline_source_sha": source_sha,
        "baseline_files": {key: hashlib.sha256(raw).hexdigest() for key, raw in files.items()},
        "selected_clause_ids": list(rules["checker_binding"]),
        "primary_clause_id": clause_id,
        "complete_u08": False,
        "samples": 8,
        "hidden_documents": 2,
        "optimization_operations": 1,
        "automatic_retry": False,
        "limits": {"requests": int(environment.get("PPAP_DEEP_MODEL_MAX_REQUESTS", "32"))},
        "oracle_basis": "existing_human_original_plus_rule_derived_variants" if name == "u08" else "independent_synthetic_rule",
        "original_sha256": U08_ORIGINAL_SHA if name == "u08" else None,
    }


def frozen_u08_files(source_sha):
    """Freeze the existing leaf, never a maintainer-authored replacement Checker."""
    require(len(source_sha) == 40 and all(char in "0123456789abcdef" for char in source_sha), "invalid_u08_source_sha")
    files = {}
    for name in ("checker.py", "resources.py", "rules.json"):
        result = subprocess.run(["git", "show", f"{source_sha}:packages/audit/src/audit/checkers/v5/UAES/U08/{name}"], cwd=ROOT, capture_output=True, timeout=10)
        require(result.returncode == 0, "u08_source_not_available")
        files[name] = result.stdout
    return files


def u08_samples():
    """Preserve the real workbook layout; vary only independently observed marks.

    The unchanged original has the existing human PASS Oracle. Derived cases are
    rule-based tests, not additional human-reviewed corpus labels. Hidden cases
    get fresh mark choices/whitespace and private nonces, outside author inputs.
    """
    raw = U08_ORIGINAL.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == U08_ORIGINAL_SHA, "u08_original_changed")
    book = load_workbook(io.BytesIO(raw))
    try:
        marks = {cell: book["Table 1"][cell].value for cell in U08_MARK_CELLS}
        require(all(isinstance(value, str) and value.strip() for value in marks.values()), "u08_mark_basis_changed")
    finally:
        book.close()
    selected = tuple(secrets.SystemRandom().sample(U08_MARK_CELLS, 3))
    variants = [None, (U08_MARK_CELLS[0],), (), selected, (U08_MARK_CELLS[-1],), (), (U08_MARK_CELLS[1], U08_MARK_CELLS[4]), ()]
    # One positive and one negative holdout; their order is fresh per run.
    if secrets.randbelow(2):
        variants[3], variants[7] = variants[7], variants[3]
    samples = []
    for ordinal, retained in enumerate(variants, 1):
        if retained is None:
            samples.append({"raw": raw, "expected": "PASS", "locator": {"sheet": "Table 1", "cell": "G20"}, "basis": "existing_human_oracle"})
            continue
        book = load_workbook(io.BytesIO(raw))
        try:
            for cell in U08_MARK_CELLS:
                book["Table 1"][cell] = marks[cell] if cell in retained else (" \n\t" + " " * secrets.randbelow(32) if ordinal % 4 == 0 else None)
            book.properties.identifier = (HOLDOUT_MARKER if ordinal % 4 == 0 else "u08-derived-") + uuid4().hex
            stream = io.BytesIO()
            book.save(stream)
        finally:
            book.close()
        samples.append(
            {
                "raw": stream.getvalue(),
                "expected": "PASS" if retained else "NG",
                "locator": {"sheet": "Table 1", "cell": retained[0] if retained else "G20"},
                "basis": "rule_derived_not_human_reviewed",
            }
        )
    return samples


def scenario_samples(name):
    if name == "u08":
        return u08_samples()
    values = make_samples()
    if name == "rule-wording":
        values = (*TRAINING_SAMPLES[:3], (-1 - secrets.randbelow(1 << 32), "NG"), *TRAINING_SAMPLES[3:], (21 + secrets.randbelow(1 << 32), "PASS"))
    return [
        {
            "raw": workbook(value, ordinal),
            "expected": ("PASS" if value >= 0 else "NG") if name == "rule-wording" else expected,
            "locator": {"sheet": "Sheet", "cell": "A1"},
            "basis": "synthetic_rule_oracle",
            "value": value,
        }
        for ordinal, (value, expected) in enumerate(values, 1)
    ]


def scenario_source(name, source_sha):
    if name == "u08":
        files = frozen_u08_files(source_sha)
        rules = json.loads(files["rules.json"])
        # Explicit, disposable clause scope, not a claim that all U08 is verified.
        # The full existing Checker/resources stay byte-for-byte unchanged.
        key = "live:UAES:U08:4:" + uuid4().hex
        rules = {**rules, "suite_key": key, "related_suite_keys": [], "rules": [row for row in rules["rules"] if row["id"] == "U08:4"], "checker_binding": {"U08:4": rules["checker_binding"]["U08:4"]}}
        return key, rules, {**files, "rules.json": json.dumps(rules, ensure_ascii=False).encode()}, "U08:4"
    key = "live:threshold:" + uuid4().hex
    threshold = 10 if name == "synthetic" else 0
    rules = {
        "kind": "engine",
        "type": "控制计划",
        "rule_profile": "v3",
        "suite_key": key,
        "customers": [],
        "models": [],
        "parts": [],
        "rules": [
            {"id": "R1", "category": "阈值", "priority": "P0", "rule_to_check": f"A1 必须大于等于 {threshold}。数值小于 {threshold} 为 NG，大于等于 {threshold} 为 PASS。", "type_of_rule": "table"}
        ],
        "checker_binding": {"R1": "checker:create_audit"},
    }
    if name in {"rule-threshold", "rule-wording"}:
        rules["rules"].append({"id": "R2", "category": "不变条款", "priority": "P0", "rule_to_check": "A1 必须大于等于 0，小于 0 为 NG，否则为 PASS。", "type_of_rule": "table"})
        rules["checker_binding"]["R2"] = "checker:create_audit"
    return key, rules, {"rules.json": json.dumps(rules).encode(), "checker.py": BASELINE.encode()}, "R1"


def finding_for(result, clause_id):
    rows = [
        row
        for row in result.get("findings", [])
        if ((row.get("locator") or {}).get("semantic") or {}).get("clause_id", (row.get("locator") or {}).get("rule_item_id", row.get("item_key"))) == clause_id
    ]
    require(len(rows) == 1, "selected_clause_result_missing_or_ambiguous")
    return rows[0]


def expected_clauses(sample):
    return sample.get("expected_by_clause", {"R1": sample["expected"]})


def feedback_ids(samples):
    return {ident for key, sample in samples.items() for ident in sample.get("feedback_ids", [key])}


def publish_rule_change(client, rules, rule_id, name):
    changed = json.loads(json.dumps(rules))
    changed["rules"][0]["rule_to_check"] = "A1 必须大于等于 10，小于 10 为 NG，否则为 PASS。" if name == "rule-threshold" else changed["rules"][0]["rule_to_check"] + " 请逐项依据原件核对。"
    # Upload the actual new rule text, not a business workbook posing as rules.
    filename = "updated-customer-rules.json"
    uploaded = client.upload(filename, "application/json", json.dumps(changed, ensure_ascii=False).encode())
    units = client.request("GET", f"/files/{uploaded['file_id']}/versions/{uploaded['id']}/rule-sources")["units"]
    require(len(units) == len(changed["rules"]), "rule_source_coverage_missing")
    clauses = []
    for row, unit in zip(changed["rules"], units, strict=True):
        require(unit["excerpt"] == row["rule_to_check"].strip(), "rule_source_text_mismatch")
        clauses.append(
            {
                "id": row["id"],
                "title": row["category"],
                "text": row["rule_to_check"],
                "original": unit["excerpt"],
                "location": unit["location"],
                "source": filename,
                "checked": True,
                "provenance": {**unit, "fileVersionId": uploaded["id"]},
            }
        )
    context = {"registration": json.dumps({"basis": {"source": {"filename": filename}, "clauses": clauses}}, ensure_ascii=False)}
    return client.request(
        "POST",
        "/rules",
        payload={"request_id": str(uuid4()), "title": "明确更新的客户规则", "body": changed, "predecessor_id": rule_id, "source_file_version_id": uploaded["id"], "case_context": context},
        status=201,
    )


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def checkpoint(path, report, stage, **verified):
    """Persist only assertions already checked, before the next fallible stage."""
    report["stage"] = stage
    report["journey"].update(verified)
    write_report(path, report)


class Client:
    def __init__(self, session):
        from product.identity import SESSION_COOKIE

        self.http = httpx.Client(base_url=service_urls(os.environ)["api"], timeout=30, follow_redirects=False, headers={"Cookie": SESSION_COOKIE + "=" + session}, trust_env=False)

    def request(self, method, path, *, payload=None, content=None, status=200):
        # Never retry a write, including an uncertain optimization admission.
        response = self.http.request(method, path, json=payload, content=content)
        require(response.status_code == status, "unexpected_product_http_status")
        return response.json()

    def poll(self, path, terminal, *, seconds=2400, on_read=lambda _: None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            result = self.request("GET", path)
            on_read(result)
            if terminal(result):
                return result
            time.sleep(1)
        raise AcceptanceFailure("receipt_pending_no_retry")

    def upload(self, name, content_type, raw):
        uploaded = self.request("POST", "/files", payload={"original_filename": name, "content_type": content_type, "byte_size": len(raw)}, status=201)
        path = f"/files/{uploaded['file_id']}/versions/{uploaded['id']}"
        self.request("PUT", path + "/blob", content=raw)
        return self.request("POST", path + "/finalize")


def validate_outcome(receipt, samples, *, scenario="synthetic"):
    """Check each independently frozen answer, never just a server 'verified' bit."""
    require(receipt.get("state") == "completed", "optimization_not_completed")
    require(bool(receipt.get("candidate_revision_id")), "candidate_missing")
    usage = receipt.get("usage", {})
    # Provider token counts are observations; an unknown count never decides acceptance.
    require(usage.get("source") in {"provider", "unknown"}, "provider_usage_source_unexpected")
    report = (receipt.get("report") or {}).get("validation") or {}
    require(report.get("verified") is True and report.get("total_samples") == report.get("passed_samples") == len(samples), "independent_validation_failed")
    cases = report.get("cases", [])
    require(len(cases) == len(samples) and {case.get("case_id") for case in cases} == set(samples), "sample_identity_mismatch")
    repaired_misses, resolved = 0, 0
    holdout_expected = set()
    for case in cases:
        expected = samples[case["case_id"]]
        require(case.get("ordinal") == expected["ordinal"], "sample_ordinal_mismatch")
        split = "holdout" if expected["ordinal"] % 4 == 0 else "training"
        require(case.get("split") == split and case.get("coverage") is True and case.get("error_code") is None, "sample_coverage_failed")
        clauses = case.get("clauses", [])
        labels = expected_clauses(expected)
        require(len(clauses) == len(labels) and {row.get("clause_id") for row in clauses} == set(labels), "clause_coverage_failed")
        for clause in clauses:
            ident = clause["clause_id"]
            baseline = expected.get("baseline_by_clause", {"R1": expected["baseline"]})[ident]
            require(clause.get("expected") == labels[ident] and clause.get("actual") == labels[ident], "independent_answer_mismatch")
            require(clause.get("baseline_actual") == baseline, "baseline_answer_mismatch")
            require(clause.get("evidence_locatable") is True and clause.get("checks_performed", 0) > 0 and clause.get("regression") is False, "evidence_or_regression_failed")
            repaired_misses += labels[ident] == "NG" and baseline == "PASS"
            resolved += labels[ident] != baseline and baseline in {"PASS", "NG", "UNABLE"}
        if split == "holdout":
            holdout_expected.add(expected["expected"])
    repair_proven = resolved > 0 if scenario == "u08" else scenario == "rule-wording" or repaired_misses > 0
    require(holdout_expected == {"PASS", "NG"} and repair_proven, "repair_or_holdout_coverage_missing")
    return report


def receipt_snapshot(receipt):
    """Legal unknown receipts may have report:null; preserve confirmed facts first."""
    nested = receipt.get("report") or {}
    usage = receipt.get("usage") or {}
    return {
        **{name: receipt.get(name) for name in ("state", "suite_revision_id", "candidate_revision_id")},
        "returned_operation_id": receipt.get("operation_id"),
        "product_failure": receipt.get("failure"),
        "usage": {name: usage[name] for name in ("source", "uncertain", "model_requests", "input_tokens", "output_tokens") if name in usage},
        "validation": nested.get("validation") or {},
    }


def retain_receipt(report, receipt):
    snapshot = receipt_snapshot(receipt)
    snapshot["usage"] = {**report.get("usage", {}), **snapshot["usage"]}
    report.update(snapshot)


def final_readback(report, receipt):
    """A read must not erase gate diagnostics or keep a changed proof accepted."""
    mismatch = receipt.get("operation_id") != report.get("operation_id")
    if report.get("accepted"):
        previous = report.get("validation") or {}
        current = (receipt.get("report") or {}).get("validation") or {}
        mismatch = mismatch or receipt.get("state") != "completed" or any(receipt.get(name) != report.get(name) for name in ("suite_revision_id", "candidate_revision_id")) or current != previous
        mismatch = mismatch or receipt_snapshot(receipt)["usage"] != report.get("usage", {})
        try:
            validate_outcome(receipt, report["samples"], scenario=report.get("scenario", "synthetic"))
        except (AcceptanceFailure, KeyError, TypeError):
            mismatch = True
    if mismatch:
        report["accepted"] = False
        report.setdefault("gate_failure", "final_receipt_identity_or_proof_changed")
    retain_receipt(report, receipt)


def validate_rendered_round(rendered, expected, clause_id="R1"):
    require(
        rendered
        == {
            "round_id": expected["id"],
            "attempt": str(expected["result_attempt"]),
            "status": {"PASS": "符合", "NG": "不符合", "UNABLE": "无法判断", "ERR": "技术失败", "NA": "不适用"}[finding_for(expected, clause_id)["status"]],
        },
        "browser_round_identity_or_result_mismatch",
    )


def validate_bindings(receipt, *, operation_id, base_revision_id, detail, frozen, input_digests):
    report = (receipt.get("report") or {}).get("validation") or {}
    require(receipt.get("operation_id") == operation_id and receipt.get("suite_revision_id") == base_revision_id, "operation_identity_mismatch")
    require(receipt.get("candidate_revision_id") == detail["id"] == report.get("suite_revision_id"), "candidate_identity_mismatch")
    require(report.get("suite_digest") == detail["resolved_digest"] and report.get("oracle_revision") == frozen["oracle_revision"], "candidate_or_oracle_digest_mismatch")
    require(report.get("library_revision") == frozen["library_revision"], "library_identity_mismatch")
    require({case["case_id"]: case.get("input_digest") for case in report.get("cases", [])} == input_digests, "validation_input_identity_mismatch")


def history_fingerprint(result, snapshot):
    frozen = {"result_attempt": result["result_attempt"], "findings": result["findings"], "snapshot": snapshot}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class BrowserJourney:
    """Real Chromium against the built Web. No mocked routes, responses or traces."""

    def __init__(self, session, clause_id="R1"):
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()
        self.clause_id = clause_id
        self.web_url = service_urls(os.environ)["web"]
        self.context = None
        self.new_session(session)

    def new_session(self, session):
        from product.identity import SESSION_COOKIE

        if self.context:
            self.context.close()
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.context.add_cookies([{"name": SESSION_COOKIE, "value": session, "url": self.web_url, "httpOnly": True}])
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)

    def read_round(self, result, clause_id=None):
        from playwright.sync_api import expect

        page = self.page
        clause_id = clause_id or self.clause_id
        page.goto(self.web_url + "/audit?round=" + result["id"])
        view = page.get_by_role("region", name="历史审核", exact=True)
        expect(view).to_have_attribute("data-round-id", result["id"])
        expect(view).to_have_attribute("data-result-attempt", str(result["result_attempt"]))
        row = view.get_by_role("row").filter(has=page.get_by_role("button", name="查看依据 " + clause_id, exact=True))
        expect(row).to_have_count(1)
        status = row.get_by_role("cell").nth(2)
        expect(status).to_have_text({"PASS": "符合", "NG": "不符合", "UNABLE": "无法判断", "ERR": "技术失败", "NA": "不适用"}[finding_for(result, clause_id)["status"]])
        validate_rendered_round({"round_id": view.get_attribute("data-round-id"), "attempt": view.get_attribute("data-result-attempt"), "status": status.inner_text()}, result, clause_id)

    def read_optimization(self, receipt, samples):
        from playwright.sync_api import expect

        self.page.goto(self.address)
        view = self.page.get_by_role("region", name="修复与回归结果", exact=True)
        expect(view).to_have_attribute("data-operation-id", receipt["operation_id"])
        expect(view).to_have_attribute("data-candidate-revision", receipt["candidate_revision_id"])
        repaired = sum(expected != row.get("baseline_by_clause", {"R1": row["baseline"]})[clause] for row in samples.values() for clause, expected in expected_clauses(row).items())
        expect(view.get_by_text(f"修好 {repaired} 条 · 新增错误 0 条 · 无法判断 0 条 · 技术失败 0 条", exact=True)).to_be_visible()
        view.get_by_text("逐项验证结果与正确答案", exact=True).click()
        expect(view.locator("article[data-case-id]")).to_have_count(len(samples))
        labels = {"PASS": "符合", "NG": "不符合", "UNABLE": "无法判断", "ERR": "技术失败", "NA": "不适用"}
        for identity, sample in samples.items():
            case = view.locator(f'article[data-case-id="{identity}"]')
            split = "独立验证样本" if sample["ordinal"] % 4 == 0 else "修复样本"
            expect(case.get_by_text(split + " · 符合已确认答案", exact=True)).to_be_visible()
            for clause, expected in expected_clauses(sample).items():
                baseline = sample.get("baseline_by_clause", {"R1": sample["baseline"]})[clause]
                expect(case.get_by_text(f"条款 {clause} · 正确答案 {labels[expected]} · 此前 {labels[baseline]} → 当前 {labels[expected]}", exact=True)).to_be_visible()

    def feedback(self, result, uploaded, expected, *, clause_id=None, locator=None, rationale=None):
        page = self.page
        clause_id, locator = clause_id or self.clause_id, locator or {"sheet": "Sheet", "cell": "A1"}
        self.read_round(result, clause_id)
        page.get_by_role("button", name="查看依据 " + clause_id, exact=True).click()
        page.get_by_role("article", name="条款判定依据", exact=True).get_by_role("button", name="这条判错了", exact=True).click()
        page.get_by_label("正确结论", exact=True).select_option(expected)
        page.get_by_text("添加我在原件中找到的依据", exact=True).click()
        page.get_by_label("工作表名称", exact=True).fill(locator["sheet"])
        page.get_by_label("单元格（例如 B12）", exact=True).fill(locator["cell"])
        page.get_by_role("button", name="加入这处依据", exact=True).click()
        page.get_by_label("为什么应当这样判断", exact=True).fill(rationale or "人工合成样本独立答案：核对 A1 与大于等于 10 的要求。")
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/checker-feedback")) as changed:
            page.get_by_role("button", name="加入待修复", exact=True).click()
        require(changed.value.status == 200, "browser_feedback_failed")
        return changed.value.json()

    def start(self, original, report, report_path):
        from playwright.sync_api import expect

        page = self.page
        clause_id = getattr(self, "clause_id", "R1")
        self.address = (
            getattr(self, "web_url", service_urls(os.environ)["web"])
            + "/audit?round="
            + original["round_id"]
            + "&file="
            + original["file_version_id"]
            + f"&clause={clause_id}&basis=1&checkerFeedbackClause={clause_id}"
        )
        page.goto(self.address)
        checkpoint(report_path, report, "selecting_feedback")
        samples = report["samples"]
        selected_ids = feedback_ids(samples)
        batch = page.get_by_role("region", name="同类文件待修复", exact=True)
        expect(batch.get_by_role("heading", name=f"已确认的样本 {len(selected_ids)} 条", exact=True)).to_be_visible()
        # The product defaults to the current feedback only. This test explicitly
        # requests one combined repair, while retaining the independent holdout.
        batch.get_by_role("button", name="选择全部已确认样本", exact=True).click()
        expect(batch.get_by_text(f"本次选择 {len(selected_ids)} 条；每条正确答案均由人单独确认。分组不是根因结论，可以取消勾选拆开修复。", exact=True)).to_be_visible()
        checkpoint(report_path, report, "starting", browser_feedback_selection=True)
        if report.get("rule_update_revision_id"):
            page.get_by_role("button", name="按新版规则起草修改", exact=True).click()
            page.get_by_label("我已核对当前所选规则及全部样本，确认以上正确答案仍适用于当前规则", exact=True).check()

        def capture(route):
            # Persist the real UI-generated identity before forwarding its sole
            # mutation. The request and real response are otherwise unchanged.
            if route.request.method != "POST":
                route.continue_()
                return
            body = route.request.post_data_json
            require(not report.get("operation_id") and body.get("consume_model") is True, "optimization_repair_not_requested")
            require(len(body.get("feedback_ids", [])) == len(selected_ids) and set(body["feedback_ids"]) == selected_ids, "optimization_feedback_selection_mismatch")
            if report.get("rule_update_revision_id"):
                require(body.get("confirmed_rule_revision_id") == report["rule_update_revision_id"], "new_rule_confirmation_missing")
            report.update(stage="admitting", operation_id=body["operation_id"])
            write_report(report_path, report)
            route.continue_()

        page.route("**/suite-revisions/*/optimizations", capture)
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/optimizations")) as started:
            page.get_by_role("button", name="修复并验证", exact=True).click()
        require(started.value.status == 200, "browser_optimization_admission_failed")
        receipt = started.value.json()
        page.reload()
        return receipt

    def publish(self):
        from playwright.sync_api import expect

        page = self.page
        page.goto(self.address)
        expect(page.get_by_role("heading", name="修复结果", exact=True)).to_be_visible()
        page.get_by_role("button", name="发布此修改", exact=True).click()
        expect(page.get_by_role("group", name="确认采用检查版本")).to_contain_text("已有报告不变")
        expect(page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True)).to_have_count(0)
        page.get_by_role("button", name="确认发布", exact=True).click()
        expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()

    def rollback(self):
        from playwright.sync_api import expect

        page = self.page
        page.goto(self.address)
        page.get_by_text("版本记录与受影响审核", exact=True).click()
        page.get_by_role("button", name="查看此版本／回滚", exact=True).last.click()
        page.get_by_role("button", name="回滚到此版本", exact=True).click()
        expect(page.get_by_role("button", name="确认回滚", exact=True)).to_be_disabled()
        page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
        page.get_by_role("button", name="确认回滚", exact=True).click()
        expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()

    def stale(self):
        from playwright.sync_api import expect

        self.page.goto(self.address)
        self.page.get_by_role("button", name="发布此修改", exact=True).click()
        expect(self.page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True)).not_to_be_checked()
        expect(self.page.get_by_role("button", name="确认发布", exact=True)).to_be_disabled()

    def close(self):
        self.browser.close()
        self.playwright.stop()


def local_settings(environment):
    from product.settings import Settings

    database, bucket = data_names(environment)
    ports, urls = service_ports(environment), service_urls(environment)
    return Settings(
        profile="remote-dev",
        git_sha=environment.get("PPAP_CHECKER_LIVE_RUNTIME_SHA", environment["PPAP_BUILD_SHA"]),
        deep_model_max_requests=int(environment["PPAP_DEEP_MODEL_MAX_REQUESTS"]),
        pg_host="127.0.0.1",
        pg_port=ports["postgres"],
        pg_database=database,
        pg_migrator_password=environment["LIVE_PG_MIGRATOR_PASSWORD"],
        s3_endpoint=urls["silo"],
        s3_bucket=bucket,
        s3_access_key=environment["LIVE_S3_ACCESS_KEY"],
        s3_secret_key=environment["LIVE_S3_SECRET_KEY"],
        web_url=urls["web"],
        llm_base_url=environment["CI_LLM_BASE_URL"],
        llm_model=environment["CI_LLM_MODEL"],
        usage_charge_enabled=False,
        feishu_notifications_enabled=False,
        deep_executor_revision="checker-live",
        feishu_app_id="checker-live",
        tenant_model_allowed_base_urls=environment["CI_LLM_BASE_URL"],
        model_credential_encryption_key=environment["LIVE_ENCRYPTION_KEY"],
    )


def run(report_path):
    base_url, model = live_configuration(os.environ)
    scenario = scenario_name(os.environ)
    source_sha = os.environ.get("PPAP_CHECKER_LIVE_BASE_SHA", os.environ["PPAP_BUILD_SHA"])
    report = {
        "accepted": False,
        "stage": "initializing",
        "candidate_sha": os.environ.get("PPAP_CHECKER_LIVE_RUNTIME_SHA", os.environ["PPAP_BUILD_SHA"]),
        "harness_sha": os.environ["PPAP_BUILD_SHA"],
        "runtime_sha": os.environ.get("PPAP_CHECKER_LIVE_RUNTIME_SHA", os.environ["PPAP_BUILD_SHA"]),
        "same_source_runtime": os.environ.get("PPAP_CHECKER_LIVE_RUNTIME_SHA", os.environ["PPAP_BUILD_SHA"]) == os.environ["PPAP_BUILD_SHA"],
        "run_id": os.environ.get("GITHUB_RUN_ID") or "local-" + os.environ["PPAP_CHECKER_LIVE_LOCAL_ID"],
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "scenario": scenario,
        "baseline_source_sha": source_sha,
        "provider_endpoint": base_url,
        "requested_model": model,
        # Production receipts do not retain response.model. Never infer it from
        # the request or pin a provider's alias as an acceptance requirement.
        "provider_returned_model": None,
        "cost": "unknown",
        "automatic_retry": False,
        "journey": {},
    }
    report["limits"] = {"requests": int(os.environ["PPAP_DEEP_MODEL_MAX_REQUESTS"])}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # One runner invocation per attempt. Re-entry may have already incurred
    # charges, even if the prior process never obtained a terminal receipt.
    try:
        with report_path.open("x") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
    except FileExistsError:
        raise TestAlreadyStarted("test_attempt_already_started") from None
    if os.environ.get("PPAP_CHECKER_LIVE_MODE") == "local":
        verify_local_source(os.environ)
        report["runtime_images"] = verify_local_images(os.environ)
        report["runtime_images"] = verify_local_runtime(os.environ, report["runtime_images"])
        report["data_services"] = verify_local_data_services(os.environ)
        probe = controller_evidence(report_path.parent / "controller.json", os.environ)
        report["guest_startup"] = verify_guest_startup(probe)
        write_report(report_path, report)
    # Only initial identity, rule and immutable faulty Suite use trusted stores.
    # All files, real baseline rounds, model consent, feedback, optimization
    # admission and result readback use the authenticated production HTTP routes.
    from product import build_product_store
    from product.storage import S3Objects
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = local_settings(os.environ)
    require(not settings.usage_charge_enabled and not settings.feishu_notifications_enabled, "business_side_effects_must_be_disabled")
    store = build_product_store(settings, objects=S3Objects(settings), role="migrator")
    principal = store.upsert_feishu_identity("checker-live-" + uuid4().hex, "acceptance", "Isolated Checker acceptance")
    session = store.create_session(principal)
    client = Client(session)
    recovery = report_path.parent / "private-client.json"
    recovery.write_text(json.dumps({"session": session}))
    recovery.chmod(0o600)
    browser = None
    suite_key, rules, source_files, clause_id = scenario_source(scenario, source_sha)
    report["scope"] = {"clause_ids": list(rules["checker_binding"]), "complete_u08": False, "original_sha256": U08_ORIGINAL_SHA if scenario == "u08" else None}
    report["baseline_files"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in source_files.items()}
    if scenario == "u08":
        report["full_u08_rules_sha256"] = hashlib.sha256(frozen_u08_files(source_sha)["rules.json"]).hexdigest()
    suites = SuiteStore(settings, store.objects, role="migrator")
    base = suites.create_revision(tenant_id=None, suite_key=suite_key, files=source_files, created_by=None)
    rule_id = str(uuid4())
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES (%s,%s,'Isolated live rule',%s)",
            (rule_id, principal.tenant_id, Jsonb({**rules, "suite_key": suite_key, "suite_revision_id": base.id})),
        )
    try:
        require(client.http.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 404, "fixture_login_exposed_to_guest")
        browser = BrowserJourney(session, clause_id)
        checkpoint(report_path, report, "baseline_and_feedback")
        client.request("PUT", f"/suites/{suite_key}/active", payload={"revision_id": base.id, "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": True})
        samples = {}
        rounds = []
        for ordinal, sample in enumerate(scenario_samples(scenario), 1):
            raw, expected = sample["raw"], sample["expected"]
            uploaded = client.upload(f"sample-{ordinal}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", raw)
            require(uploaded["sha256"] == hashlib.sha256(raw).hexdigest(), "uploaded_source_identity_mismatch")
            draft = client.request("POST", "/drafts", status=201)
            client.request("PUT", f"/drafts/{draft['id']}", payload={"analysis_mode": "standard", "items": [{"file_version_id": uploaded["id"], "role": "target", "rule_version_id": rule_id}]})
            accepted = client.request("POST", f"/drafts/{draft['id']}/accept", payload={"idempotency_key": str(uuid4())}, status=201)
            result = client.poll(f"/rounds/{accepted['id']}", lambda row: row.get("execution") in {"completed", "failed"})
            require(result.get("execution") == "completed", "baseline_not_executed")
            labels = {clause_id: expected}
            if scenario in {"rule-threshold", "rule-wording"}:
                labels["R2"] = "PASS" if sample["value"] >= 0 else "NG"
            baselines = {ident: finding_for(result, ident)["status"] for ident in labels}
            actual = baselines[clause_id]
            if scenario != "u08":
                require(all(value == ("PASS" if sample["value"] >= 0 else "NG") for value in baselines.values()), "baseline_not_executed")
            snapshot = suites.read_snapshot(result["id"], principal.tenant_id).to_dict()
            rounds.append({"round_id": result["id"], "file_version_id": uploaded["id"], "history_sha256": history_fingerprint(result, snapshot)})
            saved_rows = [
                browser.feedback(result, uploaded, label, clause_id=ident, locator=sample["locator"], rationale=f"独立依据 {sample['basis']}：逐项核对条款 {ident} 与原件，正确结果 {label}。")
                for ident, label in labels.items()
            ]
            saved = saved_rows[0]
            require(all(row["case_ordinal"] == ordinal for row in saved_rows), "oracle_ordinal_mismatch")
            samples[saved["id"]] = {
                "ordinal": ordinal,
                "expected": expected,
                "baseline": actual,
                "expected_by_clause": labels,
                "baseline_by_clause": baselines,
                "feedback_ids": [row["id"] for row in saved_rows],
                "oracle_basis": sample["basis"],
                "file_version_id": uploaded["id"],
                "sha256": uploaded["sha256"],
                "feedback": saved,
            }
        if scenario == "u08":
            original = next(iter(samples.values()))
            require(original["baseline"] in {"NG", "UNABLE"} and original["baseline"] != original["expected"], "u08_original_not_counterexample")
        current_rules, current_rule_id = rules, rule_id
        if scenario in {"rule-threshold", "rule-wording"}:
            updated = publish_rule_change(client, rules, rule_id, scenario)
            current_rules, current_rule_id = updated["body"], updated["id"]
            active = client.request("GET", f"/suites/{suite_key}/active")
            require(active["current_revision_id"] == base.id and active["verification_status"] == "stale" and active["rule_update"]["changed"], "rule_upload_did_not_mark_stale")
            with store._conn() as conn:
                require(
                    conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='checker_optimization' AND tenant_id=%s", (principal.tenant_id,)).fetchone()[0] == 0, "rule_upload_started_model_work"
                )
            report["rule_update_revision_id"] = updated["body"]["suite_revision_id"]
            checkpoint(report_path, report, "rule_updated", rule_upload_no_model=True, old_version_still_adopted=True, new_rule_stale=True)
        report.update(stage="configuring", base_revision_id=base.id, samples={ident: {key: value for key, value in row.items() if key != "feedback"} for ident, row in samples.items()})
        checkpoint(report_path, report, "configuring", browser_feedback=True)
        private = report_path.parent / "private-oracle.json"
        private.write_text(
            json.dumps(
                {
                    "hidden_case_ids": [ident for row in samples.values() if row["ordinal"] % 4 == 0 for ident in row["feedback_ids"]],
                    "hidden_source_sha256": [row["sha256"] for row in samples.values() if row["ordinal"] % 4 == 0],
                }
            )
        )
        private.chmod(0o600)
        transfer_private_oracle(private, os.environ)
        receipt = browser.start(rounds[0], report, report_path)
        checkpoint(report_path, report, "waiting", browser_model_consent=True)
        path = f"/checker-optimizations/{report['operation_id']}"

        def record(current):
            retain_receipt(report, current)
            write_report(report_path, report)

        record(receipt)
        receipt = client.poll(path, lambda row: row.get("state") not in {"queued", "running"}, on_read=record)
        validate_outcome(receipt, samples, scenario=scenario)
        require(client.request("GET", path) == receipt, "optimization_readback_changed")
        checkpoint(report_path, report, "candidate_readback", browser_optimize=True)
        detail = client.request("GET", "/suite-revisions/" + receipt["candidate_revision_id"])
        changed = any(detail["files"].get(name) != raw.decode() for name, raw in source_files.items() if name in {"checker.py", "resources.py"})
        report["code_changed"] = changed
        report["candidate_checker_sha256"] = hashlib.sha256(detail["files"]["checker.py"].encode()).hexdigest()
        from audit.checker_core import checker_digest

        program = {name: content.encode() for name, content in detail["files"].items() if name in {"checker.py", "resources.py"}}
        require(receipt["report"]["validation"]["program_digest"] == checker_digest(program), "validated_candidate_identity_mismatch")
        require(client.request("GET", f"/suites/{suite_key}/active")["current_revision_id"] == base.id, "candidate_published_without_request")
        browser.new_session(store.create_session(principal))
        browser.read_optimization(receipt, samples)
        require(client.request("GET", path) == receipt, "reconnected_receipt_changed")
        checkpoint(report_path, report, "publishing", reconnect_readback=True)
        browser.publish()
        require(client.request("GET", f"/suites/{suite_key}/active")["current_revision_id"] == receipt["candidate_revision_id"], "publication_not_applied")
        checkpoint(report_path, report, "reauditing", browser_publish=True)
        newer = client.request("POST", f"/rounds/{rounds[0]['round_id']}/resubmit", payload={"idempotency_key": str(uuid4())}, status=201)
        newer = client.poll("/rounds/" + newer["id"], lambda row: row.get("execution") in {"completed", "failed"})
        require(
            newer.get("execution") == "completed" and newer["predecessor_id"] == rounds[0]["round_id"] and finding_for(newer, clause_id)["status"] == next(iter(samples.values()))["expected"],
            "explicit_reaudit_failed",
        )
        newer_snapshot = suites.read_snapshot(newer["id"], principal.tenant_id)
        require(newer_snapshot.targets[0].suite_revision_id == receipt["candidate_revision_id"], "reaudit_used_wrong_suite")
        browser.read_round(newer)
        rounds.append({"round_id": newer["id"], "history_sha256": history_fingerprint(newer, newer_snapshot.to_dict())})
        report["reaudit_round_id"] = newer["id"]
        checkpoint(report_path, report, "rolling_back", api_explicit_reaudit_and_browser_readback=True)
        if scenario != "synthetic":
            publish_rule_change(client, current_rules, current_rule_id, "rule-wording")
            stale_head = client.request("GET", f"/suites/{suite_key}/active")
            require(
                stale_head["current_revision_id"] == receipt["candidate_revision_id"] and stale_head["verification_status"] == "stale" and stale_head["rule_update"]["changed"],
                "new_rule_did_not_mark_candidate_stale",
            )
            checkpoint(report_path, report, "new_rule_stale", new_rule_stale=True)
        browser.rollback()
        active = client.request("GET", f"/suites/{suite_key}/active")
        require(active["current_revision_id"] == base.id, "rollback_not_applied")
        checkpoint(report_path, report, "checking_stale", browser_rollback=True)
        first = next(iter(samples.values()))["feedback"]
        client.request(
            "PUT",
            f"/audit-rounds/{first['round_id']}/checker-feedback/{first['id']}",
            payload={
                "expected_revision": first["revision"],
                "expected_status": first["expected_status"],
                "rationale": first["rationale"] + " 再次人工确认。",
                "evidence": first["evidence"],
            },
        )
        stale = client.request("GET", "/suite-revisions/" + receipt["candidate_revision_id"])
        require(stale["validation"]["status"] == "stale", "changed_oracle_not_stale")
        rejected = client.http.put(
            f"/suites/{suite_key}/active", json={"revision_id": receipt["candidate_revision_id"], "expected_revision": active["revision"], "operation_id": str(uuid4()), "force_unverified": False}
        )
        require(rejected.status_code == 409, "stale_publication_not_rejected")
        browser.stale()
        checkpoint(report_path, report, "checking_history", stale_blocked=True)
        for old in rounds:
            current = client.request("GET", "/rounds/" + old["round_id"])
            require(history_fingerprint(current, suites.read_snapshot(old["round_id"], principal.tenant_id).to_dict()) == old["history_sha256"], "historical_result_changed")
        report["final_candidate_validation_status"] = stale["validation"]["status"]
        report["final_active_revision_id"] = base.id
        checkpoint(report_path, report, "verifying_frozen_proof", history_unchanged=True)
        with store._conn() as conn:
            from psycopg.rows import dict_row

            with conn.cursor(row_factory=dict_row) as cursor:
                operation = cursor.execute("SELECT * FROM product.checker_optimizations WHERE operation_id=%s AND tenant_id=%s", (report["operation_id"], principal.tenant_id)).fetchone()
            frozen = operation["frozen"]
            require({sample["id"] for sample in frozen["samples"]} == feedback_ids(samples), "frozen_sample_set_mismatch")
            by_feedback = {ident: row for row in samples.values() for ident in row["feedback_ids"]}
            for sample in frozen["samples"]:
                expected = by_feedback[sample["id"]]
                target = next(item for item in sample["files"] if item["role"] == "target")
                require(target["file_version_id"] == expected["file_version_id"] and target["sha256"] == expected["sha256"], "frozen_source_identity_mismatch")
                require(sample["expected_status"] == expected["expected_by_clause"][sample["rule_clause_id"]] and sample["case_ordinal"] == expected["ordinal"], "frozen_oracle_identity_mismatch")
            row = conn.execute("SELECT count(*), max(attempt) FROM engine.jobs WHERE kind='checker_optimization' AND payload->>'operation_id'=%s", (report["operation_id"],)).fetchone()
            require(row[0] == 1, "optimization_operation_duplicated")
            report["technical_attempts"] = row[1]
            require(conn.execute("SELECT count(*) FROM product.usage_events WHERE tenant_id=%s", (principal.tenant_id,)).fetchone()[0] == 0, "formal_audit_points_charged")
        validate_bindings(
            receipt,
            operation_id=report["operation_id"],
            base_revision_id=report.get("rule_update_revision_id", base.id),
            detail=detail,
            frozen=frozen,
            input_digests=frozen_input_digests(os.environ, store, operation),
        )
        checkpoint(report_path, report, "verifying_isolation", frozen_proof_bound=True)
        probe = controller_evidence(report_path.parent / "controller.json", os.environ)
        require(
            probe.get("adaptations") == 1
            and set(probe.get("holdout_sources", [])) == {row["sha256"] for row in samples.values() if row["ordinal"] % 4 == 0}
            and probe.get("requested_models") == [model]
            and probe.get("provider_requests") == receipt["usage"]["model_requests"]
            and 0 < probe["provider_requests"] <= report["limits"]["requests"]
            and probe.get("violation") is False
            and probe.get("guest_cannot_read_evidence") is True,
            "holdout_boundary_not_proven",
        )
        report.update(accepted=True, stage="complete", isolation_probe=probe)
        report["journey"]["isolation_and_usage"] = True
    finally:
        # Do not revoke the configuration while a timed-out operation may still
        # own it. The entire disposable stack is stopped by workflow cleanup.
        if report.get("operation_id"):
            try:
                final_readback(report, client.request("GET", "/checker-optimizations/" + report["operation_id"]))
                report["final_readback"] = "read_only_completed"
            except Exception:
                report["final_readback"] = "unavailable_known_usage_preserved"
        snapshot_controller(report_path, report)
        report["recovery"] = "Ephemeral stack; after cleanup, reconcile unknown consumption with provider billing. Never redispatch automatically."
        write_report(report_path, report)
        if browser is not None:
            browser.close()
        client.http.close()


def final_snapshot(path):
    """Read-only reconciliation before disposable containers stop, even on timeout."""
    if not path.is_file():
        return
    report = json.loads(path.read_text())
    recovery = path.parent / "private-client.json"
    if not report.get("operation_id") or not recovery.is_file():
        return
    client = Client(json.loads(recovery.read_text())["session"])
    client.http.timeout = httpx.Timeout(5)
    try:
        final_readback(report, client.request("GET", "/checker-optimizations/" + report["operation_id"]))
        report["final_readback"] = "read_only_completed"
    except Exception:
        report["final_readback"] = "unavailable_known_usage_preserved"
    finally:
        report["recovery"] = "Ephemeral stack; after cleanup, reconcile unknown consumption with provider billing. Never redispatch automatically."
        snapshot_controller(path, report)
        write_report(path, report)
        client.http.close()


def main():
    if sys.argv[1:] == ["--plan"]:
        print(json.dumps(describe_scenario(os.environ), ensure_ascii=False, indent=2))
        return 0
    path = Path(os.environ["PPAP_CHECKER_LIVE_OUTPUT"]) / "report.json"
    try:
        require(sys.argv[1:] in ([], ["--snapshot"]), "invalid_arguments")
        if sys.argv[1:] == ["--snapshot"]:
            final_snapshot(path)
        else:
            if path.exists():
                raise TestAlreadyStarted("test_attempt_already_started")
            run(path)
        # The final read can revoke an earlier proof. Both the live process and
        # the workflow's later read-only snapshot must propagate that verdict.
        report = json.loads(path.read_text()) if path.is_file() else {}
        require(report.get("accepted") is True, report.get("gate_failure") or "final_acceptance_not_proven")
        print("Checker live acceptance passed; see the bounded report.")
        return 0
    except TestAlreadyStarted:
        print("Test attempt already started; original evidence preserved. No operation was submitted.")
        return 1
    except Exception as exc:
        report = json.loads(path.read_text()) if path.exists() else {"accepted": False}
        report.update(accepted=False, gate_failure=str(exc) if isinstance(exc, AcceptanceFailure) else type(exc).__name__, automatic_retry=False, cost="unknown")
        write_report(path, report)
        print("Checker live test did not pass; inspect report.json. A fresh manual test does not reconcile unknown prior consumption.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
