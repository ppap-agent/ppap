#!/usr/bin/env python3
"""Canonical install, quality, test, production build, deploy, and backup entry."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.paths import git_sha, pythonpath

DIST = ROOT / "dist"
WEB = ROOT / "apps" / "web"
IMAGE_BUILDS = (
    ("api", "deploy/app/Dockerfile", ("PPAP_SERVICE=api",)),
    ("worker", "deploy/app/Dockerfile", ("PPAP_SERVICE=worker",)),
    ("web", "deploy/web/Dockerfile", ()),
)
COMPOSE_FILE = ROOT / "deploy" / "compose.yaml"
COMPOSE_ENV = ROOT / "deploy" / "compose.env.example"
BACKUP_ROOT = DIST / "backups"
APP_SERVICES = ("api", "worker", "web")
OBJECT_STORE_SERVICE = "silo"
OBJECT_STORE_VOLUME = "silo-data"
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_COMPOSE_ENV_COMMANDS = {"deploy", "upgrade", "undeploy", "backup", "restore", "rollback"}


def compose_env_file() -> Path:
    """Env file for compose interpolation and init. Default is the CI fixture."""
    raw = os.environ.get("PPAP_COMPOSE_ENV", "").strip()
    path = Path(raw) if raw else COMPOSE_ENV
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"compose env file not found: {path}")
    return path


def compose_file() -> Path:
    """Compose file for deploy/backup. Default is the bundled production overlay."""
    raw = os.environ.get("PPAP_COMPOSE_FILE", "").strip()
    path = Path(raw) if raw else COMPOSE_FILE
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"compose file not found: {path}")
    return path


def _compose_has_service(name: str, path: Path | None = None) -> bool:
    text = (path or compose_file()).read_text()
    marker = f"  {name}:"
    for line in text.splitlines():
        if line == marker or line.startswith(marker + " ") or line.startswith(marker + "\t"):
            if line.startswith("  ") and not line.startswith("    "):
                return True
    return False


def take_compose_env_args(argv: list[str]) -> list[str]:
    """Honor `--env-file` and `--compose-file`. Remaining args are unchanged."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--env-file":
            if i + 1 >= len(argv):
                raise ValueError("--env-file requires a path")
            os.environ["PPAP_COMPOSE_ENV"] = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--env-file="):
            os.environ["PPAP_COMPOSE_ENV"] = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--compose-file":
            if i + 1 >= len(argv):
                raise ValueError("--compose-file requires a path")
            os.environ["PPAP_COMPOSE_FILE"] = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--compose-file="):
            os.environ["PPAP_COMPOSE_FILE"] = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def run(command: list[str], *, extra_env: dict[str, str] | None = None, cwd: Path | None = None) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath(env.get("PYTHONPATH", ""))
    if extra_env:
        env.update(extra_env)
    return subprocess.call(command, cwd=str(cwd or ROOT), env=env)


def cmd_install() -> int:
    return run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"])


def cmd_quality() -> int:
    check = subprocess.call(
        ["git", "show", "--format=", "--check", "HEAD", "--", ".", ":!assets", ":!tests/fixtures"],
        cwd=str(ROOT),
    )
    compiled = run([sys.executable, "-m", "compileall", "-q", str(ROOT / "tools")])
    return check or compiled


def cmd_test(argv: list[str]) -> int:
    if argv[:1] == ["pytest"] or (argv and argv[0].startswith("tests/corpus")):
        args = argv[1:] if argv[:1] == ["pytest"] else argv
        return run([sys.executable, "-m", "pytest", *args])
    if argv[:1] == ["e2e"]:
        return run([sys.executable, "-m", "unittest", "tests.e2e.test_web_audit", "-v"])
    if argv[:1] == ["schema"]:
        return run([sys.executable, "-m", "unittest", "tests.schema.test_upgrade", "-v"])
    if argv[:1] == ["deploy"]:
        return run([sys.executable, "-m", "unittest", "tests.deploy.test_runtime", "-v"])
    if argv[:1] == ["backup"]:
        return run([sys.executable, "-m", "unittest", "tests.deploy.test_backup", "-v"])
    targets = argv or ["discover", "-s", "tests/unit", "-v"]
    return run([sys.executable, "-m", "unittest", *targets])


