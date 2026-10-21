"""Read the node CRI image identity. Never import or transfer image archives."""

import json
import subprocess
import sys
import time


def inspect_image(ref: str) -> dict:
    for _ in range(30):
        result = subprocess.run(["chroot", "/host", "/usr/local/bin/crictl", "--runtime-endpoint", "unix:///run/containerd/containerd.sock", "inspecti", ref], capture_output=True, timeout=15)
        if result.returncode == 0:
            status = json.loads(result.stdout)["status"]
            matched = ref in status.get("repoDigests", []) if "@" in ref else ref in status.get("repoTags", [])
            if matched:
                return {"image": ref, "config_digest": status["id"], "registered": True}
        time.sleep(1)
    raise RuntimeError("pulled image was not registered with the expected digest")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "inspect":
        raise ValueError("expected inspect IMAGE")
    print(json.dumps(inspect_image(sys.argv[2])), flush=True)
