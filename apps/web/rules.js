const node = (id) => document.getElementById(id);

const ruleErrors = {
  unauthenticated: "请先登录。",
  access_denied: "没有规则或来源文件的访问权限。请使用有权限的来源文件。",
  file_not_ready: "来源文件尚未就绪，请重新上传。",
  unsupported_rule_file: "此规则文件格式暂不支持。旧 .doc 请先转为 .docx。",
  empty_rule_document: "未提取到文本。扫描 PDF 请先转为可读文本资料。",
  rule_document_too_long: "规则原文超过单次解析上限，请拆分后导入。没有内容被截断或发布。",
  rule_type_required: "长文档无法仅凭文件名确定类型，请选择文档类型后重新解析。",
  unknown_rule_type: "无法确定文档类型，请选择正确类型。",
  rule_parse_failed: "规则解析失败或返回条目不完整。请检查原件、类型及模型配置后重试。",
  rule_text_extraction_failed: "无法读取规则文件，请检查格式和文件是否完整。",
  invalid_rule_json: "JSON 中没有有效的专业规则，请检查类型、条目及适用范围。",
  invalid_rule_body: "只能发布专业审核规则，不能发布资料就绪检查。",
  invalid_rule_profile: "请选择 v2 或 v3。",
  invalid_rule_title: "请填写不超过 200 字符的标题。",
  empty_rules: "至少需要一条明确的审核规则。",
  invalid_rule_item: "每条规则必须有编号、分类、检查要求和有效的检查方式。",
  duplicate_rule_id: "规则编号不能重复。",
  invalid_rule_metadata: "请检查客户、型号及零件，每行一项。",
  rule_parse_conflict: "此任务标识已用于另一份解析请求。请读取原任务，或显式开始新任务。",
  invalid_rule_request: "解析请求无效，请重新选择规则来源。",
};

function requireResult(result) {
  if (!result.ok) throw new Error(ruleErrors[result.body.error] || "请求失败，请检查网络后重试。");
  return result.body;
}

function addRuleItem(rule = {}) {
  const row = document.createElement("tr");
  const fields = ["id", "category", "priority", "rule_to_check", "type_of_rule"];
  for (const field of fields) {
    const cell = document.createElement("td");
    const choices = field === "priority" ? ["", "P0", "P1", "P2"] : field === "type_of_rule" ? ["table", "text", "image"] : null;
    const input = document.createElement(choices ? "select" : field === "rule_to_check" ? "textarea" : "input");
    if (choices) choices.forEach((value) => input.add(new Option(value || "未指定", value)));
    if (field === "rule_to_check") input.rows = 3;
    input.dataset.field = field;
    input.setAttribute("aria-label", { id: "编号", category: "分类", priority: "优先级", rule_to_check: "检查要求", type_of_rule: "检查方式" }[field]);
    input.value = rule[field] || (field === "id" ? String(node("rule-items").children.length + 1) : field === "type_of_rule" ? "table" : "");
    cell.appendChild(input);
    row.appendChild(cell);
  }
  const cell = document.createElement("td");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "删除";
  remove.addEventListener("click", () => row.remove());
  cell.appendChild(remove);
  row.appendChild(cell);
  node("rule-items").appendChild(row);
}

