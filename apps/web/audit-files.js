function escapeHistory(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function targetName(row) {
  const inputs = row.inputs || [];
  return inputs.filter(item => item.role === "target").map(item => item.original_filename).join("、") || row.id;
}

function versionLabel(row) {
  const inputs = row.inputs || [];
  const target = inputs.find((item) => item.role === "target") || inputs[0];
  return (target && target.file_version_id) || "";
}

function ruleLabel(row) {
  return (row.inputs || []).filter((item) => item.role === "target").map((item) => `${item.original_filename}: ${item.rule?.title || ""}`).join("；");
}

function resultSummary(row) {
  const counts = row.result_counts || {};
  const total = (counts.PASS || 0) + (counts.NG || 0) + (counts.ERR || 0);
  if (!total) {
    return row.execution || "";
  }
  return `PASS ${counts.PASS || 0} / NG ${counts.NG || 0} / ERR ${counts.ERR || 0}`;
}

function renderRows(items, query, execution) {
  const errorNode = document.getElementById("history-error");
  const emptyNode = document.getElementById("history-empty");
  const table = document.getElementById("history-list");
  const rows = document.getElementById("history-rows");
  const needle = (query || "").trim().toLowerCase();
  const filtered = items.filter((row) => {
    if (execution && row.execution !== execution) {
      return false;
    }
    if (!needle) {
      return true;
    }
    const hay = `${targetName(row)} ${ruleLabel(row)}`.toLowerCase();
    return hay.includes(needle);
  });
  errorNode.classList.add("hidden");
  if (!filtered.length) {
    emptyNode.classList.remove("hidden");
    table.classList.add("hidden");
    return;
  }
  emptyNode.classList.add("hidden");
  table.classList.remove("hidden");
  rows.innerHTML = filtered.map((row) => {
    const name = targetName(row);
    const rule = ruleLabel(row);
    return `<tr data-testid="history-row">
      <td>${escapeHistory(name)}</td>
      <td data-testid="history-version">${escapeHistory(versionLabel(row))}</td>
      <td>${escapeHistory(rule)}</td>
      <td>${escapeHistory(row.execution || "")}</td>
      <td data-testid="history-result">${escapeHistory(resultSummary(row))}</td>
      <td><a data-testid="history-open-round" href="./?round=${encodeURIComponent(row.id)}">打开详情</a></td>
    </tr>`;
  }).join("");
}

async function main() {
  const { api } = await window.PpapProduct.connect();
  const product = window.PpapProduct.client(api);
  const me = await product.me();
  const userNode = document.getElementById("history-user");
  if (userNode) {
    userNode.textContent = me.ok ? me.body.identity.subject : "anonymous";
  }
  const search = document.getElementById("history-search");
  const filter = document.getElementById("history-execution-filter");
  const more = document.getElementById("history-more");
  let items = [], next = null, revision = 0, busy = false;
  async function load(reset) {
    const current = ++revision;
    busy = true; more.disabled = true;
    try {
      const listed = await product.rounds.list({q: search.value, execution: filter.value, offset: reset ? 0 : next});
      if (current !== revision) return;
      if (!listed.ok) throw new Error(listed.status === 401 ? "请先登录" : "无法加载审核记录");
      items = reset ? listed.body.rounds : items.concat(listed.body.rounds);
      next = listed.body.next_offset ?? null;
      renderRows(items, "", "");
      more.hidden = next === null;
    } catch(e) {
      if (current !== revision) return;
      const errorNode = document.getElementById("history-error");
      errorNode.textContent = e.message; errorNode.classList.remove("hidden");
    } finally { if (current === revision) { busy = false; more.disabled = false; } }
  }
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => load(true), 250); });
  filter.addEventListener("change", () => load(true));
  more.addEventListener("click", () => { if (!busy) load(false); });
  await load(true);
}
main();
