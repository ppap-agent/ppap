function escapeUsage(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
async function main() {
  const { api } = await window.PpapProduct.connect();
  const product = window.PpapProduct.client(api);
  const errorNode = document.getElementById("usage-error");
  const rows = document.getElementById("usage-rows");
  const more = document.getElementById("usage-more");
  const form = document.getElementById("usage-filter");
  const me = await product.me();
  document.getElementById("usage-user").textContent = me.ok ? me.body.identity.subject : "anonymous";
  let next = null, items = [], busy = false, filter = {};
  const labels = {none: "无需上报", pending: "待上报", reporting: "正在确认", confirmed: "飞书已确认", unknown: "结果未知 · 请人工核对"};
  function render() {
    document.getElementById("usage-empty").classList.toggle("hidden", !!items.length);
    document.getElementById("usage-list").classList.toggle("hidden", !items.length);
    more.hidden = next === null;
    rows.innerHTML = items.map(row => `<tr data-testid="usage-row">
      <td>${escapeUsage(row.created_at)}</td><td>${escapeUsage(row.consumed_points)}</td>
      <td>${row.is_free_trial ? "试用" : "飞书额度"}</td>
      <td>${escapeUsage(labels[row.external_status] || row.external_status)}</td>
      <td>${escapeUsage(row.trace_id)}</td><td>${row.external_status === "pending" ? `<button data-report="${escapeUsage(row.id)}">确认上报</button>` : ""}</td></tr>`).join("");
  }
  async function load(reset) {
    if (busy) return;
    busy = true; more.disabled = true;
    if (reset) {
      filter = {};
      const since = document.getElementById("usage-since").value;
      const until = document.getElementById("usage-until").value;
      if (since) filter.since = since;
      if (until) filter.until = until;
    }
    try {
      const listed = await product.usage.list({...filter, offset: reset ? 0 : next});
      if (!listed.ok) throw new Error(listed.status === 401 ? "请先登录" : "无法加载用量记录，请检查日期后重试");
      items = reset ? listed.body.events : items.concat(listed.body.events);
      next = listed.body.next_offset ?? null;
      errorNode.classList.add("hidden"); render();
    } catch (e) { errorNode.textContent = e.message; errorNode.classList.remove("hidden"); }
    finally { busy = false; more.disabled = false; }
  }
  rows.addEventListener("click", async event => {
    const button = event.target.closest("[data-report]");
    if (!button || busy) return;
    busy = true; button.disabled = true;
    try {
      const response = await product.usage.report(button.dataset.report);
      if (response.body?.external_status) {
        items = items.map(row => row.id === button.dataset.report ? response.body : row);
        render();
      }
      if (!response.ok) {
        errorNode.textContent = response.body?.error === "feishu_app_ticket_missing"
          ? "缺少飞书应用票据，请配置事件回调后重试。" : "未确认上报。请刷新查看状态；未知结果不可重发。";
        errorNode.classList.remove("hidden");
      }
    } catch (_) { errorNode.textContent = "连接中断，请刷新状态，不要重复上报。"; errorNode.classList.remove("hidden"); }
    finally { busy = false; button.disabled = false; }
  });
  form.addEventListener("submit", event => { event.preventDefault(); load(true); });
  more.addEventListener("click", () => load(false));
  await load(true);
}
main();
