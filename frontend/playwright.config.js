import { defineConfig } from "@playwright/test";
import path from "node:path";

const storageRoot =
  process.env.RAGKB_E2E_STORAGE_ROOT ||
  path.resolve("../artifacts/frontend-e2e", `${Date.now()}-${process.pid}`, "storage");
const backendExecutable = process.env.RAGKB_E2E_BACKEND || "ragkb-backend";
const workerExecutable = process.env.RAGKB_E2E_WORKER || "ragkb-worker";
const backendCommand = "node ./scripts/run-e2e-backend.mjs";
Object.assign(process.env, {
  APP_ENV: "testing",
  RAG_RUNTIME_PROFILE: "local",
  APP_HOST: "127.0.0.1",
  APP_PORT: "8000",
  CORS_ORIGINS: "http://127.0.0.1:4173",
  LOCAL_STORAGE_ROOT: storageRoot,
  LOCAL_STORAGE_ORIGINAL_DIR: path.join(storageRoot, "original"),
  LOCAL_STORAGE_ARTIFACTS_DIR: path.join(storageRoot, "artifacts"),
  LOCAL_STORAGE_QUARANTINE_DIR: path.join(storageRoot, "quarantine"),
  LOCAL_STORAGE_TEMP_DIR: path.join(storageRoot, "temp"),
  LOCAL_STORAGE_AUDIT_DIR: path.join(storageRoot, "audit"),
  LOCAL_STORAGE_BACKUP_DIR: path.join(storageRoot, "backups"),
  QUEUE_DATABASE_PATH: path.join(storageRoot, "queue", "e2e.sqlite3"),
  VITE_API_BASE_URL: "http://127.0.0.1:8000/api/v1",
  RAGKB_E2E_BACKEND: backendExecutable,
  RAGKB_E2E_WORKER: workerExecutable,
  RAGKB_E2E_STORAGE_ROOT: storageRoot,
});

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: "http://127.0.0.1:4173",
    channel: process.env.CI ? undefined : "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: backendCommand,
      url: "http://127.0.0.1:8000/health/live",
      reuseExistingServer: false,
    },
    {
      command: "npm run build && node ./scripts/serve-dist.mjs",
      url: "http://127.0.0.1:4173/health",
      env: {
        ...process.env,
        PORT: "4173",
        FRONTEND_API_BASE_URL: "http://127.0.0.1:8000/api/v1",
        FRONTEND_PUBLIC_ORIGIN: "http://127.0.0.1:4173",
      },
      reuseExistingServer: false,
    },
  ],
});
