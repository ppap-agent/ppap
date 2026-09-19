async function readJson(url, options = {}) {
  const response = await fetch(url, { credentials: "include", headers: { Accept: "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

function params() {
  return new URLSearchParams(window.location.search);
}

function resourceIdFromLocation() {
  return params().get("resource") || "";
}

function fileIdFromLocation() {
  return params().get("file") || "";
}

function setQuery(values) {
  const url = new URL(window.location.href);
  Object.entries(values).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  });
  window.history.replaceState({}, "", url);
}

function showDenied(on) {
  document.getElementById("access-denied").classList.toggle("hidden", !on);
}

function renderDraftItems(items) {
  const node = document.getElementById("draft-items");
  if (!items || !items.length) {
    node.textContent = "尚未选择待审或参考文件";
    return;
  }
  node.textContent = items.map((item) => `${item.role}: ${item.original_filename} (${item.file_version_id})`).join("；");
}

function renderRound(api, round) {
  document.getElementById("round-id").textContent = round.id || "";
  document.getElementById("round-execution").textContent = round.execution || "";
  const coverage = round.coverage || {};
  document.getElementById("coverage-panel").textContent = JSON.stringify({
    checked: coverage.checked || [],
    unchecked: coverage.unchecked || [],
    locator_gap: Boolean(coverage.locator_gap),
  });
  document.getElementById("locator-gap").classList.toggle("hidden", !coverage.locator_gap);
  const prev = document.getElementById("previous-round");
  if (round.predecessor_id) {
    prev.classList.remove("hidden");
    prev.href = `?round=${encodeURIComponent(round.predecessor_id)}`;
  } else {
    prev.classList.add("hidden");
    prev.removeAttribute("href");
  }
  const findings = round.findings || [];
  const box = document.getElementById("findings");
  if (!findings.length) {
    box.textContent = round.execution === "completed" ? "无检查结果" : "等待 Worker 完成";
    return;
  }
  box.innerHTML = findings.map((item) => {
    const locator = item.locator;
    let evidence = "定位缺口";
    if (locator && locator.kind === "file_version") {
      evidence = `<a data-testid="open-original" href="${api}/rounds/${encodeURIComponent(round.id)}/files/${encodeURIComponent(locator.file_id)}/versions/${encodeURIComponent(locator.file_version_id)}/download">打开原件 ${locator.filename || ""}</a>`;
    } else if (locator && locator.locator) {
      evidence = locator.locator;
    }
    const review = item.review ? `${item.review.judgment} / ${item.review.rationale || ""}` : "尚未复核";
    return `<article data-testid="finding-row" data-item-key="${item.item_key}">
      <p><span class="status-${item.status}" data-testid="finding-status">${item.status}</span> ${item.classification} — ${item.item}</p>
      <p>${evidence}</p>
      <p data-testid="finding-review">${review}</p>
    </article>`;
  }).join("");
  const first = findings[0];
  if (first && first.review) {
    document.getElementById("review-judgment").value = first.review.judgment || "";
    document.getElementById("review-rationale").value = first.review.rationale || "";
    document.getElementById("review-next").value = first.review.next_step || "";
  }
}

async function uploadVersion(api, inputId, role, state) {
  const input = document.getElementById(inputId);
  const file = input.files && input.files[0];
  if (!file) {
    return;
  }
  const fileKey = role === "target" ? "targetFileId" : "referenceFileId";
  const existingId = state[fileKey];
  const meta = { original_filename: file.name, content_type: file.type || "application/octet-stream", byte_size: file.size };
  const created = existingId
    ? await readJson(`${api}/files/${encodeURIComponent(existingId)}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(meta) })
    : await readJson(`${api}/files`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(meta) });
  if (!created.ok) {
    showDenied(created.status === 401 || created.status === 403);
    return;
  }
  state[fileKey] = created.body.file_id;
  state.fileId = created.body.file_id;
  const versionId = created.body.id;
  const put = await fetch(`${api}/files/${encodeURIComponent(state.fileId)}/versions/${encodeURIComponent(versionId)}/blob`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  if (!put.ok) {
    return;
  }
  const readyVersion = await readJson(`${api}/files/${encodeURIComponent(state.fileId)}/versions/${encodeURIComponent(versionId)}/finalize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (!readyVersion.ok) {
    return;
  }
  document.getElementById("document-version-id").textContent = readyVersion.body.id;
  document.getElementById("evidence-source-sha256").textContent = readyVersion.body.sha256 || "";
  document.getElementById("download-original").href = `${api}/files/${encodeURIComponent(state.fileId)}/versions/${encodeURIComponent(versionId)}/download`;
  state.items = state.items.filter((item) => item.role !== role);
  state.items.push({ file_version_id: readyVersion.body.id, role, file_id: state.fileId, original_filename: file.name });
  renderDraftItems(state.items);
  setQuery({ file: state.fileId, draft: state.draftId, round: params().get("round") });
}

async function ensureDraft(api, state) {
  if (state.draftId) {
    return true;
  }
  const created = await readJson(`${api}/drafts`, { method: "POST" });
  if (!created.ok) {
    showDenied(created.status === 401 || created.status === 403);
    return false;
  }
  state.draftId = created.body.id;
  document.getElementById("draft-id").textContent = state.draftId;
  setQuery({ file: state.fileId, draft: state.draftId, round: params().get("round") });
  return true;
}

async function saveDraft(api, state) {
  if (!(await ensureDraft(api, state))) {
    return false;
  }
  const rule = document.getElementById("rule-select").value;
  const saved = await readJson(`${api}/drafts/${encodeURIComponent(state.draftId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule_version_id: rule, items: state.items }),
  });
  if (!saved.ok) {
    showDenied(saved.status === 401 || saved.status === 403);
    return false;
  }
  renderDraftItems(saved.body.items || []);
  return true;
}

async function pollRound(api, roundId) {
  for (let i = 0; i < 40; i += 1) {
    const round = await readJson(`${api}/rounds/${encodeURIComponent(roundId)}`);
    if (round.ok) {
      renderRound(api, round.body);
      if (round.body.execution === "completed" || round.body.execution === "failed") {
        return round.body;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return null;
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
  if (config.profile === "isolated-dev") {
    document.getElementById("isolated-dev-login-panel").classList.remove("hidden");
  }
  document.getElementById("isolated-dev-login").addEventListener("click", async () => {
    const subject = document.getElementById("isolated-dev-subject").value;
    await readJson(`${api}/isolated-dev/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subject }) });
    window.location.reload();
  });
  document.getElementById("logout").addEventListener("click", async () => {
    await readJson(`${api}/logout`, { method: "POST" });
    window.location.reload();
  });

  const state = { fileId: fileIdFromLocation(), targetFileId: fileIdFromLocation(), referenceFileId: "", draftId: params().get("draft") || "", items: [], roundId: params().get("round") || "" };
  if (me.ok) {
    const rules = await readJson(`${api}/rules`);
    const select = document.getElementById("rule-select");
    (rules.body.rules || []).forEach((rule) => {
      const option = document.createElement("option");
      option.value = rule.id;
      option.textContent = rule.title;
      select.appendChild(option);
    });
    if (state.draftId) {
      const draft = await readJson(`${api}/drafts/${encodeURIComponent(state.draftId)}`);
      if (draft.ok) {
        document.getElementById("draft-id").textContent = draft.body.id;
        state.items = (draft.body.items || []).map((item) => ({
          file_version_id: item.file_version_id,
          role: item.role,
          file_id: item.file_id,
          original_filename: item.original_filename,
        }));
        state.items.forEach((item) => {
          if (item.role === "target") state.targetFileId = item.file_id;
          if (item.role === "reference") state.referenceFileId = item.file_id;
        });
        renderDraftItems(state.items);
        if (draft.body.rule && draft.body.rule.id) {
          select.value = draft.body.rule.id;
        }
      } else {
        showDenied(draft.status === 401 || draft.status === 403);
      }
    }
    if (state.roundId) {
      document.getElementById("round-id").textContent = state.roundId;
      await pollRound(api, state.roundId);
    }
  }

  document.getElementById("prepare-audit").addEventListener("click", async () => {
    await uploadVersion(api, "source-upload", "target", state);
  });
  document.getElementById("prepare-reference").addEventListener("click", async () => {
    await uploadVersion(api, "reference-upload", "reference", state);
  });
  document.getElementById("save-draft").addEventListener("click", async () => {
    await saveDraft(api, state);
  });
  document.getElementById("submit-audit").addEventListener("click", async () => {
    if (!(await saveDraft(api, state))) {
      return;
    }
    const storageKey = `ppap-idempotency-${state.draftId}`;
    let idempotency = sessionStorage.getItem(storageKey);
    if (!idempotency) {
      idempotency = crypto.randomUUID();
      sessionStorage.setItem(storageKey, idempotency);
    }
    const accepted = await readJson(`${api}/drafts/${encodeURIComponent(state.draftId)}/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: idempotency }),
    });
    if (!accepted.ok) {
      showDenied(accepted.status === 401 || accepted.status === 403);
      return;
    }
    state.roundId = accepted.body.id;
    setQuery({ round: state.roundId, file: state.fileId });
    renderRound(api, accepted.body);
    await pollRound(api, state.roundId);
  });
  document.getElementById("save-review").addEventListener("click", async () => {
    if (!state.roundId) {
      return;
    }
    const current = await readJson(`${api}/rounds/${encodeURIComponent(state.roundId)}`);
    if (!current.ok || !(current.body.findings || []).length) {
      return;
    }
    const finding = current.body.findings[0];
    const saved = await readJson(`${api}/rounds/${encodeURIComponent(state.roundId)}/reviews`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_key: finding.item_key,
        judgment: document.getElementById("review-judgment").value,
        rationale: document.getElementById("review-rationale").value,
        next_step: document.getElementById("review-next").value,
        version: finding.review ? finding.review.version : 0,
      }),
    });
    if (!saved.ok) {
      document.getElementById("review-saved").textContent = saved.body.error || "review-failed";
      showDenied(saved.status === 401 || saved.status === 403);
      return;
    }
    document.getElementById("review-saved").textContent = "saved";
    renderRound(api, saved.body);
  });
  document.getElementById("resubmit-audit").addEventListener("click", async () => {
    if (!state.roundId) {
      return;
    }
    const accepted = await readJson(`${api}/rounds/${encodeURIComponent(state.roundId)}/resubmit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        idempotency_key: crypto.randomUUID(),
        items: state.items.length ? state.items : undefined,
        rule_version_id: document.getElementById("rule-select").value || undefined,
      }),
    });
    if (!accepted.ok) {
      showDenied(accepted.status === 401 || accepted.status === 403);
      return;
    }
    state.roundId = accepted.body.id;
    setQuery({ round: state.roundId, file: state.fileId });
    renderRound(api, accepted.body);
    await pollRound(api, state.roundId);
  });

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
