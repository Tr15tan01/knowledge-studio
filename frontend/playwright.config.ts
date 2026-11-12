import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * End-to-end tests against the real stack: FastAPI + background worker + MongoDB, and a
 * production build of this frontend. External AI providers are replaced by deterministic fakes
 * inside the API process (tests/e2e_server.py refuses to start unless APP_ENV=test).
 *
 * Requirements: a MongoDB replica set at E2E_MONGODB_URI (default local rs0) and the backend
 * virtualenv. On Windows the venv python is .venv\Scripts\python.exe.
 */
const API_PORT = Number(process.env.E2E_API_PORT || 8100);
const WEB_PORT = Number(process.env.E2E_WEB_PORT || 3100);
const backendDir = path.resolve(here, "../backend");
const python = process.platform === "win32" ? path.join(backendDir, ".venv", "Scripts", "python.exe") : path.join(backendDir, ".venv", "bin", "python");
const db = process.env.E2E_DB || `ks_e2e_${Date.now().toString(36)}`;

const backendEnv: Record<string, string> = {
  APP_ENV: "test",
  MONGODB_URI: process.env.E2E_MONGODB_URI || "mongodb://127.0.0.1:27017/?replicaSet=rs0",
  MONGODB_DATABASE: db,
  SESSION_SECRET: "e2e-only-session-secret-0123456789abcdef",
  LOCAL_STORAGE_DIR: path.join(backendDir, "var", "e2e-storage", db),
  APP_PUBLIC_URL: `http://localhost:${WEB_PORT}`,
  CORS_ALLOWED_ORIGINS: `http://localhost:${WEB_PORT}`,
  PADDLE_ENABLED: "0",
  EMAIL_PROVIDER: "none",
  LOG_JSON: "0",
  LOG_LEVEL: "WARNING",
  RATE_LIMIT_LOGIN_PER_15M: "50",
  RATE_LIMIT_REGISTER_PER_HOUR: "50",
};
// Workers inherit this, so tests can run backend CLI commands (e.g. create-admin) against the same database.
process.env.E2E_DB = db;
process.env.E2E_BACKEND_ENV = JSON.stringify(backendEnv);
process.env.E2E_BACKEND_DIR = backendDir;
process.env.E2E_PYTHON = python;

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } : {},
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `"${python}" -m tests.e2e_server --port ${API_PORT}`,
      cwd: backendDir,
      url: `http://127.0.0.1:${API_PORT}/health/ready`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: backendEnv,
    },
    {
      command: process.env.E2E_SKIP_BUILD ? `npx next start --port ${WEB_PORT}` : `npx next build && npx next start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/pricing`,
      reuseExistingServer: false,
      timeout: 300_000,
      env: { API_INTERNAL_URL: `http://127.0.0.1:${API_PORT}`, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
