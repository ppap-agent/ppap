import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { ESLint } from "eslint";
import stylelint from "stylelint";
import { writeDesignTokens } from "./build-tokens.ts";
import { projectDesignTokens } from "./design-tokens.ts";

const web = new URL("../", import.meta.url);
const designMd = new URL("../../../design.md", import.meta.url);

test("builds the runtime theme without reading or projecting design.md", async () => {
  const runtime = await projectDesignTokens();
  assert.match(runtime.css, /--canvas:/);
  assert.ok(!("designMd" in runtime));
});

test("projects one palette into the runtime theme and design.md, and design.md is current", async () => {
  const source = readFileSync(designMd, "utf8");
  const projected = await projectDesignTokens(source);
  for (const theme of [projected.theme.light, projected.theme.dark]) {
    assert.match(theme.background, /^#[0-9a-f]{6}$/);
    assert.ok(projected.css.includes(`--canvas: ${theme.background};`));
    assert.ok(projected.css.includes(`--card: ${theme.card};`));
    assert.ok(projected.designMd.includes(`"${theme.background}"`));
    assert.ok(projected.designMd.includes(`"${theme.card}"`));
  }
  assert.notEqual(projected.theme.light.background, projected.theme.dark.background);
  // The app and the Docs Add-on switch themes with the .dark class.
  assert.match(projected.css, /^\.dark \{/m);
  const radius = JSON.parse(readFileSync(new URL("../tokens/shared.tokens.json", import.meta.url), "utf8")).rounded.control.$value;
  assert.ok(projected.css.includes(`--radius: ${radius.value}${radius.unit};`));
  const defined = new Set([...projected.css.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
  const references = [...projected.css.matchAll(/var\(([^,)]+)/g)].map((match) => match[1]);
  assert.ok(references.every((name) => defined.has(name)));
  assert.equal(projected.designMd, source, "design.md is stale: run pnpm design:update");
});

test("an edited canonical token reaches both the runtime theme and design.md", async () => {
  const directory = mkdtempSync(join(tmpdir(), "ppap-tokens-"));
  try {
    cpSync(new URL("../tokens", import.meta.url), directory, { recursive: true });
    const path = join(directory, "light.tokens.json");
    const light = JSON.parse(readFileSync(path, "utf8"));
    light.color.primary.$value = { colorSpace: "srgb", components: [0.1, 0.2, 0.3], hex: "#1a334d" };
    writeFileSync(path, JSON.stringify(light));
    const shared = join(directory, "shared.tokens.json");
    const tokens = JSON.parse(readFileSync(shared, "utf8"));
    tokens.rounded.control.$value = { value: 0.5, unit: "rem" };
    writeFileSync(shared, JSON.stringify(tokens));
    const projected = await projectDesignTokens(readFileSync(designMd, "utf8"), pathToFileURL(join(directory, "web.resolver.json")));
    assert.ok(projected.css.includes("--primary: #1a334d;"));
    assert.ok(projected.css.includes("--radius: 0.5rem;"));
    assert.ok(projected.designMd.includes('  primary: "#1a334d"'));
    assert.ok(projected.designMd.includes("  control: 0.5rem"));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("check fails without writing; update rewrites only the frontmatter and keeps the prose", () => {
  const directory = mkdtempSync(join(tmpdir(), "ppap-design-document-"));
  try {
    const source = readFileSync(designMd, "utf8");
    const end = source.indexOf("\n---\n", 4) + 5;
    const prose = "\n# Human guidance\nPreserve this exact text.\n";
    const stale = source.slice(0, end).replace(/name: PPAP Web/, "name: Stale Projection") + prose;
    const path = join(directory, "design.md");
    writeFileSync(path, stale);
    const run = (mode: string) => spawnSync(process.execPath, [new URL("design-document.ts", import.meta.url).pathname, mode], { cwd: directory, encoding: "utf8" });
    assert.equal(run("check").status, 1);
    assert.equal(readFileSync(path, "utf8"), stale);
    assert.equal(run("update").status, 0);
    assert.equal(readFileSync(path, "utf8"), source.slice(0, end) + prose);
    assert.equal(run("check").status, 0);
    assert.deepEqual(readdirSync(directory), ["design.md"]);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("authored styles use tokens: raw colours, unknown tokens and laundered values are rejected", async () => {
  await writeDesignTokens();
  const lint = (code: string, file = "src/sample.css") => stylelint.lint({ code, codeFilename: new URL(file, web).pathname, configFile: new URL("stylelint.config.mjs", web).pathname, cwd: web.pathname });
  const accepted = await stylelint.lint({ files: "src/**/*.css", configFile: new URL("stylelint.config.mjs", web).pathname, cwd: web.pathname });
  assert.equal(accepted.errored, false);
  const rejected = await lint(".sample { color: #ff00aa; background: var(--not-a-token); }\n");
  const rules = rejected.results[0]?.warnings.map((warning) => warning.rule) ?? [];
  assert.ok(rules.includes("color-no-hex"));
  assert.ok(rules.includes("ppap/token-policy"));
  const structural = await lint(".sample { --track: 24px; width: var(--track); color: var(--foreground); }\n");
  assert.equal(structural.errored, false);
  const laundered = await lint(".sample { --local-elevation: 4px 4px 0 var(--border); box-shadow: var(--local-elevation); }\n");
  assert.ok(laundered.results[0]?.warnings.some((warning) => warning.rule === "ppap/token-policy"));
  const redeclared = await lint(".sample { --primary: var(--card); }\n");
  assert.ok(redeclared.results[0]?.warnings.some((warning) => warning.rule === "ppap/token-policy"));
});

test("page classes use tokens: an arbitrary colour class and an inline colour style are rejected", async () => {
  const eslint = new ESLint({ cwd: web.pathname });
  const file = new URL("src/audit/sample.tsx", web).pathname;
  const arbitrary = await eslint.lintText('export function Sample() { return <div className="bg-[#fff]" /> }\n', { filePath: file });
  assert.ok((arbitrary[0]?.errorCount ?? 0) > 0);
  const inline = await eslint.lintText('export function Sample() { return <div style={{ color: "#fff" }} /> }\n', { filePath: file });
  assert.ok((inline[0]?.errorCount ?? 0) > 0);
  const token = await eslint.lintText('export function Sample() { return <div className="bg-primary text-primary-foreground" /> }\n', { filePath: file });
  assert.equal(token[0]?.errorCount, 0);
});
