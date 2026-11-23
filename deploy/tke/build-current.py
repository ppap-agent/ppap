"""Build exact-source PPAP images and update their pins; never deploy Kubernetes objects."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# API and Worker run the same "app" image; the deployment selects the process (PPAP_SERVICE).
COMPONENTS = ("app", "web", "deep")
PREFIX = "icoastline.tencentcloudcr.com/ppap-feishu/current-"
CODEUP = "https://codeup.aliyun.com/icoastline/integration/4.0/ppap-web.git"
FLOW = "/flow/organizations/60d54c9a70ab8549fd3e10db/pipelines/5339663"
NAMESPACE = "ppap-test"
SOURCE_BRANCH = "codex/ppap-current-release"
BASES = ("system", "deps", "deep-base")
BASE_RECORD = "deploy/tke/base-images.json"


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
    # A read changes nothing, so a dropped one is simply read again; a write is never repeated blindly.
    for attempt in range(5 if method == "GET" else 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except (OSError, ValueError):
            if method == "GET" and attempt < 4:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Yunxiao {method} failed; read back before any repeated write") from None


def source_branch(sha: str, attempt: Path) -> str:
    branch = SOURCE_BRANCH
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
        if actual not in {None, sha}:
            git("fetch", "--no-tags", CODEUP, "refs/heads/" + branch)
            if git("merge-base", sha, "FETCH_HEAD").strip() == sha:
                return branch  # An older requested commit is already reachable.
        if actual != sha:
            try:
                git("push", CODEUP, sha + ":refs/heads/" + branch)
            except (RuntimeError, subprocess.TimeoutExpired):
                require(observed() == sha, "source push outcome not confirmed; do not repeat blindly")
    finally:
        askpass.unlink(missing_ok=True)
    return branch


def base_inputs(sha: str) -> dict[str, str]:
    """{level: inputs digest} of the selected commit's base levels (deploy/tke/bases.py)."""
    spec = importlib.util.spec_from_file_location("bases", HERE / "bases.py")
    require(spec is not None and spec.loader is not None, "bases.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.level_inputs(lambda path: run(["git", "show", sha + ":" + path], timeout=60))


def stale_bases(sha: str) -> list[str]:
    recorded = recorded_bases(sha)
    return [level for level, inputs in base_inputs(sha).items() if recorded.get(level, {}).get("inputs") != inputs]


def recorded_bases(sha: str) -> dict:
    try:
        return json.loads(run(["git", "show", sha + ":" + BASE_RECORD], timeout=60))
    except (RuntimeError, ValueError):
        return {}


def yunxiao(sha: str, attempt: Path, resume_run: int | None, mode: str = "release") -> dict:
    if resume_run is None:
        # A run in progress may be building this very source; its source is not readable from the run, so a person
        # decides: follow it with --run-id, or wait for it to finish. Never start a second build beside it.
        branch = source_branch(sha, attempt)
        # No automatic POST retry. Preserve the intent even if the response is lost.
        (attempt / "dispatch-intent.json").write_text(json.dumps({"sha": sha, "branch": branch}))
        try:
            envs = {"PPAP_SOURCE_SHA": sha, **({"PPAP_BUILD_MODE": mode} if mode != "release" else {})}
            rid = cloud("POST", FLOW + "/runs", {"params": json.dumps({"runningBranchs": {CODEUP: branch}, "envs": envs})})
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
        sources = observation.get("sources", [])
        if sources:
            require(len(sources) == 1 and sources[0].get("data", {}).get("repo") == CODEUP, "Flow selected another repository")
        if status == "SUCCESS":
            break
        require(status not in {"FAIL", "FAILED", "CANCELED", "CANCELLED"}, "Yunxiao build failed; deployment is forbidden")
        require(time.monotonic() < deadline, "build wait timed out; resume the same --run-id")
        time.sleep(15)
    refs: dict = {}
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
        if mode == "bases":
            for level, inputs, image in re.findall(r"PPAP_BASE_IMAGE (system|deps|deep-base) ([0-9a-f]{64}) (\S+@sha256:[0-9a-f]{64})", page["logs"]):
                refs[level] = {"inputs": inputs, "image": image}
            source_seen |= "PPAP_BASE_SOURCE " + sha in page["logs"]
        else:
            refs.update(re.findall(r"PPAP_RELEASE_IMAGE (app|web|deep) (\S+@sha256:[0-9a-f]{64})", page["logs"]))
            source_seen |= "PPAP_RELEASE_SOURCE " + sha in page["logs"]
        if not page.get("more"):
            break
        require(page["last"] > offset, "Flow log pagination did not advance")
        offset = page["last"]
    if mode == "bases":
        # Yunxiao truncates a long step log (a slow push floods it with progress lines), so a level may be missing
        # here; build_bases then reads it from the registry by its inputs tag.
        require(source_seen and set(refs) <= set(BASES), "Flow did not report its base source")
        require(all(re.fullmatch(re.escape(PREFIX + name) + r"@sha256:[0-9a-f]{64}", refs[name]["image"]) for name in refs), "Flow reported an unexpected base repository")
        return refs
    require(source_seen and set(refs) == set(COMPONENTS), "Flow did not report all fixed-source images")
    require(all(re.fullmatch(re.escape(PREFIX + c) + r"@sha256:[0-9a-f]{64}", refs[c]) for c in COMPONENTS), "Flow reported an unexpected image repository")
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


def registry_bearer(host: str, path: str, auth: str, challenge_header: str) -> str:
    challenge = dict(re.findall(r'(realm|service)="([^"]+)"', challenge_header))
    realm = challenge.get("realm", "")
    require(urllib.parse.urlsplit(realm).scheme == "https" and urllib.parse.urlsplit(realm).hostname == host, "untrusted registry authentication realm")
    tokenurl = realm + "?" + urllib.parse.urlencode({"service": challenge["service"], "scope": "repository:" + path + ":pull"})
    tokenurl += "&" + urllib.parse.urlencode({"account": base64.b64decode(auth).decode().split(":", 1)[0]})
    token_code, _, body = registry_get(tokenurl, {"Authorization": "Basic " + auth, "Cache-Control": "no-cache"})
    require(token_code == 200, "TCR authentication failed")
    bearer = json.loads(body)
    return bearer.get("token") or bearer["access_token"]


def tcr_auth() -> str:
    secret = json.loads(kube("get", "secret", tcr_pull_secret(), "-o", "json"))
    cred = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))["auths"]["icoastline.tencentcloudcr.com"]
    return cred.get("auth") or base64.b64encode((cred["username"] + ":" + cred["password"]).encode()).decode()


