async function readJson(url, options = {}) {
  const response = await fetch(url, { credentials: "include", headers: { Accept: "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

const AUDIT_COST_PER_FILE = 500;
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

function quotaBlocksStart(quota, selectedCount) {
  const cost = selectedCount * AUDIT_COST_PER_FILE;
  const remaining = Number(quota.remaining_trial_ai_points || 0);
  const paidOk = Boolean(quota.ai_package_purchased && quota.enabled && quota.quota_sufficient);
  return !paidOk && (remaining < cost || remaining <= 0);
}

function renderDocuments(items, selected) {
  const box = document.getElementById("addon-documents");
  if (!items.length) {
    box.textContent = "暂无可识别文件";
    return;
  }
  box.innerHTML = items.map((item) => {
    const checked = selected.has(item.token) ? "checked" : "";
    return `<label class="document">
      <input data-testid="addon-doc-${item.token}" type="checkbox" value="${item.token}" ${checked} />
      <span>${item.name || item.token}</span>
    </label>`;
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
  const state = { api: "", fileToken: "", selectable: [], quota: null, roundId: "" };

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
    const links = await authorizedJson(
      state.api,
      host,
      `${state.api}/integrations/feishu/docs/links?file_token=${encodeURIComponent(state.fileToken)}`
    );
    if (!links.ok) {
      throw new Error(links.body.error === "feishu_user_token_missing" ? "飞书登录失败，请重试" : "文件列表加载失败，请稍后重试。");
    }
    const split = splitDocuments(links.body.doc_links || []);
    state.selectable = split.selectable;
    split.selectable.forEach((item) => selected.add(item.token));
    renderDocuments(split.selectable, selected);
    renderAbnormal(split.cloud, split.nonExcel);
    const quota = await authorizedJson(state.api, host, `${state.api}/integrations/feishu/usage/quota`);
    state.quota = quota.body;
    const quotaNode = document.getElementById("addon-quota");
    quotaNode.textContent = quota.body.detailed_message || quota.body.message || "";
    quotaNode.classList.toggle("hidden", !quotaNode.textContent);
    setStatus("选择要审核的文件");
  } catch (error) {
    setStatus("飞书登录失败，请重试");
    showError(error.message || "飞书登录失败，请重试");
    return;
  }

  function selectedCount() {
    return selected.size;
  }

  function refreshSubmit() {
    const blocked = !state.selectable.length || selectedCount() === 0 || quotaBlocksStart(state.quota || {}, selectedCount());
    submit.disabled = blocked;
  }

  document.getElementById("addon-documents").addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "checkbox") {
      return;
    }
    if (input.checked) {
      selected.add(input.value);
    } else {
      selected.delete(input.value);
    }
    refreshSubmit();
  });
  refreshSubmit();

  async function writeCurrentDocument() {
    if (!state.roundId || !state.fileToken) {
      return;
    }
    const result = await authorizedJson(
      state.api,
      host,
      `${state.api}/integrations/feishu/rounds/${encodeURIComponent(state.roundId)}/writeback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_token: state.fileToken }),
      }
    );
    const node = document.getElementById("addon-writeback-result");
    if (!result.ok) {
      node.textContent = result.body.error || "写回失败";
      return;
    }
    node.textContent = result.body.inserted ? "已写回当前文档" : "未写回";
  }

  submit.addEventListener("click", async () => {
    if (submit.disabled) {
      return;
    }
    showError("");
    const tokens = Array.from(selected);
    const rules = await authorizedJson(state.api, host, `${state.api}/rules`);
    const ruleId = (rules.body.rules || [])[0] && (rules.body.rules || [])[0].id;
    if (!ruleId) {
      showError("没有可用规则");
      return;
    }
    submit.disabled = true;
    setStatus("正在发起审核...");
    const created = await authorizedJson(state.api, host, `${state.api}/integrations/feishu/audits`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_token: tokens[0],
        compare_file_tokens: tokens.slice(1),
        rule_version_id: ruleId,
        idempotency_key: crypto.randomUUID(),
      }),
    });
    if (!created.ok) {
      showError("发起审核失败，请稍后重试。");
      refreshSubmit();
      return;
    }
    state.roundId = created.body.id;
    document.getElementById("addon-round").classList.remove("hidden");
    document.getElementById("addon-round-id").textContent = state.roundId;
    const open = document.getElementById("addon-open-round");
    open.href = `./?round=${encodeURIComponent(state.roundId)}`;
    const round = await pollRound(state.api, state.roundId);
    if (!round.ok) {
      showError("审核结果读取失败");
      refreshSubmit();
      return;
    }
    setStatus(round.body.execution === "completed" ? "审核完成" : "审核未完成");
    if (round.body.execution === "completed") {
      await writeCurrentDocument();
    }
    refreshSubmit();
  });

  writeback.addEventListener("click", () => {
    void writeCurrentDocument();
  });
}

main();
