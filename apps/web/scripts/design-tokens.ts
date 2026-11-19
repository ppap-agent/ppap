import { readFileSync } from "node:fs";
import { lint } from "@google/design.md/linter";
import { build, defineConfig, parse, type TokenNormalized, type TokenNormalizedSet } from "@terrazzo/parser";
import css from "@terrazzo/plugin-css";

// One projection serves the runtime theme and the token frontmatter of design.md (docs/DESIGN_SYSTEM.md).
const resolver = new URL("../tokens/web.resolver.json", import.meta.url);

function variableName(token: TokenNormalized): string {
  if (token.id.startsWith("color.")) return token.id.slice("color.".length);
  if (token.id === "rounded.control") return "radius";
  if (token.id === "font.sans") return "font-family-sans";
  return token.id.replaceAll(".", "-");
}

function rgb(components: readonly (number | "none")[], alpha: number): string {
  const [r, g, b] = components.map((value) => Math.round(Number(value) * 255));
  return `rgb(${r} ${g} ${b} / ${Math.round(alpha * 100)}%)`;
}

function cssValue(token: TokenNormalized): string | undefined {
  if (token.$type !== "color") return undefined;
  const { hex, alpha, components } = token.$value;
  if (!hex || !/^#[0-9a-f]{6}$/.test(hex)) throw new Error(`Color ${token.id} did not resolve to a 6-digit sRGB hex.`);
  return alpha !== undefined && alpha < 1 ? rgb(components, alpha) : hex;
}

const config = defineConfig(
  {
    plugins: [
      css({
        filename: "tokens.css",
        // The plugin also uses this name in aliases, where the CSS -- prefix is required.
        variableName: (token) => `--${variableName(token)}`,
        transform: cssValue,
        permutations: [
          { input: { theme: "light" }, prepare: (contents) => `:root {\n${contents}\n}\n` },
          // The app and the Docs Add-on switch themes with the .dark class.
          { input: { theme: "dark" }, prepare: (contents) => `.dark {\n${contents}\n}\n` },
        ],
      }),
    ],
  },
  { cwd: new URL("../", import.meta.url) },
);

function hex(tokens: TokenNormalizedSet, id: string): string {
  const token = tokens[id];
  if (!token || token.$type !== "color" || !token.$value.hex) throw new Error(`Missing resolved color ${id}.`);
  return token.$value.hex;
}

function opaque(tokens: TokenNormalizedSet, id: string): boolean {
  const token = tokens[id];
  return token?.$type === "color" && (token.$value.alpha === undefined || token.$value.alpha >= 1);
}

function dimension(tokens: TokenNormalizedSet, id: string): string {
  const token = tokens[id];
  if (!token || token.$type !== "dimension") throw new Error(`Missing resolved dimension ${id}.`);
  return `${token.$value.value}${token.$value.unit}`;
}

function fontFamily(tokens: TokenNormalizedSet, id: string): string {
  const token = tokens[id];
  if (!token || token.$type !== "fontFamily") throw new Error(`Missing resolved font family ${id}.`);
  const generic = new Set(["serif", "sans-serif", "monospace", "system-ui", "ui-sans-serif", "ui-monospace"]);
  const families = typeof token.$value === "string" ? [token.$value] : token.$value;
  return families.map((name) => (generic.has(name) || /^-?[A-Za-z][A-Za-z-]*$/.test(name) ? name : `"${name}"`)).join(", ");
}

// design.md colour names follow the DESIGN.md format; the runtime keeps the shadcn names the components use.
const projected: readonly (readonly [string, string])[] = [
  ["primary", "primary"],
  ["on-primary", "primary-foreground"],
  ["background", "canvas"],
  ["surface", "card"],
  ["on-surface", "foreground"],
  ["muted", "muted-foreground"],
  ["border", "border"],
  ["input-border", "input"],
  ["secondary", "secondary"],
  ["accent", "primary-soft"],
  ["success", "success"],
  ["warning", "warning"],
  ["error", "destructive"],
  ["info", "info"],
];

// Each role is shown on the component that uses it, once per theme.
function components(prefix: string, suffix: string, tokens: TokenNormalizedSet): string {
  const c = (name: string) => `"{colors.${prefix}${name}}"`;
  // A translucent border is not a design.md colour, so its components are projected only when it is opaque.
  const lines = [
    `  input${suffix}:\n    backgroundColor: ${c("surface")}\n    textColor: ${c("on-surface")}\n    rounded: "{rounded.control}"`,
    ...(opaque(tokens, "color.border") ? [`  divider${suffix}:\n    backgroundColor: ${c("border")}`] : []),
    ...(opaque(tokens, "color.input") ? [`  input-outline${suffix}:\n    backgroundColor: ${c("input-border")}`] : []),
  ];
  return `${lines.join("\n")}
  button-primary${suffix}:
    backgroundColor: ${c("primary")}
    textColor: ${c("on-primary")}
    rounded: "{rounded.control}"
  button-secondary${suffix}:
    backgroundColor: ${c("secondary")}
    textColor: ${c("on-surface")}
    rounded: "{rounded.control}"
  button-destructive${suffix}:
    backgroundColor: ${c("error")}
    textColor: ${c("on-primary")}
    rounded: "{rounded.control}"
  page${suffix}:
    backgroundColor: ${c("background")}
    textColor: ${c("on-surface")}
  card${suffix}:
    backgroundColor: ${c("surface")}
    textColor: ${c("on-surface")}
    rounded: "{rounded.control}"
  selected-row${suffix}:
    backgroundColor: ${c("accent")}
    textColor: ${c("on-surface")}
  caption${suffix}:
    textColor: ${c("muted")}
  badge-success${suffix}:
    textColor: ${c("success")}
  badge-warning${suffix}:
    textColor: ${c("warning")}
  badge-info${suffix}:
    textColor: ${c("info")}`;
}

// The design.md roles of each step of the type scale (Tailwind text-<step> at runtime).
const typeRoles: readonly (readonly [string, string, number])[] = [
  ["label-sm", "2xs", 600],
  ["body-sm", "xs", 400],
  ["label-md", "xs", 500],
  ["body-md", "sm", 400],
  ["body-lg", "base", 400],
  ["headline-sm", "base", 600],
  ["headline-md", "lg", 600],
  ["headline-lg", "xl", 600],
  ["headline-xl", "2xl", 600],
];

function typography(tokens: TokenNormalizedSet): string {
  const family = fontFamily(tokens, "font.sans");
  return typeRoles
    .map(([role, step, weight]) => `  ${role}:\n    fontFamily: '${family}'\n    fontSize: ${dimension(tokens, `font.size.${step}`)}\n    fontWeight: ${weight}\n    lineHeight: ${dimension(tokens, `font.leading.${step}`)}`)
    .join("\n");
}

function frontmatter(light: TokenNormalizedSet, dark: TokenNormalizedSet): string {
  const lines = (tokens: TokenNormalizedSet, prefix: string) =>
    projected.filter(([, id]) => opaque(tokens, `color.${id}`)).map(([name, id]) => `  ${prefix}${name}: "${hex(tokens, `color.${id}`)}"`);
  return `---
version: alpha
name: PPAP Web
description: "PRD V3.33 palette: grey-blue neutrals, one blue for commitments, a dark navigation rail, crisp 1px borders and 8px radius for the PPAP web app and Docs Add-on."
colors:
${[...lines(light, ""), ...lines(dark, "dark-")].join("\n")}
typography:
${typography(light)}
rounded:
  control: ${dimension(light, "rounded.control")}
components:
${components("", "", light)}
${components("dark-", "-dark", dark)}
---
`;
}

function designBody(source: string): string {
  const close = source.indexOf("\n---\n", 4);
  if (!source.startsWith("---\n") || close < 0) throw new Error("design.md frontmatter is missing.");
  return source.slice(close + "\n---\n".length);
}

function lintDesignMd(contents: string): void {
  const report = lint(contents);
  if (report.summary.errors > 0) {
    const errors = report.findings.filter((finding) => finding.severity === "error").map((finding) => finding.message);
    throw new Error(`design.md lint reported errors:\n${errors.join("\n")}`);
  }
}

export interface DesignTokenProjection {
  readonly css: string;
  readonly designMd: string;
  readonly theme: {
    readonly light: { readonly background: string; readonly card: string };
    readonly dark: { readonly background: string; readonly card: string };
  };
}

export function projectDesignTokens(): Promise<Omit<DesignTokenProjection, "designMd">>;
export function projectDesignTokens(designMdSource: string, filename?: URL): Promise<DesignTokenProjection>;
export async function projectDesignTokens(designMdSource?: string, filename = resolver): Promise<Omit<DesignTokenProjection, "designMd"> | DesignTokenProjection> {
  const parsed = await parse([{ filename, src: readFileSync(filename, "utf8") }], { config });
  if (!parsed.resolver) throw new Error("Web token resolver did not load.");
  const light = parsed.resolver.apply({ theme: "light" });
  const dark = parsed.resolver.apply({ theme: "dark" });
  const built = await build(light, { resolver: parsed.resolver, sources: parsed.sources, config });
  const file = built.outputFiles.find((entry) => String(entry.filename).endsWith("tokens.css"));
  if (!file) throw new Error("Terrazzo did not generate tokens.css.");
  const theme = {
    light: { background: hex(light, "color.canvas"), card: hex(light, "color.card") },
    dark: { background: hex(dark, "color.canvas"), card: hex(dark, "color.card") },
  };
  const runtime = { css: `/* Generated from tokens/ by scripts/design-tokens.ts. Do not edit. */\n${String(file.contents).replace(/^--/gm, "  --")}`, theme };
  if (designMdSource === undefined) return runtime;
  const designMd = `${frontmatter(light, dark)}${designBody(designMdSource)}`;
  lintDesignMd(designMd);
  return { ...runtime, designMd };
}
