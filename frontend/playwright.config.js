import { defineConfig } from "@playwright/test";
import path from "node:path";

const apiPort = process.env.RAGKB_E2E_API_PORT || "8000";
const webPort = process.env.RAGKB_E2E_WEB_PORT || "4173";
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const webOrigin = `http://127.0.0.1:${webPort}`;
const storageRoot =
  process.env.RAGKB_E2E_STORAGE_ROOT ||
  path.resolve("../artifacts/frontend-e2e", `${Date.now()}-${process.pid}`, "storage");
const backendExecutable = process.env.RAGKB_E2E_BACKEND || "ragkb-backend";
const workerExecutable = process.env.RAGKB_E2E_WORKER || "ragkb-worker";
const backendCommand = "node ./scripts/run-e2e-backend.mjs";
Object.assign(process.env, {
  APP_ENV: "testing",
  RAG_RUNTIME_PROFILE: "local",
  VECTOR_BACKEND: "local",
  AUTH_MODE: "local_single_user",
  REAL_PROVIDER_CALLS_ENABLED: "false",
  EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED: "false",
  OTEL_ENABLED: "false",
  APP_HOST: "127.0.0.1",
  APP_PORT: apiPort,
  CORS_ORIGINS: webOrigin,
  LOCAL_STORAGE_ROOT: storageRoot,
  LOCAL_STORAGE_ORIGINAL_DIR: path.join(storageRoot, "original"),
  LOCAL_STORAGE_ARTIFACTS_DIR: path.join(storageRoot, "artifacts"),
  LOCAL_STORAGE_QUARANTINE_DIR: path.join(storageRoot, "quarantine"),
  LOCAL_STORAGE_TEMP_DIR: path.join(storageRoot, "temp"),
  LOCAL_STORAGE_AUDIT_DIR: path.join(storageRoot, "audit"),
  LOCAL_STORAGE_BACKUP_DIR: path.join(storageRoot, "backups"),
  QUEUE_DATABASE_PATH: path.join(storageRoot, "queue", "e2e.sqlite3"),
  VITE_API_BASE_URL: `${apiOrigin}/api/v1`,
  RAGKB_E2E_BACKEND: backendExecutable,
  RAGKB_E2E_WORKER: workerExecutable,
  RAGKB_E2E_STORAGE_ROOT: storageRoot,
});

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: webOrigin,
    channel: process.env.CI ? undefined : "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: backendCommand,
      url: `${apiOrigin}/health/live`,
      reuseExistingServer: false,
    },
    {
      command: "npm run build && node ./scripts/serve-dist.mjs",
      url: `${webOrigin}/health`,
      env: {
        ...process.env,
        PORT: webPort,
        FRONTEND_API_BASE_URL: `${apiOrigin}/api/v1`,
        FRONTEND_PUBLIC_ORIGIN: webOrigin,
      },
      reuseExistingServer: false,
    },
  ],
});