def _write_identity(payload: dict) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    path = DIST / "identity.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _copy_web(sha: str) -> Path:
    dest = DIST / "web"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(WEB, dest, ignore=shutil.ignore_patterns("runtime-config.json"))
    (dest / "build-identity.json").write_text(json.dumps({"git_sha": sha}) + "\n")
    return dest


def _docker_build(sha: str) -> dict[str, str]:
    images: dict[str, str] = {}
    enabled = os.environ.get("PPAP_BUILD_IMAGES", "").strip().lower() in {"1", "true", "yes"}
    if not enabled or not shutil.which("docker"):
        return images
    for name, dockerfile, extra_args in IMAGE_BUILDS:
        tag = f"ppap-{name}:{sha}"
        command = [
            "docker",
            "build",
            "-f",
            str(ROOT / dockerfile),
            "--build-arg",
            f"PPAP_BUILD_SHA={sha}",
        ]
        for argument in extra_args:
            command.extend(["--build-arg", argument])
        command.extend(["-t", tag, str(ROOT)])
        code = run(command)
        if code != 0:
            raise SystemExit(code)
        images[name] = tag
    return images


def _product_pythonpath() -> None:
    for part in pythonpath("").split(os.pathsep):
        if part and part not in sys.path:
            sys.path.insert(0, part)


