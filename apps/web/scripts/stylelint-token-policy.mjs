import stylelint from "stylelint";

const ruleName = "ppap/token-policy";
const messages = stylelint.utils.ruleMessages(ruleName, {
  redeclared: (property) => `Do not redeclare generated token ${property}.`,
  unknown: (name) => `Unknown token reference ${name}.`,
  laundered: (property) => `Custom property ${property} cannot hide a raw color, shape, or elevation value.`,
  unproven: (property) => `Custom property ${property} must reference a generated token.`,
  structural: (name) => `Structural custom property ${name} cannot supply a visual value.`,
});

const meta = { url: "https://github.com/ppap-agent/ppap/blob/main/docs/DESIGN_SYSTEM.md" };
const visualProperty = /^(?:color|background(?:-color)?|border(?:-color|-radius)?|box-shadow|outline(?:-color)?|fill|stroke|caret-color|text-decoration-color)$/;

export function aliasTarget(value) {
  return value.match(/^\s*var\(\s*(--[a-z0-9-]+)\s*\)\s*$/i)?.[1] ?? null;
}

export function directThemeAliases(declarations, generated) {
  const structural = new Set();
  const targets = new Map();
  for (const [property, value] of declarations) {
    if (generated.has(property)) continue;
    const target = aliasTarget(value);
    if (target) {
      const known = targets.get(property) ?? [];
      known.push(target);
      targets.set(property, known);
    } else if (classifyCustomProperty(value) === "structural") structural.add(property);
  }
  const aliases = [];
  for (const [property, known] of targets) {
    if (!structural.has(property) && known.every((target) => generated.has(target))) aliases.push(property);
  }
  return { aliases, structural: [...structural] };
}

export function classifyCustomProperty(value) {
  if (aliasTarget(value)) return "alias";
  const lengths = value.match(/-?\d*\.?\d+(?:px|rem|em|%|vh|vw|dvh|dvw)/gi) ?? [];
  let stripped = value;
  let previous;
  do {
    previous = stripped;
    stripped = stripped.replace(/\bvar\(\s*--[a-z0-9-]+\s*\)/gi, " ");
    stripped = stripped.replace(/\b(?:calc|env)\([^()]*\)/gi, " ");
  } while (stripped !== previous);
  const residue = stripped
    .replace(/-?\d*\.?\d+(?:px|rem|em|%|vh|vw|dvh|dvw|ms|s)\b/gi, " ")
    .replace(/[%.,+\-*/()\s\d]/g, "");
  const hasRawColor = /#[0-9a-f]{3,8}\b/i.test(value) || /[a-z]/i.test(residue);
  if (hasRawColor || (lengths.length > 1 && !/^\s*calc\(/i.test(value))) return "visual";
  return "structural";
}

function rule(primary) {
  return (root, result) => {
    const valid = stylelint.utils.validateOptions(result, ruleName, {
      actual: primary,
      possible: { tokens: [String], aliases: [String], structural: [String], runtime: [String] },
    });
    if (!valid) return;
    const generated = new Set(primary.tokens);
    const aliases = new Set(primary.aliases ?? []);
    const structural = new Set(primary.structural ?? []);
    const runtime = new Set(primary.runtime ?? []);
    const aliasDeclarations = [];
    root.walkDecls((decl) => {
      if (!decl.prop.startsWith("--") || generated.has(decl.prop)) {
        if (decl.prop.startsWith("--") && generated.has(decl.prop)) {
          stylelint.utils.report({ ruleName, result, node: decl, message: messages.redeclared(decl.prop) });
        }
        return;
      }
      // Tailwind's --animate-* namespace names a keyframe, duration and easing: motion, not colour, shape or elevation.
      const kind = decl.prop.startsWith("--animate-") ? "structural" : classifyCustomProperty(decl.value);
      if (kind === "visual") {
        stylelint.utils.report({ ruleName, result, node: decl, message: messages.laundered(decl.prop) });
      } else if (kind === "structural") structural.add(decl.prop);
      else aliasDeclarations.push(decl);
    });
    for (const decl of aliasDeclarations) {
      if (structural.has(decl.prop)) continue;
      const target = aliasTarget(decl.value);
      if (target && generated.has(target)) aliases.add(decl.prop);
      else stylelint.utils.report({ ruleName, result, node: decl, message: messages.unproven(decl.prop) });
    }
    root.walkDecls((decl) => {
      if (decl.prop.startsWith("--")) return;
      for (const match of decl.value.matchAll(/var\(\s*(--[A-Za-z0-9-]+)/g)) {
        const name = match[1];
        if (generated.has(name) || aliases.has(name) || runtime.has(name) || name.startsWith("--radix-")) continue;
        if (structural.has(name)) {
          if (visualProperty.test(decl.prop)) {
            stylelint.utils.report({ ruleName, result, node: decl, message: messages.structural(name) });
          }
          continue;
        }
        stylelint.utils.report({ ruleName, result, node: decl, message: messages.unknown(name) });
      }
    });
  };
}

rule.ruleName = ruleName;
rule.messages = messages;
rule.meta = meta;

export default stylelint.createPlugin(ruleName, rule);