async function main() {
  const { api } = await window.PpapProduct.connect();
  const product = window.PpapProduct.client(api);
  const params = new URL(window.location.href).searchParams;
  const state = { sourceVersion: "", predecessor: params.get("predecessor") || "", parseId: params.get("parse") || "", busy: false, parseSource: null };
  node("rules-login").href = `${api}/login/feishu?return_to=${encodeURIComponent(window.location.href)}`;

  function setBusy(busy) {
    state.busy = busy;
    document.querySelectorAll("button, input, select, textarea").forEach((input) => { input.disabled = busy; });
  }

  function setParseId(id) {
    state.parseId = id;
    const url = new URL(window.location.href);
    for (const field of ["parse", "predecessor"]) url.searchParams.delete(field);
    if (id) {
      url.searchParams.set("parse", id);
      if (state.predecessor) url.searchParams.set("predecessor", state.predecessor);
    }
    history.replaceState(null, "", url);
    node("resume-rule").classList.toggle("hidden", !id);
    node("rerun-rule").classList.add("hidden");
  }

  async function action(work) {
    if (state.busy) return;
    setBusy(true);
    node("rule-message").textContent = "处理中…";
    try { await work(); } catch (error) { node("rule-message").textContent = error.message || "请求失败"; }
    finally { setBusy(false); }
  }

  function showCandidate(candidate, sourceFileId) {
    const body = candidate.body;
    state.sourceVersion = candidate.source_file_version_id || "";
    node("rule-title").value = candidate.title;
    node("rule-type").value = body.type;
    node("rule-profile").value = body.rule_profile;
    for (const field of ["customers", "models", "parts"]) node(`rule-${field}`).value = (body[field] || []).join("\n");
    node("rule-items").replaceChildren();
    (body.rules || []).forEach(addRuleItem);
    node("rule-source").href = product.files.downloadUrl(sourceFileId, state.sourceVersion);
    node("rule-origin").textContent = state.predecessor ? `基于版本 ${state.predecessor}；发布将创建新版本。` : "新规则，尚未发布。";
    node("rule-editor").classList.remove("hidden");
    setBusy(state.busy);
  }

  async function refreshVersions() {
    const listed = requireResult(await product.rules());
    node("rule-versions").replaceChildren();
    if (!listed.rules.length) node("rule-versions").textContent = "尚无规则，请先导入。";
    for (const version of listed.rules) {
      const row = document.createElement("li");
      row.appendChild(document.createTextNode(`${version.title} · ${version.type || "开发资料检查"} · ${version.created_at || ""} · ${version.id.slice(0, 8)} `));
      if (version.kind === "engine") {
        const open = document.createElement("button");
        open.type = "button";
        open.textContent = "查看 / 编辑为新版本";
        open.addEventListener("click", () => action(async () => {
          const candidate = requireResult(await product.ruleVersions.get(version.id));
          state.predecessor = candidate.id;
          setParseId("");
          showCandidate(candidate, candidate.source_file_id);
          node("rule-message").textContent = "已加载发布版本。修改不会覆盖原版本。";
        }));
        row.appendChild(open);
      }
      node("rule-versions").appendChild(row);
    }
    setBusy(state.busy);
  }

  async function readParse() {
    node("rule-editor").classList.add("hidden");
    while (state.parseId) {
      const job = requireResult(await product.ruleVersions.parseResult(state.parseId));
      state.parseSource = { source_file_id: job.source_file_id, source_file_version_id: job.source_file_version_id, document_type: job.document_type };
      node("parse-type").value = job.document_type || "";
      if (job.status === "succeeded") {
        showCandidate(job.result, job.source_file_id);
        node("rule-message").textContent = `已提取 ${job.result.body.rules.length} 条规则。尚未发布，请逐项确认。`;
        return;
      }
      if (job.status === "unknown") {
        node("rule-message").textContent = "解析结果未知，原任务可能仍会完成。重新读取不会重跑；显式重新解析会新建任务，可能再次消耗模型费用。";
        node("rerun-rule").classList.remove("hidden");
        return;
      }
      if (job.status === "failed") {
        node("rule-message").textContent = ruleErrors[job.result?.error] || "解析失败，没有发布规则。";
        node("rerun-rule").classList.remove("hidden");
        return;
      }
      if (!["queued", "leased"].includes(job.status)) throw new Error("无法确认解析状态。请重新读取任务，不会自动重跑。");
      node("rule-message").textContent = "解析任务处理中。可以离开此页，或刷新后继续读取同一任务，不会重新解析。";
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  async function startParse(source) {
    state.parseSource = source;
    // Save identity before acceptance: even a lost response can be read after refresh.
    setParseId(crypto.randomUUID());
    requireResult(await product.ruleVersions.parse({ request_id: state.parseId, ...source }));
    await readParse();
  }

  node("new-rule").addEventListener("click", () => {
    if (state.busy) return;
    state.sourceVersion = "";
    state.predecessor = "";
    state.parseSource = null;
    setParseId("");
    node("rule-file").value = "";
    node("rule-editor").classList.add("hidden");
    node("rule-message").textContent = "选择规则文件开始导入。";
  });
  node("add-rule-item").addEventListener("click", () => { if (!state.busy) addRuleItem(); });
  node("resume-rule").addEventListener("click", () => action(readParse));
  node("rerun-rule").addEventListener("click", () => action(async () => { await startParse({ ...state.parseSource, document_type: node("parse-type").value }); }));
  node("parse-rule").addEventListener("click", () => action(async () => {
    const file = node("rule-file").files[0];
    if (!file) throw new Error("请先选择规则文件。");
    state.sourceVersion = "";
    node("rule-editor").classList.add("hidden");
    const version = requireResult(await product.files.create({ original_filename: file.name, content_type: file.type || "application/octet-stream", byte_size: file.size }));
    const path = `${api}/files/${encodeURIComponent(version.file_id)}/versions/${encodeURIComponent(version.id)}`;
    const uploaded = await fetch(`${path}/blob`, { method: "PUT", credentials: "include", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
    if (!uploaded.ok) throw new Error("上传失败，请重试。");
    requireResult(await window.PpapProduct.readJson(`${path}/finalize`, { method: "POST" }));
    await startParse({ source_file_id: version.file_id, source_file_version_id: version.id, document_type: node("parse-type").value });
  }));
  node("publish-rule").addEventListener("click", () => action(async () => {
    const body = { kind: "engine", type: node("rule-type").value, rule_profile: node("rule-profile").value, rules: [] };
    for (const field of ["customers", "models", "parts"]) body[field] = node(`rule-${field}`).value.split("\n").map((value) => value.trim()).filter(Boolean);
    for (const row of node("rule-items").children) {
      const rule = {};
      row.querySelectorAll("[data-field]").forEach((input) => { rule[input.dataset.field] = input.value; });
      rule.priority = rule.priority || null;
      body.rules.push(rule);
    }
    const published = requireResult(await product.ruleVersions.publish({ title: node("rule-title").value, body, source_file_version_id: state.sourceVersion, predecessor_id: state.predecessor || null }));
    state.predecessor = published.id;
    setParseId("");
    showCandidate(published, published.source_file_id);
    await refreshVersions();
    node("rule-message").textContent = `已发布新版本 ${published.id}，现在可以回工作台选择此规则。`;
  }));

  setBusy(true);
  const me = requireResult(await product.me());
  node("rules-user").textContent = me.identity.subject;
  node("rules-login").classList.add("hidden");
  const types = requireResult(await product.ruleVersions.types());
  types.types.forEach((name) => {
    node("parse-type").add(new Option(name, name));
    node("rule-type").add(new Option(name, name));
  });
  await refreshVersions();
  setParseId(state.parseId);
  setBusy(false);
  if (state.parseId) await action(readParse);
}

main().catch((error) => { node("rule-message").textContent = error.message || "无法加载审核规则"; });
