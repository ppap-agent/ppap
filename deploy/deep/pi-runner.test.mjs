import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const runner = fileURLToPath(new URL("./pi-runner.mjs", import.meta.url));
function respond(response, item, number, status = "completed", reportUsage = true) {
  response.writeHead(200, { "Content-Type": "text/event-stream" });
  const send = (event) => response.write(`data: ${JSON.stringify(event)}\n\n`);
  const metadata = { id: `resp_${number}`, model: "test-model", output: [], status: "in_progress" };
  send({ type: "response.created", response: metadata });
  send({ type: "response.output_item.added", output_index: 0,
    item: item.type === "function_call" ? { ...item, arguments: "" } : { ...item, content: [] } });
  if (item.type === "function_call") {
    send({ type: "response.function_call_arguments.delta", output_index: 0, delta: item.arguments });
  } else {
    send({ type: "response.content_part.added", output_index: 0, content_index: 0,
      part: { type: "output_text", text: "", annotations: [] } });
    send({ type: "response.output_text.delta", output_index: 0, content_index: 0, delta: item.content[0].text });
  }
  send({ type: "response.output_item.done", output_index: 0, item });
  send({ type: status === "completed" ? "response.completed" : "response.incomplete",
    response: { ...metadata, status, output: [item],
      ...(status === "incomplete" ? { incomplete_details: { reason: "max_output_tokens" } } : {}),
      ...(reportUsage ? { usage: { input_tokens: 13, output_tokens: 5, total_tokens: 18, input_tokens_details: { cached_tokens: 10 } } } : {}) } });
  response.end();
}
const textItem = { type: "message", id: "msg_1", role: "assistant", status: "completed",
  content: [{ type: "output_text", text: '{"completed":true,"artifact":"checker.py"}', annotations: [] }] };
const writeItem = { type: "function_call", id: "fc_1", call_id: "call_1", name: "write",
  arguments: JSON.stringify({ path: "checker.py", content: "def create_audit(audit_file, rule, group_context=None): return None\n" }) };

async function runFixture(t, handler, addressSpaceLimit = false) {
  const work = await mkdtemp(join(tmpdir(), "ppap-pi-test-"));
  t.after(() => rm(work, { recursive: true, force: true }));
  let calls = 0;
  const bodies = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    bodies.push(JSON.parse(raw));
    calls += 1;
    handler(response, calls);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  await mkdir(join(work, "home"));
  await writeFile(join(work, "prompt.txt"), "Write checker.py, then return its completion receipt.");
  await writeFile(join(work, "request.json"), JSON.stringify({
    home: join(work, "home"), prompt: join(work, "prompt.txt"), token: "test-grant-secret",
    base_url: `http://127.0.0.1:${server.address().port}/v1`, model: "test-model",
    reasoning_effort: "low",
  }));
  const command = addressSpaceLimit ? "prlimit" : runner;
  const args = [...(addressSpaceLimit ? ["--as=1610612736", "--core=0", "--", runner] : []), join(work, "request.json")];
  const child = spawn(command, args, {
    cwd: work, env: { PATH: process.env.PATH, HOME: join(work, "home"), PI_OFFLINE: "1", PI_TELEMETRY: "0" },
  });
  let stdout = "", stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const timer = setTimeout(() => child.kill("SIGKILL"), 30000);
  const code = await new Promise((resolve) => child.on("close", resolve));
  clearTimeout(timer);
  assert.equal(stderr, "");
  assert.ok(!stdout.includes("test-grant-secret"));
  const events = stdout.trim().split("\n").map((line) => JSON.parse(line));
  return { work, calls, bodies, code, events, result: events.at(-1) };
}

test("production launcher completes HTTP and file work under the 1536 MiB guest address limit", {
  skip: process.platform !== "linux",
}, async (t) => {
  const run = await runFixture(t, (response, calls) => respond(response, calls === 1 ? writeItem : textItem, calls), true);
  assert.equal(run.code, 0);
  assert.equal(run.calls, 2);
  assert.equal(run.result.is_error, false);
  assert.deepEqual(run.result.usage, { input_tokens: 26, output_tokens: 10 });
  assert.match(await readFile(join(run.work, "checker.py"), "utf8"), /def create_audit/);
});

test("Pi SDK writes the actual file, settles, and counts cached input once", async (t) => {
  const run = await runFixture(t, (response, calls) => respond(response, calls === 1 ? writeItem : textItem, calls));
  assert.equal(run.code, 0);
  assert.equal(run.calls, 2);
  assert.equal(run.result.is_error, false);
  assert.equal(run.result.num_turns, 2);
  assert.deepEqual(run.result.usage, { input_tokens: 26, output_tokens: 10 });
  assert.equal(run.result.tool_calls, 1);
  assert.equal(run.result.completed_tool_calls, 1);
  assert.match(await readFile(join(run.work, "checker.py"), "utf8"), /def create_audit/);
  assert.equal(run.bodies[0].model, "test-model");
  assert.ok(run.events.filter((event) => event.type === "message_end").every((event) => !("cost" in event.message.usage)));
});

test("A completed stage without provider token usage is still a success", async (t) => {
  const run = await runFixture(t, (response, calls) => respond(response, calls === 1 ? writeItem : textItem, calls, "completed", false));
  assert.equal(run.code, 0);
  assert.equal(run.result.is_error, false);
  assert.match(await readFile(join(run.work, "checker.py"), "utf8"), /def create_audit/);
});

test("A transient provider failure is retried a bounded number of times, then fails", async (t) => {
  const run = await runFixture(t, (response) => {
    response.writeHead(503, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: { message: "unavailable" } }));
  });
  assert.equal(run.calls, 3);
  assert.equal(run.code, 1);
  assert.equal(run.result.is_error, true);
});

test("A retried provider failure can still complete the stage", async (t) => {
  const run = await runFixture(t, (response, calls) => {
    if (calls === 1) {
      response.writeHead(503, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "unavailable" } }));
      return;
    }
    respond(response, calls === 2 ? writeItem : textItem, calls);
  });
  assert.equal(run.code, 0);
  assert.equal(run.result.is_error, false);
  assert.match(await readFile(join(run.work, "checker.py"), "utf8"), /def create_audit/);
});

test("Pi cannot report success for truncated output", async (t) => {
  const run = await runFixture(t, (response, calls) => respond(response, textItem, calls, "incomplete"));
  assert.equal(run.calls, 1);
  assert.equal(run.code, 1);
  assert.equal(run.result.is_error, true);
});
