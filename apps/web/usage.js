async function main() {
  const { api } = await window.PpapProduct.connect();
  const product = window.PpapProduct.client(api);
  const errorNode = document.getElementById("usage-error");
  const emptyNode = document.getElementById("usage-empty");
  const table = document.getElementById("usage-list");
  const rows = document.getElementById("usage-rows");
  const listed = await product.usage.list();
  if (!listed.ok) {
    errorNode.textContent = listed.status === 401 ? "请先登录" : "无法加载用量记录";
    errorNode.classList.remove("hidden");
    return;
  }
  const items = listed.body.events || [];
  if (!items.length) {
    emptyNode.classList.remove("hidden");
    return;
  }
  table.classList.remove("hidden");
  rows.innerHTML = items.map((row) => {
    const trial = row.is_free_trial ? "试用" : "确认中";
    return `<tr data-testid="usage-row">
      <td>${row.created_at || ""}</td>
      <td>${row.consumed_points}</td>
      <td>${trial}</td>
      <td>${row.external_status || ""}</td>
      <td>${row.trace_id || ""}</td>
    </tr>`;
  }).join("");
}

main();
