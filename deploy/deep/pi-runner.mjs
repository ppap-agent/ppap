#!/usr/bin/env -S node --disable-wasm-trap-handler
// Fixed SDK entrypoint. The Python adapter supplies only an execution-scoped grant.
import { readFileSync } from "node:fs";
import {
  createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager,
} from "@earendil-works/pi-coding-agent";

const version = JSON.parse(readFileSync(new URL("../package.json", import.meta.resolve("@earendil-works/pi-coding-agent")), "utf8")).version;
if (process.argv[2] === "--version") {
  console.log(version);
  process.exit(0);
}
const config = JSON.parse(readFileSync(process.argv[2], "utf8"));
const emit = (event) => process.stdout.write(JSON.stringify(event).replaceAll(config.token, "[redacted]") + "\n");
const usage = { input_tokens: 0, output_tokens: 0 };
let turns = 0, toolCalls = 0, completedToolCalls = 0, compactions = 0;
// Token counts are observations. A missing count never fails the stage; the gateway's
// request limit is the only model limit, and the wall deadline stops the process.
let usageKnown = true, lastMessage, session;
const recordUsage = (value) => {
  // Pi separates cached input from uncached input. Provider input includes both.
  const counts = [value?.input, value?.output, value?.cacheRead, value?.cacheWrite];
  if (counts.some((count) => !Number.isSafeInteger(count) || count < 0)) {
    usageKnown = false;
    return;
  }
  usage.input_tokens += counts[0] + counts[2] + counts[3];
  usage.output_tokens += counts[1];
};
try {
  if (version !== "1.5.0") throw new Error("Pi version mismatch");
  const settingsManager = SettingsManager.inMemory({
    // A transient provider failure may be retried a little; the gateway bounds requests.
    retry: { enabled: true, maxRetries: 2, baseDelayMs: 1000, provider: { maxRetries: 0 } },
    cacheWarming: "off",
  });
  const modelRuntime = await ModelRuntime.create({
    authPath: config.home + "/auth.json", modelsPath: null, refreshOnCreate: false,
    modelsStorePath: config.home + "/models-store.json",
  });
  modelRuntime.registerProvider("ppap", {
    baseUrl: config.base_url, api: "openai-responses", apiKey: config.token,
    models: [{ id: config.model, reasoning: true, input: ["text", "image"], contextWindow: 128000, maxTokens: 32768,
      // Required SDK metadata. PPAP records provider tokens and does not publish these price estimates.
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
  });
  const resourceLoader = new DefaultResourceLoader({
    cwd: process.cwd(), agentDir: config.home, settingsManager,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
  });
  await resourceLoader.reload();
  ({ session } = await createAgentSession({
    cwd: process.cwd(), agentDir: config.home, modelRuntime,
    model: modelRuntime.getModel("ppap", config.model), thinkingLevel: config.reasoning_effort,
    settingsManager, resourceLoader, sessionManager: SessionManager.inMemory(process.cwd()),
  }));
  const prepareRequest = session.agent.prepareRequest;
  session.agent.prepareRequest = async (...args) => {
    turns += 1;
    return prepareRequest?.(...args);
  };
  session.subscribe((event) => {
    if (event.type === "message_end" && event.message.role === "assistant") {
      lastMessage = event.message;
      recordUsage(lastMessage.usage);
      const { cost: _estimate, ...tokenUsage } = lastMessage.usage ?? {};
      emit({ ...event, message: { ...lastMessage, usage: tokenUsage } });
    } else if (event.type === "tool_execution_start") {
      toolCalls += 1;
      emit(event);
    } else if (event.type === "tool_execution_end") {
      completedToolCalls += 1;
      emit(event);
    } else if (event.type === "compaction_end" && event.result) {
      compactions += 1;
      recordUsage(event.result.usage);
      const { cost: _estimate, ...tokenUsage } = event.result.usage ?? {};
      emit({ ...event, result: { ...event.result, usage: tokenUsage } });
    }
  });
  await session.prompt(readFileSync(config.prompt, "utf8"));
  // prompt resolves after automatic work settles. End-of-turn alone is insufficient.
  const success = lastMessage?.stopReason === "stop";
  emit({ type: "result", subtype: success ? "success" : "error", is_error: !success,
    stop_reason: success ? "end_turn" : "error", usage_known: usageKnown,
    result: lastMessage?.content.filter((block) => block.type === "text").map((block) => block.text).join("\n") ?? "",
    num_turns: turns, usage, tool_calls: toolCalls, completed_tool_calls: completedToolCalls, compactions });
  process.exitCode = success ? 0 : 1;
} catch {
  // Raw SDK errors can include headers, provider responses, or credentials.
  emit({ type: "result", subtype: "error", is_error: true, stop_reason: "error",
    usage_known: usageKnown, num_turns: turns, usage,
    tool_calls: toolCalls, completed_tool_calls: completedToolCalls, compactions });
  process.exitCode = 1;
} finally {
  session?.dispose();
}
