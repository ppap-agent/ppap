"""Copy the Feishu app_ticket that the legacy UAC still receives into the current store.

Transitional (deploy/tke/README.md「飞书票据：暂以旧版为来源」): until the event callback is switched to
the current API, Feishu pushes app_ticket only to the legacy UAC, which keeps it in Redis. This job
reads it read-only, has Feishu confirm it with a side-effect-free app_access_token exchange, and stores
it through the store's own write path without an event time (no event time is invented). Business code
keeps reading only its own store. Delete the CronJob after the callback switch.

Never prints the ticket, the Redis password or any token.
"""

import json
import os
import socket
import sys

import httpx
from product.composition import build_product_store
from product.settings import Settings

# Spring's JdkSerializationRedisSerializer key: AC ED 00 05 74 <u16 length> "feishu-app-ticket".
LEGACY_KEY = b"\xac\xed\x00\x05\x74\x00\x11feishu-app-ticket"
LEGACY_DATABASE = b"11"
APP_ACCESS_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token"


def report(result: str, **fields) -> None:
    print(json.dumps({"event": "legacy_ticket_sync", "result": result, **fields}), flush=True)


def command(stream, *args: bytes):
    stream.write(b"*%d\r\n" % len(args) + b"".join(b"$%d\r\n%s\r\n" % (len(arg), arg) for arg in args))
    stream.flush()
    line = stream.readline()
    kind, rest = line[:1], line[1:-2]
    if kind == b"-":
        raise RuntimeError("redis_error")
    if kind == b"$":
        size = int(rest)
        return None if size < 0 else stream.read(size + 2)[:-2]
    return rest


def legacy_ticket() -> str | None:
    with socket.create_connection((os.environ["LEGACY_REDIS_HOST"], int(os.environ.get("LEGACY_REDIS_PORT", "6379"))), timeout=10) as sock:
        stream = sock.makefile("rwb")
        command(stream, b"AUTH", os.environ["LEGACY_REDIS_PASSWORD"].encode())
        command(stream, b"SELECT", LEGACY_DATABASE)
        raw = command(stream, b"GET", LEGACY_KEY)
    if not raw or raw[:5] != b"\xac\xed\x00\x05\x74":
        return None
    size = int.from_bytes(raw[5:7], "big")
    return raw[7 : 7 + size].decode("utf-8").strip() or None


def main() -> int:
    settings = Settings()
    try:
        ticket = legacy_ticket()
    except (OSError, RuntimeError, ValueError) as error:
        report("legacy_unreachable", reason=type(error).__name__)
        return 1
    if not ticket:
        report("legacy_missing")
        return 1
    reply = httpx.post(APP_ACCESS_TOKEN_URL, json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret, "app_ticket": ticket}, timeout=20).json()
    if reply.get("code") != 0 or not reply.get("app_access_token"):
        # A stale legacy copy is never stored over the current one.
        report("legacy_invalid", code=reply.get("code"))
        return 1
    store = build_product_store(settings)
    if store.app_ticket(settings.feishu_app_id) == ticket:
        report("unchanged")
        return 0
    updated = store.save_app_ticket(settings.feishu_app_id, ticket)
    # False means a callback-delivered ticket with an event time already owns the row: the switch happened.
    report("updated" if updated else "superseded_by_callback")
    return 0


if __name__ == "__main__":
    sys.exit(main())
