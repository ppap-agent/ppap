#!/usr/bin/env python3
"""Isolated-dev process supervisor for PostgreSQL, MinIO, API, worker, and local web."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.paths import git_sha, pythonpath as shared_pythonpath

LOCAL = ROOT / ".local" / "stack"
BIN = ROOT / ".local" / "bin"
VENV = ROOT / ".venv"
WEB = ROOT / "apps" / "web"
PGDATA = LOCAL / "pgdata"
MINIO_DATA = LOCAL / "minio"
LOGS = LOCAL / "logs"
PIDS = LOCAL / "pids"

MINIO_RELEASE = "RELEASE.2025-09-07T16-13-09Z"
MINIO_URL = (
    "https://github.com/minio/minio/releases/download/"
    f"{MINIO_RELEASE}/minio.linux-amd64.{MINIO_RELEASE}"
)
MINIO_SHA256 = "7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f"

PROCESSES = ("postgres", "minio", "api", "worker", "web")


def pythonpath() -> str:
    return shared_pythonpath()


def map_llm_names(env: dict[str, str]) -> dict[str, str]:
    """Environment-side mapping only. Application settings still read LLM_*."""
    out = dict(env)
    for dest, src in (
        ("LLM_API_KEY", "OPENAI_API_KEY"),
        ("LLM_BASE_URL", "OPENAI_BASE_URL"),
        ("LLM_MODEL", "MODEL_NAME"),
    ):
        if not out.get(dest) and out.get(src):
            out[dest] = out[src]
    return out


def child_env() -> dict[str, str]:
    from ppap_core.settings import ISOLATED_DEV_LLM_BASE_URL, ISOLATED_DEV_LLM_MODEL

    env = map_llm_names(dict(os.environ))
    sha = git_sha()
    env["PYTHONPATH"] = pythonpath()
    env["PPAP_PROFILE"] = env.get("PPAP_PROFILE") or "isolated-dev"
    env["PPAP_BUILD_SHA"] = sha
    env["PPAP_WEB_URL"] = env.get("PPAP_WEB_URL") or "http://127.0.0.1:4173"
    env["PPAP_VERSION_URL"] = env.get("PPAP_VERSION_URL") or "http://127.0.0.1:8080/version"
    env["PPAP_PUBLIC_API_URL"] = env.get("PPAP_PUBLIC_API_URL") or "http://127.0.0.1:8080"
    env["PPAP_ENVIRONMENT"] = env.get("PPAP_ENVIRONMENT") or "isolated-dev"
    if env["PPAP_PROFILE"] != "production":
        if not str(env.get("LLM_BASE_URL") or "").strip():
            env["LLM_BASE_URL"] = ISOLATED_DEV_LLM_BASE_URL
        if not str(env.get("LLM_MODEL") or "").strip():
            env["LLM_MODEL"] = ISOLATED_DEV_LLM_MODEL
    return env


def pg_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for version in ("16", "17", "15"):
        candidate = Path(f"/usr/lib/postgresql/{version}/bin/{name}")
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"missing PostgreSQL binary {name}; install postgresql")


def venv_python() -> Path:
    return VENV / "bin" / "python"


def ensure_dirs() -> None:
    for path in (LOCAL, BIN, LOGS, PIDS, MINIO_DATA, WEB):
        path.mkdir(parents=True, exist_ok=True)


def pid_path(name: str) -> Path:
    return PIDS / f"{name}.pid"


def log_path(name: str) -> Path:
    return LOGS / f"{name}.log"


def read_pid(name: str) -> int | None:
    path = pid_path(name)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        path.unlink(missing_ok=True)
        return None
    return pid


def write_pid(name: str, pid: int) -> None:
    pid_path(name).write_text(str(pid) + "\n")


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def wait_port(host: str, port: int, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(host, port):
            return
        time.sleep(0.2)
    raise SystemExit(f"timeout waiting for {host}:{port}")


def spawn(name: str, command: list[str], env: dict[str, str], cwd: Path | None = None) -> None:
    if read_pid(name):
        return
    log = log_path(name).open("ab")
    proc = subprocess.Popen(
        command,
        cwd=str(cwd or ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write_pid(name, proc.pid)


def stop_pid(name: str) -> None:
    pid = read_pid(name)
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pid_path(name).unlink(missing_ok=True)
        return
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.2)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    pid_path(name).unlink(missing_ok=True)


def download_minio() -> Path:
    dest = BIN / "minio"
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == MINIO_SHA256:
        dest.chmod(0o755)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".partial")
    with urllib.request.urlopen(MINIO_URL, timeout=120) as response, tmp.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if digest != MINIO_SHA256:
        tmp.unlink(missing_ok=True)
        raise SystemExit("minio checksum mismatch")
    tmp.replace(dest)
    dest.chmod(0o755)
    return dest


def install() -> None:
    ensure_dirs()
    pg_bin("postgres")
    pg_bin("initdb")
    download_minio()
    if not venv_python().exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    subprocess.check_call([str(venv_python()), "-m", "pip", "install", "-q", "-e", str(ROOT) + "[dev]"])


def reexec_in_venv() -> None:
    install()
    target = venv_python()
    if Path(sys.prefix).resolve() != VENV.resolve():
        os.execv(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]])


def init_postgres(env: dict[str, str]) -> None:
    if (PGDATA / "PG_VERSION").exists():
        return
    password_file = LOCAL / "pg.pw"
    password_file.write_text(env.get("PPAP_PG_BOOTSTRAP_PASSWORD", "ppap_isolated_dev"))
    password_file.chmod(0o600)
    subprocess.check_call(
        [
            pg_bin("initdb"),
            "-D",
            str(PGDATA),
            "--locale=C",
            "--encoding=UTF8",
            "--username",
            env.get("PPAP_PG_BOOTSTRAP_USER", "ppap"),
            "--pwfile",
            str(password_file),
            "--auth=scram-sha-256",
        ],
        cwd=ROOT,
    )
    (PGDATA / "postgresql.conf").write_text(
        "listen_addresses = '127.0.0.1'\n"
        "port = 5432\n"
        "unix_socket_directories = '{sockets}'\n"
        "password_encryption = scram-sha-256\n".format(sockets=LOCAL / "pg_run")
    )
    (LOCAL / "pg_run").mkdir(parents=True, exist_ok=True)
    (PGDATA / "pg_hba.conf").write_text(
        "local all all scram-sha-256\n"
        "host all all 127.0.0.1/32 scram-sha-256\n"
        "host all all ::1/128 scram-sha-256\n"
    )


def start_postgres(env: dict[str, str]) -> None:
    init_postgres(env)
    (LOCAL / "pg_run").mkdir(parents=True, exist_ok=True)
    if not read_pid("postgres"):
        spawn(
            "postgres",
            [pg_bin("postgres"), "-D", str(PGDATA)],
            env,
        )
    wait_port("127.0.0.1", int(env.get("PPAP_PG_PORT", "5432")))


def bootstrap_roles(env: dict[str, str]) -> None:
    from tools.bootstrap import bootstrap_roles as apply_bootstrap

    apply_bootstrap(env)


def migrate(env: dict[str, str]) -> None:
    import psycopg

    sys.path[:0] = [str(ROOT / "packages")]
    from ppap_core.settings import Settings
    from tools.migrate import apply_migrations

    settings = Settings()
    user = env.get("PPAP_PG_MIGRATOR_USER", settings.pg_migrator_user)
    password = env.get("PPAP_PG_MIGRATOR_PASSWORD", settings.pg_migrator_password)
    database = env.get("PPAP_PG_DATABASE", settings.pg_database)
    dsn = f"postgresql://{user}:{password}@{settings.pg_host}:{settings.pg_port}/{database}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        apply_migrations(conn)


def start_minio(env: dict[str, str]) -> None:
    minio = download_minio()
    child = dict(env)
    child["MINIO_ROOT_USER"] = env.get("PPAP_S3_ACCESS_KEY", "ppap_isolated_dev")
    child["MINIO_ROOT_PASSWORD"] = env.get("PPAP_S3_SECRET_KEY", "ppap_isolated_dev")
    spawn(
        "minio",
        [str(minio), "server", str(MINIO_DATA), "--address", "127.0.0.1:9000", "--quiet"],
        child,
    )
    wait_port("127.0.0.1", 9000)
    sys.path[:0] = [str(ROOT / "packages")]
    from ppap_core.settings import Settings
    from ppap_core.storage import ensure_bucket

    ensure_bucket(Settings())


def write_web_runtime(env: dict[str, str]) -> None:
    payload = {"api_url": env.get("PPAP_PUBLIC_API_URL", "http://127.0.0.1:8080").rstrip("/")}
    (WEB / "runtime-config.json").write_text(json.dumps(payload) + "\n")


def start_apps(env: dict[str, str]) -> None:
    py = str(venv_python())
    write_web_runtime(env)
    spawn(
        "api",
        [py, "-m", "uvicorn", "ppap_api:app", "--host", env.get("PPAP_API_BIND", "127.0.0.1"), "--port", env.get("PPAP_API_PORT", "8080")],
        env,
    )
    spawn("worker", [py, "-m", "ppap_worker.main"], env)
    spawn(
        "web",
        [py, str(ROOT / "tools" / "web_server.py")],
        env,
        cwd=WEB,
    )
    wait_port("127.0.0.1", int(env.get("PPAP_API_PORT", "8080")))
    wait_port("127.0.0.1", int(env.get("PPAP_WEB_PORT", "4173")))


def up() -> None:
    reexec_in_venv()
    env = child_env()
    if port_open("127.0.0.1", 5432) and not read_pid("postgres"):
        raise SystemExit("127.0.0.1:5432 is already in use; stop the other PostgreSQL or set PPAP_PG_PORT")
    start_postgres(env)
    os.environ.update({k: v for k, v in env.items() if k.startswith(("PPAP_", "LLM_", "PYTHONPATH"))})
    bootstrap_roles(env)
    migrate(env)
    start_minio(env)
    start_apps(env)
    print(json.dumps({
        "status": "started",
        "web": env["PPAP_WEB_URL"],
        "api": env["PPAP_PUBLIC_API_URL"],
        "version": env["PPAP_VERSION_URL"],
        "git_sha": env["PPAP_BUILD_SHA"],
    }))


def down() -> None:
    for name in reversed(PROCESSES):
        if name == "postgres" and read_pid("postgres"):
            try:
                subprocess.call(
                    [pg_bin("pg_ctl"), "-D", str(PGDATA), "-m", "fast", "stop"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except SystemExit:
                pass
        stop_pid(name)
    print(json.dumps({"status": "stopped"}))


def restart() -> None:
    down()
    up()


def status() -> dict[str, object]:
    return {name: ("running" if read_pid(name) else "stopped") for name in PROCESSES}


def http_json(url: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode())
            return response.status, body
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode()) if exc.fp else {}
        return exc.code, body


def verify() -> int:
    env = child_env()
    report = {
        "processes": status(),
        "health": None,
        "ready": None,
        "version": None,
        "public_config": None,
        "web": None,
        "worker_heartbeat": None,
        "ok": False,
    }
    try:
        code, health = http_json(env["PPAP_PUBLIC_API_URL"].rstrip("/") + "/health")
        report["health"] = {"http": code, "status": health.get("status")}
        code, ready = http_json(env["PPAP_PUBLIC_API_URL"].rstrip("/") + "/ready")
        report["ready"] = {"http": code, "status": ready.get("status"), "postgres": ready.get("postgres"), "object_storage": ready.get("object_storage")}
        code, version = http_json(env["PPAP_VERSION_URL"])
        report["version"] = {"http": code, "git_sha": version.get("git_sha")}
        code, public = http_json(env["PPAP_PUBLIC_API_URL"].rstrip("/") + "/public-config")
        secret_like = [key for key in public if any(part in key.lower() for part in ("secret", "password", "token", "api_key", "access_key"))]
        report["public_config"] = {"http": code, "keys": sorted(public), "secret_fields": secret_like}
        with urllib.request.urlopen(env["PPAP_WEB_URL"], timeout=5) as response:
            html = response.read().decode()
            report["web"] = {"http": response.status, "has_build_hook": 'data-testid="build-revision"' in html}
        import psycopg
        from ppap_core.settings import Settings
        settings = Settings()
        row = None
        deadline = time.time() + 12
        while time.time() < deadline and not row:
            with psycopg.connect(settings.dsn("api"), autocommit=True) as conn:
                row = conn.execute(
                    "SELECT worker_id FROM engine.worker_heartbeats WHERE last_seen_at > now() - interval '10 seconds' LIMIT 1"
                ).fetchone()
            if not row:
                time.sleep(0.4)
        report["worker_heartbeat"] = bool(row)
        report["ok"] = (
            report["processes"] == {name: "running" for name in PROCESSES}
            and report["health"]["status"] == "alive"
            and report["ready"]["status"] == "ready"
            and report["version"]["git_sha"] == env["PPAP_BUILD_SHA"]
            and not secret_like
            and report["web"]["has_build_hook"]
            and report["worker_heartbeat"] is True
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def seed_auth() -> int:
    import httpx

    env = child_env()
    destination = ROOT / ".local"
    destination.mkdir(parents=True, exist_ok=True)
    mapping = {"alice": destination / "auth.json", "carol": destination / "other-tenant-auth.json"}
    files = {}
    with httpx.Client(base_url=env["PPAP_PUBLIC_API_URL"].rstrip("/"), timeout=10) as client:
        for subject, path in mapping.items():
            response = client.post("/isolated-dev/sessions", json={"subject": subject})
            cookie = response.cookies.get("ppap_session")
            if response.status_code != 200 or not cookie:
                print(json.dumps({"ok": False, "subject": subject, "http": response.status_code}))
                return 1
            path.write_text(json.dumps({
                "cookies": [{
                    "name": "ppap_session",
                    "value": cookie,
                    "domain": "127.0.0.1",
                    "path": "/",
                    "httpOnly": True,
                    "secure": False,
                    "sameSite": "Lax",
                }],
                "origins": [],
            }, indent=2) + "\n")
            path.chmod(0o600)
            files[subject] = str(path)
    print(json.dumps({"ok": True, "files": files}))
    return 0


def verify_auth() -> int:
    import httpx
    from ppap_core.fixtures import RESOURCE_A

    env = child_env()
    report = {"ok": False}
    try:
        api = env["PPAP_PUBLIC_API_URL"].rstrip("/")

        def mint(subject: str) -> str:
            response = httpx.post(api + "/isolated-dev/sessions", json={"subject": subject}, timeout=10)
            cookie = response.cookies.get("ppap_session")
            if response.status_code != 200 or not cookie:
                raise RuntimeError("fixture_session_failed")
            return cookie

        login = httpx.get(api + "/login/feishu", follow_redirects=False, timeout=10)
        report["oauth_start"] = login.status_code == 302 and "accounts.feishu.cn" in login.headers.get("location", "")
        alice = mint("alice")
        bob = mint("bob")
        carol = mint("carol")
        allowed = httpx.get(api + "/resources/" + RESOURCE_A, cookies={"ppap_session": alice}, timeout=10)
        member = httpx.get(api + "/resources/" + RESOURCE_A, cookies={"ppap_session": bob}, timeout=10)
        denied = httpx.get(api + "/resources/" + RESOURCE_A, cookies={"ppap_session": carol}, timeout=10)
        report["alice_read"] = allowed.status_code == 200 and allowed.json().get("review_allowed") is True
        report["bob_read_not_review"] = member.status_code == 200 and member.json().get("review_allowed") is False
        report["carol_denied"] = denied.status_code == 403 and denied.json() == {"error": "access_denied"}
        logout = httpx.post(api + "/logout", cookies={"ppap_session": alice}, timeout=10)
        me = httpx.get(api + "/me", cookies={"ppap_session": alice}, timeout=10)
        report["logout"] = logout.status_code == 200 and me.status_code == 401
        with urllib.request.urlopen(env["PPAP_WEB_URL"] + "?resource=" + RESOURCE_A, timeout=5) as response:
            html = response.read().decode()
        report["web_hooks"] = all(
            marker in html
            for marker in (
                'data-testid="access-denied"',
                'data-testid="login-feishu"',
                'data-testid="current-user"',
                'data-testid="logout"',
            )
        )
        report["ok"] = all(
            report[key] is True
            for key in ("oauth_start", "alice_read", "bob_read_not_review", "carol_denied", "logout", "web_hooks")
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def verify_uploads() -> int:
    import hashlib
    import httpx

    env = child_env()
    report = {"ok": False}
    try:
        api = env["PPAP_PUBLIC_API_URL"].rstrip("/")

        def mint(subject: str) -> str:
            response = httpx.post(api + "/isolated-dev/sessions", json={"subject": subject}, timeout=10)
            cookie = response.cookies.get("ppap_session")
            if response.status_code != 200 or not cookie:
                raise RuntimeError("fixture_session_failed")
            return cookie

        def headers(cookie: str) -> dict[str, str]:
            return {"Cookie": "ppap_session=" + cookie}

        alice, carol = mint("alice"), mint("carol")
        first, second = b"ppap-version-a", b"ppap-version-b"
        created = httpx.post(
            api + "/files",
            json={"original_filename": "report.xlsx", "content_type": "application/octet-stream", "byte_size": len(first)},
            headers=headers(alice),
            timeout=10,
        )
        file_id, v1 = created.json()["file_id"], created.json()["id"]
        put = httpx.put(api + f"/files/{file_id}/versions/{v1}/blob", content=first, headers=headers(alice), timeout=10)
        ready = httpx.post(api + f"/files/{file_id}/versions/{v1}/finalize", headers=headers(alice), timeout=10)
        download = httpx.get(api + f"/files/{file_id}/versions/{v1}/download", headers=headers(alice), timeout=10)
        report["alice_upload"] = (
            created.status_code == 201
            and put.status_code == 200
            and ready.status_code == 200
            and download.content == first
            and ready.json().get("sha256") == hashlib.sha256(first).hexdigest()
        )
        extra = httpx.post(
            api + f"/files/{file_id}/versions",
            json={"original_filename": "report.xlsx", "content_type": "application/octet-stream", "byte_size": len(second)},
            headers=headers(alice),
            timeout=10,
        )
        v2 = extra.json()["id"]
        httpx.put(api + f"/files/{file_id}/versions/{v2}/blob", content=second, headers=headers(alice), timeout=10)
        later = httpx.post(api + f"/files/{file_id}/versions/{v2}/finalize", headers=headers(alice), timeout=10)
        old = httpx.get(api + f"/files/{file_id}/versions/{v1}/download", headers=headers(alice), timeout=10)
        new = httpx.get(api + f"/files/{file_id}/versions/{v2}/download", headers=headers(alice), timeout=10)
        report["history_preserved"] = old.content == first and new.content == second and later.json().get("sha256") == hashlib.sha256(second).hexdigest()
        denied = httpx.get(api + f"/files/{file_id}/versions/{v1}/download", headers=headers(carol), timeout=10)
        report["carol_denied"] = denied.status_code == 403 and denied.json() == {"error": "access_denied"} and first not in denied.content
        replay = httpx.post(api + f"/files/{file_id}/versions/{v1}/finalize", headers=headers(alice), timeout=10)
        overwrite = httpx.put(api + f"/files/{file_id}/versions/{v1}/blob", content=b"nope", headers=headers(alice), timeout=10)
        report["immutable_write"] = replay.status_code == 200 and overwrite.status_code == 409
        from concurrent.futures import ThreadPoolExecutor

        from ppap_core.files import ObjectExists, version_object_key
        from ppap_core.fixtures import TENANT_A
        from ppap_core.settings import Settings
        from ppap_core.storage import S3Objects

        race_a, race_b = b"minio-winner-a", b"minio-loser--b"
        raced = httpx.post(
            api + "/files",
            json={
                "original_filename": "race.bin",
                "content_type": "application/octet-stream",
                "byte_size": len(race_a),
            },
            headers=headers(alice),
            timeout=10,
        )
        race_file, race_vid = raced.json()["file_id"], raced.json()["id"]
        objects = S3Objects(Settings())
        race_key = version_object_key(TENANT_A, race_file, race_vid)
        minio_outcomes: list[bytes | str] = []

        def exclusive_put(payload: bytes) -> None:
            try:
                objects.put_exclusive(race_key, payload, "application/octet-stream")
                minio_outcomes.append(payload)
            except ObjectExists:
                minio_outcomes.append("exists")

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(exclusive_put, (race_a, race_b)))
        minio_winners = [item for item in minio_outcomes if item != "exists"]
        race_ready = httpx.post(
            api + f"/files/{race_file}/versions/{race_vid}/finalize",
            headers=headers(alice),
            timeout=10,
        )
        race_download = httpx.get(
            api + f"/files/{race_file}/versions/{race_vid}/download",
            headers=headers(alice),
            timeout=10,
        )
        http_meta = httpx.post(
            api + "/files",
            json={
                "original_filename": "race-http.bin",
                "content_type": "application/octet-stream",
                "byte_size": len(race_a),
            },
            headers=headers(alice),
            timeout=10,
        )
        http_file, http_vid = http_meta.json()["file_id"], http_meta.json()["id"]

        def http_put(payload: bytes):
            return httpx.put(
                api + f"/files/{http_file}/versions/{http_vid}/blob",
                content=payload,
                headers=headers(alice),
                timeout=10,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            http_results = list(pool.map(http_put, (race_a, race_b)))
        http_ready = httpx.post(
            api + f"/files/{http_file}/versions/{http_vid}/finalize",
            headers=headers(alice),
            timeout=10,
        )
        http_download = httpx.get(
            api + f"/files/{http_file}/versions/{http_vid}/download",
            headers=headers(alice),
            timeout=10,
        )
        report["concurrent_put"] = (
            raced.status_code == 201
            and len(minio_winners) == 1
            and minio_outcomes.count("exists") == 1
            and sorted(item.status_code for item in http_results) == [200, 409]
        )
        report["concurrent_winner"] = (
            race_ready.status_code == 200
            and race_download.content == minio_winners[0]
            and race_ready.json().get("sha256") == hashlib.sha256(race_download.content).hexdigest()
            and http_ready.status_code == 200
            and http_download.content in {race_a, race_b}
            and http_ready.json().get("sha256") == hashlib.sha256(http_download.content).hexdigest()
        )
        unsafe = httpx.post(api + "/files/from-url", json={"url": "http://127.0.0.1/secret"}, headers=headers(alice), timeout=10)
        report["ssrf_blocked"] = unsafe.status_code == 400
        leftover = httpx.post(
            api + f"/files/{file_id}/versions",
            json={"original_filename": "report.xlsx", "content_type": "application/octet-stream", "byte_size": 1},
            headers=headers(alice),
            timeout=10,
        )
        httpx.put(api + f"/files/{file_id}/versions/{leftover.json()['id']}/blob", content=b"x", headers=headers(alice), timeout=10)
        reaped = httpx.post(api + "/isolated-dev/reap-staging", json={"max_age_seconds": 0}, headers=headers(alice), timeout=10)
        still = httpx.get(api + f"/files/{file_id}/versions/{v1}/download", headers=headers(alice), timeout=10)
        report["staging_reaped"] = reaped.status_code == 200 and reaped.json().get("reaped", 0) >= 1 and still.content == first
        with urllib.request.urlopen(env["PPAP_WEB_URL"], timeout=5) as response:
            html = response.read().decode()
        report["web_hooks"] = all(
            marker in html
            for marker in (
                'data-testid="source-upload"',
                'data-testid="prepare-audit"',
                'data-testid="document-version-id"',
                'data-testid="download-original"',
            )
        )
        report["ok"] = all(
            report[key] is True
            for key in (
                "alice_upload",
                "history_preserved",
                "carol_denied",
                "immutable_write",
                "concurrent_put",
                "concurrent_winner",
                "ssrf_blocked",
                "staging_reaped",
                "web_hooks",
            )
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def verify_audit() -> int:
    import httpx
    from ppap_core.fixtures import USER_BOB

    env = child_env()
    report = {"ok": False}
    try:
        api = env["PPAP_PUBLIC_API_URL"].rstrip("/")

        def mint(subject: str) -> str:
            response = httpx.post(api + "/isolated-dev/sessions", json={"subject": subject}, timeout=10)
            cookie = response.cookies.get("ppap_session")
            if response.status_code != 200 or not cookie:
                raise RuntimeError("fixture_session_failed")
            return cookie

        def headers(cookie: str) -> dict[str, str]:
            return {"Cookie": "ppap_session=" + cookie}

        def upload(cookie: str, name: str, payload: bytes, file_id: str | None = None) -> dict:
            meta = {"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(payload)}
            created = httpx.post(
                api + (f"/files/{file_id}/versions" if file_id else "/files"),
                json=meta,
                headers=headers(cookie),
                timeout=10,
            )
            fid, vid = created.json()["file_id"], created.json()["id"]
            httpx.put(api + f"/files/{fid}/versions/{vid}/blob", content=payload, headers=headers(cookie), timeout=10)
            ready = httpx.post(api + f"/files/{fid}/versions/{vid}/finalize", headers=headers(cookie), timeout=10)
            return ready.json()

        def wait_round(cookie: str, round_id: str) -> dict:
            deadline = time.time() + 20
            body = {}
            while time.time() < deadline:
                response = httpx.get(api + f"/rounds/{round_id}", headers=headers(cookie), timeout=10)
                body = response.json()
                if response.status_code == 200 and body.get("execution") in {"completed", "failed"}:
                    return body
                time.sleep(0.25)
            raise RuntimeError("worker_timeout")

        alice, bob, carol = mint("alice"), mint("bob"), mint("carol")
        submit_key = "isolated-dev-submit-" + str(uuid.uuid4())
        lease_key = "isolated-dev-lease-retry-" + str(uuid.uuid4())
        resubmit_key = "isolated-dev-resubmit-" + str(uuid.uuid4())
        rules = httpx.get(api + "/rules", headers=headers(alice), timeout=10)
        rule_id = rules.json()["rules"][0]["id"]
        target = upload(alice, "report.xlsx", b"audit-target-v1")
        reference = upload(alice, "context.pdf", b"audit-reference")
        draft = httpx.post(api + "/drafts", headers=headers(alice), timeout=10)
        saved = httpx.put(
            api + f"/drafts/{draft.json()['id']}",
            json={
                "rule_version_id": rule_id,
                "items": [
                    {"file_version_id": target["id"], "role": "target"},
                    {"file_version_id": reference["id"], "role": "reference"},
                ],
            },
            headers=headers(alice),
            timeout=10,
        )
        report["draft_open"] = draft.status_code == 201 and saved.json().get("status") == "open"
        first = httpx.post(
            api + f"/drafts/{draft.json()['id']}/accept",
            json={"idempotency_key": submit_key},
            headers=headers(alice),
            timeout=10,
        )
        replay = httpx.post(
            api + f"/drafts/{draft.json()['id']}/accept",
            json={"idempotency_key": submit_key},
            headers=headers(alice),
            timeout=10,
        )
        report["accept_idempotent"] = (
            first.status_code == 201 and replay.status_code == 201 and first.json()["id"] == replay.json()["id"]
        )
        round_id = first.json()["id"]
        body = wait_round(alice, round_id)
        report["worker_completed"] = body.get("execution") == "completed" and {row["status"] for row in body.get("findings", [])} == {"PASS"}
        report["multi_file_coverage"] = (
            "report.xlsx" in (body.get("coverage") or {}).get("checked", [])
            and any("context.pdf" in row for row in (body.get("coverage") or {}).get("unchecked", []))
        )
        locator = next(row["locator"] for row in body["findings"] if row["locator"] and row["locator"].get("kind") == "file_version")
        original = httpx.get(
            api + f"/rounds/{round_id}/files/{locator['file_id']}/versions/{locator['file_version_id']}/download",
            headers=headers(alice),
            timeout=10,
        )
        report["open_original"] = original.content in {b"audit-target-v1", b"audit-reference"}
        import psycopg
        from ppap_api.store import AuthStore
        from ppap_core.audits import ClaimedJob
        from ppap_core.settings import Settings

        stop_pid("worker")
        lease_target = upload(alice, "lease.xlsx", b"lease-target")
        lease_draft = httpx.post(api + "/drafts", headers=headers(alice), timeout=10)
        httpx.put(
            api + f"/drafts/{lease_draft.json()['id']}",
            json={"rule_version_id": rule_id, "items": [{"file_version_id": lease_target["id"], "role": "target"}]},
            headers=headers(alice),
            timeout=10,
        )
        lease_accept = httpx.post(
            api + f"/drafts/{lease_draft.json()['id']}/accept",
            json={"idempotency_key": lease_key},
            headers=headers(alice),
            timeout=10,
        )
        lease_round = lease_accept.json()["id"]
        settings = Settings()
        with psycopg.connect(settings.dsn("worker"), autocommit=True) as conn:
            job = conn.execute(
                "SELECT id FROM engine.jobs WHERE audit_round_id = %s",
                (lease_round,),
            ).fetchone()
            conn.execute(
                """
                UPDATE engine.jobs
                SET status = 'leased',
                    attempt = 1,
                    lease_owner = 'crashed-worker',
                    lease_until = now() - interval '1 second',
                    updated_at = now()
                WHERE id = %s
                """,
                (job[0],),
            )
            conn.execute(
                """
                INSERT INTO engine.attempts (id, job_id, attempt, worker_id, diagnostic)
                VALUES (gen_random_uuid(), %s, 1, 'crashed-worker', '{}'::jsonb)
                """,
                (job[0],),
            )
            conn.execute(
                "UPDATE product.audit_rounds SET execution = 'running' WHERE id = %s",
                (lease_round,),
            )
        spawn("worker", [str(venv_python()), "-m", "ppap_worker.main"], env)
        lease_body = wait_round(alice, lease_round)
        late = AuthStore(settings, role="worker").complete_job(
            ClaimedJob(str(job[0]), "audit", {"round_id": lease_round}, 1, lease_round),
            "crashed-worker",
            {
                "findings": [
                    {
                        "item_key": "target:" + lease_target["id"],
                        "classification": "资料",
                        "item": "待审文件版本已就绪",
                        "priority": "P0",
                        "status": "ERR",
                        "suggestions": "late-attempt-1",
                        "locator": None,
                    }
                ],
                "coverage": {"checked": [], "unchecked": [], "locator_gap": True},
            },
        )
        after_late = httpx.get(api + f"/rounds/{lease_round}", headers=headers(alice), timeout=10).json()
        report["lease_reclaim"] = (
            lease_accept.status_code == 201
            and lease_body.get("execution") == "completed"
            and lease_body.get("result_attempt") == 2
            and late is False
            and after_late.get("result_attempt") == 2
            and {row["status"] for row in after_late.get("findings", [])} == {"PASS"}
            and all(row.get("suggestions") != "late-attempt-1" for row in after_late.get("findings", []))
        )
        carol_denied = httpx.get(api + f"/rounds/{round_id}", headers=headers(carol), timeout=10)
        report["carol_denied"] = carol_denied.status_code == 403 and b"audit-target-v1" not in carol_denied.content
        with psycopg.connect(settings.dsn("api"), autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO product.resource_grants (resource_id, user_id, capability)
                VALUES (%s, %s, 'read')
                ON CONFLICT DO NOTHING
                """,
                (round_id, USER_BOB),
            )
        bob_read = httpx.get(api + f"/rounds/{round_id}", headers=headers(bob), timeout=10)
        bob_review = httpx.put(
            api + f"/rounds/{round_id}/reviews",
            json={"item_key": body["findings"][0]["item_key"], "judgment": "nope", "rationale": "", "next_step": "", "version": 0},
            headers=headers(bob),
            timeout=10,
        )
        report["bob_read_not_review"] = bob_read.status_code == 200 and bob_review.status_code == 403
        review = httpx.put(
            api + f"/rounds/{round_id}/reviews",
            json={
                "item_key": body["findings"][0]["item_key"],
                "judgment": "需修改",
                "rationale": "补充尺寸",
                "next_step": "重传",
                "version": 0,
            },
            headers=headers(alice),
            timeout=10,
        )
        machine = review.json()["findings"][0]["status"]
        conflict = httpx.put(
            api + f"/rounds/{round_id}/reviews",
            json={"item_key": body["findings"][0]["item_key"], "judgment": "覆盖", "rationale": "", "next_step": "", "version": 0},
            headers=headers(alice),
            timeout=10,
        )
        report["review"] = (
            review.status_code == 200
            and machine == "PASS"
            and review.json()["findings"][0]["review"]["judgment"] == "需修改"
            and conflict.status_code == 409
        )
        newer = upload(alice, "report.xlsx", b"audit-target-v2", file_id=target["file_id"])
        resubmit = httpx.post(
            api + f"/rounds/{round_id}/resubmit",
            json={"idempotency_key": resubmit_key, "items": [{"file_version_id": newer["id"], "role": "target"}]},
            headers=headers(alice),
            timeout=10,
        )
        new_id = resubmit.json()["id"]
        new_body = wait_round(alice, new_id)
        old = httpx.get(api + f"/rounds/{round_id}", headers=headers(alice), timeout=10).json()
        old_file = httpx.get(
            api + f"/rounds/{round_id}/files/{target['file_id']}/versions/{target['id']}/download",
            headers=headers(alice),
            timeout=10,
        )
        report["resubmit"] = (
            resubmit.status_code == 201
            and new_id != round_id
            and new_body.get("predecessor_id") == round_id
            and old["findings"][0]["review"]["judgment"] == "需修改"
            and old_file.content == b"audit-target-v1"
        )
        with urllib.request.urlopen(env["PPAP_WEB_URL"], timeout=5) as response:
            html = response.read().decode()
        report["web_hooks"] = all(
            marker in html
            for marker in (
                'data-testid="submit-audit"',
                'data-testid="round-id"',
                'data-testid="findings"',
                'data-testid="save-review"',
                'data-testid="resubmit-audit"',
                'data-testid="locator-gap"',
                'type="file" multiple',
            )
        )
        report["ok"] = all(
            report[key] is True
            for key in (
                "draft_open",
                "accept_idempotent",
                "worker_completed",
                "multi_file_coverage",
                "open_original",
                "lease_reclaim",
                "carol_denied",
                "bob_read_not_review",
                "review",
                "resubmit",
                "web_hooks",
            )
        )
    except Exception as exc:
        report["error"] = type(exc).__name__ + ":" + str(exc)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "up", "down", "restart", "status", "verify", "migrate", "seed-auth", "verify-auth", "verify-uploads", "verify-audit"])
    args = parser.parse_args()
    if args.command == "install":
        install()
        print(json.dumps({"status": "installed"}))
        return 0
    if args.command == "up":
        reexec_in_venv()
        up()
        return 0
    if args.command == "down":
        down()
        return 0
    if args.command == "restart":
        reexec_in_venv()
        restart()
        return 0
    if args.command == "status":
        print(json.dumps(status()))
        return 0
    if args.command == "migrate":
        reexec_in_venv()
        env = child_env()
        start_postgres(env)
        os.environ.update({k: v for k, v in env.items() if k.startswith(("PPAP_", "LLM_", "PYTHONPATH"))})
        bootstrap_roles(env)
        migrate(env)
        print(json.dumps({"status": "migrated"}))
        return 0
    if args.command == "seed-auth":
        reexec_in_venv()
        return seed_auth()
    if args.command == "verify-auth":
        reexec_in_venv()
        return verify_auth()
    if args.command == "verify-uploads":
        reexec_in_venv()
        return verify_uploads()
    if args.command == "verify-audit":
        reexec_in_venv()
        return verify_audit()
    reexec_in_venv()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
