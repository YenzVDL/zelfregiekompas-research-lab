import { test, expect } from "@playwright/test";

test("Models shows real unavailable-connection and install failures without claiming execution", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await page.getByRole("textbox", { name: "Ollama address" }).fill("http://127.0.0.1:1");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByText("Saved address:")).toContainText("http://127.0.0.1:1");
  await page.getByRole("button", { name: "Test connection", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Cannot connect to Ollama");
  await expect(page.getByText(/Connection failed ·/)).toBeVisible();
  await page.getByText("Advanced / install by exact tag", { exact: true }).click();
  await page.getByRole("textbox", { name: "Exact model name and tag" }).fill("fixture:tiny");
  await page.getByRole("button", { name: "Install exact tag", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Install fixture:tiny");
  await expect(page.getByText("Installed and verified locally", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Test inference", exact: true })).toBeDisabled();
  await page.screenshot({ path: "../docs/browser-models-offline.png", fullPage: true });
  // Restore this isolated browser-test database's default for the following tests.
  await page.getByRole("textbox", { name: "Ollama address" }).fill("http://127.0.0.1:11434");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByText("Saved address:")).toContainText("http://127.0.0.1:11434");
});

test("test-only API fixtures: explicit install, metadata and shared selectors with separate evaluator", async ({ page }) => {
  // Browser wiring verification only. These fixtures do not represent installed model weights.
  let installed = false, pulls = 0;
  const models = ["browser-fixture:tiny", "evaluator-fixture:tiny"].map((name) => ({
    name, digest: `sha256:${name}`, size: 1024 ** 3,
    details: { parameter_size: "1B", quantization_level: "Q4_K_M" },
  }));
  await page.route("**/api/models", (route) => route.fulfill({ json: { available: true, models: installed ? models : [] } }));
  await page.route("**/api/models/install", (route) => {
    pulls++; installed = true;
    return route.fulfill({ contentType: "application/x-ndjson", body:
      '{"status":"pulling layer","digest":"fixture-layer","completed":100,"total":100}\n' +
      JSON.stringify({ done: true, status: "Installed and verified locally", model: models[0] }) + "\n" });
  });
  await page.route("**/api/models/test", (route) => route.fulfill({ json: { success: true, response: "TEST FIXTURE: ready", elapsed_seconds: 0.01 } }));
  await page.goto("/");
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await page.getByText("Advanced / install by exact tag", { exact: true }).click();
  await page.getByRole("textbox", { name: "Exact model name and tag" }).fill("browser-fixture:tiny");
  expect(pulls).toBe(0);
  await page.getByRole("button", { name: "Install exact tag", exact: true }).click();
  await expect(page.getByText("Installed and verified locally", { exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "sha256:browser-fixture:tiny", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "1.00 GiB", exact: true })).toHaveCount(2);
  await page.getByRole("combobox", { name: "Model to test", exact: true }).selectOption("browser-fixture:tiny");
  await page.getByRole("button", { name: "Test inference", exact: true }).click();
  await expect(page.getByText(/Inference succeeded ·/)).toBeVisible();
  expect(pulls).toBe(1);

  const dataset = await (await page.request.post("/api/datasets", { data: { name: "Browser model selector fixture" } })).json();
  const scenario = await (await page.request.post("/api/scenarios", { data: { dataset_id: dataset.id, row: { inputs: { learning_goal: "MODEL SELECTOR FIXTURE" } } } })).json();
  await page.reload();
  await page.getByRole("button", { name: "Scenarios", exact: true }).click();
  await page.getByRole("combobox", { name: "Dataset", exact: true }).selectOption(dataset.id);
  await page.getByRole("button", { name: "Generate locally", exact: true }).click();
  await page.getByRole("combobox", { name: "Local model", exact: true }).selectOption("browser-fixture:tiny");
  await expect(page.getByRole("combobox", { name: "Local model", exact: true })).toHaveValue("browser-fixture:tiny");
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: /MODEL SELECTOR FIXTURE/ }).click();
  await page.getByText("Request an AI revision", { exact: true }).click();
  await page.getByRole("combobox", { name: "Local model", exact: true }).selectOption("evaluator-fixture:tiny");
  await expect(page.getByRole("combobox", { name: "Local model", exact: true })).toHaveValue("evaluator-fixture:tiny");
  expect(scenario.id).toBeTruthy();

  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await page.getByRole("checkbox", { name: /Retrieval-augmented generation/ }).check();
  await page.getByRole("checkbox", { name: /Constrained LLM/ }).check();
  await page.getByRole("checkbox", { name: /browser-fixture:tiny/ }).check();
  await page.getByRole("combobox", { name: "AI control", exact: true }).selectOption("Yes");
  await page.getByRole("combobox", { name: "Separate local evaluator model", exact: true }).selectOption("evaluator-fixture:tiny");
  await expect(page.getByRole("checkbox", { name: /browser-fixture:tiny/ })).toBeChecked();
  await expect(page.getByRole("combobox", { name: "Separate local evaluator model", exact: true })).toHaveValue("evaluator-fixture:tiny");
});

test("catalog picker: real cached search/filter API, keyboard dialog, explicit test-fixture installation", async ({ page }) => {
  let pulls = 0, installed = false;
  const model = { name: "qwen3:0.6b-q8_0", digest: "TEST-FIXTURE-DIGEST", size: 832000000, details: { parameter_size: "752M", quantization_level: "Q8_0" } };
  await page.route("**/api/models", (route) => route.fulfill({ json: { available: true, models: installed ? [model] : [] } }));
  await page.route("**/api/models/install", (route) => {
    expect(route.request().postDataJSON().model).toBe(model.name);
    pulls++; installed = true;
    return route.fulfill({ contentType: "application/x-ndjson", body: '{"status":"pulling layer","digest":"TEST-LAYER","total":100,"completed":100}\n' + JSON.stringify({ done: true, model }) + "\n" });
  });
  // Public-source failure is a test fixture; browsing still uses the real cached backend catalog.
  await page.route("**/api/model-catalog/refresh", (route) => route.fulfill({ status: 503, json: { detail: "TEST FIXTURE: public source offline" } }));
  await page.goto("/");
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Exact model name and tag" })).not.toBeVisible();
  await page.getByRole("button", { name: "Add model", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add model", exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Search model catalog" })).toBeFocused();
  await expect(dialog.getByText("13 of 13 curated variants", { exact: true })).toBeVisible();
  await dialog.getByRole("combobox", { name: "Family", exact: true }).selectOption("Qwen");
  await dialog.getByRole("combobox", { name: "Generation / version", exact: true }).selectOption("3");
  await dialog.getByRole("combobox", { name: "Reported parameters", exact: true }).selectOption("752M");
  await dialog.getByRole("combobox", { name: "Quantization", exact: true }).selectOption("Q8_0");
  await expect(dialog.getByText("1 of 13 curated variants", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: model.name, exact: true }).click();
  await expect(dialog.getByRole("heading", { name: model.name, exact: true })).toBeVisible();
  await expect(dialog.getByText("Not installed", { exact: true })).toBeVisible();
  expect(pulls).toBe(0);
  await page.screenshot({ path: "../docs/browser-model-picker.png", fullPage: true });
  await dialog.getByRole("button", { name: "Use this variant", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(pulls).toBe(0);
  await page.getByRole("button", { name: "Install", exact: true }).click();
  await expect(page.getByText("Installed and verified locally", { exact: true })).toBeVisible();
  expect(pulls).toBe(1);
  await expect(page.getByRole("combobox", { name: "Model to test", exact: true })).toContainText(model.name);
  await page.getByRole("button", { name: "Add model", exact: true }).click();
  await expect(dialog.getByText(/Already installed/)).toBeVisible();
  await dialog.getByRole("button", { name: "Refresh catalog metadata", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Cached entries remain available");
  await dialog.getByRole("textbox", { name: "Search model catalog" }).fill("qwen3");
  await expect(dialog.getByRole("button", { name: model.name, exact: true })).toBeVisible();
  expect(pulls).toBe(1);
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Add model", exact: true })).toBeFocused();
});

test("live local inference through the real browser and backend when qwen3 is installed", async ({ page, request }) => {
  const state = await (await request.get("/api/models")).json();
  test.skip(!state.models?.some((m: { name: string }) => m.name === "qwen3:0.6b"), "Live qwen3:0.6b is unavailable; no successful model response is simulated");
  await page.goto("/");
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await page.getByRole("combobox", { name: "Model to test", exact: true }).selectOption("qwen3:0.6b");
  await page.getByRole("button", { name: "Test inference", exact: true }).click();
  await expect(page.getByText(/Inference succeeded ·/)).toBeVisible({ timeout: 70000 });
  await expect(page.getByText("OK", { exact: true })).toBeVisible();
  await expect(page.getByText(/Model load:.*Generation:/)).toBeVisible();
  await page.getByText("Inference test diagnostics and effective settings", { exact: true }).click();
  await expect(page.getByText(/"think": false/)).toBeVisible();
  await page.screenshot({ path: "../docs/browser-live-inference.png", fullPage: true });
});
