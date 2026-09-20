function targetName(row) {
  const inputs = row.inputs || [];
  const target = inputs.find((item) => item.role === "target");
  return (target && target.original_filename) || (inputs[0] && inputs[0].original_filename) || row.id;
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
    const hay = `${targetName(row)} ${(row.rule && row.rule.title) || ""}`.toLowerCase();
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
    const rule = (row.rule && row.rule.title) || "";
    return `<tr data-testid="history-row">
      <td>${name}</td>
      <td>${rule}</td>
      <td>${row.execution || ""}</td>
      <td><a data-testid="history-open-round" href="./?round=${encodeURIComponent(row.id)}">回访</a></td>
    </tr>`;
  }).join("");
}

async function main() {
  const { api } = await window.PpapProduct.connect();
  const product = window.PpapProduct.client(api);
  const listed = await product.rounds.list();
  if (!listed.ok) {
    const errorNode = document.getElementById("history-error");
    errorNode.textContent = listed.status === 401 ? "请先登录" : "无法加载审核记录";
    errorNode.classList.remove("hidden");
    return;
  }
  const items = listed.body.rounds || [];
  const search = document.getElementById("history-search");
  const filter = document.getElementById("history-execution-filter");
  const redraw = () => renderRows(items, search.value, filter.value);
  search.addEventListener("input", redraw);
  filter.addEventListener("change", redraw);
  redraw();
}

main();
