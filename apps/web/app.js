const readJson = (...args) => window.PpapProduct.readJson(...args);

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

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

let previewObjectUrl = "";

function canPreviewInline(name) {
  return /\.(pdf|png|jpe?g|gif|webp)(\?|$)/i.test(name || "");
}

async function openPreview(href, name) {
  const dialog = document.getElementById("file-preview");
  const frame = document.getElementById("file-preview-frame");
  const fallback = document.getElementById("file-preview-fallback");
  const download = document.getElementById("file-preview-download");
  document.getElementById("file-preview-name").textContent = name || "原件预览";
  download.href = href;
  download.removeAttribute("download");
  const inline = canPreviewInline(name);
  frame.hidden = true;
  fallback.hidden = false;
  if (previewObjectUrl) {
    URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = "";
  }
  frame.src = "about:blank";
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }
  try {
    const response = await fetch(href, { credentials: "include" });
    if (!response.ok) {
      return;
    }
    const blob = await response.blob();
    previewObjectUrl = URL.createObjectURL(blob);
    download.href = previewObjectUrl;
    if (name) {
      download.setAttribute("download", name);
    }
    if (inline) {
      frame.src = previewObjectUrl;
      frame.hidden = false;
      fallback.hidden = true;
    }
  } catch (_err) {
    download.href = href;
  }
}

function versionHref(api, roundId, item) {
  if (!api || !item || !item.file_id || !item.file_version_id) {
    return "";
  }
  if (roundId) {
    return `${api}/rounds/${encodeURIComponent(roundId)}/files/${encodeURIComponent(item.file_id)}/versions/${encodeURIComponent(item.file_version_id)}/download`;
  }
  return `${api}/files/${encodeURIComponent(item.file_id)}/versions/${encodeURIComponent(item.file_version_id)}/download`;
}

function renderVersionList(api, items, roundId) {
  const node = document.getElementById("file-version-list");
  if (!items || !items.length) {
    node.innerHTML = "<li data-testid=\"file-version-empty\">还没有固化文件版本</li>";
    return;
  }
  node.innerHTML = items.map((item) => {
    const role = item.role === "target" ? "待审" : item.role === "reference" ? "参考" : item.role;
    const href = versionHref(api, roundId, item);
    const preview = href ? `<a data-testid="open-version" href="${href}">预览</a>` : "";
    return `<li data-testid="file-version-row">${escapeHtml(role)} · ${escapeHtml(item.original_filename)} · ${escapeHtml(item.file_version_id)} ${preview}</li>`;
  }).join("");
}

function renderDraftItems(api, items, roundId) {
  const node = document.getElementById("draft-items");
  if (!items || !items.length) {
    node.textContent = "尚未选择待审或参考文件";
    renderVersionList(api, items, roundId);
    return;
  }
  node.textContent = items.map((item) => `${item.role}: ${item.original_filename} (${item.file_version_id})`).join("；");
  renderVersionList(api, items, roundId);
}

function coverageLabel(coverage) {
  const checked = coverage.checked || [];
  const unchecked = coverage.unchecked || [];
  const parts = [];
  if (checked.length) {
    parts.push(`已检查 ${checked.join("、")}`);
  }
  if (unchecked.length) {
    parts.push(`参考 ${unchecked.join("、")}`);
  }
  return parts.join("；");
}

function selectFinding(item) {
  const label = document.getElementById("review-item-label");
  document.querySelectorAll("[data-testid=finding-row]").forEach((row) => {
    row.classList.toggle("is-selected", row.getAttribute("data-item-key") === (item && item.item_key));
  });
  if (!item) {
    label.textContent = "选择一条检查结果后复核";
    return;
  }
  label.textContent = `${item.status} · ${item.item || item.item_key}`;
  document.getElementById("review-judgment").value = (item.review && item.review.judgment) || "";
  document.getElementById("review-rationale").value = (item.review && item.review.rationale) || "";
  document.getElementById("review-next").value = (item.review && item.review.next_step) || "";
}

async function refreshRoundHistory(api, state) {
  const node = document.getElementById("round-history-list");
  if (!node) {
    return;
  }
  const listed = await readJson(`${api}/rounds`);
  if (!listed.ok) {
    return;
  }
  const rounds = listed.body.rounds || [];
  if (!rounds.length) {
    node.innerHTML = "<li data-testid=\"round-history-empty\">还没有历史轮次</li>";
    return;
  }
  node.innerHTML = rounds.map((row) => {
    const inputs = row.inputs || [];
    const target = inputs.find((item) => item.role === "target") || inputs[0] || {};
    const name = target.original_filename || row.id;
    const counts = row.result_counts || {};
    const total = (counts.PASS || 0) + (counts.NG || 0) + (counts.ERR || 0);
    const result = total
      ? `PASS ${counts.PASS || 0} / NG ${counts.NG || 0} / ERR ${counts.ERR || 0}`
      : (row.execution || "");
    const current = row.id === (state && state.roundId) ? " is-current" : "";
    return `<li class="round-history-row${current}" data-testid="round-history-row">
      <a data-testid="round-history-open" href="?round=${encodeURIComponent(row.id)}">${escapeHtml(name)} · ${escapeHtml(result)}</a>
    </li>`;
  }).join("");
}

