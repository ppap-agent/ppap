"""Build identity and recovery without a live build or deployment."""

import json
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD = runpy.run_path(str(ROOT / "deploy/tke/build-current.py"))
SHA = "a" * 40
COMPONENTS = BUILD["COMPONENTS"]
IMAGES = {c: BUILD["PREFIXES"]["yunxiao"] + c + "@sha256:" + "b" * 64 for c in COMPONENTS}
CONFIGS = {c: "sha256:" + "c" * 64 for c in COMPONENTS}


def source_run(sha=SHA, status="SUCCESS"):
    return {
        "status": status,
        "pipelineConfigId": 1,
        "sources": [{"data": {"repo": BUILD["CODEUP"], "commit": json.dumps([{"commitId": sha}])}}],
        "stages": [{"stageInfo": {"jobs": [{"id": 9}]}}],
    }


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

    operation = BUILD["yunxiao"]
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
            return {"pipelineConfig": {"pipelineConfigId": 1, "flow": (ROOT / "deploy/tke/yunxiao.yaml").read_text()}} if path == BUILD["FLOW"] else []
        if method == "POST":
            assert isinstance(body["params"], str)
            assert json.loads(body["params"]) == {"runningBranchs": {BUILD["CODEUP"]: BUILD["SOURCE_BRANCH"]}, "envs": {"PPAP_SOURCE_SHA": SHA}}
            raise RuntimeError("response lost")

    operation = BUILD["yunxiao"]
    with patch.dict(operation.__globals__, {"cloud": cloud, "source_branch": lambda *args: BUILD["SOURCE_BRANCH"]}):
        with pytest.raises(RuntimeError, match="unknown"):
            operation(SHA, tmp_path, None)
    assert calls.count("POST") == 1
    assert (tmp_path / "dispatch-intent.json").exists()


@pytest.mark.parametrize("fault", [None, "source", "platform", "manifest", "config"])
@pytest.mark.parametrize("legacy", [False, True])
def test_registry_proves_bytes_platform_and_source_before_pinning(fault, legacy):
    operation = BUILD["registry_images"]
    image = {"architecture": "arm64" if fault == "platform" else "amd64", "os": "linux", "config": {"Env": ["PPAP_BUILD_SHA=" + ("0" * 40 if fault == "source" else SHA)]}}
    if not legacy:
        image["config"]["Labels"] = {"org.opencontainers.image.revision": SHA, "org.opencontainers.image.source": "https://github.com/ppap-agent/ppap"}
    config = json.dumps(image).encode()
    config_digest = BUILD["digest"](config)
    raw = json.dumps({"config": {"digest": config_digest, "size": len(config)}}).encode()
    manifest_digest = BUILD["digest"](raw)
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
    operation = BUILD["build"]
    source = ROOT / "deploy/tke/current.yaml"
    original = source.read_text()
    config = tmp_path / "current.yaml"
    config.write_text(original)
    calls = []

    def checked(sha, refs):
        assert sha == SHA and refs == IMAGES
        calls.append("verified")
        return refs, CONFIGS

    def run(args, **kwargs):
        assert args[0] == "git", "build must not query CI"
        return SHA.encode() if args[1] == "rev-parse" else b""

    with patch.dict(operation.__globals__, {"HERE": tmp_path, "run": run, "yunxiao": lambda *args: IMAGES, "registry_images": checked}):
        operation(SHA, "yunxiao", tmp_path, 2)
    text = config.read_text()
    changed = [(a, b) for a, b in zip(original.splitlines(), text.splitlines(), strict=True) if a != b]
    assert len(changed) == 5 and calls == ["verified"]
    assert "PPAP_BUILD_SHA: " + SHA in text
    assert all(ref in text for ref in IMAGES.values())


def test_failed_registry_verification_leaves_configuration_unchanged(tmp_path):
    operation = BUILD["build"]
    config = tmp_path / "current.yaml"
    config.write_text("sentinel")

    def invalid(*args):
        raise ValueError("source identity mismatch")

    with patch.dict(operation.__globals__, {"HERE": tmp_path, "source_commit": lambda sha: None, "run": lambda args: b"", "yunxiao": lambda *args: IMAGES, "registry_images": invalid}):
        with pytest.raises(ValueError):
            operation(SHA, "yunxiao", tmp_path, 2)
    assert config.read_text() == "sentinel"


@pytest.mark.parametrize("candidate,actual", [("short", SHA), (SHA, "0" * 40)])
def test_invalid_source_blocks_build_before_cloud_actions(tmp_path, candidate, actual):
    operation = BUILD["build"]

    def forbidden(*args):
        pytest.fail("invalid source must not reach the cloud")

    with patch.dict(operation.__globals__, {"run": lambda *args, **kwargs: actual.encode(), "yunxiao": forbidden}):
        with pytest.raises(ValueError, match="source"):
            operation(candidate, "yunxiao", tmp_path, None)


