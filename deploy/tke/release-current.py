"""Build tested PPAP images, or deploy the reviewed TKE configuration.

Run build SOURCE_SHA first; review/commit its current.yaml pins, then run deploy.
A failed remote mutation is reconciled, never automatically repeated.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import runpy
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPONENTS = ("api", "worker", "web", "deep")
PREFIXES = {"yunxiao": "icoastline.tencentcloudcr.com/ppap-feishu/current-", "unregistry": "unregistry.xixifusi.online/ppap-"}
CODEUP = "https://codeup.aliyun.com/icoastline/integration/4.0/ppap-web.git"
FLOW = "/flow/organizations/60d54c9a70ab8549fd3e10db/pipelines/5339663"
PRELOAD = runpy.run_path(str(HERE / "preload-current.py"))
run = PRELOAD["command"]
kube = PRELOAD["kube"]
require_images = PRELOAD["release_images"]
native_objects = PRELOAD["native_objects"]


class ReleaseError(ValueError):
    """An operator-safe blocker, without remote output or credentials."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def tested_source(sha: str) -> None:
    require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "source must be a full Git SHA")
    require(run(["git", "rev-parse", sha + "^{commit}"], timeout=30).decode().strip() == sha, "source commit is unavailable")
    data = json.loads(run(["gh", "api", f"repos/ppap-agent/ppap/actions/workflows/tests.yml/runs?head_sha={sha}&per_page=100"]))
    trusted = [r for r in data["workflow_runs"] if r["head_sha"] == sha and r["event"] in {"push", "workflow_dispatch"} and r["head_repository"]["full_name"] == "ppap-agent/ppap"]
    require(bool(trusted) and max(trusted, key=lambda r: r["id"])["conclusion"] == "success", "exact source needs a successful latest Tests run; no test bypass")


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
    return len(sources) == 1 and sources[0].get("data", {}).get("repo") == CODEUP and [c.get("commitId") for c in json.loads(sources[0]["data"].get("commit") or "[]")] == [sha]


def yunxiao(sha: str, attempt: Path, resume_run: int | None) -> dict[str, str]:
    expected_config_id = None
    if resume_run is None:
        existing = cloud("GET", FLOW + "/runs", query={"page": 1})
        for row in existing:
            rid = row.get("pipelineRunId", row.get("id"))
            previous = cloud("GET", FLOW + f"/runs/{rid}")
            if run_source(previous, sha) and previous["status"] not in {"FAIL", "FAILED", "CANCELED", "CANCELLED"}:
                return yunxiao(sha, attempt, int(rid))
        require(all(r["status"] in {"SUCCESS", "FAIL", "FAILED", "CANCELED", "CANCELLED"} for r in existing), "release pipeline has an active run; do not reconfigure it")
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
        require(run_source(observation, sha), "Flow run source differs from selected source")
        if expected_config_id is not None:
            require(observation["pipelineConfigId"] == expected_config_id, "Flow used another pipeline configuration; do not deploy")
        status = observation["status"]
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