function renderRound(api, round, state) {
  document.getElementById("round-id").textContent = round.id || "";
  document.getElementById("round-execution").textContent = round.execution || "";
  if (state) {
    state.roundId = round.id || state.roundId;
  }
  const coverage = round.coverage || {};
  document.getElementById("coverage-panel").textContent = coverageLabel(coverage);
  document.getElementById("locator-gap").classList.toggle("hidden", !coverage.locator_gap);
  const prev = document.getElementById("previous-round");
  if (round.predecessor_id) {
    prev.classList.remove("hidden");
    prev.href = `?round=${encodeURIComponent(round.predecessor_id)}`;
  } else {
    prev.classList.add("hidden");
    prev.removeAttribute("href");
  }
  if (round.inputs && round.inputs.length) {
    renderVersionList(api, round.inputs, round.id);
  }
  refreshRoundHistory(api, state);
  const findings = round.findings || [];
  const box = document.getElementById("findings");
  const summary = document.getElementById("result-summary");
  if (!findings.length) {
    const waiting = round.execution === "completed" ? "无检查结果" : "等待 Worker 完成";
    box.textContent = waiting;
    summary.textContent = waiting;
    return;
  }
  const counts = { PASS: 0, NG: 0, ERR: 0 };
  findings.forEach((item) => {
    if (counts[item.status] !== undefined) {
      counts[item.status] += 1;
    }
  });
  summary.innerHTML = `<span class="${counts.PASS ? "status-PASS" : ""}">PASS ${counts.PASS}</span> · <span class="${counts.NG ? "status-NG" : ""}">NG ${counts.NG}</span> · <span class="${counts.ERR ? "status-ERR" : ""}">ERR ${counts.ERR}</span>`;
  box.innerHTML = findings.map((item) => {
    const locator = item.locator;
    let evidence = "定位缺口";
    if (locator && locator.kind === "file_version") {
      const name = locator.filename || "";
      evidence = `<a data-testid="open-original" href="${api}/rounds/${encodeURIComponent(round.id)}/files/${encodeURIComponent(locator.file_id)}/versions/${encodeURIComponent(locator.file_version_id)}/download">打开原件 ${escapeHtml(name)}</a>`;
    } else if (locator && locator.locator) {
      evidence = escapeHtml(locator.locator);
    }
    const suggestion = item.suggestions ? escapeHtml(item.suggestions) : "无模型建议";
    const review = item.review ? `${item.review.judgment} / ${item.review.rationale || ""}` : "尚未复核";
    return `<article class="finding-row" data-testid="finding-row" data-item-key="${escapeHtml(item.item_key)}">
      <p><span class="status-${escapeHtml(item.status)}" data-testid="finding-status">${escapeHtml(item.status)}</span> ${escapeHtml(item.classification)} — ${escapeHtml(item.item)}</p>
      <p class="finding-evidence" data-testid="finding-evidence"><span class="pc-kicker">依据</span> ${evidence}</p>
      <p class="finding-suggestion" data-testid="finding-suggestion"><span class="pc-kicker">建议</span> ${suggestion}</p>
      <p data-testid="finding-review">${escapeHtml(review)}</p>
    </article>`;
  }).join("");
  const selectedKey = state && state.selectedItemKey;
  const selected = findings.find((item) => item.item_key === selectedKey) || findings[0];
  if (state) {
    state.selectedItemKey = selected.item_key;
  }
  selectFinding(selected);
}