def tagged_base(level: str, inputs: str) -> str:
    """The pinned image the Flow pushed for this level, read from the registry by its inputs tag."""
    repository = PREFIX + level
    host, path = repository.split("/", 1)
    url = f"https://{host}/v2/{path}/manifests/{inputs}"
    headers = {"Accept": "application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json"}
    code, response_headers, _ = registry_get(url, headers)
    require(code == 401, "TCR registry did not provide an authentication challenge")
    headers["Authorization"] = "Bearer " + registry_bearer(host, path, tcr_auth(), response_headers.get("WWW-Authenticate", ""))
    code, response_headers, raw = registry_get(url, headers)
    require(code == 200, "base image for these inputs is absent: " + level)
    manifest_digest = digest(raw)
    require(response_headers.get("Docker-Content-Digest") == manifest_digest, "registry manifest identity mismatch")
    return repository + "@" + manifest_digest


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
    """Resolve tags and verify all manifests/configs before any rollout."""
    configs, pins = {}, {}
    auth = tcr_auth()

    def check(item: tuple[str, str]) -> tuple[str, str, str]:
        component, ref = item
        repository = ref.split("@", 1)[0].rsplit(":", 1)[0]
        host, path = repository.split("/", 1)
        selected = ref.split("@", 1)[1] if "@" in ref else ref.rsplit(":", 1)[1]
        url = f"https://{host}/v2/{path}/manifests/{selected}"
        headers = {"Accept": "application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json"}
        code, response_headers, raw = registry_get(url, headers)
        require(code == 401, "TCR registry did not provide an authentication challenge")
        headers["Authorization"] = "Bearer " + registry_bearer(host, path, auth, response_headers.get("WWW-Authenticate", ""))
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
        labels = cfg.get("Labels") or {}
        require("PPAP_BUILD_SHA=" + sha in cfg.get("Env", []), "image does not contain the selected baked source identity")
        require(
            labels.get("org.opencontainers.image.revision") == sha and labels.get("org.opencontainers.image.source") == "https://github.com/ppap-agent/ppap",
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


def build(sha: str, attempt: Path, run_id: int | None) -> None:
    source_commit(sha)
    config = HERE / "current.yaml"
    original = config.read_text()
    pins = None
    images = {c: PREFIX + c + ":" + sha for c in COMPONENTS}
    if run_id is None:
        try:
            pins, _ = registry_images(sha, images)
        except RegistryImageMissing:
            pass  # Only a confirmed missing image permits a new build.
        else:
            print("Reusing verified images; build skipped.", flush=True)
    if pins is None:
        # Releases stack on the recorded dependency images; they must come from these exact inputs.
        require(
            not stale_bases(sha),
            "base images are stale for this source: run build-current.py --bases, commit deploy/tke/base-images.json, then release that commit",
        )
        images = yunxiao(sha, attempt, run_id)
    if pins is None:
        pins, _ = registry_images(sha, images)
    text = original
    text, count = re.subn(r"(?m)^(  PPAP_BUILD_SHA: ).+$", lambda m: m[1] + sha, text)
    require(count == 1, "expected one application source pin")
    for component, ref in pins.items():
        text, count = re.subn(r"(?m)^([ ]+image: )(?:\S+/)?(?:ppap-|current-)?" + component + r"(?:@|:).+$", lambda m: m[1] + ref, text)
        # The app image runs the API, the Worker, the legacy ticket sync CronJob and the Suite seed Job; all move together.
        require(count == (4 if component == "app" else 1), "unexpected image pin count for " + component)
    with (HERE / ".build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(config.read_text() == original, "current.yaml changed during build; image pins are not applied")
        config.write_text(text)
    print("Verified all images. Review and commit deploy/tke/current.yaml; deploy with the native commands in deploy/tke/README.md.", flush=True)


def build_bases(sha: str, attempt: Path, run_id: int | None) -> None:
    """Build the selected commit's stale base levels on Yunxiao and record all three for later releases."""
    source_commit(sha)
    if run_id is None and not stale_bases(sha):
        print("Recorded base images already match these inputs; build skipped.", flush=True)
        return
    refs = yunxiao(sha, attempt, run_id, mode="bases")
    inputs = base_inputs(sha)
    for level in BASES:
        if level not in refs:
            refs[level] = {"inputs": inputs[level], "image": tagged_base(level, inputs[level])}
    require(all(refs[level]["inputs"] == inputs[level] for level in BASES), "Flow built base images from different inputs")
    (ROOT / BASE_RECORD).write_text(json.dumps({level: refs[level] for level in BASES}, indent=2) + "\n")
    print("Recorded base images. Commit deploy/tke/base-images.json, then release a commit that contains it.", flush=True)


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="full application source SHA")
    parser.add_argument("--run-id", type=int, help="resume an existing Yunxiao run")
    parser.add_argument("--bases", action="store_true", help="build and record the shared dependency images instead of a release")
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ.setdefault("KUBECONFIG", str(Path.home() / "Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig"))
    attempt = Path(tempfile.mkdtemp(prefix="ppap-build-"))
    print("Build/recovery files: " + str(attempt), flush=True)
    if args.bases:
        build_bases(args.source, attempt, args.run_id)
    else:
        build(args.source, attempt, args.run_id)


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as error:
        # Release errors are written for the operator and carry no remote output or credentials.
        print("ReleaseError: " + str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, RuntimeError) else ""
        print(type(error).__name__ + ": build incomplete" + (f" ({message})" if message else "") + "; inspect the build recovery directory before retrying.", file=sys.stderr)
        sys.exit(1)
