"""Release safety at external-effect boundaries, without a live build/deploy."""

import json
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
RELEASE = runpy.run_path(str(ROOT / "deploy/tke/release-current.py"))
SHA = "a" * 40
COMPONENTS = RELEASE["COMPONENTS"]
IMAGES = {c: RELEASE["PREFIXES"]["yunxiao"] + c + "@sha256:" + "b" * 64 for c in COMPONENTS}
CONFIGS = {c: "sha256:" + "c" * 64 for c in COMPONENTS}


def source_run(sha=SHA, status="SUCCESS"):
    return {
        "status": status,
        "pipelineConfigId": 1,
        "sources": [{"data": {"repo": RELEASE["CODEUP"], "commit": json.dumps([{"commitId": sha}])}}],
        "stages": [{"stageInfo": {"jobs": [{"id": 9}]}}],
    }


@pytest.mark.parametrize("bad", ["sha", "repo", "extra-source"])
def test_flow_source_requires_exact_selected_repository_and_commit(bad):
    observation = source_run()
    assert RELEASE["run_source"](observation, SHA)
    if bad == "sha":
        observation = source_run("0" * 40)
    elif bad == "repo":
        observation["sources"][0]["data"]["repo"] = "https://foreign.invalid/repo"
    else:
        observation["sources"].append(observation["sources"][0])
    assert not RELEASE["run_source"](observation, SHA)


