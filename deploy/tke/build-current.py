"""Build exact-source PPAP images and update their pins; never deploy Kubernetes objects."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPONENTS = ("api", "worker", "web", "deep")
PREFIXES = {"yunxiao": "icoastline.tencentcloudcr.com/ppap-feishu/current-", "unregistry": "unregistry.xixifusi.online/ppap-"}
CODEUP = "https://codeup.aliyun.com/icoastline/integration/4.0/ppap-web.git"
FLOW = "/flow/organizations/60d54c9a70ab8549fd3e10db/pipelines/5339663"
NAMESPACE = "ppap-test"


def run(args: list[str], *, data: bytes | None = None, timeout: int = 1800) -> bytes:
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} {args[1]} failed (exit {result.returncode})")
    return result.stdout


def kube(*args: str, data: bytes | None = None, timeout: int = 1800) -> bytes:
    return run(["kubectl", "-n", NAMESPACE, *args], data=data, timeout=timeout)


class ReleaseError(ValueError):
    """An operator-safe blocker, without remote output or credentials."""


class RegistryImageMissing(ReleaseError):
    """The registry confirmed that a selected image is absent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def source_commit(sha: str) -> None:
    require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "source must be a full Git SHA")
    require(run(["git", "rev-parse", sha + "^{commit}"], timeout=30).decode().strip() == sha, "source commit is unavailable")


def cloud(method: str, path: str, body=None, query=None):
    token = os.environ.get("ALIYUN_DEV_OPS_TOKEN", "")
    require(bool(token), "set ALIYUN_DEV_OPS_TOKEN in the operator process")
    url = "https://openapi-rdc.aliyuncs.com/oapi/v1" + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"x-yunxiao-token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except (OSError, ValueError):
        raise RuntimeError(f"Yunxiao {method} failed; read back before any repeated write") from None


def source_branch(sha: str, attempt: Path) -> str:
    branch = "codex/ppap-current-release-" + sha
    askpass = attempt / "askpass.py"
    askpass.write_text("#!/usr/bin/env python3\nimport os,sys\nprint('oauth2' if 'Username' in sys.argv[1] else os.environ['ALIYUN_DEV_OPS_TOKEN'])\n")
    askpass.chmod(0o700)
    env = {**os.environ, "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0", "GIT_TRACE": "0", "GIT_CURL_VERBOSE": "0"}

    def git(*args):
        result = subprocess.run(["git", "-c", "credential.helper=", *args], env=env, cwd=ROOT, capture_output=True, timeout=900)
        if result.returncode:
            raise RuntimeError("Codeup operation failed; inspect remote ref before retrying")
        return result.stdout.decode()

    def observed():
        rows = git("ls-remote", CODEUP, "refs/heads/" + branch).split()
        return rows[0] if rows else None

    try:
        actual = observed()
        require(actual in {None, sha}, "Codeup release branch already points to another commit; do not force")
        if actual is None:
            try:
                git("push", CODEUP, sha + ":refs/heads/" + branch)
            except (RuntimeError, subprocess.TimeoutExpired):
                require(observed() == sha, "source push outcome not confirmed; do not repeat blindly")
        require(observed() == sha, "Codeup source readback does not match")
    finally:
        askpass.unlink(missing_ok=True)
    return branch


def run_source(observation: dict, sha: str) -> bool:
    sources = observation.get("sources", [])
    if len(sources) != 1 or sources[0].get("data", {}).get("repo") != CODEUP:
        return False
    # Flow reports the selected head first, followed by the changed commit history.
    commits = json.loads(sources[0]["data"].get("commit") or "[]")
    return bool(commits) and commits[0].get("commitId") == sha


