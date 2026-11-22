import { readFileSync, writeFileSync } from "node:fs";
import { projectDesignTokens } from "./design-tokens.ts";

// design.md's token frontmatter is a projection of tokens/; its prose is reviewed by people (tokens/README.md).
const [mode, path = "design.md"] = process.argv.slice(2);
if (mode !== "update" && mode !== "check") throw new Error("Expected update or check.");
const source = readFileSync(path, "utf8");
const { designMd } = await projectDesignTokens(source);
if (mode === "update") writeFileSync(path, designMd);
else if (source !== designMd) {
  console.error("design.md is stale. Run pnpm design:update and review the projection.");
  process.exit(1);
}