@pytest.mark.parametrize("status", ["FAIL", "SUCCESS"])
def test_resume_never_dispatches_and_failed_build_never_resolves_images(tmp_path, status):
    calls = []

    def cloud(method, path, *args, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path.endswith("/runs/2"):
            return source_run(status=status)
        if path.endswith("/steps"):
            return [{"buildId": 10}]
        return {"logs": "\n".join(["PPAP_RELEASE_SOURCE " + SHA, *(f"PPAP_RELEASE_IMAGE {c} {IMAGES[c]}" for c in COMPONENTS)]), "more": False}

    operation = RELEASE["yunxiao"]
    with patch.dict(operation.__globals__, {"cloud": cloud}):
        if status == "FAIL":
            with pytest.raises(ValueError, match="build failed"):
                operation(SHA, tmp_path, 2)
        else:
            assert operation(SHA, tmp_path, 2) == IMAGES
    assert all(method == "GET" for method, _ in calls)
    assert not (tmp_path / "dispatch-intent.json").exists()


def test_unknown_dispatch_is_recorded_once_and_never_retried(tmp_path):
    calls = []

    def cloud(method, path, body=None, query=None):
        calls.append(method)
        if method == "GET":
            return {"pipelineConfig": {"pipelineConfigId": 1, "flow": (ROOT / "deploy/tke/yunxiao.yaml").read_text().replace("__SOURCE_SHA__", SHA)}} if path == RELEASE["FLOW"] else []
        if method == "POST":
            assert isinstance(body["params"], str)
            assert json.loads(body["params"])["runningBranchs"][RELEASE["CODEUP"]].endswith(SHA)
            raise RuntimeError("response lost")

    operation = RELEASE["yunxiao"]
    with patch.dict(operation.__globals__, {"cloud": cloud, "source_branch": lambda *args: "codex/ppap-current-release-" + SHA}):
        with pytest.raises(RuntimeError, match="unknown"):
            operation(SHA, tmp_path, None)
    assert calls.count("POST") == 1
    assert (tmp_path / "dispatch-intent.json").exists()


def test_existing_matching_build_is_reused_without_reconfiguring(tmp_path):
    operation = RELEASE["yunxiao"]
    posts = []

    def cloud(method, path, *args, **kwargs):
        posts.append(method)
        if path.endswith("/runs"):
            return [{"pipelineRunId": 2, "status": "SUCCESS"}]
        if path.endswith("/runs/2"):
            return source_run()
        if path.endswith("/steps"):
            return [{"buildId": 10}]
        return {"logs": "\n".join(["PPAP_RELEASE_SOURCE " + SHA, *(f"PPAP_RELEASE_IMAGE {c} {IMAGES[c]}" for c in COMPONENTS)]), "more": False}

    with patch.dict(operation.__globals__, {"cloud": cloud}):
        assert operation(SHA, tmp_path, None) == IMAGES
    assert set(posts) == {"GET"}


@pytest.mark.parametrize("fault", [None, "source", "platform", "manifest", "config"])
@pytest.mark.parametrize("legacy", [False, True])
def test_registry_proves_bytes_platform_and_source_before_pinning(fault, legacy):
    operation = RELEASE["registry_images"]
    image = {"architecture": "arm64" if fault == "platform" else "amd64", "os": "linux", "config": {"Env": ["PPAP_BUILD_SHA=" + ("0" * 40 if fault == "source" else SHA)]}}
    if not legacy:
        image["config"]["Labels"] = {"org.opencontainers.image.revision": SHA, "org.opencontainers.image.source": "https://github.com/ppap-agent/ppap"}
    config = json.dumps(image).encode()
    config_digest = RELEASE["digest"](config)
    raw = json.dumps({"config": {"digest": config_digest, "size": len(config)}}).encode()
    manifest_digest = RELEASE["digest"](raw)
    images = {c: "unregistry.xixifusi.online/ppap-" + c + (":" + SHA if legacy else "@" + manifest_digest) for c in COMPONENTS}

    def get(url, headers):
        if "/blobs/" in url:
            return 200, {}, config + (b"corrupt" if fault == "config" else b"")
        return 200, {"Docker-Content-Digest": "wrong" if fault == "manifest" else manifest_digest}, raw

    with patch.dict(operation.__globals__, {"registry_get": get}):
        if fault:
            with pytest.raises(ValueError):
                operation(SHA, images)
        else:
            pins, configs = operation(SHA, images)
            assert configs == dict.fromkeys(COMPONENTS, config_digest)
            assert all(ref.endswith("@" + manifest_digest) for ref in pins.values())


def test_build_updates_only_source_and_four_pins_after_verification(tmp_path):
    operation = RELEASE["build"]
    source = ROOT / "deploy/tke/current.yaml"
    original = source.read_text()
    config = tmp_path / "current.yaml"
    config.write_text(original)
    calls = []

    def checked(sha, refs):
        assert sha == SHA and refs == IMAGES
        calls.append("verified")
        return refs, CONFIGS

    with patch.dict(operation.__globals__, {"HERE": tmp_path, "tested_source": lambda sha: None, "run": lambda args: b"", "yunxiao": lambda *args: IMAGES, "registry_images": checked}):
        operation(SHA, "yunxiao", tmp_path, 2)
    text = config.read_text()
    changed = [(a, b) for a, b in zip(original.splitlines(), text.splitlines(), strict=True) if a != b]
    assert len(changed) == 5 and calls == ["verified"]
    assert "PPAP_BUILD_SHA: " + SHA in text
    assert all(ref in text for ref in IMAGES.values())


def test_failed_registry_verification_leaves_configuration_unchanged(tmp_path):
    operation = RELEASE["build"]
    config = tmp_path / "current.yaml"
    config.write_text("sentinel")

    def invalid(*args):
        raise ValueError("source identity mismatch")

    with patch.dict(operation.__globals__, {"HERE": tmp_path, "tested_source": lambda sha: None, "run": lambda args: b"", "yunxiao": lambda *args: IMAGES, "registry_images": invalid}):
        with pytest.raises(ValueError):
            operation(SHA, "yunxiao", tmp_path, 2)
    assert config.read_text() == "sentinel"


@pytest.mark.parametrize("fault", [None, "preload", "schema", "active-job", "apply", "noop"])
@pytest.mark.parametrize("full", [False, True])
def test_deploy_owns_gates_fixed_render_and_readback(tmp_path, fault, full):
    operation = RELEASE["deploy"]
    rendered = b"canonical fixed render\n"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_start.sql").write_text("SQL")
    config = {"PPAP_BUILD_SHA": SHA, "PPAP_WEB_URL": "https://ppap.example"}
    objects = [{"kind": "ConfigMap", "metadata": {"name": "ppap-current"}, "data": config}]
    events = []
    digests = []

    def kube(*args, data=None):
        if args[0] == "create":
            assert data == rendered
            return json.dumps(objects).encode()
        if args[0] == "get":
            return json.dumps({"data": {"0001_start.sql": "SQL"}}).encode()
        if args[0] == "apply":
            digests.append(RELEASE["digest"](Path(args[-1]).read_bytes()))
            if "--dry-run=server" not in args:
                events.append("apply")
                assert (tmp_path / "apply-intent").exists()
                if fault == "apply":
                    raise RuntimeError("unknown partial apply")
            return b"configmap/ppap-current unchanged (server dry run)\n" if fault == "noop" else b"configmap/ppap-current configured (server dry run)\n"
        return b""

    def preload(*args):
        events.append("both-node-gate")
        if fault == "preload":
            raise RuntimeError("node failed")

    def database(**kwargs):
        events.append("full-db" if kwargs.get("full") else "fresh-db")
        return {"migrations": [] if fault == "schema" else ["0001_start.sql"], "active_jobs": [["audit", "leased", 1]] if fault == "active-job" else []}

    preloader = {**RELEASE["PRELOAD"], "preload": preload}
    bindings = {
        "ROOT": tmp_path,
        "run": lambda *args: rendered,
        "native_objects": lambda raw: json.loads(raw),
        "require_images": lambda docs: IMAGES,
        "tested_source": lambda sha: None,
        "full_check_reason": lambda *args: "runtime changed" if full else None,
        "registry_images": lambda *args: (IMAGES, CONFIGS),
        "kube": kube,
        "PRELOAD": preloader,
        "database": database,
        "public": lambda *args: b"legacy",
        "stable_resources": lambda: {},
        "verify": lambda path: events.append("live-readback"),
    }
    with patch.dict(operation.__globals__, bindings):
        if fault not in {None, "noop"} and (fault != "preload" or full):
            with pytest.raises((ValueError, RuntimeError)):
                operation(tmp_path)
        else:
            operation(tmp_path)
    assert len(set(digests)) <= 1
    if fault in {"schema", "active-job"} or fault == "preload" and full:
        assert "apply" not in events and not (tmp_path / "apply-intent").exists()
    elif fault == "apply":
        assert events.count("apply") == 1 and "live-readback" not in events
    elif fault == "noop":
        assert "apply" not in events and not (tmp_path / "apply-intent").exists()
        assert events[-1] == "live-readback"
        assert len(digests) == (2 if full else 1)
    else:
        assert events == (["fresh-db", "both-node-gate", "full-db", "apply", "live-readback"] if full else ["fresh-db", "apply", "live-readback"])
        assert len(digests) == (3 if full else 2)


@pytest.mark.parametrize("fault", [None, "pod-image", "public-sha", "legacy", "history", "resources", "schema"])
@pytest.mark.parametrize("full", [False, True])
def test_live_readback_cannot_report_success_for_wrong_target_or_state(tmp_path, fault, full):
    operation = RELEASE["verify"]
    owners = RELEASE["PRELOAD"]["OWNERS"]
    target = {
        "sha": SHA,
        "images": IMAGES,
        "configs": CONFIGS,
        "base": "https://ppap.example",
        "legacy_html": RELEASE["digest"](b"legacy"),
        "database": {"migrations": []},
        "resources": {},
        "full_check": full,
    }
    (tmp_path / "target.json").write_text(json.dumps(target))

    def kube(*args, **kwargs):
        if args[:2] == ("get", "deployment"):
            owner = args[2]
            return json.dumps(
                {
                    "metadata": {"generation": 1},
                    "spec": {"replicas": 1, "selector": {"matchLabels": {"app": owner}}},
                    "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1, "updatedReplicas": 1},
                }
            ).encode()
        if args[:2] == ("get", "pods"):
            owner = args[3].split("=", 1)[1]
            names = [c for c in COMPONENTS if owners[c] == owner]
            return json.dumps(
                {
                    "items": [
                        {
                            "metadata": {},
                            "spec": {"containers": [{"name": c, "image": IMAGES[c]} for c in names]},
                            "status": {"containerStatuses": [{"name": c, "imageID": "wrong" if fault == "pod-image" else CONFIGS[c], "ready": True, "restartCount": 0} for c in names]},
                        }
                    ]
                }
            ).encode()
        return b'{"worker_heartbeat":true,"deep_socket":true}'

    def public(base, path, **kwargs):
        if path == "/":
            return b"changed" if fault == "legacy" else b"legacy"
        return json.dumps({"git_sha": "0" * 40 if fault == "public-sha" else SHA}).encode()

    bindings = {
        "kube": kube,
        "public": public,
        "database": lambda **kwargs: {"migrations": ["unexpected"]} if fault == "schema" else {"migrations": [], "changed": True} if fault == "history" else {"migrations": []},
        "stable_resources": lambda: {"changed": True} if fault == "resources" else {},
    }
    rejected = fault is not None and (full or fault not in {"history", "resources"})
    with patch.dict(operation.__globals__, bindings):
        if rejected:
            with pytest.raises(ValueError):
                operation(tmp_path)
        else:
            operation(tmp_path)
    assert (tmp_path / "verified.json").exists() == (not rejected)


def test_exact_tests_failure_blocks_source_before_cloud_actions():
    operation = RELEASE["tested_source"]

    def run(args, **kwargs):
        if args[0] == "git":
            return SHA.encode()
        return json.dumps(
            {
                "workflow_runs": [
                    {"id": 1, "head_sha": SHA, "conclusion": "success", "event": "push", "head_repository": {"full_name": "ppap-agent/ppap"}},
                    {"id": 2, "head_sha": SHA, "conclusion": "failure", "event": "workflow_dispatch", "head_repository": {"full_name": "ppap-agent/ppap"}},
                ]
            }
        ).encode()

    with patch.dict(operation.__globals__, {"run": run}):
        with pytest.raises(ValueError, match="successful latest Tests"):
            operation(SHA)


@pytest.mark.parametrize(
    "output", [b"job.batch/ppap-current-init-db configured (server dry run)\n", b"deployment.apps/edition-proxy created (server dry run)\n", b""], ids=["completed-job", "proxy", "empty"]
)
def test_server_dry_run_rejects_non_application_mutations(output, tmp_path):
    operation = RELEASE["dry_run"]
    with patch.dict(operation.__globals__, {"kube": lambda *args: output}):
        with pytest.raises(ValueError):
            operation(tmp_path / "render", [{"kind": "Job"}])


@pytest.mark.parametrize(
    "change", ["application", "pin-record", "deploy-doc", "dependency", "runtime", "migration", "unknown", "config", "deployment", "registry", "unhealthy", "forced", "same-source-image"]
)
def test_full_checks_only_when_runtime_risk_is_present(change):
    operation = RELEASE["full_check_reason"]
    live_sha = "d" * 40
    config = {"PPAP_BUILD_SHA": SHA, "setting": "value"}
    spec = {"replicas": 1, "template": {"spec": {"containers": [{"name": "api", "image": IMAGES["api"]}]}}}
    docs = [{"kind": "ConfigMap", "metadata": {"name": "ppap-current"}, "data": config}, {"kind": "Deployment", "metadata": {"name": "ppap-current-api"}, "spec": spec}]
    previous = json.loads(json.dumps(spec))
    if change == "deployment":
        previous["template"]["spec"]["serviceAccountName"] = "another-account"
    if change == "registry":
        previous["template"]["spec"]["containers"][0]["image"] = "unregistry.xixifusi.online/ppap-api@sha256:" + "f" * 64
    if change == "same-source-image":
        live_sha = SHA
        previous["template"]["spec"]["containers"][0]["image"] = IMAGES["api"].replace("b" * 64, "e" * 64)
    deployment = {
        "metadata": {"generation": 1, "annotations": {"kubectl.kubernetes.io/last-applied-configuration": json.dumps({"spec": previous})}},
        "spec": spec,
        "status": {"observedGeneration": 1, "readyReplicas": 0 if change == "unhealthy" else 1, "updatedReplicas": 1, "replicas": 1},
    }
    files = {"dependency": "uv.lock", "runtime": "deploy/app/entrypoint.sh", "migration": "migrations/0034_change.sql", "pin-record": "deploy/tke/current.yaml", "deploy-doc": "deploy/tke/README.md"}

    def kube(*args):
        if args[1] == "configmap":
            return json.dumps({"data": {"PPAP_BUILD_SHA": live_sha, "setting": "other" if change == "config" else "value"}}).encode()
        return json.dumps(deployment).encode()

    def run(*args):
        if change == "unknown":
            raise RuntimeError("source not available")
        return (files.get(change, "services/api/src/ppap_api/routes.py") + "\n").encode()

    with patch.dict(operation.__globals__, {"kube": kube, "run": run}):
        reason = operation(docs, SHA, change == "forced")
    assert (reason is None) == (change in {"application", "pin-record", "deploy-doc"})


def test_existing_success_outside_first_flow_page_is_not_rebuilt(tmp_path):
    operation = RELEASE["yunxiao"]
    pages = []

    def cloud(method, path, body=None, query=None):
        assert method == "GET"
        if path.endswith("/runs"):
            pages.append(query["page"])
            return [{"pipelineRunId": 3 if query["page"] == 1 else 2, "status": "SUCCESS"}]
        if path.endswith("/runs/3"):
            return source_run("0" * 40)
        if path.endswith("/runs/2"):
            return source_run()
        if path.endswith("/steps"):
            return [{"buildId": 10}]
        return {"logs": "\n".join(["PPAP_RELEASE_SOURCE " + SHA, *(f"PPAP_RELEASE_IMAGE {c} {IMAGES[c]}" for c in COMPONENTS)]), "more": False}

    with patch.dict(operation.__globals__, {"cloud": cloud}):
        assert operation(SHA, tmp_path, None) == IMAGES
    assert pages == [1, 2]
    assert not (tmp_path / "dispatch-intent.json").exists()


def test_backup_reuses_verified_images_without_build_or_duplicate_metadata_reads(tmp_path):
    operation = RELEASE["build"]
    (tmp_path / "current.yaml").write_text((ROOT / "deploy/tke/current.yaml").read_text())
    checks = []
    images = {c: RELEASE["PREFIXES"]["unregistry"] + c + "@sha256:" + "b" * 64 for c in COMPONENTS}

    def registry(*args):
        checks.append(args)
        return images, CONFIGS

    with patch.dict(operation.__globals__, {"HERE": tmp_path, "tested_source": lambda sha: None, "run": lambda *args: b"", "registry_images": registry}):
        operation(SHA, "unregistry", tmp_path, None)
    assert len(checks) == 1
    assert not (tmp_path / "source.tar").exists() and not (tmp_path / "source").exists()


def test_routine_database_read_does_not_scan_history(monkeypatch, capsys):
    from unittest.mock import MagicMock

    import ppap_core.db
    import ppap_core.settings

    queries = []
    connection = MagicMock()
    connection.__enter__.return_value = connection

    def execute(query):
        queries.append(query)
        result = MagicMock()
        if "product.schema_migrations" in query:
            result.fetchall.return_value = [("0001_start.sql",)]
        elif "engine.jobs" in query:
            result.fetchall.return_value = []
        else:
            assert "string_agg" not in query and "md5" not in query, "routine deployment must not scan business tables"
        return result

    connection.execute.side_effect = execute
    monkeypatch.setattr(ppap_core.db, "connect", lambda *args: connection)
    monkeypatch.setattr(ppap_core.settings, "load_settings", lambda: None)
    monkeypatch.setattr("sys.argv", ["-", "routine"])
    exec(RELEASE["DB_SNAPSHOT"], {})
    state = json.loads(capsys.readouterr().out)
    assert state == {"migrations": ["0001_start.sql"], "active_jobs": []}
    assert len(queries) == 4


def test_queued_build_waits_for_source_metadata_without_dispatching(tmp_path):
    operation = RELEASE["yunxiao"]
    observations = iter([{"status": "QUEUED", "sources": []}, source_run()])
    waits = []

    def cloud(method, path, *args, **kwargs):
        assert method == "GET"
        if path.endswith("/runs/2"):
            return next(observations)
        if path.endswith("/steps"):
            return [{"buildId": 10}]
        return {"logs": "\n".join(["PPAP_RELEASE_SOURCE " + SHA, *(f"PPAP_RELEASE_IMAGE {c} {IMAGES[c]}" for c in COMPONENTS)]), "more": False}

    with patch.dict(operation.__globals__, {"cloud": cloud}), patch.object(RELEASE["time"], "sleep", side_effect=waits.append):
        assert operation(SHA, tmp_path, 2) == IMAGES
    assert waits == [15]
