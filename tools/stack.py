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
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]
LOCAL = ROOT / ".local" / "stack"
BIN = ROOT / ".local" / "bin"
VENV = ROOT / ".venv"
WEB = ROOT / "apps" / "web"
MIGRATIONS = ROOT / "migrations"
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
    parts = [
        str(ROOT / "packages"),
        str(ROOT / "services" / "api"),
        str(ROOT / "services" / "worker"),
    ]
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


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


def git_sha() -> str:
    forced = os.environ.get("PPAP_BUILD_SHA", "").strip()
    if forced:
        return forced
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def child_env() -> dict[str, str]:
    env = map_llm_names(dict(os.environ))
    sha = git_sha()
    env["PYTHONPATH"] = pythonpath()
    env["PPAP_PROFILE"] = env.get("PPAP_PROFILE") or "isolated-dev"
    env["PPAP_BUILD_SHA"] = sha
    env["PPAP_WEB_URL"] = env.get("PPAP_WEB_URL") or "http://127.0.0.1:4173"
    env["PPAP_VERSION_URL"] = env.get("PPAP_VERSION_URL") or "http://127.0.0.1:8080/version"
    env["PPAP_PUBLIC_API_URL"] = env.get("PPAP_PUBLIC_API_URL") or "http://127.0.0.1:8080"
    env["PPAP_ENVIRONMENT"] = env.get("PPAP_ENVIRONMENT") or "isolated-dev"
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
    subprocess.check_call([str(venv_python()), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])


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
    import psycopg
    from psycopg import sql

    secret = env.get("PPAP_PG_API_PASSWORD", "ppap_isolated_dev")
    roles = {
        env.get("PPAP_PG_MIGRATOR_USER", "ppap_migrator"): env.get("PPAP_PG_MIGRATOR_PASSWORD", secret),
        env.get("PPAP_PG_API_USER", "ppap_api"): env.get("PPAP_PG_API_PASSWORD", secret),
        env.get("PPAP_PG_WORKER_USER", "ppap_worker"): env.get("PPAP_PG_WORKER_PASSWORD", secret),
    }
    user = env.get("PPAP_PG_BOOTSTRAP_USER", "ppap")
    password = env.get("PPAP_PG_BOOTSTRAP_PASSWORD", "ppap_isolated_dev")
    host = env.get("PPAP_PG_HOST", "127.0.0.1")
    port = env.get("PPAP_PG_PORT", "5432")
    database = env.get("PPAP_PG_DATABASE", "ppap")
    migrator = env.get("PPAP_PG_MIGRATOR_USER", "ppap_migrator")
    with psycopg.connect(f"postgresql://{user}:{password}@{host}:{port}/postgres", autocommit=True) as conn:
        for role, role_password in roles.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if exists:
                conn.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(role_password))
                )
            else:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(role_password))
                )
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(migrator)))


