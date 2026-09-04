import { spawn } from "node:child_process";
import { createWriteStream, mkdirSync } from "node:fs";
import path from "node:path";

const executable = process.env.RAGKB_E2E_BACKEND || "ragkb-backend";
const logDirectory = path.resolve("../artifacts/frontend-e2e");
mkdirSync(logDirectory, { recursive: true });
const log = createWriteStream(path.join(logDirectory, "backend.log"), { flags: "a" });
const backend = spawn(executable, ["--host", "127.0.0.1", "--port", "8000"], {
  env: process.env,
  stdio: ["ignore", "pipe", "pipe"],
});

for (const stream of [backend.stdout, backend.stderr]) {
  stream.pipe(log, { end: false });
  stream.pipe(stream === backend.stdout ? process.stdout : process.stderr);
}

backend.once("error", (error) => {
  console.error(`failed to start ${executable}: ${error.message}`);
  process.exitCode = 1;
});
backend.once("exit", (code, signal) => {
  log.end();
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => backend.kill(signal));
}
