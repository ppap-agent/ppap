async function readJson(url, options = {}) {
  const response = await fetch(url, { credentials: "include", headers: { Accept: "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

const EXCEL_EXT = /\.(xlsx|xls|xlsm)$/i;
const HOST_TIMEOUT_MS = 31000;

function showError(message) {
  const node = document.getElementById("addon-error");
  node.textContent = message || "";
  node.classList.toggle("hidden", !message);
}

function setStatus(message) {
  document.getElementById("addon-status").textContent = message;
}

function hostUnavailableError() {
  const error = new Error("当前环境不支持 docs-addon 登录");
  error.reason = "host-unavailable";
  return error;
}

async function resolveHost() {
  if (window.__PPAP_DOCS_ADDON_HOST__) {
    return window.__PPAP_DOCS_ADDON_HOST__;
  }
  let inHostFrame = false;
  try {
    inHostFrame = window.self !== window.top;
  } catch {
    inHostFrame = true;
  }
  if (!inHostFrame) {
    throw hostUnavailableError();
  }
  let Client = window.BlockitClient;
  if (!Client) {
    try {
      const mod = await import("./block-docs-addon-api.js");
      Client = mod.BlockitClient || (mod.default && mod.default.BlockitClient);
    } catch {
      Client = null;
    }
  }
  if (typeof Client !== "function") {
    throw hostUnavailableError();
  }
  const api = new Client().initAPI();
  return {
    login: () => api.Service.User.login(),
    documentToken: async () => {
      const ref = await api.getActiveDocumentRef();
      return ref && ref.docToken;
    },
  };
}

function waitForHostLogin(host) {
  if (typeof host.login !== "function") {
    return Promise.reject(hostUnavailableError());
  }
  const timeout = new Promise((_, reject) => {
    window.setTimeout(() => {
      const error = new Error("获取飞书登录态超时，请重试");
      error.reason = "host-timeout";
      reject(error);
    }, HOST_TIMEOUT_MS);
  });
  return Promise.race([host.login(), timeout]).then((code) => {
    const value = typeof code === "string" ? code.trim() : "";
    if (!value) {
      const error = new Error("未获取到飞书授权码，请重试");
      error.reason = "empty-code";
      throw error;
    }
    return value;
  });
}

function subjectOf(principal) {
  return (principal && principal.identity && principal.identity.subject) || "anonymous";
}

async function existingSession(api) {
  const me = await readJson(`${api}/me`);
  if (me.ok && me.body && me.body.identity) {
    return me.body;
  }
  return null;
}

async function loginWithHost(api, host) {
  const code = await waitForHostLogin(host);
  const logged = await readJson(`${api}/login/feishu/host`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!logged.ok) {
    if (logged.status === 403 && logged.body.error === "tenant_not_allowed") {
      throw new Error("当前仅海岸线团队可使用新版文档侧栏。请打开工作台旧版。");
    }
    throw new Error("飞书登录失败，请重试");
  }
  return logged.body;
}

async function ensureSession(api, host) {
  const current = await existingSession(api);
  if (current) {
    return current;
  }
  return loginWithHost(api, host);
}

async function authorizedJson(api, host, url, options = {}) {
  let result = await readJson(url, options);
  if (result.status === 401) {
    await loginWithHost(api, host);
    result = await readJson(url, options);
  }
  return result;
}

function isExcelFile(item) {
  const type = String(item.doc_type || item.type || "").toLowerCase();
  if (type !== "file") {
    return false;
  }
  return EXCEL_EXT.test(String(item.name || item.url || ""));
}

function splitDocuments(items) {
  const selectable = [];
  const cloud = [];
  const nonExcel = [];
  (items || []).forEach((item) => {
    const type = String(item.doc_type || item.type || "").toLowerCase();
    if (type !== "file") {
      cloud.push(item);
      return;
    }
    if (isExcelFile(item)) {
      selectable.push(item);
      return;
    }
    nonExcel.push(item);
  });
  return { selectable, cloud, nonExcel };
}

function quotaBlocksStart(quota) {
  if (!quota || quota.unavailable) return false;
  return quota.accept_allowed === false;
}

function writebackTarget(fileToken) {
  if (!fileToken) return null;
  return { provider: "feishu", object_kind: "docx", object_ref: fileToken };
}

function quotaLabel(quota) {
  if (!quota) return "";
  if (quota.unavailable) return "无法读取 Partner AI";
  const parts = [];
  if (quota.estimated_points != null) parts.push("预计 " + quota.estimated_points + " 点");
  if (quota.remaining_trial_ai_points != null) parts.push("试用剩余 " + quota.remaining_trial_ai_points + " 点");
  parts.push(quota.quota_sufficient ? "Partner AI 可用" : (quota.detailed_message || quota.message || "Partner AI 不可用"));
  if (quota.accept_allowed === false && quota.accept_reason) parts.push(quota.accept_reason);
  return parts.filter(Boolean).join(" · ");
}

function escapeHtml(value) {
  return String(value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderDocuments(items, selected, rules) {
  const box = document.getElementById("addon-documents");
  if (!items.length) {
    box.textContent = "暂无可识别文件";
    return;
  }
  box.innerHTML = items.map((item) => {
    const checked = selected.has(item.token) ? "checked" : "";
    return `<div class="document" data-token="${escapeHtml(item.token)}">
      <label><input data-testid="addon-doc-${escapeHtml(item.token)}" type="checkbox" value="${escapeHtml(item.token)}" ${checked} /> ${escapeHtml(item.name || item.token)}</label>
      <select data-testid="addon-role" aria-label="文件角色"><option value="target" ${item.role === "target" ? "selected" : ""}>待审</option><option value="reference" ${item.role === "reference" ? "selected" : ""}>参考</option></select>
      <select data-testid="addon-rule" aria-label="规则版本" ${item.role === "reference" ? "disabled" : ""}><option value="">请选择规则版本</option>${rules.map((rule) => `<option value="${escapeHtml(rule.id)}" ${item.rule_version_id === rule.id ? "selected" : ""}>${escapeHtml(rule.title)} · ${escapeHtml(rule.type || rule.kind)} · ${escapeHtml(rule.id.slice(0, 8))}</option>`).join("")}</select>
    </div>`;
  }).join("");
}

function renderAbnormal(cloud, nonExcel) {
  const box = document.getElementById("addon-abnormal");
  const parts = [];
  if (cloud.length) {
    parts.push(`<p>提示：商店应用暂不支持飞书表格审核，请选择有后缀的文件审核</p><p>${cloud.map((item) => item.name || item.token).join("、")}</p>`);
  }
  if (nonExcel.length) {
    parts.push(`<p>仅支持xlsx、xls、xlsm等Excel格式</p><p>${nonExcel.map((item) => item.name || item.token).join("、")}</p>`);
  }
  box.innerHTML = parts.join("");
  box.classList.toggle("hidden", parts.length === 0);
}

async function pollRound(api, roundId) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const round = await readJson(`${api}/rounds/${encodeURIComponent(roundId)}`);
    if (!round.ok) {
      return round;
    }
    const execution = round.body.execution || "";
    document.getElementById("addon-round-execution").textContent = execution;
    const findings = round.body.findings || [];
    document.getElementById("addon-findings").innerHTML = findings.map((item) => {
      return `<p><span class="status-${item.status}">${item.status}</span> ${item.classification || ""} — ${item.item || ""}</p>`;
    }).join("");
    if (execution === "completed" || execution === "failed") {
      return round;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  return { ok: false, status: 408, body: { error: "round_timeout" } };
}

async function main() {
  const submit = document.getElementById("addon-submit");
  const writeback = document.getElementById("addon-writeback");
  const selected = new Set();
  const query = new URLSearchParams(window.location.search);
  const state = { api: "", fileToken: "", selectable: [], rules: [], quota: null, roundId: query.get("round") || "", draftId: query.get("draft") || "", draftAccepted: false, busy: false };
  function remember() {
    const url = new URL(window.location.href);
    if (state.draftId) url.searchParams.set("draft", state.draftId);
    if (state.roundId) url.searchParams.set("round", state.roundId);
    window.history.replaceState({}, "", url);
  }
  async function request(path, body, method) {
    const verb = method || (body === undefined ? "GET" : "POST");
    const options = verb === "GET" ? {} : { method: verb, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) };
    const result = await authorizedJson(state.api, host, `${state.api}${path}`, options);
    if (!result.ok) throw new Error(result.body.error || "操作失败，请重试");
    return result.body;
  }

  const runtime = (await readJson("./runtime-config.json")).body;
  const config = (await readJson(`${runtime.api_url}/public-config`)).body;
  state.api = config.api_url;

  let host;
  try {
    host = await resolveHost();
  } catch (error) {
    setStatus(error.message || "当前环境不支持 docs-addon 登录");
    showError(error.message || "当前环境不支持 docs-addon 登录");
    return;
  }

  try {
    const principal = await ensureSession(state.api, host);
    document.getElementById("addon-user").textContent = subjectOf(principal);
    setStatus("正在加载文件列表...");
    state.fileToken = await host.documentToken();
    if (!state.fileToken) {
      throw new Error("缺少 fileToken，无法加载关联文件。");
    }
    state.rules = (await request("/rules")).rules || [];
    if (state.draftId) {
      const draft = await request(`/drafts/${encodeURIComponent(state.draftId)}`);
      state.draftAccepted = draft.status === "accepted";
      state.selectable = (draft.items || []).map((item) => ({ ...item, token: item.file_version_id, name: item.original_filename }));
    } else if (!state.roundId) {
      const links = await request(`/integrations/feishu/docs/links?file_token=${encodeURIComponent(state.fileToken)}`);
      const split = splitDocuments(links.doc_links || []);
      state.selectable = split.selectable.map((item) => ({ ...item, role: "target", rule_version_id: null }));
      renderAbnormal(split.cloud, split.nonExcel);
    }
    state.selectable.forEach((item) => selected.add(item.token));
    renderDocuments(state.selectable, selected, state.rules);
    if (!state.draftId && !state.roundId) {
      state.draftId = (await request("/drafts", {})).id;
      remember();
    }
    if (state.draftId && state.fileToken && !state.draftAccepted) {
      await request(`/drafts/${encodeURIComponent(state.draftId)}`, { writeback_target: writebackTarget(state.fileToken) }, "PUT");
    }
    setStatus("为每个待审文件选择规则；参考文件只提供上下文");
  } catch (error) {
    setStatus("飞书登录失败，请重试");
    showError(error.message || "飞书登录失败，请重试");
    return;
  }

  function targets() {
    return state.selectable.filter((item) => selected.has(item.token) && item.role === "target");
  }

  async function loadQuota() {
    const count = targets().length;
    const quota = await authorizedJson(state.api, host, `${state.api}/integrations/feishu/usage/quota?target_count=${count}`);
    const quotaNode = document.getElementById("addon-quota");
    if (!quota.ok) {
      state.quota = { unavailable: true };
    } else {
      state.quota = quota.body || {};
    }
    quotaNode.textContent = quotaLabel(state.quota);
    quotaNode.classList.toggle("hidden", !quotaNode.textContent);
    refreshSubmit();
  }

  function refreshSubmit() {
    const blocked = state.busy || Boolean(state.roundId) || !targets().length || quotaBlocksStart(state.quota);
    submit.disabled = blocked;
    document.querySelectorAll("#addon-documents input, #addon-documents select").forEach((node) => {
      const item = state.selectable.find((row) => row.token === node.closest("[data-token]").dataset.token);
      node.disabled = state.busy || state.draftAccepted || Boolean(state.roundId) || (node.dataset.testid === "addon-rule" && item.role === "reference");
    });
  }

  document.getElementById("addon-documents").addEventListener("change", (event) => {
    const input = event.target;
    const item = state.selectable.find((row) => row.token === input.closest("[data-token]")?.dataset.token);
    if (!item) return;
    if (input.type === "checkbox") {
      if (input.checked) selected.add(input.value);
      else selected.delete(input.value);
    } else if (input.dataset.testid === "addon-role") {
      item.role = input.value;
      item.rule_version_id = null;
      renderDocuments(state.selectable, selected, state.rules);
    } else item.rule_version_id = input.value || null;
    void loadQuota();
  });
  void loadQuota();

  async function writeCurrentDocument() {
    if (!state.roundId) {
      return;
    }
    const result = await authorizedJson(
      state.api,
      host,
      `${state.api}/integrations/feishu/rounds/${encodeURIComponent(state.roundId)}/writeback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }
    );
    const node = document.getElementById("addon-writeback-result");
    if (!result.ok) {
      node.textContent = result.body.error || "写回失败";
      return;
    }
    node.textContent = result.body.inserted ? "已写回当前文档" : "未写回";
  }

  async function showRound() {
    document.getElementById("addon-round").classList.remove("hidden");
    document.getElementById("addon-round-id").textContent = state.roundId;
    document.getElementById("addon-open-round").href = `./?round=${encodeURIComponent(state.roundId)}`;
    const round = await pollRound(state.api, state.roundId);
    if (!round.ok) throw new Error("审核结果读取失败");
    setStatus(round.body.execution === "completed" ? "审核完成" : "审核未完成");
    return round.body;
  }

  async function saveImportedItems() {
    const items = state.selectable.filter((item) => selected.has(item.token) && item.file_version_id).map((item) => ({ file_version_id: item.file_version_id, role: item.role, rule_version_id: item.rule_version_id || null }));
    const payload = { items, writeback_target: writebackTarget(state.fileToken) };
    await request(`/drafts/${encodeURIComponent(state.draftId)}`, payload, "PUT");
  }

  submit.addEventListener("click", async () => {
    if (submit.disabled) {
      return;
    }
    showError("");
    if (targets().some((item) => !item.rule_version_id)) {
      showError("请为每个待审文件选择规则版本");
      return;
    }
    state.busy = true;
    refreshSubmit();
    setStatus("正在发起审核...");
    try {
      if (!state.draftId) {
        state.draftId = (await request("/drafts", {})).id;
        remember();
      }
      if (!state.draftAccepted) {
        for (const item of state.selectable.filter((row) => selected.has(row.token))) {
          if (!item.file_version_id) {
            const imported = await request("/integrations/feishu/files/import", { file_token: item.token, doc_type: "file" });
            item.file_version_id = imported.id;
            item.file_id = imported.file_id;
            await saveImportedItems();
          }
        }
        await saveImportedItems();
      }
      state.roundId = (await request(`/drafts/${encodeURIComponent(state.draftId)}/accept`, { idempotency_key: `addon:${state.draftId}` })).id;
      state.draftAccepted = true;
      remember();
      const round = await showRound();
      if (round.execution === "completed") await writeCurrentDocument();
    } catch (error) {
      showError(error.message || "发起审核失败，请重试");
    } finally {
      state.busy = false;
      refreshSubmit();
    }
  });

  writeback.addEventListener("click", () => {
    void writeCurrentDocument();
  });
  if (state.roundId) {
    try { await showRound(); } catch (error) { showError(error.message); }
  }
}

main();
