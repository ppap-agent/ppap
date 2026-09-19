async function readJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error("request_failed");
  }
  return response.json();
}

async function main() {
  const runtime = await readJson("./runtime-config.json");
  const config = await readJson(`${runtime.api_url}/public-config`);
  document.getElementById("build-revision").textContent = config.git_sha || "unknown";
  try {
    const ready = await readJson(`${config.api_url}/ready`);
    document.getElementById("stack-state").textContent = ready.status;
  } catch (err) {
    document.getElementById("stack-state").textContent = "not_ready";
  }
  const capabilities = await readJson(`${config.api_url}/capabilities`);
  document.getElementById("capabilities").textContent = JSON.stringify(capabilities, null, 2);
}

main().catch(() => {
  document.getElementById("build-revision").textContent = "unavailable";
  document.getElementById("stack-state").textContent = "unavailable";
});
