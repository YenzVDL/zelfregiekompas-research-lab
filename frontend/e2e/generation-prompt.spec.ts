import { test, expect } from "@playwright/test";

test("prompt UI fixtures: captured messages, successful retry, earlier attempt, and unknown historical prompt", async ({ page }) => {
  // These are explicitly test-authored records, not evidence of model execution.
  const dataset = { id: "PROMPT-UI-FIXTURE-DATASET", name: "TEST FIXTURE prompt display" };
  const attempt = (id: string, n: number, status: string) => ({
    id, attempt: n, status, model: "fixture:local",
    request: { payload: { model: "fixture:local", system: `RECORDED SYSTEM ${n}`, prompt: `RECORDED USER ${n}`, format: { type: "object", additionalProperties: false } } },
    effective_settings: { options: { temperature: 0.2 }, stream: true },
  });
  const first = attempt("a", 1, "invalid"), saved = attempt("b", 2, "valid");
  const base = { dataset_id: dataset.id, family_id: "FIXTURE-FAMILY", version: 1, review_status: "awaiting_review", warnings: [], tags: [], inputs: { learning_goal: "Understand SQL joins" } };
  const rows = [
    { ...base, id: "PROMPT-UI-SCENARIO", scenario_id: "RECORDED-PROMPT-ID", generation: { ...saved, system_prompt: "DECOY TEMPLATE NOT SENT", prompt: "DECOY TEMPLATE NOT SENT", saved_attempt_id: "b", attempts: [first, saved] } },
    { ...base, id: "PROMPT-UI-OLDER", scenario_id: "OLDER-PROMPT-ID", generation: { model: "fixture:local" } },
  ];
  await page.route("**/api/datasets", (route) => route.fulfill({ json: [dataset] }));
  await page.route("**/api/scenarios?**", (route) => route.fulfill({ json: rows }));
  await page.route("**/api/datasets/*/coverage", (route) => route.fulfill({ json: { total: 2 } }));
  await page.route("**/api/reviews?**", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await page.getByRole("button", { name: "Scenarios", exact: true }).click();
  await page.locator("button.list-item").filter({ hasText: "RECORDED-PROMPT-ID" }).click();
  const details = page.locator("details").filter({ has: page.locator("summary", { hasText: /^View generation prompt$/ }) });
  await details.locator("summary").click();
  await expect(details.locator("pre").nth(0)).toHaveText("RECORDED SYSTEM 2");
  await expect(details.locator("pre").nth(1)).toHaveText("RECORDED USER 2");
  await expect(details.getByRole("combobox", { name: "Generation attempt" })).toHaveValue("1");
  await expect(details.getByText("DECOY TEMPLATE NOT SENT", { exact: true })).toHaveCount(0);
  await details.getByRole("combobox", { name: "Generation attempt" }).selectOption("0");
  await expect(details.locator("pre").nth(0)).toHaveText("RECORDED SYSTEM 1");
  await expect(details.locator("pre").nth(1)).toHaveText("RECORDED USER 1");
  await expect(details.getByText("Earlier attempt; this response was not saved as the scenario.", { exact: true })).toBeVisible();
  await page.locator("button.list-item").filter({ hasText: "OLDER-PROMPT-ID" }).click();
  await details.locator("summary").click();
  await expect(details.getByText("Generation prompt not recorded", { exact: true })).toBeVisible();
});
