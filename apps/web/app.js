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
  const denied = document.getElementById("access-denied");
  const findingsEl = document.getElementById("findings");
  const reviewsEl = document.getElementById("reviews");
  const usageListEl = document.getElementById("usage-list");
  const evidencePanel = document.getElementById("evidence-panel");
  const showDenied = (on) => {
    denied.classList.toggle("hidden", !on);
    if (on) {
      findingsEl.replaceChildren();
      reviewsEl.replaceChildren();
      usageListEl.replaceChildren();
      document.getElementById("usage-state").textContent = "";
      document.getElementById("original-slot").replaceChildren();
    }
  };
  const showOriginal = (targetFileId, targetVersionId, sha256) => {
    const slot = document.getElementById("original-slot");
    slot.replaceChildren();
    if (!targetFileId || !targetVersionId) {
      return;
    }
    const link = document.createElement("a");
    link.dataset.testid = "download-original";
    link.id = "download-original";
    link.textContent = "下载原件";
    link.href = `${api}/files/${encodeURIComponent(targetFileId)}/versions/${encodeURIComponent(targetVersionId)}/download`;
    slot.appendChild(link);
    document.getElementById("document-version-id").textContent = targetVersionId;
    document.getElementById("evidence-source-sha256").textContent = sha256 || "";
  };
  const renderFindings = (round) => {
    findingsEl.replaceChildren();
    evidencePanel.classList.add("hidden");
    evidencePanel.hidden = true;
    document.getElementById("evidence-locator").textContent = "";
    const coverage = round.coverage || {};
    document.getElementById("coverage").textContent =
      `待审 ${coverage.target_count || 0} 份，参考 ${coverage.reference_count || 0} 份（参考不是已审通过），检查项 ${coverage.finding_count || 0}`;
    for (const item of round.visible_findings || []) {
      const row = document.createElement("li");
      row.className = "finding";
      row.dataset.testid = "finding";
      const idEl = document.createElement("code");
      idEl.dataset.testid = "finding-id";
      idEl.textContent = item.id || "";
      const statusEl = document.createElement("code");
      statusEl.dataset.testid = "finding-status";
      statusEl.textContent = item.status || "ERR";
      const evidenceEl = document.createElement("code");
      evidenceEl.dataset.testid = "evidence-state";
      evidenceEl.textContent = item.evidence_state || "missing";
      const nextEl = document.createElement("span");
      nextEl.dataset.testid = "next-action";
      nextEl.textContent = item.next_action || "记录复核意见（不改机器结果）";
      row.append(idEl, document.createTextNode(" "), statusEl, document.createTextNode(" "), evidenceEl, document.createTextNode(" "), nextEl);
      if (item.evidence_state === "available" && item.locator) {
        const view = document.createElement("button");
        view.type = "button";
        view.dataset.testid = "view-evidence";
        view.textContent = "查看证据";
        view.addEventListener("click", () => {
          evidencePanel.classList.remove("hidden");
          evidencePanel.hidden = false;
          document.getElementById("evidence-locator").textContent = item.locator;
        });
        row.appendChild(view);
      } else {
        const missing = document.createElement("span");
        missing.textContent = " 证据缺失（不是模型建议）";
        row.appendChild(missing);
      }
      findingsEl.appendChild(row);
    }
  };
  const renderReviews = (round) => {
    reviewsEl.replaceChildren();
    for (const item of round.reviews || []) {
      const row = document.createElement("li");
      row.dataset.testid = "review-entry";
      row.textContent = `${item.judgment}: ${item.reason} → ${item.next_action}`;
      if (item.result_id) {
        row.dataset.resultId = item.result_id;
      }
      reviewsEl.appendChild(row);
    }
  };
  const renderUsage = (items, roundUsage) => {
    usageListEl.replaceChildren();
    document.getElementById("usage-state").textContent = (roundUsage && roundUsage.state) || "";
    for (const item of items || []) {
      const row = document.createElement("li");
      row.dataset.testid = "usage-entry";
      row.textContent = `${item.state} ${item.business_key} charged=${item.charged}`;
      usageListEl.appendChild(row);
    }
  };
  const refreshUsage = async () => {
    const listed = await readJson(`${api}/usage`);
    if (!listed.ok) {
      return [];
    }
    return listed.body.items || [];
  };
  const renderRound = (round) => {
    if (!round || !round.id) {
      return;
    }
    showDenied(false);
    roundId = round.id;
    document.getElementById("round-id").textContent = round.id;
    document.getElementById("execution-state").textContent = round.execution_state || round.status || "";
    const target = (round.inputs || []).find((item) => item.role === "target") || {};
    if (target.product_rule_version_id) {
      document.getElementById("rule-version").textContent = target.product_rule_version_id;
    }
    if (target.engine_rule_id) {
      document.getElementById("engine-rule-id").textContent = target.engine_rule_id;
    }
    showOriginal(target.file_id, target.file_version_id, target.sha256);
    renderFindings(round);
    renderReviews(round);
    refreshUsage().then((items) => renderUsage(items, round.usage));
    const url = new URL(window.location.href);
    url.searchParams.set("round", round.id);
    window.history.replaceState({}, "", url);
  };
  const refreshRound = async () => {
    if (!roundId) {
      return;
    }
    const latest = await readJson(`${api}/rounds/${encodeURIComponent(roundId)}`);
    if (latest.status === 401 || latest.status === 403) {
      showDenied(true);
      return;
    }
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
      showDenied(created.status === 401 || created.status === 403);
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
      showDenied(created.status === 401 || created.status === 403);
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
    showOriginal(fileId, versionId, readyVersion.body.sha256 || "");
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
      showDenied(accepted.status === 401 || accepted.status === 403);
      return;
    }
    renderRound(accepted.body);
  });
  const conflictEl = document.getElementById("review-conflict");
  const showConflict = (on) => conflictEl.classList.toggle("hidden", !on);
  document.getElementById("save-review").addEventListener("click", async () => {
    if (!roundId) {
      return;
    }
    showConflict(false);
    const latest = await readJson(`${api}/rounds/${encodeURIComponent(roundId)}`);
    if (!latest.ok) {
      showDenied(latest.status === 401 || latest.status === 403);
      return;
    }
    const mine = (latest.body.reviews || []).find((item) => item.actor_user_id === me.body.user_id);
    const reason = document.getElementById("review-reason").value.trim();
    if (!reason) {
      return;
    }
    const payload = {
      judgment: "noted",
      reason,
      next_action: "记录复核意见（不改机器结果）",
    };
    if (mine) {
      payload.expected_version = mine.version;
    }
    const saved = await readJson(`${api}/rounds/${encodeURIComponent(roundId)}/reviews`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (saved.status === 409) {
      showConflict(true);
      return;
    }
    if (!saved.ok) {
      showDenied(saved.status === 401 || saved.status === 403);
      return;
    }
    renderRound(saved.body);
  });
  if (me.ok && !roundId) {
    renderUsage(await refreshUsage(), null);
  }
  if (roundId) {
    await refreshRound();
  }
  setInterval(refreshRound, 2000);
  const resourceId = resourceIdFromLocation();
  if (!resourceId) {
    return;
  }
  const resource = await readJson(`${api}/resources/${encodeURIComponent(resourceId)}`);
  if (resource.status === 401 || resource.status === 403) {
    showDenied(true);
    document.getElementById("resource-panel").textContent = "";
    return;
  }
  showDenied(false);
  if (resource.ok) {
    document.getElementById("resource-panel").textContent = JSON.stringify(resource.body, null, 2);
  }
}

main().catch(() => {
  document.getElementById("build-revision").textContent = "unavailable";
  document.getElementById("stack-state").textContent = "unavailable";
});
