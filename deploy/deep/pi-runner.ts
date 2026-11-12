#!/usr/bin/env -S node --disable-wasm-trap-handler --max-old-space-size=768
// Fixed SDK entrypoint. The Python adapter supplies only an execution-scoped grant.
// Pi loads its fixed instructions (AGENTS.md) and reference skills natively from the
// agent directory the adapter prepared; nothing in the work directory is trusted.
import { readFileSync } from "node:fs";
import { join, sep } from "node:path";
import {
  type AgentSession, createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager,
} from "@earendil-works/pi-coding-agent";

type ReasoningEffort = "xhigh" | "high" | "medium" | "low";
interface RunnerConfig {
  home: string;
  prompt: string;
  base_url: string;
  token: string;
  model: string;
  reasoning_effort: ReasoningEffort;
}
interface TokenUsage {
  input?: number;
  output?: number;
  cacheRead?: number;
  cacheWrite?: number;
  cost?: unknown;
}
interface AssistantText {
  type: string;
  text?: string;
}
interface AssistantMessage {
  role: string;
  stopReason?: string;
  usage?: TokenUsage;
  content: AssistantText[];
}

const version: string = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.resolve("@earendil-works/pi-coding-agent")), "utf8"),
).version;
if (process.argv[2] === "--version") {
  console.log(version);
  process.exit(0);
}
const configPath = process.argv[2];
if (!configPath) throw new Error("Missing runner config");
const config: RunnerConfig = JSON.parse(readFileSync(configPath, "utf8"));
const emit = (event: object): void => {
  process.stdout.write(JSON.stringify(event).replaceAll(config.token, "[redacted]") + "\n");
};
const usage = { input_tokens: 0, output_tokens: 0 };
let turns = 0, toolCalls = 0, completedToolCalls = 0, compactions = 0;
// Token counts are observations. A missing count never fails the stage; the gateway's
// request limit is the only model limit, and the wall deadline stops the process.
let usageKnown = true;
let lastMessage: AssistantMessage | undefined;
let session: AgentSession | undefined;
const recordUsage = (value: TokenUsage | undefined): void => {
  // Pi separates cached input from uncached input. Provider input includes both.
  const counts = [value?.input, value?.output, value?.cacheRead, value?.cacheWrite];
  if (counts.some((count) => typeof count !== "number" || !Number.isSafeInteger(count) || count < 0)) {
    usageKnown = false;
    return;
  }
  const [input, output, cacheRead, cacheWrite] = counts as [number, number, number, number];
  usage.input_tokens += input + cacheRead + cacheWrite;
  usage.output_tokens += output;
};
const withoutCost = (value: TokenUsage | undefined): Omit<TokenUsage, "cost"> => {
  const { cost: _estimate, ...tokenUsage } = value ?? {};
  return tokenUsage;
};

/** Exactly the shipped AGENTS.md and skills may load; anything else fails the stage. */
function requireShippedResources(loader: DefaultResourceLoader, home: string): void {
  const contextFiles = loader.getAgentsFiles().agentsFiles.map((file) => file.path);
  const skills = loader.getSkills().skills;
  const skillsRoot = join(home, "skills") + sep;
  if (contextFiles.length !== 1 || contextFiles[0] !== join(home, "AGENTS.md")) {
    throw new Error("Unexpected Pi context files");
  }
  if (skills.length === 0 || skills.some((skill) => !skill.filePath.startsWith(skillsRoot))) {
    throw new Error("Unexpected Pi skills");
  }
  if (loader.getPrompts().prompts.length || loader.getExtensions().extensions.length) {
    throw new Error("Unexpected Pi prompts or extensions");
  }
  emit({ type: "resources", context_files: ["AGENTS.md"], skills: skills.map((skill) => skill.name).sort() });
}

try {
  if (version !== "1.5.0") throw new Error("Pi version mismatch");
  // Project resources (a .pi folder or context files in the work directory) are never trusted.
  const settingsManager = SettingsManager.inMemory({
    // A transient provider failure may be retried a little; the gateway bounds requests.
    retry: { enabled: true, maxRetries: 2, baseDelayMs: 1000, provider: { maxRetries: 0 } },
    cacheWarming: "off",
  }, { projectTrusted: false });
  const modelRuntime = await ModelRuntime.create({
    authPath: config.home + "/auth.json", modelsPath: null, refreshOnCreate: false,
    modelsStorePath: config.home + "/models-store.json",
  });
  modelRuntime.registerProvider("ppap", {
    baseUrl: config.base_url, api: "openai-responses", apiKey: config.token,
    models: [{ id: config.model, name: config.model, reasoning: true, input: ["text", "image"], contextWindow: 128000, maxTokens: 32768,
      // Required SDK metadata. PPAP records provider tokens and does not publish these price estimates.
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
  });
  const resourceLoader = new DefaultResourceLoader({
    cwd: process.cwd(), agentDir: config.home, settingsManager,
    noExtensions: true, noPromptTemplates: true, noThemes: true,
  });
  await resourceLoader.reload({ resolveProjectTrust: async () => false });
  requireShippedResources(resourceLoader, config.home);
  const model = modelRuntime.getModel("ppap", config.model);
  if (!model) throw new Error("Pi model unavailable");
  ({ session } = await createAgentSession({
    cwd: process.cwd(), agentDir: config.home, modelRuntime,
    model, thinkingLevel: config.reasoning_effort,
    settingsManager, resourceLoader, sessionManager: SessionManager.inMemory(process.cwd()),
  }));
  type PrepareRequest = NonNullable<AgentSession["agent"]["prepareRequest"]>;
  const prepareRequest = session.agent.prepareRequest;
  const counted: PrepareRequest = (...args) => {
    turns += 1;
    return prepareRequest?.(...args);
  };
  session.agent.prepareRequest = counted;
  session.subscribe((event) => {
    if (event.type === "message_end" && event.message.role === "assistant") {
      lastMessage = event.message as unknown as AssistantMessage;
      recordUsage(lastMessage.usage);
      emit({ ...event, message: { ...lastMessage, usage: withoutCost(lastMessage.usage) } });
    } else if (event.type === "tool_execution_start") {
      toolCalls += 1;
      emit(event);
    } else if (event.type === "tool_execution_end") {
      completedToolCalls += 1;
      emit(event);
    } else if (event.type === "compaction_end" && event.result) {
      compactions += 1;
      const result = event.result as { usage?: TokenUsage };
      recordUsage(result.usage);
      emit({ ...event, result: { ...event.result, usage: withoutCost(result.usage) } });
    }
  });
  await session.prompt(readFileSync(config.prompt, "utf8"));
  // prompt resolves after automatic work settles. End-of-turn alone is insufficient.
  const final: AssistantMessage | undefined = lastMessage;
  const success = final?.stopReason === "stop";
  emit({ type: "result", subtype: success ? "success" : "error", is_error: !success,
    stop_reason: success ? "end_turn" : "error", usage_known: usageKnown,
    result: final?.content.filter((block) => block.type === "text").map((block) => block.text ?? "").join("\n") ?? "",
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