async function uploadOneFile(api, file, role, state) {
  const existing = state.items.find((item) => item.role === role && item.original_filename === file.name);
  const existingId = existing ? existing.file_id : "";
  const meta = { original_filename: file.name, content_type: file.type || "application/octet-stream", byte_size: file.size };
  const created = existingId
    ? await readJson(`${api}/files/${encodeURIComponent(existingId)}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(meta) })
    : await readJson(`${api}/files`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(meta) });
  if (!created.ok) {
    showDenied(created.status === 401 || created.status === 403);
    return false;
  }
  const fileId = created.body.file_id;
  const versionId = created.body.id;
  const put = await fetch(`${api}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/blob`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  if (!put.ok) {
    return false;
  }
  const readyVersion = await readJson(`${api}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/finalize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (!readyVersion.ok) {
    return false;
  }
  const next = { file_version_id: readyVersion.body.id, role, file_id: fileId, original_filename: file.name };
  if (existing) {
    state.items = state.items.map((item) => (item === existing ? next : item));
  } else {
    state.items.push(next);
  }
  state.fileId = fileId;
  document.getElementById("document-version-id").textContent = readyVersion.body.id;
  document.getElementById("evidence-source-sha256").textContent = readyVersion.body.sha256 || "";
  document.getElementById("download-original").href = `${api}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/download`;
  return true;
}

async function uploadVersion(api, inputId, role, state) {
  const input = document.getElementById(inputId);
  const files = Array.from(input.files || []);
  if (!files.length) {
    return;
  }
  for (const file of files) {
    const ok = await uploadOneFile(api, file, role, state);
    if (!ok) {
      return;
    }
  }
  input.value = "";
  renderDraftItems(api, state.items, state.roundId);
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
  renderDraftItems(api, saved.body.items || [], state.roundId);
  return true;
}

async function pollRound(api, roundId, state) {
  for (let i = 0; i < 40; i += 1) {
    const round = await readJson(`${api}/rounds/${encodeURIComponent(roundId)}`);
    if (round.ok) {
      renderRound(api, round.body, state);
      if (round.body.execution === "completed" || round.body.execution === "failed") {
        return round.body;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return null;
}

async function main() {
  const { api, config } = await window.PpapProduct.connect();
  const product = window.PpapProduct.client(api);
  document.getElementById("build-revision").textContent = config.git_sha || "unknown";
  document.getElementById("login-feishu").href = `${api}/login/feishu?return_to=${encodeURIComponent(window.location.href)}`;
  try {
    const ready = await product.ready();
    document.getElementById("stack-state").textContent = ready.body.status || "not_ready";
  } catch (err) {
    document.getElementById("stack-state").textContent = "not_ready";
  }
  const capabilities = await product.capabilities();
  document.getElementById("capabilities").textContent = JSON.stringify(capabilities.body, null, 2);
  const me = await product.me();
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
    await product.isolatedDevSession(subject);
    window.location.reload();
  });
  document.getElementById("logout").addEventListener("click", async () => {
    await product.logout();
    window.location.reload();
  });

  const state = { fileId: fileIdFromLocation(), draftId: params().get("draft") || "", items: [], roundId: params().get("round") || "", selectedItemKey: "" };
  renderVersionList(api, state.items, state.roundId);
  if (me.ok) {
    const rules = await product.rules();
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
        renderDraftItems(api, state.items, state.roundId);
        if (draft.body.rule && draft.body.rule.id) {
          select.value = draft.body.rule.id;
        }
      } else {
        showDenied(draft.status === 401 || draft.status === 403);
      }
    }
    await refreshRoundHistory(api, state);
    if (state.roundId) {
      document.getElementById("round-id").textContent = state.roundId;
      await pollRound(api, state.roundId, state);
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
    renderRound(api, accepted.body, state);
    await pollRound(api, state.roundId, state);
  });
  document.getElementById("download-original").addEventListener("click", (event) => {
    const href = event.currentTarget.getAttribute("href");
    if (!href || href === "#") {
      return;
    }
    event.preventDefault();
    const current = state.items.find((item) => item.role === "target") || state.items[0] || {};
    openPreview(href, current.original_filename || "原件");
  });
  document.getElementById("file-version-list").addEventListener("click", (event) => {
    const link = event.target.closest("[data-testid=open-version]");
    if (!link || !link.getAttribute("href")) {
      return;
    }
    event.preventDefault();
    openPreview(link.getAttribute("href"), link.closest("[data-testid=file-version-row]").textContent.trim());
  });
  document.getElementById("file-preview").addEventListener("close", () => {
    document.getElementById("file-preview-frame").src = "about:blank";
    if (previewObjectUrl) {
      URL.revokeObjectURL(previewObjectUrl);
      previewObjectUrl = "";
    }
  });
  document.getElementById("findings").addEventListener("click", async (event) => {
    const original = event.target.closest("[data-testid=open-original]");
    if (original && original.getAttribute("href") && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      openPreview(original.getAttribute("href"), original.textContent.replace(/^打开原件\s*/, "").trim() || original.textContent.trim());
      return;
    }
    const row = event.target.closest("[data-testid=finding-row]");
    if (!row || !state.roundId) {
      return;
    }
    const current = await readJson(`${api}/rounds/${encodeURIComponent(state.roundId)}`);
    if (!current.ok) {
      return;
    }
    const item = (current.body.findings || []).find((rowItem) => rowItem.item_key === row.getAttribute("data-item-key"));
    if (item) {
      state.selectedItemKey = item.item_key;
      selectFinding(item);
    }
  });
  document.getElementById("save-review").addEventListener("click", async () => {
    if (!state.roundId) {
      return;
    }
    const current = await readJson(`${api}/rounds/${encodeURIComponent(state.roundId)}`);
    if (!current.ok || !(current.body.findings || []).length) {
      return;
    }
    const finding = (current.body.findings || []).find((item) => item.item_key === state.selectedItemKey)
      || current.body.findings[0];
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
    renderRound(api, saved.body, state);
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
    renderRound(api, accepted.body, state);
    await pollRound(api, state.roundId, state);
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