@pytest.mark.parametrize("provider", ["yunxiao", "unregistry"])
def test_existing_source_images_skip_all_flow_history_and_build_calls(tmp_path, provider):
    operation = BUILD["build"]
    (tmp_path / "current.yaml").write_text((ROOT / "deploy/tke/current.yaml").read_text())
    checks = []

    def registry(sha, refs):
        checks.append((sha, refs))
        return IMAGES, CONFIGS

    def forbidden(*args, **kwargs):
        pytest.fail("verified images must skip cloud/build calls")

    with patch.dict(
        operation.__globals__,
        {
            "HERE": tmp_path,
            "source_commit": lambda sha: None,
            "run": lambda *args: b"",
            "registry_images": registry,
            "yunxiao": forbidden,
            "cloud": forbidden,
        },
    ):
        operation(SHA, provider, tmp_path, None)
    assert len(checks) == 1
    assert checks[0][1] == {c: BUILD["PREFIXES"][provider] + c + ":" + SHA for c in COMPONENTS}


def test_unknown_registry_result_cannot_dispatch_a_new_build(tmp_path):
    operation = BUILD["build"]
    (tmp_path / "current.yaml").write_text("unchanged")

    def registry(*args):
        raise RuntimeError("registry transport failed")

    def forbidden(*args):
        pytest.fail("unknown is not a missing image")

    with patch.dict(
        operation.__globals__,
        {
            "HERE": tmp_path,
            "source_commit": lambda sha: None,
            "run": lambda *args: b"",
            "registry_images": registry,
            "yunxiao": forbidden,
        },
    ):
        with pytest.raises(RuntimeError, match="transport"):
            operation(SHA, "yunxiao", tmp_path, None)
    assert (tmp_path / "current.yaml").read_text() == "unchanged"


def test_backup_reuses_verified_images_without_build_or_duplicate_metadata_reads(tmp_path):
    operation = BUILD["build"]
    (tmp_path / "current.yaml").write_text((ROOT / "deploy/tke/current.yaml").read_text())
    checks = []
    images = {c: BUILD["PREFIXES"]["unregistry"] + c + "@sha256:" + "b" * 64 for c in COMPONENTS}

    def registry(*args):
        checks.append(args)
        return images, CONFIGS

    with patch.dict(operation.__globals__, {"HERE": tmp_path, "source_commit": lambda sha: None, "run": lambda *args: b"", "registry_images": registry}):
        operation(SHA, "unregistry", tmp_path, None)
    assert len(checks) == 1
    assert not (tmp_path / "source.tar").exists() and not (tmp_path / "source").exists()


def test_queued_build_waits_for_source_metadata_without_dispatching(tmp_path):
    operation = BUILD["yunxiao"]
    observations = iter([{"status": "QUEUED", "sources": []}, source_run()])
    waits = []

    def cloud(method, path, *args, **kwargs):
        assert method == "GET"
        if path.endswith("/runs/2"):
            return next(observations)
        if path.endswith("/steps"):
            return [{"buildId": 10}]
        return {"logs": "\n".join(["PPAP_RELEASE_SOURCE " + SHA, *(f"PPAP_RELEASE_IMAGE {c} {IMAGES[c]}" for c in COMPONENTS)]), "more": False}

    with patch.dict(operation.__globals__, {"cloud": cloud}), patch.object(BUILD["time"], "sleep", side_effect=waits.append):
        assert operation(SHA, tmp_path, 2) == IMAGES
    assert waits == [15]


def test_tcr_verification_uses_authenticated_token_without_anonymous_permission_probe():
    import base64

    operation = BUILD["registry_images"]
    image = {
        "architecture": "amd64",
        "os": "linux",
        "config": {
            "Env": ["PPAP_BUILD_SHA=" + SHA],
            "Labels": {"org.opencontainers.image.revision": SHA, "org.opencontainers.image.source": "https://github.com/ppap-agent/ppap"},
        },
    }
    config = json.dumps(image).encode()
    config_digest = BUILD["digest"](config)
    manifest = json.dumps({"config": {"digest": config_digest, "size": len(config)}}).encode()
    manifest_digest = BUILD["digest"](manifest)
    credential = base64.b64encode(b"probe-user:probe-password").decode()
    secret = {
        "data": {
            ".dockerconfigjson": base64.b64encode(
                json.dumps(
                    {
                        "auths": {
                            "icoastline.tencentcloudcr.com": {"auth": credential},
                        }
                    }
                ).encode()
            ).decode()
        }
    }
    tokens = []

    def get(url, headers):
        if "/token?" in url:
            assert headers.get("Authorization") == "Basic " + credential
            tokens.append(url)
            return 200, {}, b'{"token":"probe-bearer"}'
        if not headers.get("Authorization"):
            return 401, {"WWW-Authenticate": 'Bearer realm="https://icoastline.tencentcloudcr.com/token",service="tcr"'}, b""
        assert headers["Authorization"] == "Bearer probe-bearer"
        if "/blobs/" in url:
            return 200, {}, config
        return 200, {"Docker-Content-Digest": manifest_digest}, manifest

    with patch.dict(
        operation.__globals__,
        {
            "tcr_pull_secret": lambda: "pull-secret",
            "kube": lambda *args: json.dumps(secret).encode(),
            "registry_get": get,
        },
    ):
        pins, _ = operation(SHA, {c: BUILD["PREFIXES"]["yunxiao"] + c + ":" + SHA for c in COMPONENTS})
    assert len(tokens) == len(COMPONENTS)
    assert all(ref.endswith("@" + manifest_digest) for ref in pins.values())


