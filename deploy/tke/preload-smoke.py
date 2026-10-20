"""Verify real processes against Pod-local fixtures, never production queues."""

import json
import os
import socket
import sys
import time
import urllib.request

import psycopg
from ppap_deep.client import read_frame, write_frame


def verify(sha: str, worker: str) -> dict:
    assert os.environ["PPAP_PG_HOST"] == "127.0.0.1"
    assert os.environ["PPAP_PG_DATABASE"] == "ppap_preload"
    assert os.environ["PPAP_S3_ENDPOINT"] == "http://127.0.0.1:9000"
    assert os.environ["PPAP_FEISHU_NOTIFICATIONS_ENABLED"] == "false"
    assert os.environ["PPAP_USAGE_CHARGE_ENABLED"] == "false"
    assert os.environ["PPAP_BUILD_SHA"] == sha
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(url):
        with opener.open(url, timeout=5) as response:
            return json.loads(response.read())

    deadline = time.monotonic() + 120
    while True:
        try:
            get("http://127.0.0.1:8080/ready")
            assert get("http://127.0.0.1:8080/version")["git_sha"] == sha
            assert get("http://127.0.0.1:4173/build-identity.json")["git_sha"] == sha
            with psycopg.connect(host="127.0.0.1", dbname="ppap_preload", user="ppap_api", password=os.environ["PPAP_PG_API_PASSWORD"]) as connection:
                heartbeat = connection.execute("SELECT git_sha FROM engine.worker_heartbeats WHERE worker_id=%s AND last_seen_at > now()-interval '30 seconds'", (worker,)).fetchone()
                assert heartbeat and heartbeat[0] == sha
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect("/run/ppap-deep/controller.sock")
                # Invalid protocol request proves the controller responds without
                # authorizing a document, creating a guest or calling a model.
                write_frame(connection, {"op": "preload-startup-probe"})
                assert read_frame(connection)["code"] == "deep_protocol_invalid"
            return {"git_sha": sha, "api_ready": True, "web_ready": True, "worker_heartbeat": True, "deep_socket_response": True, "model_called": False, "production_data_accessed": False}
        except (OSError, AssertionError, psycopg.Error):
            if time.monotonic() >= deadline:
                raise RuntimeError("isolated full-image startup did not become healthy") from None
            time.sleep(2)


if __name__ == "__main__":
    print(json.dumps(verify(*sys.argv[1:])))
