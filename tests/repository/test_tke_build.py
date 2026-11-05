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


@pytest.mark.parametrize("bad", ["sha", "repo", "extra-source"])
def test_flow_source_requires_exact_selected_repository_and_commit(bad):
    observation = source_run()
    assert BUILD["run_source"](observation, SHA)
    if bad == "sha":
        observation = source_run("0" * 40)
    elif bad == "repo":
        observation["sources"][0]["data"]["repo"] = "https://foreign.invalid/repo"
    else:
        observation["sources"].append(observation["sources"][0])
    assert not BUILD["run_source"](observation, SHA)


def test_flow_commit_history_does_not_replace_the_selected_head():
    observation = source_run()
    data = observation["sources"][0]["data"]
    data["commit"] = json.dumps([{"commitId": SHA}, {"commitId": "0" * 40}])
    assert BUILD["run_source"](observation, SHA)
    assert not BUILD["run_source"](observation, "0" * 40)
    data["commit"] = "[]"
    assert not BUILD["run_source"](observation, SHA)


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
            return {"pipelineConfig": {"pipelineConfigId": 1, "flow": (ROOT / "deploy/tke/yunxiao.yaml").read_text().replace("__SOURCE_SHA__", SHA)}} if path == BUILD["FLOW"] else []
        if method == "POST":
            assert isinstance(body["params"], str)
            assert json.loads(body["params"])["runningBranchs"][BUILD["CODEUP"]].endswith(SHA)
            raise RuntimeError("response lost")

    operation = BUILD["yunxiao"]
    with patch.dict(operation.__globals__, {"cloud": cloud, "source_branch": lambda *args: "codex/ppap-current-release-" + SHA}):
        with pytest.raises(RuntimeError, match="unknown"):
            operation(SHA, tmp_path, None)
    assert calls.count("POST") == 1
    assert (tmp_path / "dispatch-intent.json").exists()


def test_existing_matching_build_is_reused_without_reconfiguring(tmp_path):
    operation = BUILD["yunxiao"]
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


def test_existing_success_outside_first_flow_page_is_not_rebuilt(tmp_path):
    operation = BUILD["yunxiao"]
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
