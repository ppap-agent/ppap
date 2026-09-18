async function readJson(url, options = {}) {
  const response = await fetch(url, { credentials: "include", headers: { Accept: "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

function resourceIdFromLocation() {
  const params = new URLSearchParams(window.location.search);
  return params.get("resource") || "";
}

async function main() {
  const runtime = (await readJson("./runtime-config.json")).body;
  const config = (await readJson(`${runtime.api_url}/public-config`)).body;
  const api = config.api_url;
  document.getElementById("build-revision").textContent = config.git_sha || "unknown";
  document.getElementById("login-feishu").href = `${api}/login/feishu?return_to=${encodeURIComponent(window.location.href)}`;
  try {
    const ready = await readJson(`${api}/ready`);
    document.getElementById("stack-state").textContent = ready.body.status || "not_ready";
  } catch (err) {
    document.getElementById("stack-state").textContent = "not_ready";
  }
  const capabilities = await readJson(`${api}/capabilities`);
  document.getElementById("capabilities").textContent = JSON.stringify(capabilities.body, null, 2);
  const me = await readJson(`${api}/me`);
  if (me.ok) {
    document.getElementById("current-user").textContent = me.body.identity.subject;
  } else {
    document.getElementById("current-user").textContent = "anonymous";
  }
  document.getElementById("logout").addEventListener("click", async () => {
    await readJson(`${api}/logout`, { method: "POST" });
    window.location.reload();
  });
  const resourceId = resourceIdFromLocation();
  if (!resourceId) {
    return;
  }
  const resource = await readJson(`${api}/resources/${encodeURIComponent(resourceId)}`);
  const denied = document.getElementById("access-denied");
  if (resource.status === 401 || resource.status === 403) {
    denied.classList.remove("hidden");
    document.getElementById("resource-panel").textContent = "";
    return;
  }
  denied.classList.add("hidden");
  if (resource.ok) {
    document.getElementById("resource-panel").textContent = JSON.stringify(resource.body, null, 2);
  }
}

main().catch(() => {
  document.getElementById("build-revision").textContent = "unavailable";
  document.getElementById("stack-state").textContent = "unavailable";
});
