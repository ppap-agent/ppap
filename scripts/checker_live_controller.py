"""Observational live guard around the unchanged production Controller/runtime.

No model, artifact, trial or provider response is supplied or replaced. The
client source is not mounted here. Private Oracle identities and evidence are
readable by this trusted Controller, but denied to the guest UID.
"""

import hashlib
import io
import json
import os
import subprocess
import threading
import zipfile
from pathlib import Path

MARKER = b"checker-live-holdout-"
PRIVATE_READ_PROBE = "from pathlib import Path\ntry:\n Path('/evidence/controller.json').read_bytes()\nexcept PermissionError:\n pass\nelse:\n raise SystemExit(1)"


def contains_holdout(raw):
    if MARKER in raw:
        return True
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return any(MARKER in archive.read(name) for name in archive.namelist() if name.endswith(".xml"))
    return False


class Probe:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.author_active = False
        self.hidden_ids = set()
        self.hidden_sources = set()
        self.facts = {"adaptations": 0, "holdout_fixed_trials": 0, "provider_requests": 0, "requested_models": [], "violation": False, "holdout_sources": []}
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.facts))
        temporary.replace(self.path)

    def reject(self):
        self.facts["violation"] = True
        self.save()
        raise ValueError("live_holdout_boundary_violation")

    def observe_job(self, job, inputs):
        with self.lock:
            if job.operation == "adapt-checker":
                private = self.path.parent / "private-oracle.json"
                if not private.is_file():
                    self.reject()
                oracle = json.loads(private.read_text())
                self.hidden_ids = set(oracle["hidden_case_ids"])
                self.hidden_sources = set(oracle["hidden_source_sha256"])
                if not self.hidden_ids or not self.hidden_sources:
                    self.reject()
            sources = [path.read_bytes() for path in inputs.iterdir() if path.is_file()]
            hidden = any(contains_holdout(raw) or any(ident.encode() in raw for ident in self.hidden_ids) or hashlib.sha256(raw).hexdigest() in self.hidden_sources for raw in sources)
            if job.operation == "adapt-checker":
                if hidden or self.facts["adaptations"]:
                    self.reject()
                self.facts["adaptations"] += 1
            elif hidden and self.facts["adaptations"]:
                if job.operation != "execute-audit" or job.allow_model_calls:
                    self.reject()
                self.facts["holdout_fixed_trials"] += 1
                self.facts["holdout_sources"] = sorted(
                    set(self.facts["holdout_sources"]) | {hashlib.sha256(raw).hexdigest() for raw in sources if hashlib.sha256(raw).hexdigest() in self.hidden_sources}
                )
            self.save()

    def observe_provider(self, body):
        with self.lock:
            encoded = json.dumps(body, ensure_ascii=False).encode()
            if not self.author_active or self.facts["adaptations"] != 1 or contains_holdout(encoded) or any(ident.encode() in encoded for ident in self.hidden_ids):
                self.reject()
            self.facts["provider_requests"] += 1
            # Grants.authorize selected the real outbound model, including
            # internal CLI aliases. This is not a provider receipt or billing.
            model = body.get("model")
            from product.model_endpoint import valid_model

            self.facts["requested_models"] = sorted(set(self.facts["requested_models"]) | {valid_model(model)})
            self.save()


def main():
    from agent.execution.gateway import Grants
    from agent.execution.isolation.container import ContainerRuntime
    from worker.deep_controller import main as controller_main

    probe = Probe(Path("/evidence/controller.json"))
    # Exercise the actual guest UID against the report/oracle boundary before
    # any paid work. The client script must not exist in this container.
    if os.getuid() != 0 or Path("/live/run_checker_live.py").exists():
        probe.reject()
    checked = subprocess.run(
        [
            "/usr/bin/setpriv",
            "--reuid=10001",
            "--regid=10001",
            "--clear-groups",
            "--",
            "/app/.venv/bin/python",
            "-c",
            PRIVATE_READ_PROBE,
        ],
        capture_output=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin"},
    )
    if checked.returncode:
        probe.reject()
    probe.facts["guest_cannot_read_evidence"] = True
    probe.save()
    run, authorize = ContainerRuntime.run, Grants.authorize

    def observed_run(runtime, job, inputs, *args, **kwargs):
        probe.observe_job(job, inputs)
        probe.author_active = job.operation == "adapt-checker"
        try:
            return run(runtime, job, inputs, *args, **kwargs)
        finally:
            probe.author_active = False

    def observed_authorize(grants, token, body, request_bytes, **kwargs):
        lease = authorize(grants, token, body, request_bytes, **kwargs)
        probe.observe_provider(body)
        return lease

    ContainerRuntime.run = observed_run
    Grants.authorize = observed_authorize
    return controller_main()


if __name__ == "__main__":
    raise SystemExit(main())
