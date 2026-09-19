#!/usr/bin/env python3
"""Canonical install, quality, test, and production build entry."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
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
    identity = _write_identity(payload)
    print(json.dumps({"status": "built", "identity": "dist/identity.json", **payload}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "quality", "test", "build", "migrate"])
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    rest = list(args.args)
    if rest[:1] == ["--"]:
        rest = rest[1:]
    if args.command == "install":
        return cmd_install()
    if args.command == "quality":
        return cmd_quality()
    if args.command == "test":
        return cmd_test(rest)
    if args.command == "migrate":
        return cmd_migrate()
    return cmd_build()


if __name__ == "__main__":
    raise SystemExit(main())
