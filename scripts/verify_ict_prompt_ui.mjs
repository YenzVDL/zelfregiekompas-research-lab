// Live read-only UI check. No response fixtures or model download.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium } = require("playwright");
const { expect } = require("@playwright/test");
const evidence = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const baseURL = evidence.base_url || "http://127.0.0.1:8001";
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  // Keep production Host/Origin protections unchanged. Only the Origin header
  // is mapped to the allowed app origin for this isolated test server's port.
  if (baseURL === "http://127.0.0.1:8001") {
    await page.route("http://127.0.0.1:8001/**", async (route) => {
      const response = await route.fetch({ headers: { ...route.request().headers(), origin: "http://127.0.0.1:8000" } });
      await route.fulfill({ response });
    });
  }
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(baseURL);
  await page.getByRole("button", { name: "Scenarios", exact: true }).click();
  await page.getByRole("combobox", { name: "Dataset", exact: true }).selectOption(evidence.dataset_id);
  const versions = [evidence.drafts[0], evidence.revised_scenario].filter(Boolean);
  for (const scenario of versions) {
    if (!scenario) throw new Error("Live generation or revision did not produce a scenario");
    await page.locator("button.list-item").filter({ hasText: scenario.scenario_id }).click();
    const section = page.locator("details").filter({ has: page.locator("summary", { hasText: /^View generation prompt$/ }) });
    await section.locator("summary").click();
    const request = scenario.generation.request.payload;
    await expect(section.locator("pre").nth(0)).toHaveText(request.system);
    await expect(section.locator("pre").nth(1)).toHaveText(request.prompt);
    await expect(section.getByRole("heading", { name: "Output schema", exact: true })).toBeVisible();
    await expect(section.getByRole("heading", { name: "Effective generation settings", exact: true })).toBeVisible();
    await expect(section.getByRole("link", { name: "Export scenario with generation prompt" })).toHaveAttribute("href", `/api/scenarios/${scenario.id}/export`);
  }
  await page.screenshot({ path: fileURLToPath(new URL("../docs/" + (evidence.browser_screenshot || "browser-ict-generation-prompt.png"), import.meta.url)), fullPage: true });
  // The manually authored parent has no generation trace. No historical prompt may be fabricated.
  if (evidence.revision_parent) {
    await page.getByRole("combobox", { name: "Scenario version history", exact: true }).selectOption(evidence.revision_parent.id);
    const old = page.locator("details").filter({ has: page.locator("summary", { hasText: /^View generation prompt$/ }) });
    await old.locator("summary").click();
    await expect(old.getByText("Generation prompt not recorded", { exact: true })).toBeVisible();
  }
  expect(errors).toEqual([]);
  console.log(`Passed live browser on ${baseURL}: exact captured prompts, schema/settings and export links for ${versions.length} scenario version(s).${evidence.revision_parent ? " Missing historical prompt also checked." : " No request/response interception used."}`);
} finally {
  await browser.close();
}
