window.PpapProduct = (function () {
  async function readJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "include",
      headers: { Accept: "application/json" },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  }

  async function connect() {
    const runtime = (await readJson("./runtime-config.json")).body;
    const config = (await readJson(`${runtime.api_url}/public-config`)).body;
    return { api: config.api_url, config, runtime };
  }

  function client(api) {
    return {
      me: () => readJson(`${api}/me`),
      ready: () => readJson(`${api}/ready`),
      capabilities: () => readJson(`${api}/capabilities`),
      logout: () => readJson(`${api}/logout`, { method: "POST" }),
      isolatedDevSession: (subject) =>
        readJson(`${api}/isolated-dev/sessions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subject }),
        }),
      rules: () => readJson(`${api}/rules`),
      ruleVersions: {
        types: () => readJson(`${api}/rule-types`),
        get: (id) => readJson(`${api}/rules/${encodeURIComponent(id)}`),
        parseResult: (id) => readJson(`${api}/rules/parse/${encodeURIComponent(id)}`),
        parse: (body) => readJson(`${api}/rules/parse`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        }),
        publish: (body) => readJson(`${api}/rules`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        }),
      },
      resource: (id) => readJson(`${api}/resources/${encodeURIComponent(id)}`),
      files: {
        create: (meta) =>
          readJson(`${api}/files`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(meta),
          }),
        createVersion: (fileId, meta) =>
          readJson(`${api}/files/${encodeURIComponent(fileId)}/versions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(meta),
          }),
        downloadUrl: (fileId, versionId) =>
          `${api}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/download`,
      },
      drafts: {
        create: () => readJson(`${api}/drafts`, { method: "POST" }),
        get: (id) => readJson(`${api}/drafts/${encodeURIComponent(id)}`),
        save: (id, body) =>
          readJson(`${api}/drafts/${encodeURIComponent(id)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
        accept: (id, body) =>
          readJson(`${api}/drafts/${encodeURIComponent(id)}/accept`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
      },
      rounds: {
        list: () => readJson(`${api}/rounds`),
        get: (id) => readJson(`${api}/rounds/${encodeURIComponent(id)}`),
        review: (id, body) =>
          readJson(`${api}/rounds/${encodeURIComponent(id)}/reviews`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
        resubmit: (id, body) =>
          readJson(`${api}/rounds/${encodeURIComponent(id)}/resubmit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
        originalUrl: (roundId, fileId, versionId) =>
          `${api}/rounds/${encodeURIComponent(roundId)}/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/download`,
      },
      usage: {
        list: (params = {}) => readJson(`${api}/usage?${new URLSearchParams(params)}`),
        report: (id) => readJson(`${api}/integrations/feishu/usage/events/${encodeURIComponent(id)}/report`, { method: "POST" }),
      },
    };
  }

  return { readJson, connect, client };
})();