def migrate(env: dict[str, str]) -> None:
    import psycopg

    sys.path[:0] = [str(ROOT / "packages")]
    from ppap_core.settings import Settings

    settings = Settings()
    user = env.get("PPAP_PG_MIGRATOR_USER", settings.pg_migrator_user)
    password = env.get("PPAP_PG_MIGRATOR_PASSWORD", settings.pg_migrator_password)
    dsn = (
        f"postgresql://{user}:{password}@{settings.pg_host}:{settings.pg_port}/{settings.pg_database}"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS product")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product.schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM product.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            version = path.name
            if version in applied:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO product.schema_migrations (version) VALUES (%s)", (version,))


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
        [py, "-m", "http.server", env.get("PPAP_WEB_PORT", "4173"), "--bind", env.get("PPAP_WEB_BIND", "127.0.0.1")],
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
    print(json.dumps({"ok": True, "files": files, "s02_login": "NOT_PROVEN"}))
    return 0


def verify_auth() -> int:
    import httpx
    from ppap_core.fixtures import RESOURCE_A

    env = child_env()
    report = {"ok": False, "s02_login": "NOT_PROVEN"}
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
    report = {"ok": False, "s03_history": "NOT_PROVEN"}
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
            for key in ("alice_upload", "history_preserved", "carol_denied", "immutable_write", "ssrf_blocked", "staging_reaped", "web_hooks")
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def verify_engine() -> int:
    import hashlib
    import httpx
    from openpyxl import Workbook
    from ppap_core.corpus import corpus_status

    env = child_env()
    report = {"ok": False, "corpus": corpus_status()["state"], "s01_audit": "NOT_PROVEN"}
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

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.xlsx"
            Workbook().save(path)
            body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        alice, carol = mint("alice"), mint("carol")
        created = httpx.post(
            api + "/files",
            json={"original_filename": "sample.xlsx", "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "byte_size": len(body)},
            headers=headers(alice),
            timeout=10,
        )
        file_id, version_id = created.json()["file_id"], created.json()["id"]
        httpx.put(api + f"/files/{file_id}/versions/{version_id}/blob", content=body, headers=headers(alice), timeout=10)
        ready = httpx.post(api + f"/files/{file_id}/versions/{version_id}/finalize", headers=headers(alice), timeout=10)
        queued = httpx.post(
            api + "/isolated-dev/execute-audit",
            json={
                "file_id": file_id,
                "version_id": version_id,
                "document_type": "不是已登记的资料类型",
                "product_rule_version_id": "isolated-dev:unregistered",
                "engine_rule_id": "unregistered",
            },
            headers=headers(alice),
            timeout=10,
        )
        job_id = queued.json()["id"]
        deadline = time.time() + 60
        job = None
        while time.time() < deadline:
            job = httpx.get(api + f"/isolated-dev/jobs/{job_id}", headers=headers(alice), timeout=10)
            if job.status_code == 200 and job.json().get("status") in {"succeeded", "failed"}:
                break
            time.sleep(1)
        result = (job.json().get("result") or {}) if job is not None else {}
        findings = result.get("findings") or []
        denied = httpx.get(api + f"/isolated-dev/jobs/{job_id}", headers=headers(carol), timeout=10)
        report["alice_execute"] = (
            created.status_code == 201
            and ready.status_code == 200
            and ready.json().get("sha256") == digest
            and queued.status_code == 202
            and job is not None
            and job.status_code == 200
            and job.json().get("status") == "succeeded"
            and result.get("sha256") == digest
            and result.get("file_version_id") == version_id
            and bool(findings)
            and all(item.get("status") in {"PASS", "NG", "ERR"} for item in findings)
            and any(item.get("status") == "ERR" for item in findings)
        )
        report["carol_denied"] = denied.status_code == 403 and denied.json() == {"error": "access_denied"}
        report["corpus_unproven"] = report["corpus"] == "NOT_PROVEN"
        report["ok"] = all(report[key] is True for key in ("alice_execute", "carol_denied", "corpus_unproven"))
    except Exception as exc:
        report["error"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def verify_drafts() -> int:
    import hashlib
    import httpx
    from openpyxl import Workbook
    from ppap_core.fixtures import CATALOG_PROJECT

    env = child_env()
    report = {"ok": False, "s01_audit": "NOT_PROVEN"}
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

        def xlsx(name: str) -> tuple[bytes, str]:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / name
                Workbook().save(path)
                return path.read_bytes(), name

        def upload(cookie: str, filename: str, body: bytes) -> tuple[str, str, str]:
            created = httpx.post(
                api + "/files",
                json={"original_filename": filename, "content_type": "application/octet-stream", "byte_size": len(body)},
                headers=headers(cookie),
                timeout=10,
            )
            file_id, version_id = created.json()["file_id"], created.json()["id"]
            httpx.put(api + f"/files/{file_id}/versions/{version_id}/blob", content=body, headers=headers(cookie), timeout=10)
            ready = httpx.post(api + f"/files/{file_id}/versions/{version_id}/finalize", headers=headers(cookie), timeout=10)
            if created.status_code != 201 or ready.status_code != 200:
                raise RuntimeError("upload_failed")
            return file_id, version_id, ready.json()["sha256"]

        def wait_identified(cookie: str, draft_id: str) -> dict:
            deadline = time.time() + 60
            latest = None
            while time.time() < deadline:
                latest = httpx.get(api + f"/drafts/{draft_id}", headers=headers(cookie), timeout=10)
                if latest.status_code == 200:
                    items = latest.json().get("items") or []
                    if items and all(item.get("identification_status") != "pending" for item in items):
                        return latest.json()
                time.sleep(1)
            return latest.json() if latest is not None and latest.status_code == 200 else {}

        alice, carol = mint("alice"), mint("carol")
        catalog = httpx.get(api + f"/rule-versions?project_id={CATALOG_PROJECT}", headers=headers(alice), timeout=10)
        psw = next(item for item in catalog.json()["items"] if item["engine_rule_id"] == "零件提交保证书")
        target_body, _ = xlsx("PSW.xlsx")
        other_body, _ = xlsx("control-plan.xlsx")
        ref_body, _ = xlsx("ref.xlsx")
        t1 = upload(alice, "PSW.xlsx", target_body)
        t2 = upload(alice, "control-plan.xlsx", other_body)
        r1 = upload(alice, "ref.xlsx", ref_body)
        unknown = httpx.post(api + "/drafts", json={"project_id": "no-such-project"}, headers=headers(alice), timeout=10)
        created = httpx.post(api + "/drafts", json={"label": "prep", "project_id": CATALOG_PROJECT}, headers=headers(alice), timeout=10)
        draft_id = created.json()["id"]
        saved = httpx.put(
            api + f"/drafts/{draft_id}",
            json={
                "items": [
                    {"file_id": t1[0], "file_version_id": t1[1], "role": "target", "product_rule_version_id": psw["id"]},
                    {"file_id": t2[0], "file_version_id": t2[1], "role": "target"},
                    {"file_id": r1[0], "file_version_id": r1[1], "role": "reference"},
                ]
            },
            headers=headers(alice),
            timeout=10,
        )
        if saved.status_code != 200:
            raise RuntimeError("draft_replace_failed:" + str(saved.status_code))
        fetched = httpx.get(api + f"/drafts/{draft_id}", headers=headers(alice), timeout=10)
        identified = httpx.post(api + f"/drafts/{draft_id}/identify", headers=headers(alice), timeout=10)
        after_identify = wait_identified(alice, draft_id)
        kinds = []
        for job in identified.json().get("jobs") or []:
            body = httpx.get(api + f"/isolated-dev/jobs/{job['id']}", headers=headers(alice), timeout=10)
            kinds.append(body.json().get("kind"))
        mutated = dict(psw["content"])
        mutated["checks"] = [{"id": "rule_only_one", "description": "truncated"}]
        published = httpx.post(
            api + "/isolated-dev/rule-versions",
            json={
                "project_id": CATALOG_PROJECT,
                "engine_rule_id": psw["engine_rule_id"],
                "document_type": psw["document_type"],
                "label": psw["label"],
                "content": mutated,
            },
            headers=headers(alice),
            timeout=10,
        )
        after_publish = httpx.get(api + f"/drafts/{draft_id}", headers=headers(alice), timeout=10)
        r2_body, _ = xlsx("ref-new.xlsx")
        r2 = upload(alice, "ref-new.xlsx", r2_body)
        changed_ref = httpx.put(
            api + f"/drafts/{draft_id}",
            json={
                "items": [
                    {"file_id": t1[0], "file_version_id": t1[1], "role": "target", "product_rule_version_id": psw["id"]},
                    {"file_id": t2[0], "file_version_id": t2[1], "role": "target"},
                    {"file_id": r2[0], "file_version_id": r2[1], "role": "reference"},
                ]
            },
            headers=headers(alice),
            timeout=10,
        )
        txt_id = upload(alice, "notes.txt", b"not an audit file")
        unsupported_draft = httpx.post(api + "/drafts", json={"project_id": CATALOG_PROJECT}, headers=headers(alice), timeout=10)
        httpx.put(
            api + f"/drafts/{unsupported_draft.json()['id']}",
            json={"items": [{"file_id": txt_id[0], "file_version_id": txt_id[1], "role": "target"}]},
            headers=headers(alice),
            timeout=10,
        )
        httpx.post(api + f"/drafts/{unsupported_draft.json()['id']}/identify", headers=headers(alice), timeout=10)
        unsupported = wait_identified(alice, unsupported_draft.json()["id"])
        carol_denied = httpx.get(api + f"/drafts/{draft_id}", headers=headers(carol), timeout=10)
        carol_rule = httpx.get(api + f"/rule-versions/{psw['id']}", headers=headers(carol), timeout=10)
        with urllib.request.urlopen(env["PPAP_WEB_URL"], timeout=5) as response:
            html = response.read().decode()
        first = (after_identify.get("items") or [{}])[0]
        published_ok = published.status_code == 201 and published.json()["id"] != psw["id"]
        bound_unchanged = (
            after_publish.status_code == 200
            and after_publish.json()["items"][0]["product_rule_version_id"] == psw["id"]
            and after_publish.json()["items"][0]["bound_content_sha256"] == psw["content_sha256"]
            and after_publish.json()["items"][0]["bound_check_count"] == psw["check_count"]
            and after_publish.json()["items"][0]["bound_content_sha256"] != published.json().get("content_sha256")
        )
        report["unknown_project"] = unknown.status_code == 400 and unknown.json() == {"error": "unknown_project"}
        report["round_trip"] = (
            created.status_code == 201
            and saved.status_code == 200
            and fetched.status_code == 200
            and fetched.json()["items"] == saved.json()["items"]
            and fetched.json()["accepted"] is False
            and [item["role"] for item in fetched.json()["items"]] == ["target", "target", "reference"]
        )
        report["no_accept"] = fetched.json()["accepted"] is False and identified.json().get("accepted") is False and "audit.execute" not in kinds
        report["identify"] = (
            identified.status_code == 202
            and identified.json().get("accepted") is False
            and kinds == ["draft.identify", "draft.identify", "draft.identify"]
            and first.get("identification_status") == "matched"
            and first.get("matched_document_type") == "零件提交保证书"
            and first.get("product_rule_version_id") == psw["id"]
            and first.get("engine_rule_id") == "零件提交保证书"
            and first.get("product_rule_version_id") != first.get("engine_rule_id")
            and first.get("match_evidence", {}).get("rule_profile") is None
            and hashlib.sha256(target_body).hexdigest() == first.get("sha256")
        )
        report["unsupported"] = (unsupported.get("items") or [{}])[0].get("identification_status") == "unsupported"
        report["reference_change"] = (
            changed_ref.status_code == 200
            and changed_ref.json()["items"][0]["product_rule_version_id"] == psw["id"]
            and changed_ref.json()["items"][2]["file_version_id"] == r2[1]
        )
        report["rule_history"] = published_ok and bound_unchanged
        report["carol_denied"] = (
            carol_denied.status_code == 403
            and carol_denied.json() == {"error": "access_denied"}
            and carol_rule.status_code == 403
        )
        report["web_hooks"] = all(
            marker in html
            for marker in (
                'data-testid="audit-draft-id"',
                'data-testid="item-role"',
                'data-testid="rule-selector"',
                'data-testid="rule-version"',
                'data-testid="engine-rule-id"',
                'data-testid="identification-status"',
            )
        )
        report["ok"] = all(
            report[key] is True
            for key in (
                "unknown_project",
                "round_trip",
                "no_accept",
                "identify",
                "unsupported",
                "reference_change",
                "rule_history",
                "carol_denied",
                "web_hooks",
            )
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
        report["error_detail"] = str(exc)[:200]
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def verify_rounds() -> int:
    import httpx
    from openpyxl import Workbook
    from ppap_core.fixtures import CATALOG_PROJECT

    env = child_env()
    report = {"ok": False, "s01_audit": "NOT_PROVEN", "s04_live": "NOT_PROVEN"}
    worker_stopped = False
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

        def xlsx(name: str) -> bytes:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / name
                Workbook().save(path)
                return path.read_bytes()

        def upload(cookie: str, filename: str, body: bytes) -> tuple[str, str, str]:
            created = httpx.post(
                api + "/files",
                json={"original_filename": filename, "content_type": "application/octet-stream", "byte_size": len(body)},
                headers=headers(cookie),
                timeout=10,
            )
            file_id, version_id = created.json()["file_id"], created.json()["id"]
            httpx.put(api + f"/files/{file_id}/versions/{version_id}/blob", content=body, headers=headers(cookie), timeout=10)
            ready = httpx.post(api + f"/files/{file_id}/versions/{version_id}/finalize", headers=headers(cookie), timeout=10)
            if created.status_code != 201 or ready.status_code != 200:
                raise RuntimeError("upload_failed")
            return file_id, version_id, ready.json()["sha256"]

        def wait_round(cookie: str, round_id: str) -> dict:
            deadline = time.time() + 90
            latest = None
            while time.time() < deadline:
                latest = httpx.get(api + f"/rounds/{round_id}", headers=headers(cookie), timeout=10)
                if latest.status_code == 200 and latest.json().get("status") in {"succeeded", "failed"}:
                    return latest.json()
                time.sleep(1)
            return latest.json() if latest is not None and latest.status_code == 200 else {}

        def bound_draft(cookie: str, target: tuple[str, str, str], ref: tuple[str, str, str], rule_id: str) -> str:
            created = httpx.post(api + "/drafts", json={"project_id": CATALOG_PROJECT}, headers=headers(cookie), timeout=10)
            saved = httpx.put(
                api + f"/drafts/{created.json()['id']}",
                json={
                    "items": [
                        {
                            "file_id": target[0],
                            "file_version_id": target[1],
                            "role": "target",
                            "product_rule_version_id": rule_id,
                        },
                        {"file_id": ref[0], "file_version_id": ref[1], "role": "reference"},
                    ]
                },
                headers=headers(cookie),
                timeout=10,
            )
            if created.status_code != 201 or saved.status_code != 200:
                raise RuntimeError("draft_prepare_failed")
            return created.json()["id"]

        alice, carol = mint("alice"), mint("carol")
        catalog = httpx.get(api + f"/rule-versions?project_id={CATALOG_PROJECT}", headers=headers(alice), timeout=10)
        psw = next(item for item in catalog.json()["items"] if item["engine_rule_id"] == "零件提交保证书")
        target = upload(alice, "PSW.xlsx", xlsx("PSW.xlsx"))
        ref = upload(alice, "ref.xlsx", xlsx("ref.xlsx"))
        draft_id = bound_draft(alice, target, ref, psw["id"])
        key1, key2, fence_key = "k1-" + uuid.uuid4().hex, "k2-" + uuid.uuid4().hex, "fence-" + uuid.uuid4().hex
        first = httpx.post(
            api + f"/drafts/{draft_id}/accept",
            json={"idempotency_key": key1},
            headers=headers(alice),
            timeout=10,
        )
        if first.status_code != 201 or "id" not in first.json():
            raise RuntimeError("accept_failed:" + str(first.status_code))
        finished = wait_round(alice, first.json()["id"])
        replay = httpx.post(
            api + f"/drafts/{draft_id}/accept",
            json={"idempotency_key": key1},
            headers=headers(alice),
            timeout=10,
        )
        before_get = len(finished.get("jobs") or [])
        fetched = httpx.get(api + f"/rounds/{first.json()['id']}", headers=headers(alice), timeout=10)
        other = upload(alice, "other.xlsx", xlsx("other.xlsx"))
        httpx.put(
            api + f"/drafts/{draft_id}",
            json={
                "items": [
                    {
                        "file_id": other[0],
                        "file_version_id": other[1],
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    }
                ]
            },
            headers=headers(alice),
            timeout=10,
        )
        conflict = httpx.post(
            api + f"/drafts/{draft_id}/accept",
            json={"idempotency_key": key1},
            headers=headers(alice),
            timeout=10,
        )
        resubmit = httpx.post(
            api + f"/drafts/{draft_id}/accept",
            json={"idempotency_key": key2},
            headers=headers(alice),
            timeout=10,
        )
        old_after = httpx.get(api + f"/rounds/{first.json()['id']}", headers=headers(alice), timeout=10)
        retry = httpx.post(api + f"/rounds/{first.json()['id']}/retry", headers=headers(alice), timeout=10)
        carol_denied = httpx.get(api + f"/rounds/{first.json()['id']}", headers=headers(carol), timeout=10)
        stop_pid("worker")
        worker_stopped = True
        fence_draft = bound_draft(alice, target, ref, psw["id"])
        fenced = httpx.post(
            api + f"/drafts/{fence_draft}/accept",
            json={"idempotency_key": fence_key},
            headers=headers(alice),
            timeout=10,
        )
        job_id = (fenced.json().get("jobs") or [{}])[0].get("id")
        stale_claim = httpx.post(
            api + "/isolated-dev/claim-job",
            json={"job_id": job_id, "worker": "stale-worker"},
            headers=headers(alice),
            timeout=10,
        )
        httpx.post(api + "/isolated-dev/expire-job-lease", json={"job_id": job_id}, headers=headers(alice), timeout=10)
        live_claim = httpx.post(
            api + "/isolated-dev/claim-job",
            json={"job_id": job_id, "worker": "live-worker"},
            headers=headers(alice),
            timeout=10,
        )
        stale = httpx.post(
            api + "/isolated-dev/forge-job-result",
            json={"job_id": job_id, "worker": "stale-worker", "attempt": 1, "result": {"findings": [{"status": "PASS"}]}},
            headers=headers(alice),
            timeout=10,
        )
        httpx.post(api + "/isolated-dev/expire-job-lease", json={"job_id": job_id}, headers=headers(alice), timeout=10)
        spawn("worker", [str(venv_python()), "-m", "ppap_worker.main"], env)
        worker_stopped = False
        recovered = wait_round(alice, fenced.json()["id"])
        with urllib.request.urlopen(env["PPAP_WEB_URL"], timeout=5) as response:
            html = response.read().decode()
        first_findings = ((finished.get("results") or [{}])[0].get("findings") or [])
        report["accept_atomic"] = (
            first.status_code == 201
            and first.json()["status"] in {"accepted", "running", "succeeded"}
            and len(first.json()["jobs"]) == 1
            and first.json()["jobs"][0]["kind"] == "audit.execute"
            and [item["role"] for item in first.json()["inputs"]] == ["target", "reference"]
            and first.json()["inputs"][0]["sha256"] == target[2]
            and first.json()["inputs"][0]["product_rule_version_id"] == psw["id"]
        )
        report["idempotent"] = (
            replay.status_code == 200
            and replay.json()["id"] == first.json()["id"]
            and len(replay.json()["jobs"]) == 1
        )
        report["conflict"] = conflict.status_code == 409 and conflict.json() == {"error": "idempotency_conflict"}
        report["resubmit"] = (
            resubmit.status_code == 201
            and resubmit.json()["id"] != first.json()["id"]
            and old_after.json()["id"] == first.json()["id"]
            and old_after.json()["inputs"][0]["original_filename"] == "PSW.xlsx"
        )
        report["get_no_execute"] = fetched.status_code == 200 and len(fetched.json()["jobs"]) == before_get
        report["complete"] = (
            finished.get("id") == first.json()["id"]
            and finished.get("status") == "succeeded"
            and bool(first_findings)
            and all(item.get("status") in {"PASS", "NG", "ERR"} for item in first_findings)
        )
        report["retry_keeps_history"] = (
            retry.status_code == 200
            and retry.json()["id"] == first.json()["id"]
            and retry.json()["inputs"][0]["original_filename"] == "PSW.xlsx"
            and len(retry.json()["results"]) == len(finished.get("results") or [])
        )
        report["fencing"] = (
            fenced.status_code == 201
            and stale_claim.status_code == 200
            and stale_claim.json().get("attempt") == 1
            and live_claim.status_code == 200
            and live_claim.json().get("attempt") == 2
            and stale.status_code == 409
            and stale.json() == {"applied": False}
            and recovered.get("id") == fenced.json()["id"]
            and recovered.get("status") == "succeeded"
            and recovered.get("jobs")
            and recovered["jobs"][0]["attempt"] >= 3
        )
        report["carol_denied"] = carol_denied.status_code == 403 and carol_denied.json() == {"error": "access_denied"}
        report["web_hooks"] = all(
            marker in html
            for marker in (
                'data-testid="submit-audit"',
                'data-testid="round-id"',
                'data-testid="execution-state"',
            )
        )
        report["ok"] = all(
            report[key] is True
            for key in (
                "accept_atomic",
                "idempotent",
                "conflict",
                "resubmit",
                "get_no_execute",
                "complete",
                "retry_keeps_history",
                "fencing",
                "carol_denied",
                "web_hooks",
            )
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
        report["error_detail"] = str(exc)[:200]
    finally:
        if worker_stopped:
            spawn("worker", [str(venv_python()), "-m", "ppap_worker.main"], env)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def verify_web() -> int:
    import hashlib
    import httpx
    from openpyxl import Workbook
    from ppap_core.fixtures import CATALOG_PROJECT

    env = child_env()
    report = {"ok": False, "s01_audit": "NOT_PROVEN"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report["error"] = "playwright_missing"
        print(json.dumps(report, ensure_ascii=False))
        return 1
    try:
        api = env["PPAP_PUBLIC_API_URL"].rstrip("/")
        web = env["PPAP_WEB_URL"].rstrip("/")

        def mint(subject: str) -> str:
            response = httpx.post(api + "/isolated-dev/sessions", json={"subject": subject}, timeout=10)
            cookie = response.cookies.get("ppap_session")
            if response.status_code != 200 or not cookie:
                raise RuntimeError("fixture_session_failed")
            return cookie

        def headers(cookie: str) -> dict[str, str]:
            return {"Cookie": "ppap_session=" + cookie}

        def xlsx(name: str) -> bytes:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / name
                Workbook().save(path)
                return path.read_bytes()

        def upload(cookie: str, filename: str, body: bytes) -> tuple[str, str, str]:
            created = httpx.post(
                api + "/files",
                json={"original_filename": filename, "content_type": "application/octet-stream", "byte_size": len(body)},
                headers=headers(cookie),
                timeout=10,
            )
            file_id, version_id = created.json()["file_id"], created.json()["id"]
            httpx.put(api + f"/files/{file_id}/versions/{version_id}/blob", content=body, headers=headers(cookie), timeout=10)
            ready = httpx.post(api + f"/files/{file_id}/versions/{version_id}/finalize", headers=headers(cookie), timeout=10)
            if created.status_code != 201 or ready.status_code != 200:
                raise RuntimeError("upload_failed")
            return file_id, version_id, ready.json()["sha256"]

        alice, carol = mint("alice"), mint("carol")
        catalog = httpx.get(api + f"/rule-versions?project_id={CATALOG_PROJECT}", headers=headers(alice), timeout=10)
        psw = next(item for item in catalog.json()["items"] if item["engine_rule_id"] == "零件提交保证书")
        body = xlsx("PSW.xlsx")
        digest = hashlib.sha256(body).hexdigest()
        target = upload(alice, "PSW.xlsx", body)
        ref = upload(alice, "ref.xlsx", xlsx("ref.xlsx"))
        created = httpx.post(api + "/drafts", json={"project_id": CATALOG_PROJECT}, headers=headers(alice), timeout=10)
        httpx.put(
            api + f"/drafts/{created.json()['id']}",
            json={
                "items": [
                    {
                        "file_id": target[0],
                        "file_version_id": target[1],
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    },
                    {"file_id": ref[0], "file_version_id": ref[1], "role": "reference"},
                ]
            },
            headers=headers(alice),
            timeout=10,
        )
        accepted = httpx.post(
            api + f"/drafts/{created.json()['id']}/accept",
            json={"idempotency_key": "web-" + uuid.uuid4().hex},
            headers=headers(alice),
            timeout=10,
        )
        round_id = accepted.json()["id"]
        deadline = time.time() + 90
        latest = {}
        while time.time() < deadline:
            fetched = httpx.get(api + f"/rounds/{round_id}", headers=headers(alice), timeout=10)
            if fetched.status_code == 200 and fetched.json().get("status") in {"succeeded", "failed"}:
                latest = fetched.json()
                break
            time.sleep(1)
        round_url = f"{web}/?round={round_id}"
        with urllib.request.urlopen(web, timeout=5) as response:
            html = response.read().decode()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            def open_as(cookie: str, url: str):
                context = browser.new_context()
                context.add_cookies([{"name": "ppap_session", "value": cookie, "domain": "127.0.0.1", "path": "/"}])
                page = context.new_page()
                page.goto(url, wait_until="networkidle")
                return context, page
            first_ctx, page = open_as(alice, round_url)
            page.wait_for_function("() => document.querySelector('[data-testid=execution-state]')?.textContent === 'completed'", timeout=30000)
            round_shown = page.get_by_test_id("round-id").inner_text().strip()
            findings = page.get_by_test_id("finding")
            finding_count = findings.count()
            statuses = []
            for i in range(finding_count):
                row = findings.nth(i)
                statuses.append(row.get_by_test_id("finding-status").inner_text().strip())
                if not row.get_by_test_id("next-action").inner_text().strip():
                    raise RuntimeError("next_action_absent")
            with page.expect_download() as event:
                page.get_by_test_id("download-original").click()
            download = event.value
            downloaded = Path(download.path()).read_bytes()
            coverage = page.get_by_test_id("coverage").inner_text()
            first_ctx.close()
            return_ctx, returned = open_as(alice, round_url)
            returned.wait_for_function("() => document.querySelector('[data-testid=execution-state]')?.textContent === 'completed'", timeout=30000)
            returned_id = returned.get_by_test_id("round-id").inner_text().strip()
            return_ctx.close()
            carol_ctx, foreign = open_as(carol, round_url)
            foreign.wait_for_function("() => !document.querySelector('[data-testid=access-denied]').classList.contains('hidden')", timeout=15000)
            carol_findings = foreign.get_by_test_id("finding").count()
            carol_download = foreign.get_by_test_id("download-original").count()
            carol_ctx.close()
            browser.close()
        report["leave_and_return"] = round_shown == round_id and returned_id == round_id
        report["findings"] = (
            finding_count > 0
            and all(status in {"PASS", "NG", "ERR"} for status in statuses)
            and latest.get("coverage", {}).get("reference_count") == 1
            and "参考" in coverage
            and "已审通过" in coverage
        )
        report["original"] = hashlib.sha256(downloaded).hexdigest() == digest == target[2]
        report["carol_denied"] = carol_findings == 0 and carol_download == 0
        report["pwa_manifest"] = 'rel="manifest"' in html and "manifest.webmanifest" in html
        report["ok"] = all(
            report[key] is True for key in ("leave_and_return", "findings", "original", "carol_denied", "pwa_manifest")
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
        report["error_detail"] = str(exc)[:200]
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def verify_reviews() -> int:
    import httpx
    from openpyxl import Workbook
    from ppap_core.fixtures import CATALOG_PROJECT

    env = child_env()
    report = {"ok": False, "s05_live": "NOT_PROVEN"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report["error"] = "playwright_missing"
        print(json.dumps(report, ensure_ascii=False))
        return 1
    try:
        api = env["PPAP_PUBLIC_API_URL"].rstrip("/")
        web = env["PPAP_WEB_URL"].rstrip("/")

        def mint(subject: str) -> str:
            response = httpx.post(api + "/isolated-dev/sessions", json={"subject": subject}, timeout=10)
            cookie = response.cookies.get("ppap_session")
            if response.status_code != 200 or not cookie:
                raise RuntimeError("fixture_session_failed")
            return cookie

        def headers(cookie: str) -> dict[str, str]:
            return {"Cookie": "ppap_session=" + cookie}

        def xlsx(name: str) -> bytes:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / name
                Workbook().save(path)
                return path.read_bytes()

        def upload(cookie: str, filename: str, body: bytes) -> tuple[str, str, str]:
            created = httpx.post(
                api + "/files",
                json={"original_filename": filename, "content_type": "application/octet-stream", "byte_size": len(body)},
                headers=headers(cookie),
                timeout=10,
            )
            file_id, version_id = created.json()["file_id"], created.json()["id"]
            httpx.put(api + f"/files/{file_id}/versions/{version_id}/blob", content=body, headers=headers(cookie), timeout=10)
            ready = httpx.post(api + f"/files/{file_id}/versions/{version_id}/finalize", headers=headers(cookie), timeout=10)
            return file_id, version_id, ready.json()["sha256"]

        alice, carol = mint("alice"), mint("carol")
        catalog = httpx.get(api + f"/rule-versions?project_id={CATALOG_PROJECT}", headers=headers(alice), timeout=10)
        psw = next(item for item in catalog.json()["items"] if item["engine_rule_id"] == "零件提交保证书")
        target = upload(alice, "PSW.xlsx", xlsx("PSW.xlsx"))
        created = httpx.post(api + "/drafts", json={"project_id": CATALOG_PROJECT}, headers=headers(alice), timeout=10)
        httpx.put(
            api + f"/drafts/{created.json()['id']}",
            json={
                "items": [
                    {
                        "file_id": target[0],
                        "file_version_id": target[1],
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    }
                ]
            },
            headers=headers(alice),
            timeout=10,
        )
        accepted = httpx.post(
            api + f"/drafts/{created.json()['id']}/accept",
            json={"idempotency_key": "review-" + uuid.uuid4().hex},
            headers=headers(alice),
            timeout=10,
        )
        round_id = accepted.json()["id"]
        latest = {}
        deadline = time.time() + 90
        while time.time() < deadline:
            fetched_round = httpx.get(api + f"/rounds/{round_id}", headers=headers(alice), timeout=10)
            if fetched_round.status_code == 200 and fetched_round.json().get("status") in {"succeeded", "failed"}:
                latest = fetched_round.json()
                break
            time.sleep(1)
        if not latest:
            raise RuntimeError("round_not_finished")
        machine = latest.get("results")
        saved = httpx.post(
            api + f"/rounds/{round_id}/reviews",
            json={"judgment": "questioned", "reason": "need check", "next_action": "核对原件"},
            headers=headers(alice),
            timeout=10,
        )
        alice_again = mint("alice")
        fetched = httpx.get(api + f"/rounds/{round_id}", headers=headers(alice_again), timeout=10)
        conflict = httpx.post(
            api + f"/rounds/{round_id}/reviews",
            json={"judgment": "noted", "reason": "updated", "next_action": "keep watching", "expected_version": 1},
            headers=headers(alice),
            timeout=10,
        )
        stale = httpx.post(
            api + f"/rounds/{round_id}/reviews",
            json={"judgment": "noted", "reason": "stale", "next_action": "none", "expected_version": 1},
            headers=headers(alice),
            timeout=10,
        )
        resubmit = httpx.post(
            api + f"/drafts/{created.json()['id']}/accept",
            json={"idempotency_key": "resubmit-" + uuid.uuid4().hex},
            headers=headers(alice),
            timeout=10,
        )
        old = httpx.get(api + f"/rounds/{round_id}", headers=headers(alice), timeout=10)
        carol_denied = httpx.post(
            api + f"/rounds/{round_id}/reviews",
            json={"judgment": "noted", "reason": "no", "next_action": "none"},
            headers=headers(carol),
            timeout=10,
        )
        with urllib.request.urlopen(web, timeout=5) as response:
            html = response.read().decode()
        note = "browser-review-" + uuid.uuid4().hex
        round_url = f"{web}/?round={round_id}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            context.add_cookies([{"name": "ppap_session", "value": alice, "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            page.goto(round_url, wait_until="networkidle")
            page.wait_for_function(
                "() => document.querySelector('[data-testid=round-id]')?.textContent?.length > 0",
                timeout=30000,
            )
            before_findings = [
                page.get_by_test_id("finding").nth(i).get_by_test_id("finding-status").inner_text().strip()
                for i in range(page.get_by_test_id("finding").count())
            ]
            page.get_by_test_id("review-reason").fill(note)
            page.get_by_test_id("save-review").click()
            page.wait_for_function(
                "text => [...document.querySelectorAll('[data-testid=review-entry]')].some(el => el.textContent.includes(text))",
                arg=note,
                timeout=15000,
            )
            after_findings = [
                page.get_by_test_id("finding").nth(i).get_by_test_id("finding-status").inner_text().strip()
                for i in range(page.get_by_test_id("finding").count())
            ]
            context.close()
            returned_ctx = browser.new_context()
            returned_ctx.add_cookies([{"name": "ppap_session", "value": alice, "domain": "127.0.0.1", "path": "/"}])
            returned = returned_ctx.new_page()
            returned.goto(round_url, wait_until="networkidle")
            returned.wait_for_function(
                "text => [...document.querySelectorAll('[data-testid=review-entry]')].some(el => el.textContent.includes(text))",
                arg=note,
                timeout=15000,
            )
            returned_findings = [
                returned.get_by_test_id("finding").nth(i).get_by_test_id("finding-status").inner_text().strip()
                for i in range(returned.get_by_test_id("finding").count())
            ]
            returned_ctx.close()
            browser.close()
        bound = saved.json()["reviews"][0].get("result_id")
        report["saved"] = (
            saved.status_code == 200
            and saved.json()["results"] == machine
            and saved.json()["reviews"][0]["reason"] == "need check"
            and fetched.json()["reviews"][0]["judgment"] == "questioned"
            and bound == latest.get("current_result_id")
            and bound is not None
        )
        report["conflict"] = conflict.status_code == 200 and stale.status_code == 409 and stale.json() == {"error": "review_conflict"}
        report["resubmit_independent"] = (
            resubmit.status_code == 201
            and resubmit.json()["id"] != round_id
            and resubmit.json()["reviews"] == []
            and old.json()["reviews"][0]["reason"] == "updated"
            and old.json()["results"] == machine
        )
        report["carol_denied"] = carol_denied.status_code == 403
        report["web_hooks"] = all(
            marker in html
            for marker in ('data-testid="review-reason"', 'data-testid="save-review"', 'data-testid="review-conflict"')
        )
        report["browser_save_return"] = (
            before_findings == after_findings == returned_findings
            and all(status in {"PASS", "NG", "ERR"} for status in returned_findings)
            and bool(returned_findings)
        )
        report["ok"] = all(
            report[key] is True
            for key in ("saved", "conflict", "resubmit_independent", "carol_denied", "web_hooks", "browser_save_return")
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
        report["error_detail"] = str(exc)[:200]
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "up", "down", "restart", "status", "verify", "migrate", "seed-auth", "verify-auth", "verify-uploads", "verify-engine", "verify-drafts", "verify-rounds", "verify-web", "verify-reviews"])
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
    if args.command == "verify-engine":
        reexec_in_venv()
        return verify_engine()
    if args.command == "verify-drafts":
        reexec_in_venv()
        return verify_drafts()
    if args.command == "verify-rounds":
        reexec_in_venv()
        return verify_rounds()
    if args.command == "verify-web":
        reexec_in_venv()
        return verify_web()
    if args.command == "verify-reviews":
        reexec_in_venv()
        return verify_reviews()
    reexec_in_venv()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
