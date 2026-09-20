(function () {
  const path = (window.location.pathname.replace(/\/$/, "") || "/").split("/").pop();
  const page = path === "audit-files" || path === "audit-files.html"
    ? "audit-files"
    : path === "usage" || path === "usage.html"
      ? "usage"
      : path === "rules" || path === "rules.html"
        ? "rules"
      : "workbench";
  document.querySelectorAll("[data-nav]").forEach((node) => {
    node.classList.toggle("is-active", node.getAttribute("data-nav") === page);
  });
})();
