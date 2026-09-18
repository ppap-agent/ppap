async function readJson(url, options = {}) {
  const response = await fetch(url, { credentials: "include", headers: { Accept: "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

function resourceIdFromLocation() {
  const params = new URLSearchParams(window.location.search);
  return params.get("resource") || "";
}

function fileIdFromLocation() {
  return new URLSearchParams(window.location.search).get("file") || "";
}

function draftIdFromLocation() {
  return new URLSearchParams(window.location.search).get("draft") || "";
}

function roundIdFromLocation() {
  return new URLSearchParams(window.location.search).get("round") || "";
}

function acceptKeyForDraft(draftId) {
  const storageKey = `ppap-accept-${draftId}`;
  let key = sessionStorage.getItem(storageKey);
  if (!key) {
    key = crypto.randomUUID();
    sessionStorage.setItem(storageKey, key);
  }
  return key;
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
  let fileId = fileIdFromLocation();
  let versionId = "";
  let draftId = draftIdFromLocation();
  let roundId = roundIdFromLocation();
  const renderRound = (round) => {
    if (!round || !round.id) {
      return;
    }
    roundId = round.id;
    document.getElementById("round-id").textContent = round.id;
    document.getElementById("execution-state").textContent = round.status || "";
    const url = new URL(window.location.href);
    url.searchParams.set("round", round.id);
    window.history.replaceState({}, "", url);
  };
  const refreshRound = async () => {
    if (!roundId) {
      return;
    }
    const latest = await readJson(`${api}/rounds/${encodeURIComponent(roundId)}`);
    if (latest.ok) {
      renderRound(latest.body);
    }
  };
  const renderDraft = (draft) => {
    if (!draft || !draft.id) {
      return;
    }
    draftId = draft.id;
    document.getElementById("audit-draft-id").textContent = draft.id;
    document.getElementById("draft-panel").textContent = JSON.stringify(draft, null, 2);
    const first = (draft.items || [])[0] || {};
    document.getElementById("rule-version").textContent = first.product_rule_version_id || "";
    document.getElementById("engine-rule-id").textContent = first.engine_rule_id || "";
    document.getElementById("identification-status").textContent = first.identification_status || "";
    const url = new URL(window.location.href);
    url.searchParams.set("draft", draft.id);
    window.history.replaceState({}, "", url);
  };
  const ensureDraft = async () => {
    if (draftId) {
      const existing = await readJson(`${api}/drafts/${encodeURIComponent(draftId)}`);
      if (existing.ok) {
        renderDraft(existing.body);
        return existing.body;
      }
    }
    const created = await readJson(`${api}/drafts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: "isolated-dev" }) });
    if (!created.ok) {
      document.getElementById("access-denied").classList.toggle("hidden", created.status !== 401 && created.status !== 403);
      return null;
    }
    renderDraft(created.body);
    return created.body;
  };
  const catalog = await readJson(`${api}/rule-versions?project_id=isolated-dev`);
  const selector = document.getElementById("rule-selector");
  for (const item of catalog.body.items || []) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = `${item.label} (${item.engine_rule_id})`;
    selector.appendChild(option);
  }
  if (draftId) {
    await ensureDraft();
  }
  document.getElementById("prepare-audit").addEventListener("click", async () => {
    const input = document.getElementById("source-upload");
    const file = input.files && input.files[0];
    if (!file) {
      return;
    }
    const meta = { original_filename: file.name, content_type: file.type || "application/octet-stream", byte_size: file.size };
    const created = fileId
      ? await readJson(`${api}/files/${encodeURIComponent(fileId)}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(meta) })
      : await readJson(`${api}/files`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(meta) });
    if (!created.ok) {
      document.getElementById("access-denied").classList.toggle("hidden", created.status !== 401 && created.status !== 403);
      return;
    }
    fileId = created.body.file_id;
    versionId = created.body.id;
    const put = await fetch(`${api}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/blob`, {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    if (!put.ok) {
      return;
    }
    const readyVersion = await readJson(`${api}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/finalize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!readyVersion.ok) {
      return;
    }
    document.getElementById("document-version-id").textContent = readyVersion.body.id;
    document.getElementById("evidence-source-sha256").textContent = readyVersion.body.sha256 || "";
    document.getElementById("download-original").href = `${api}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/download`;
    const url = new URL(window.location.href);
    url.searchParams.set("file", fileId);
    window.history.replaceState({}, "", url);
  });
  document.getElementById("add-to-draft").addEventListener("click", async () => {
    if (!fileId || !versionId) {
      return;
    }
    const draft = await ensureDraft();
    if (!draft) {
      return;
    }
    const role = document.getElementById("item-role").value;
    const items = (draft.items || []).filter((item) => item.file_version_id !== versionId);
    items.push({ file_id: fileId, file_version_id: versionId, role });
    const saved = await readJson(`${api}/drafts/${encodeURIComponent(draft.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    if (saved.ok) {
      renderDraft(saved.body);
    }
  });
  document.getElementById("bind-rule").addEventListener("click", async () => {
    const draft = await ensureDraft();
    if (!draft) {
      return;
    }
    const bound = document.getElementById("rule-selector").value;
    const items = (draft.items || []).map((item, index) => ({
      file_id: item.file_id,
      file_version_id: item.file_version_id,
      role: item.role,
      product_rule_version_id: index === 0 && item.role === "target" ? bound : item.product_rule_version_id,
    }));
    const saved = await readJson(`${api}/drafts/${encodeURIComponent(draft.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    if (saved.ok) {
      renderDraft(saved.body);
    }
  });
  document.getElementById("identify-draft").addEventListener("click", async () => {
    const draft = await ensureDraft();
    if (!draft) {
      return;
    }
    await readJson(`${api}/drafts/${encodeURIComponent(draft.id)}/identify`, { method: "POST" });
    const latest = await readJson(`${api}/drafts/${encodeURIComponent(draft.id)}`);
    if (latest.ok) {
      renderDraft(latest.body);
    }
  });
  document.getElementById("submit-audit").addEventListener("click", async () => {
    const draft = await ensureDraft();
    if (!draft) {
      return;
    }
    const accepted = await readJson(`${api}/drafts/${encodeURIComponent(draft.id)}/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: acceptKeyForDraft(draft.id) }),
    });
    if (!accepted.ok) {
      document.getElementById("access-denied").classList.toggle("hidden", accepted.status !== 401 && accepted.status !== 403);
      return;
    }
    renderRound(accepted.body);
  });
  if (roundId) {
    await refreshRound();
  }
  setInterval(refreshRound, 2000);
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
