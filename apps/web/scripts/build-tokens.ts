import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { projectDesignTokens } from "./design-tokens.ts";

const output = new URL("../src/generated/tokens.css", import.meta.url);

/** Write the runtime theme the stylesheet imports; unchanged content is not rewritten, so no reload is triggered. */
export async function writeDesignTokens(): Promise<void> {
  const { css } = await projectDesignTokens();
  let current = "";
  try {
    current = readFileSync(output, "utf8");
  } catch {
    mkdirSync(new URL(".", output), { recursive: true });
  }
  if (current !== css) writeFileSync(output, css);
}

if (import.meta.url === `file://${process.argv[1]}`) await writeDesignTokens();