def registry_images(sha: str, images: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve legacy tags and verify all four manifests/configs before any rollout."""
    configs, pins = {}, {}
    auth = None
    if next(iter(images.values())).startswith(PREFIXES["yunxiao"]):
        name = PRELOAD["tcr_pull_secret"]()
        secret = json.loads(kube("get", "secret", name, "-o", "json"))
        cred = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))["auths"]["icoastline.tencentcloudcr.com"]
        auth = cred.get("auth") or base64.b64encode((cred["username"] + ":" + cred["password"]).encode()).decode()
    for component, ref in images.items():
        repository = ref.split("@", 1)[0].rsplit(":", 1)[0]
        host, path = repository.split("/", 1)
        selected = ref.split("@", 1)[1] if "@" in ref else ref.rsplit(":", 1)[1]
        url = f"https://{host}/v2/{path}/manifests/{selected}"
        headers = {"Accept": "application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json"}
        code, response_headers, raw = registry_get(url, headers)
        if auth:
            require(code == 401, "TCR repository must reject anonymous access")
            challenge = dict(re.findall(r'(realm|service)="([^"]+)"', response_headers.get("WWW-Authenticate", "")))
            realm = challenge.get("realm", "")
            require(urllib.parse.urlsplit(realm).scheme == "https" and urllib.parse.urlsplit(realm).hostname == host, "untrusted registry authentication realm")
            tokenurl = realm + "?" + urllib.parse.urlencode({"service": challenge["service"], "scope": "repository:" + path + ":pull"})
            token_code, _, body = registry_get(tokenurl, {})
            require(token_code == 200, "anonymous registry token unavailable")
            anonymous = json.loads(body)
            anonymous_headers = {**headers, "Authorization": "Bearer " + (anonymous.get("token") or anonymous["access_token"])}
            require(registry_get(url, anonymous_headers)[0] in {401, 403}, "TCR image is publicly accessible")
            tokenurl += "&" + urllib.parse.urlencode({"account": base64.b64decode(auth).decode().split(":", 1)[0]})
            token_code, _, body = registry_get(tokenurl, {"Authorization": "Basic " + auth, "Cache-Control": "no-cache"})
            require(token_code == 200, "TCR authentication failed")
            bearer = json.loads(body)
            headers["Authorization"] = "Bearer " + (bearer.get("token") or bearer["access_token"])
            code, response_headers, raw = registry_get(url, headers)
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
        pins[component] = repository + "@" + manifest_digest
        configs[component] = config["digest"]
    return pins, configs


def build(sha: str, provider: str, attempt: Path, run_id: int | None) -> None:
    tested_source(sha)
    require(not run(["git", "status", "--porcelain", "--", "deploy/tke/current.yaml"]).strip(), "current.yaml has local edits; review/commit them before updating pins")
    prefix = PREFIXES[provider]
    if provider == "yunxiao":
        images = yunxiao(sha, attempt, run_id)
    else:
        require(run_id is None, "--run-id applies only to Yunxiao")
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
        images = {c: prefix + c + ":" + sha for c in COMPONENTS}
    pins, _ = registry_images(sha, images)
    config = HERE / "current.yaml"
    text = config.read_text()
    text, count = re.subn(r"(?m)^(  PPAP_BUILD_SHA: ).+$", lambda m: m[1] + sha, text)
    require(count == 1, "expected one application source pin")
    for component, ref in pins.items():
        text, count = re.subn(r"(?m)^([ ]+image: )(?:\S+/)?(?:ppap-|current-)?" + component + r"(?:@|:).+$", lambda m: m[1] + ref, text)
        require(count == 1, "expected one image pin for " + component)
    config.write_text(text)
    print("Verified all four images. Review and commit deploy/tke/current.yaml; run deploy only after deployment authorization.", flush=True)


# The deploy operation is below; it consumes only reviewed configuration.
DB_SNAPSHOT = """
import json
from ppap_core.settings import load_settings
from ppap_core.db import connect
with connect(load_settings(), 'api') as c:
    c.execute('SET default_transaction_read_only = on')
    with c.transaction():
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        result = {}
        for name in ('file_versions','rule_versions','audit_drafts','audit_rounds','findings','report_exports','usage_events'):
            query = 'SELECT count(*),md5(string_agg(fingerprint,chr(44) ORDER BY fingerprint)) '
            query += 'FROM (SELECT md5(to_jsonb(t)::text) AS fingerprint FROM product.'+name+' t) rows'
            result[name] = c.execute(query).fetchone()
        result['migrations'] = [r[0] for r in c.execute('SELECT version FROM product.schema_migrations ORDER BY version').fetchall()]
        result['active_jobs'] = c.execute("SELECT kind,status,count(*) FROM engine.jobs WHERE status IN ('queued','leased') GROUP BY kind,status ORDER BY kind,status").fetchall()
    print(json.dumps(result))
"""
LIVE_PROBE = """
import json,socket,sys
from ppap_core.settings import load_settings
from ppap_core.db import connect
sha=sys.argv[1]
with connect(load_settings(),'api') as c:
    c.execute('SET TRANSACTION READ ONLY')
    assert c.execute("SELECT 1 FROM engine.worker_heartbeats WHERE git_sha=%s AND last_seen_at>now()-interval '45 seconds'",(sha,)).fetchone()
print(json.dumps({'worker_heartbeat':True}))
"""
DEEP_PROBE = """
import socket,json
from ppap_deep.client import read_frame,write_frame
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as c:
    c.settimeout(10)
    c.connect('/run/ppap-deep/controller.sock')
    write_frame(c,{'op':'preload-startup-probe'})
    assert read_frame(c)['code']=='deep_protocol_invalid'
print(json.dumps({'deep_socket':True,'model_called':False}))
"""


def database() -> dict:
    return json.loads(kube("exec", "-i", "deployment/ppap-current-api", "--", "python", "-", data=DB_SNAPSHOT.encode()))


def public(base: str, path: str, *, current: bool = False) -> bytes:
    headers = {"Cookie": "ppap_edition=current"} if current else {}
    with urllib.request.urlopen(urllib.request.Request(base.rstrip("/") + path, headers=headers), timeout=30) as response:
        return response.read()


def stable_resources() -> dict:
    result = {}
    for kind in ("deployments", "jobs", "services", "persistentvolumeclaims", "ingresses"):
        for obj in json.loads(kube("get", kind, "-o", "json"))["items"]:
            if kind == "deployments" and obj["metadata"]["name"] in set(PRELOAD["OWNERS"].values()):
                continue
            result[kind + "/" + obj["metadata"]["name"]] = {"uid": obj["metadata"]["uid"], "spec_digest": digest(json.dumps(obj["spec"], sort_keys=True).encode())}
    return result


def verify(attempt: Path) -> None:
    expected = json.loads((attempt / "target.json").read_text())
    sha, images, configs = expected["sha"], expected["images"], expected["configs"]
    for owner in set(PRELOAD["OWNERS"].values()):
        deployment = json.loads(kube("get", "deployment", owner, "-o", "json"))
        status = deployment["status"]
        replicas = deployment["spec"]["replicas"]
        require(
            replicas > 0
            and status.get("observedGeneration") == deployment["metadata"]["generation"]
            and status.get("readyReplicas") == status.get("updatedReplicas") == replicas
            and status.get("replicas") == replicas,
            "deployment is not fully ready: " + owner,
        )
        selector = ",".join(k + "=" + v for k, v in deployment["spec"]["selector"]["matchLabels"].items())
        pods = [p for p in json.loads(kube("get", "pods", "-l", selector, "-o", "json"))["items"] if not p["metadata"].get("deletionTimestamp")]
        require(len(pods) == replicas, "unexpected live replica count")
        for pod in pods:
            statuses = {s["name"]: s for s in pod["status"].get("containerStatuses", [])}
            for container in pod["spec"]["containers"]:
                name = container["name"]
                if name not in images:
                    continue
                actual = statuses[name]
                require(container["image"] == images[name] and actual["ready"] and actual["restartCount"] == 0, "application is unhealthy or uses another image")
                require(
                    actual["imageID"] in {images[name], "docker-pullable://" + images[name], configs[name], "containerd://" + configs[name]}, "actual image ID differs from verified registry identity"
                )
    base = expected["base"]
    require(json.loads(public(base, "/ppap/version"))["git_sha"] == sha, "public API version differs")
    require(json.loads(public(base, "/build-identity.json", current=True))["git_sha"] == sha, "public Web version differs")
    public(base, "/ppap/ready")
    require(digest(public(base, "/")) == expected["legacy_html"], "default legacy page changed")
    require(json.loads(kube("exec", "-i", "deployment/ppap-current-api", "--", "python", "-", sha, data=LIVE_PROBE.encode()))["worker_heartbeat"], "fresh Worker heartbeat missing")
    require(json.loads(kube("exec", "-i", "deployment/ppap-current-worker", "-c", "worker", "--", "python", "-", data=DEEP_PROBE.encode()))["deep_socket"], "Deep socket unavailable")
    after = database()
    # Concurrent legitimate writes are not attributed to the release. Reconcile them.
    require(after == expected["database"], "database/queue changed during rollout; reconcile before claiming preservation")
    require(stable_resources() == expected["resources"], "non-target resources changed; reconcile before completion")
    (attempt / "verified.json").write_text(json.dumps({"sha": sha, "images": images, "verified": True}, indent=2))
    print(f"Live API/Web/Worker/Deep verified at {sha}; default legacy, history and non-target resources preserved.", flush=True)


def dry_run(path: Path, objects: list[dict]) -> None:
    output = kube("apply", "--dry-run=server", "-f", str(path)).decode().splitlines()
    allowed = {"configmap/ppap-current"} | {"deployment.apps/" + name for name in PRELOAD["OWNERS"].values()}
    require(len(output) == len(objects), "server dry-run did not report every object")
    for line in output:
        fields = line.split()
        require(
            len(fields) >= 2 and (fields[1] == "unchanged" or fields[1] == "configured" and fields[0] in allowed), "release would change non-application resources; review that operation separately"
        )


def deploy(attempt: Path) -> None:
    rendered = run([str(HERE / "render-current.sh")])
    objects = native_objects(kube("create", "--dry-run=client", "-f", "-", "-o", "json", data=rendered))
    images = require_images(objects)
    require(bool(images), "no application release in canonical render")
    config = next(obj["data"] for obj in objects if obj.get("kind") == "ConfigMap" and obj["metadata"]["name"] == "ppap-current")
    sha = config["PPAP_BUILD_SHA"]
    tested_source(sha)
    pins, configs = registry_images(sha, images)
    # Old unregistry SHA tags remain accepted for rollback, but cannot skip identity/startup checks.
    require(all(ref == pins[c] or ref == PREFIXES["unregistry"] + c + ":" + sha for c, ref in images.items()), "rendered image does not match verified identity")
    migrations = {p.name: p.read_text() for p in (ROOT / "migrations").iterdir() if p.is_file()}
    require(
        json.loads(kube("get", "configmap", "ppap-current-migrations", "-o", "json"))["data"] == migrations,
        "migration payload differs; review/apply schema upgrade separately under Issue #15; completed init Jobs are not rotated",
    )
    render_path = attempt / "rendered.yaml"
    render_path.write_bytes(rendered)
    dry_run(render_path, objects)
    PRELOAD["preload"](objects, configs)
    # Refresh business state after slow image pulls, immediately before apply.
    before = database()
    require(before["migrations"] == sorted(p.name for p in (ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")), "live schema does not match numbered migrations")
    require(all(row[0] == "feishu_notification" for row in before["active_jobs"]), "in-flight business jobs block rollout; never reset or replay them")
    target = {
        "sha": sha,
        "images": images,
        "configs": configs,
        "base": config["PPAP_WEB_URL"],
        "legacy_html": digest(public(config["PPAP_WEB_URL"], "/")),
        "database": before,
        "resources": stable_resources(),
        "render_digest": digest(rendered),
    }
    (attempt / "target.json").write_text(json.dumps(target, indent=2))
    dry_run(render_path, objects)
    # The intent precedes the mutation. An interrupted/failed apply may be partial.
    (attempt / "apply-intent").write_text(target["render_digest"])
    try:
        kube("apply", "-f", str(render_path))
        for owner in sorted(set(PRELOAD["OWNERS"].values())):
            kube("rollout", "status", "deployment/" + owner, "--timeout=300s")
        verify(attempt)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        raise RuntimeError(f"apply/readback incomplete; do not repeat automatically. Run verify --attempt {attempt} and reconcile the observed state") from None


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build_parser = commands.add_parser("build", help="build tested source and update desired pins; never deploy")
    build_parser.add_argument("source")
    build_parser.add_argument("--provider", choices=tuple(PREFIXES), default="yunxiao")
    build_parser.add_argument("--run-id", type=int, help="resume/reconcile an existing Yunxiao run; never dispatch")
    commands.add_parser("deploy", help="deploy reviewed current.yaml; no build or migration rotation")
    verify_parser = commands.add_parser("verify", help="read back an interrupted deployment; no mutations")
    verify_parser.add_argument("--attempt", required=True, type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ.setdefault("KUBECONFIG", str(Path.home() / "Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig"))
    if args.action == "verify":
        verify(args.attempt)
        return
    # This per-operation directory is recovery evidence, not a repository state ledger.
    lock = (HERE / ".release.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ReleaseError("another local release operation is running") from None
    attempt = Path(tempfile.mkdtemp(prefix="ppap-" + args.action + "-"))
    print("Operation/recovery files: " + str(attempt), flush=True)
    if args.action == "build":
        build(args.source, args.provider, attempt, args.run_id)
    else:
        deploy(attempt)


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        # Never expose subprocess output, credentials, signed URLs or application logs.
        print(type(error).__name__ + ": operation incomplete; inspect the recovery directory and README before retrying.", file=sys.stderr)
        sys.exit(1)