def test_confirmed_missing_images_build_once_then_verify_before_pinning(tmp_path):
    operation = BUILD["build"]
    (tmp_path / "current.yaml").write_text((ROOT / "deploy/tke/current.yaml").read_text())
    events = []

    def registry(sha, refs):
        events.append("registry")
        if len(events) == 1:
            raise BUILD["RegistryImageMissing"]("confirmed missing")
        assert refs == IMAGES
        return IMAGES, CONFIGS

    def flow(sha, attempt, run_id):
        events.append("build")
        return IMAGES

    with patch.dict(
        operation.__globals__,
        {
            "HERE": tmp_path,
            "source_commit": lambda sha: None,
            "run": lambda *args: b"",
            "registry_images": registry,
            "yunxiao": flow,
        },
    ):
        operation(SHA, "yunxiao", tmp_path, None)
    assert events == ["registry", "build", "registry"]
    assert all(ref in (tmp_path / "current.yaml").read_text() for ref in IMAGES.values())


@pytest.mark.parametrize("branch_head", [SHA, "b" * 40])
def test_run_uses_requested_commit_when_fixed_branch_moves(tmp_path, branch_head):
    operation = BUILD["yunxiao"]
    calls = []

    def cloud(method, path, body=None, query=None):
        calls.append((method, path))
        if path == BUILD["FLOW"]:
            assert method == "GET"
            return {"pipelineConfig": {"pipelineConfigId": 1, "flow": (ROOT / "deploy/tke/yunxiao.yaml").read_text()}}
        if method == "POST":
            assert json.loads(body["params"])["envs"]["PPAP_SOURCE_SHA"] == SHA
            return 2
        if path.endswith("/runs/2"):
            return source_run(branch_head)
        if path.endswith("/steps"):
            return [{"buildId": 10}]
        return {"logs": "\n".join(["PPAP_RELEASE_SOURCE " + SHA, *(f"PPAP_RELEASE_IMAGE {c} {IMAGES[c]}" for c in COMPONENTS)]), "more": False}

    with patch.dict(operation.__globals__, {"cloud": cloud, "source_branch": lambda *args: BUILD["SOURCE_BRANCH"]}):
        assert operation(SHA, tmp_path, None) == IMAGES
    assert sum(method == "POST" for method, _ in calls) == 1
    assert not any(method == "PUT" or path == BUILD["FLOW"] or path.endswith("/runs") and method == "GET" for method, path in calls)


def test_other_run_receipt_cannot_supply_selected_source_images(tmp_path):
    operation = BUILD["yunxiao"]

    def cloud(method, path, *args, **kwargs):
        assert method == "GET"
        if path.endswith("/runs/2"):
            return source_run()
        if path.endswith("/steps"):
            return [{"buildId": 10}]
        return {"logs": "\n".join(["PPAP_RELEASE_SOURCE " + "b" * 40, *(f"PPAP_RELEASE_IMAGE {c} {IMAGES[c]}" for c in COMPONENTS)]), "more": False}

    with patch.dict(operation.__globals__, {"cloud": cloud}):
        with pytest.raises(ValueError, match="four fixed-source images"):
            operation(SHA, tmp_path, 2)


def test_completed_build_preserves_a_concurrently_changed_manifest(tmp_path):
    operation = BUILD["build"]
    config = tmp_path / "current.yaml"
    config.write_text((ROOT / "deploy/tke/current.yaml").read_text())

    def registry(*args):
        config.write_text("newer operator selection")
        return IMAGES, CONFIGS

    with patch.dict(operation.__globals__, {"HERE": tmp_path, "source_commit": lambda sha: None, "registry_images": registry}):
        with pytest.raises(ValueError, match="changed during build"):
            operation(SHA, "yunxiao", tmp_path, None)
    assert config.read_text() == "newer operator selection"


def test_fixed_source_branch_supports_advances_and_older_commits(tmp_path):
    import subprocess

    operation = BUILD["source_branch"]
    repo, remote = tmp_path / "repo", tmp_path / "remote.git"
    repo.mkdir()

    def git(*args, cwd=repo):
        return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()

    git("init", "--bare", str(remote))
    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    commits = []
    for value in ["first", "second"]:
        (repo / "source").write_text(value)
        git("add", "source")
        git("commit", "-m", value)
        commits.append(git("rev-parse", "HEAD"))
    with patch.dict(operation.__globals__, {"ROOT": repo, "CODEUP": str(remote)}):
        for sha in [*commits, commits[0]]:
            assert operation(sha, tmp_path) == BUILD["SOURCE_BRANCH"]
    refs = git("for-each-ref", "--format=%(refname)", cwd=remote).splitlines()
    assert refs == ["refs/heads/" + BUILD["SOURCE_BRANCH"]]
    assert git("rev-parse", refs[0], cwd=remote) == commits[1]
    assert not (tmp_path / "askpass.py").exists()