def yunxiao(sha: str, attempt: Path, resume_run: int | None) -> dict[str, str]:
    expected_config_id = None
    if resume_run is None:
        # Completed builds are reused by verified registry identity in build().
        # Query only active runs, not every successful run in the pipeline history.
        seen = set()
        active = False
        for page in range(1, 101):
            rows = cloud("GET", FLOW + "/runs", query={"page": page, "perPage": 30, "status": "RUNNING"})
            if not rows:
                break
            ids = {row.get("pipelineRunId", row.get("id")) for row in rows}
            require(not ids & seen, "Flow pagination repeated; use --run-id instead of rebuilding blindly")
            seen.update(ids)
            with ThreadPoolExecutor(max_workers=4) as pool:
                observations = list(pool.map(lambda rid: cloud("GET", FLOW + f"/runs/{rid}"), ids))
            for rid, previous in zip(ids, observations, strict=True):
                if run_source(previous, sha) and previous["status"] not in {"FAIL", "FAILED", "CANCELED", "CANCELLED"}:
                    return yunxiao(sha, attempt, int(rid))
                active |= previous["status"] not in {"SUCCESS", "FAIL", "FAILED", "CANCELED", "CANCELLED"}
        else:
            raise ReleaseError("Flow active-run search limit reached; select --run-id")
        require(not active, "release pipeline has an active run; do not reconfigure it")
        branch = source_branch(sha, attempt)
        config = (HERE / "yunxiao.yaml").read_text().replace("__SOURCE_SHA__", sha)
        readback = cloud("GET", FLOW)
        if readback["pipelineConfig"]["flow"].strip() != config.strip():
            try:
                cloud("PUT", FLOW, {"name": "PPAP current release TCR", "content": config})
            except RuntimeError:
                pass  # Reconcile the owning resource; never repeat the PUT blindly.
            readback = cloud("GET", FLOW)
        require(readback["pipelineConfig"]["flow"].strip() == config.strip(), "pipeline definition readback differs; do not dispatch")
        expected_config_id = readback["pipelineConfig"]["pipelineConfigId"]
        # No automatic POST retry. Preserve the intent even if the response is lost.
        (attempt / "dispatch-intent.json").write_text(json.dumps({"sha": sha, "branch": branch}))
        try:
            rid = cloud("POST", FLOW + "/runs", {"params": json.dumps({"runningBranchs": {CODEUP: branch}})})
        except RuntimeError:
            raise RuntimeError("dispatch result unknown; inspect pipeline runs and use --run-id; never dispatch again blindly") from None
        require(isinstance(rid, (int, str)) and str(rid).isdigit(), "unexpected run response; inspect pipeline runs before retry")
        resume_run = int(rid)
        (attempt / "run-id").write_text(str(resume_run))
    print(f"Yunxiao pipeline 5339663 run {resume_run}; source {sha}", flush=True)
    deadline = time.monotonic() + 7200
    while True:
        observation = cloud("GET", FLOW + f"/runs/{resume_run}")
        status = observation["status"]
        source_known = any(source.get("data", {}).get("commit") for source in observation.get("sources", []))
        if source_known or status == "SUCCESS":
            require(run_source(observation, sha), "Flow run source differs from selected source")
        if expected_config_id is not None:
            require(observation["pipelineConfigId"] == expected_config_id, "Flow used another pipeline configuration; do not deploy")
        if status == "SUCCESS":
            break
        require(status not in {"FAIL", "FAILED", "CANCELED", "CANCELLED"}, "Yunxiao build failed; deployment is forbidden")
        require(time.monotonic() < deadline, "build wait timed out; resume the same --run-id")
        time.sleep(15)
    refs = {}
    source_seen = False
    jobs = [job for stage in observation["stages"] for job in stage["stageInfo"]["jobs"]]
    require(len(jobs) == 1, "unexpected build graph")
    jid = jobs[0]["id"]
    steps = cloud("GET", FLOW + f"/pipelineRuns/{resume_run}/jobs/{jid}/steps")
    build_id = next(step["buildId"] for step in steps if "buildId" in step)
    offset = 0
    for _ in range(1000):
        page = cloud("GET", FLOW + f"/pipelineRuns/{resume_run}/jobs/{jid}/step/log", query={"stepIndex": 5, "offset": offset, "limit": 10000, "buildId": build_id})
        # Never print raw build logs: they may contain temporary signed URLs.
        refs.update(re.findall(r"PPAP_RELEASE_IMAGE (api|worker|web|deep) (\S+@sha256:[0-9a-f]{64})", page["logs"]))
        source_seen |= "PPAP_RELEASE_SOURCE " + sha in page["logs"]
        if not page.get("more"):
            break
        require(page["last"] > offset, "Flow log pagination did not advance")
        offset = page["last"]
    require(source_seen and set(refs) == set(COMPONENTS), "Flow did not report all four fixed-source images")
    require(all(re.fullmatch(re.escape(PREFIXES["yunxiao"] + c) + r"@sha256:[0-9a-f]{64}", refs[c]) for c in COMPONENTS), "Flow reported an unexpected image repository")
    return refs


class RegistryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(urllib.parse.urlsplit(newurl).scheme == "https", "registry redirect must use HTTPS")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            redirected.remove_header("Authorization")
        return redirected


def registry_get(url: str, headers: dict):
    try:
        with urllib.request.build_opener(RegistryRedirect()).open(urllib.request.Request(url, headers=headers), timeout=90) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, b""
    except OSError:
        raise RuntimeError("registry transport failed; image identity is unverified") from None


def tcr_pull_secret() -> str:
    """Reuse the legacy Web pull binding without copying registry credentials."""
    deployment = json.loads(kube("get", "deployment", "web-base", "-o", "json"))
    refs = deployment["spec"]["template"]["spec"].get("imagePullSecrets", [])
    for ref in refs:
        secret = json.loads(kube("get", "secret", ref["name"], "-o", "json"))
        if secret.get("type") != "kubernetes.io/dockerconfigjson":
            continue
        registry = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
        if "icoastline.tencentcloudcr.com" in registry.get("auths", {}):
            return ref["name"]
    raise ValueError("web-base must bind an existing pull Secret for the owned TCR host")


def registry_images(sha: str, images: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve legacy tags and verify all four manifests/configs before any rollout."""
    configs, pins = {}, {}
    auth = None
    if next(iter(images.values())).startswith(PREFIXES["yunxiao"]):
        name = tcr_pull_secret()
        secret = json.loads(kube("get", "secret", name, "-o", "json"))
        cred = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))["auths"]["icoastline.tencentcloudcr.com"]
        auth = cred.get("auth") or base64.b64encode((cred["username"] + ":" + cred["password"]).encode()).decode()

    def check(item: tuple[str, str]) -> tuple[str, str, str]:
        component, ref = item
        repository = ref.split("@", 1)[0].rsplit(":", 1)[0]
        host, path = repository.split("/", 1)
        selected = ref.split("@", 1)[1] if "@" in ref else ref.rsplit(":", 1)[1]
        url = f"https://{host}/v2/{path}/manifests/{selected}"
        headers = {"Accept": "application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json"}
        code, response_headers, raw = registry_get(url, headers)
        if auth:
            require(code == 401, "TCR registry did not provide an authentication challenge")
            challenge = dict(re.findall(r'(realm|service)="([^"]+)"', response_headers.get("WWW-Authenticate", "")))
            realm = challenge.get("realm", "")
            require(urllib.parse.urlsplit(realm).scheme == "https" and urllib.parse.urlsplit(realm).hostname == host, "untrusted registry authentication realm")
            tokenurl = realm + "?" + urllib.parse.urlencode({"service": challenge["service"], "scope": "repository:" + path + ":pull"})
            tokenurl += "&" + urllib.parse.urlencode({"account": base64.b64decode(auth).decode().split(":", 1)[0]})
            token_code, _, body = registry_get(tokenurl, {"Authorization": "Basic " + auth, "Cache-Control": "no-cache"})
            require(token_code == 200, "TCR authentication failed")
            bearer = json.loads(body)
            headers["Authorization"] = "Bearer " + (bearer.get("token") or bearer["access_token"])
            code, response_headers, raw = registry_get(url, headers)
        if code == 404:
            raise RegistryImageMissing("registry image is absent: " + component)
        require(code == 200, "registry manifest is unavailable")
        manifest_digest = digest(raw)
        require("@" not in ref or manifest_digest == selected, "manifest digest differs from selected image")
        require(response_headers.get("Docker-Content-Digest") == manifest_digest, "registry manifest identity mismatch")
        manifest = json.loads(raw)
        require("config" in manifest, "release must be a single linux/amd64 manifest, not an index")
        config = manifest["config"]
        code, _, raw = registry_get(f"https://{host}/v2/{path}/blobs/{config['digest']}", headers)
        require(code == 200 and len(raw) == config["size"] and digest(raw) == config["digest"], "config blob identity mismatch")
        image = json.loads(raw)
        require(image["architecture"] == "amd64" and image["os"] == "linux", "release platform differs from linux/amd64")
        cfg = image["config"]
        labels = cfg.get("Labels", {})
        legacy = ref == PREFIXES["unregistry"] + component + ":" + sha
        require("PPAP_BUILD_SHA=" + sha in cfg.get("Env", []), "image does not contain the selected baked source identity")
        require(
            labels.get("org.opencontainers.image.revision", sha if legacy else None) == sha
            and labels.get("org.opencontainers.image.source", "https://github.com/ppap-agent/ppap" if legacy else None) == "https://github.com/ppap-agent/ppap",
            "image source labels differ from the selected source",
        )
        return component, repository + "@" + manifest_digest, config["digest"]

    missing = False
    with ThreadPoolExecutor(max_workers=len(COMPONENTS)) as pool:
        futures = [pool.submit(check, item) for item in images.items()]
        for future in futures:
            try:
                component, pin, config_digest = future.result()
                pins[component] = pin
                configs[component] = config_digest
            except RegistryImageMissing:
                missing = True
    if missing:
        raise RegistryImageMissing("one or more selected images are absent")
    return pins, configs


def build(sha: str, provider: str, attempt: Path, run_id: int | None) -> None:
    source_commit(sha)
    require(not run(["git", "status", "--porcelain", "--", "deploy/tke/current.yaml"]).strip(), "current.yaml has local edits; review/commit them before updating pins")
    prefix = PREFIXES[provider]
    pins = None
    images = {c: prefix + c + ":" + sha for c in COMPONENTS}
    if run_id is None:
        try:
            pins, _ = registry_images(sha, images)
        except RegistryImageMissing:
            pass  # Only a confirmed missing image permits a new build.
        else:
            print("Reusing four verified images; build skipped.", flush=True)
    if provider == "yunxiao":
        if pins is None:
            images = yunxiao(sha, attempt, run_id)
    else:
        require(run_id is None, "--run-id applies only to Yunxiao")
        if pins is None:
            # Run this fallback on the unregistry build host with its existing Docker login.
            # Only tracked files from the selected commit enter the build context.
            archive = attempt / "source.tar"
            archive.write_bytes(run(["git", "archive", sha]))
            source = attempt / "source"
            source.mkdir()
            with tarfile.open(archive) as stream:
                stream.extractall(source, filter="data")
            archive.unlink()
            env = {**os.environ, "PPAP_BUILD_SHA": sha, "PPAP_IMAGE_PREFIX": "localhost:5001/ppap-"}
            result = subprocess.run(
                ["docker", "buildx", "bake", "release", "--set", "*.platform=linux/amd64", "--provenance=false", "--push"],
                cwd=source,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=7200,
            )
            require(result.returncode == 0, "unregistry build failed; do not deploy")
    if pins is None:
        pins, _ = registry_images(sha, images)
    config = HERE / "current.yaml"
    text = config.read_text()
    text, count = re.subn(r"(?m)^(  PPAP_BUILD_SHA: ).+$", lambda m: m[1] + sha, text)
    require(count == 1, "expected one application source pin")
    for component, ref in pins.items():
        text, count = re.subn(r"(?m)^([ ]+image: )(?:\S+/)?(?:ppap-|current-)?" + component + r"(?:@|:).+$", lambda m: m[1] + ref, text)
        require(count == 1, "expected one image pin for " + component)
    config.write_text(text)
    print("Verified all four images. Review and commit deploy/tke/current.yaml; deploy with the native commands in deploy/tke/README.md.", flush=True)


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="full application source SHA")
    parser.add_argument("--provider", choices=tuple(PREFIXES), default="yunxiao")
    parser.add_argument("--run-id", type=int, help="resume an existing Yunxiao run")
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ.setdefault("KUBECONFIG", str(Path.home() / "Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig"))
    with (HERE / ".build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempt = Path(tempfile.mkdtemp(prefix="ppap-build-"))
        print("Build/recovery files: " + str(attempt), flush=True)
        build(args.source, args.provider, attempt, args.run_id)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(type(error).__name__ + ": build incomplete; inspect the build recovery directory before retrying.", file=sys.stderr)
        sys.exit(1)