def cmd_bootstrap() -> int:
    _product_pythonpath()
    from tools.bootstrap import bootstrap_roles

    try:
        bootstrap_roles(dict(os.environ))
    except Exception as exc:
        print(f"bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "bootstrapped"}))
    return 0


def _ensure_bucket() -> None:
    from ppap_core.settings import Settings
    from ppap_core.storage import ensure_bucket

    last: Exception | None = None
    for _ in range(20):
        try:
            ensure_bucket(Settings())
            return
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(f"object store unavailable: {last}")


def cmd_migrate() -> int:
    _product_pythonpath()
    import psycopg

    from ppap_core.settings import Settings
    from tools.migrate import apply_migrations, latest_version

    settings = Settings()
    dsn = (
        f"postgresql://{settings.pg_migrator_user}:{settings.pg_migrator_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_database}"
    )
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            applied = apply_migrations(conn)
    except Exception as exc:
        print(f"migrate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "migrated", "applied": applied, "latest": latest_version()}))
    return 0


def cmd_provision() -> int:
    code = cmd_bootstrap()
    if code != 0:
        return code
    code = cmd_migrate()
    if code != 0:
        return code
    try:
        _ensure_bucket()
    except Exception as exc:
        print(f"provision failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "provisioned"}))
    return 0


def cmd_build() -> int:
    sha = git_sha() or "unknown"
    _copy_web(sha)
    images = _docker_build(sha)
    payload = {
        "git_sha": sha,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "web": "dist/web",
        "images": images,
    }
    _write_identity(payload)
    print(json.dumps({"status": "built", "identity": "dist/identity.json", **payload}))
    return 0


def _compose_argv(sha: str, *args: str) -> tuple[list[str], dict[str, str]]:
    env_file = compose_env_file()
    compose = compose_file()
    env = dict(os.environ)
    env["PPAP_BUILD_SHA"] = sha
    env["PYTHONPATH"] = pythonpath(env.get("PYTHONPATH", ""))
    env["PPAP_COMPOSE_ENV_FILE"] = str(env_file)
    return (
        [
            "docker",
            "compose",
            "-f",
            str(compose),
            "--env-file",
            str(env_file),
            *args,
        ],
        env,
    )


def _compose(sha: str, *args: str) -> int:
    try:
        command, env = _compose_argv(sha, *args)
    except FileNotFoundError as exc:
        print(f"compose env failed: {exc}", file=sys.stderr)
        return 1
    return subprocess.call(command, cwd=str(ROOT), env=env)


def _pg_admin_run(
    sha: str,
    script: str,
    *,
    stdin: bytes | None = None,
    stdout: int | object | None = subprocess.PIPE,
) -> subprocess.CompletedProcess[bytes]:
    if _compose_has_service("postgres"):
        return _compose_run(sha, "exec", "-T", "postgres", "sh", "-c", script, stdin=stdin, stdout=stdout)
    return _compose_run(
        sha,
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "--entrypoint",
        "sh",
        "init",
        "-c",
        script,
        stdin=stdin,
        stdout=stdout,
    )


def _pg_dump_script(database: str) -> str:
    if _compose_has_service("postgres"):
        return f'pg_dump -U "$POSTGRES_USER" --no-owner --format=plain {database}'
    return (
        'PGPASSWORD="$PPAP_PG_BOOTSTRAP_PASSWORD" pg_dump '
        '-h "$PPAP_PG_HOST" -p "$PPAP_PG_PORT" -U "$PPAP_PG_BOOTSTRAP_USER" '
        f"--no-owner --format=plain {database}"
    )


def _psql_admin_script(sql: str) -> str:
    if _compose_has_service("postgres"):
        return f'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "{sql}"'
    return (
        'PGPASSWORD="$PPAP_PG_BOOTSTRAP_PASSWORD" psql '
        '-h "$PPAP_PG_HOST" -p "$PPAP_PG_PORT" -U "$PPAP_PG_BOOTSTRAP_USER" '
        f'-d postgres -v ON_ERROR_STOP=1 -c "{sql}"'
    )


def _psql_database_script(database: str) -> str:
    if _compose_has_service("postgres"):
        return f'psql -U "$POSTGRES_USER" -d {database} -v ON_ERROR_STOP=1'
    return (
        'PGPASSWORD="$PPAP_PG_BOOTSTRAP_PASSWORD" psql '
        '-h "$PPAP_PG_HOST" -p "$PPAP_PG_PORT" -U "$PPAP_PG_BOOTSTRAP_USER" '
        f"-d {database} -v ON_ERROR_STOP=1"
    )


def _wait_http(url: str, *, timeout: int = 60) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read().decode()
                if response.status == 200:
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        return {"body": body, "status": response.status}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = str(exc)
        time.sleep(1)
    raise TimeoutError(f"timeout waiting for {url}: {last}")


def _compose_envfile() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in compose_env_file().read_text().splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        values[key] = value
    return values


def _sql_ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"invalid identifier: {name}")
    return name


def _object_store_run(
    sha: str,
    *command: str,
    stdin: bytes | None = None,
    stdout: int | object | None = subprocess.PIPE,
    entrypoint: str = "tar",
) -> subprocess.CompletedProcess[bytes]:
    """Read or write Silo `/data`. Classic ubi-micro has `sh`/`mcli`/`curl`, not `tar`."""
    return _compose_run(
        sha,
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "--entrypoint",
        entrypoint,
        "-v",
        f"{OBJECT_STORE_VOLUME}:/data",
        "init",
        *command,
        stdin=stdin,
        stdout=stdout,
    )


def _compose_run(
    sha: str,
    *args: str,
    stdin: bytes | None = None,
    stdout: int | object | None = subprocess.PIPE,
) -> subprocess.CompletedProcess[bytes]:
    try:
        command, env = _compose_argv(sha, *args)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args=["docker", "compose"], returncode=1, stdout=b"", stderr=str(exc).encode())
    return subprocess.run(
        command,
        cwd=str(ROOT),
        env=env,
        input=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        check=False,
    )


def _proc_error(action: str, proc: subprocess.CompletedProcess[bytes]) -> int:
    err = (proc.stderr or b"").decode(errors="replace")[-4000:]
    print(f"{action} failed: exit {proc.returncode}: {err}", file=sys.stderr)
    return 1


def _pause_app(sha: str) -> int:
    return _compose(sha, "stop", *APP_SERVICES)


def _resume_app(sha: str) -> int:
    code = _compose(sha, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "90", *APP_SERVICES)
    if code != 0:
        return code
    try:
        health = _wait_http("http://127.0.0.1:8080/health", timeout=90)
        ready = _wait_http("http://127.0.0.1:8080/ready", timeout=90)
    except Exception as exc:
        _compose(sha, "logs")
        print(f"resume failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if health.get("status") != "alive" or ready.get("status") != "ready":
        _compose(sha, "logs")
        print(f"resume failed: health={health!r} ready={ready!r}", file=sys.stderr)
        return 1
    return 0


def _backup_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def latest_backup() -> Path:
    if not BACKUP_ROOT.exists():
        raise FileNotFoundError("no backups")
    candidates = sorted(
        path
        for path in BACKUP_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith(".") and (path / "manifest.json").is_file()
    )
    if not candidates:
        raise FileNotFoundError("no backups")
    return candidates[-1]


def _require_backup_dir(path: Path) -> Path:
    resolved = path if path.is_absolute() else (ROOT / path)
    if not (resolved / "manifest.json").is_file() or not (resolved / "postgres.sql").is_file() or not (resolved / "objects.tar.gz").is_file():
        raise FileNotFoundError(f"incomplete backup: {resolved}")
    return resolved


def cmd_backup() -> int:
    if not shutil.which("docker"):
        print("backup failed: docker is required", file=sys.stderr)
        return 1
    sha = git_sha() or os.environ.get("PPAP_BUILD_SHA") or "unknown"
    env = _compose_envfile()
    try:
        database = _sql_ident(env["PPAP_PG_DATABASE"])
    except (KeyError, ValueError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUP_ROOT / stamp
    tmp = BACKUP_ROOT / f".{stamp}.tmp"
    suffix = 1
    while dest.exists() or tmp.exists():
        suffix += 1
        dest = BACKUP_ROOT / f"{stamp}-{suffix}"
        tmp = BACKUP_ROOT / f".{stamp}-{suffix}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    paused = False
    try:
        code = _pause_app(sha)
        if code != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return code
        paused = True
        dump = tmp / "postgres.sql"
        with dump.open("wb") as fh:
            proc = _pg_admin_run(sha, _pg_dump_script(database), stdout=fh)
        if proc.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return _proc_error("backup postgres", proc)
        objects = tmp / "objects.tar.gz"
        with objects.open("wb") as fh:
            proc = _object_store_run(sha, "-C", "/data", "-czf", "-", ".", stdout=fh)
        if proc.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return _proc_error("backup objects", proc)
        manifest = {
            "git_sha": sha,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "postgres": "postgres.sql",
            "objects": "objects.tar.gz",
            "database": database,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        tmp.rename(dest)
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"backup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if paused:
            resumed = _resume_app(sha)
            if resumed != 0:
                return resumed
    print(json.dumps({"status": "backed_up", "backup": _backup_label(dest), **manifest}))
    return 0


def _restore_dir(path: Path) -> int:
    if not shutil.which("docker"):
        print("restore failed: docker is required", file=sys.stderr)
        return 1
    sha = git_sha() or os.environ.get("PPAP_BUILD_SHA") or "unknown"
    env = _compose_envfile()
    try:
        backup = _require_backup_dir(path)
        database = _sql_ident(env["PPAP_PG_DATABASE"])
        migrator = _sql_ident(env["PPAP_PG_MIGRATOR_USER"])
    except (KeyError, ValueError, FileNotFoundError) as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    dump = backup / "postgres.sql"
    objects = backup / "objects.tar.gz"
    paused = False
    try:
        code = _pause_app(sha)
        if code != 0:
            return code
        paused = True
        terminate = (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{database}' AND pid <> pg_backend_pid();"
        )
        proc = _pg_admin_run(sha, _psql_admin_script(terminate))
        if proc.returncode != 0:
            return _proc_error("restore terminate", proc)
        proc = _pg_admin_run(sha, _psql_admin_script(f"DROP DATABASE IF EXISTS {database};"))
        if proc.returncode != 0:
            return _proc_error("restore drop", proc)
        proc = _pg_admin_run(sha, _psql_admin_script(f"CREATE DATABASE {database} OWNER {migrator};"))
        if proc.returncode != 0:
            return _proc_error("restore create", proc)
        proc = _pg_admin_run(sha, _psql_database_script(database), stdin=dump.read_bytes())
        if proc.returncode != 0:
            return _proc_error("restore postgres", proc)
        proc = _object_store_run(sha, "-c", "rm -rf /data/.[!.]* /data/*", entrypoint="sh")
        if proc.returncode != 0:
            return _proc_error("restore wipe objects", proc)
        proc = _object_store_run(sha, "-C", "/data", "-xzf", "-", stdin=objects.read_bytes())
        if proc.returncode != 0:
            return _proc_error("restore objects", proc)
        proc = _compose_run(sha, "restart", OBJECT_STORE_SERVICE)
        if proc.returncode != 0:
            return _proc_error("restore restart silo", proc)
    except Exception as exc:
        print(f"restore failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if paused:
            resumed = _resume_app(sha)
            if resumed != 0:
                return resumed
    print(json.dumps({"status": "restored", "backup": _backup_label(backup)}))
    return 0


def cmd_restore(argv: list[str]) -> int:
    if not argv:
        print("restore failed: backup directory required", file=sys.stderr)
        return 1
    return _restore_dir(Path(argv[0]))


def cmd_rollback() -> int:
    try:
        path = latest_backup()
    except FileNotFoundError as exc:
        print(f"rollback failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "rolling_back", "backup": _backup_label(path)}))
    return _restore_dir(path)


def cmd_deploy() -> int:
    if not shutil.which("docker"):
        print("deploy failed: docker is required", file=sys.stderr)
        return 1
    sha = git_sha() or "unknown"
    os.environ["PPAP_BUILD_IMAGES"] = "1"
    os.environ["PPAP_BUILD_SHA"] = sha
    if cmd_build() != 0:
        return 1
    code = _compose(sha, "up", "-d", "--build", "--wait", "--wait-timeout", "180")
    if code != 0:
        _compose(sha, "logs")
        return code
    try:
        version = _wait_http("http://127.0.0.1:8080/version")
        health = _wait_http("http://127.0.0.1:8080/health")
        ready = _wait_http("http://127.0.0.1:8080/ready", timeout=90)
    except Exception as exc:
        _compose(sha, "logs")
        print(f"deploy failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "deployed", "git_sha": sha, "version": version, "health": health, "ready": ready}))
    if version.get("git_sha") != sha:
        print(f"deploy failed: /version git_sha {version.get('git_sha')!r} != {sha!r}", file=sys.stderr)
        return 1
    if health.get("status") != "alive":
        print(f"deploy failed: /health {health!r}", file=sys.stderr)
        return 1
    if ready.get("status") != "ready":
        print(f"deploy failed: /ready {ready!r}", file=sys.stderr)
        return 1
    return 0


def cmd_undeploy() -> int:
    if not shutil.which("docker"):
        print("undeploy failed: docker is required", file=sys.stderr)
        return 1
    sha = git_sha() or os.environ.get("PPAP_BUILD_SHA") or "unknown"
    return _compose(sha, "down", "-v", "--remove-orphans")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "install",
            "quality",
            "test",
            "build",
            "bootstrap",
            "migrate",
            "provision",
            "deploy",
            "upgrade",
            "undeploy",
            "backup",
            "restore",
            "rollback",
        ],
    )
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    rest = list(args.args)
    if rest[:1] == ["--"]:
        rest = rest[1:]
    if args.command in _COMPOSE_ENV_COMMANDS:
        try:
            rest = take_compose_env_args(rest)
        except ValueError as exc:
            print(f"{args.command} failed: {exc}", file=sys.stderr)
            return 1
    if args.command == "install":
        return cmd_install()
    if args.command == "quality":
        return cmd_quality()
    if args.command == "test":
        return cmd_test(rest)
    if args.command == "bootstrap":
        return cmd_bootstrap()
    if args.command == "migrate":
        return cmd_migrate()
    if args.command == "provision":
        return cmd_provision()
    if args.command == "deploy" or args.command == "upgrade":
        return cmd_deploy()
    if args.command == "undeploy":
        return cmd_undeploy()
    if args.command == "backup":
        return cmd_backup()
    if args.command == "restore":
        return cmd_restore(rest)
    if args.command == "rollback":
        return cmd_rollback()
    return cmd_build()


if __name__ == "__main__":
    raise SystemExit(main())
