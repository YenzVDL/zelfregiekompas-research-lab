import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";
const isWindows = process.platform === "win32";
const python = path.resolve(
  "..",
  ".venv",
  isWindows ? "Scripts/python.exe" : "bin/python",
);
const edgeInstalled =
  isWindows &&
  existsSync("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe");
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8000",
    channel: edgeInstalled ? "msedge" : undefined,
    headless: true,
    viewport: { width: 1440, height: 1050 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `"${python}" -m uvicorn backend.main:app --app-dir .. --host 127.0.0.1 --port 8000`,
    url: "http://127.0.0.1:8000/api/health",
    reuseExistingServer: false,
    env: { RESEARCH_DB: `../data/browser-tests-${Date.now()}.sqlite3` },
    timeout: 30_000,
  },
});
