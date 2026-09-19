async function readJson(url, options = {}) {
  const response = await fetch(url, { credentials: "include", headers: { Accept: "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

function targetName(row) {
  const inputs = row.inputs || [];
  const target = inputs.find((item) => item.role === "target");
  return (target && target.original_filename) || (inputs[0] && inputs[0].original_filename) || row.id;
}

async function main() {
  const runtime = (await readJson("./runtime-config.json")).body;
  const config = (await readJson(`${runtime.api_url}/public-config`)).body;
  const api = config.api_url;
  const errorNode = document.getElementById("history-error");
  const emptyNode = document.getElementById("history-empty");
  const table = document.getElementById("history-list");
  const rows = document.getElementById("history-rows");
  const listed = await readJson(`${api}/rounds`);
  if (!listed.ok) {
    errorNode.textContent = listed.status === 401 ? "请先登录" : "无法加载审核记录";
    errorNode.classList.remove("hidden");
    return;
  }
  const items = listed.body.rounds || [];
  if (!items.length) {
    emptyNode.classList.remove("hidden");
    return;
  }
  table.classList.remove("hidden");
  rows.innerHTML = items.map((row) => {
    const name = targetName(row);
    const rule = (row.rule && row.rule.title) || "";
    return `<tr data-testid="history-row">
      <td>${name}</td>
      <td>${rule}</td>
      <td>${row.execution || ""}</td>
      <td><a data-testid="history-open-round" href="./?round=${encodeURIComponent(row.id)}">回访</a></td>
    </tr>`;
  }).join("");
}

main();
