import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("Playwright uses installed cross-platform CLIs and one backend launcher", () => {
  const config = readFileSync(new URL("../playwright.config.js", import.meta.url), "utf8");

  assert.doesNotMatch(config, /\.venv[\\/]Scripts/);
  assert.doesNotMatch(config, /ragkb-(?:backend|worker)\.exe/);
  assert.match(config, /RAGKB_E2E_BACKEND \|\| "ragkb-backend"/);
  assert.match(config, /RAGKB_E2E_WORKER \|\| "ragkb-worker"/);
  assert.match(config, /node \.\/scripts\/run-e2e-backend\.mjs/);
  assert.doesNotMatch(config, /process\.env\.CI\s*\?\s*"bash/);
});
