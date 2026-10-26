import { test, expect } from "@playwright/test";

test("real browser: import, revision, review, frozen inputs, rule experiment, human labels and metrics", async ({
  page,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (e) => failures.push(e.message));
  const token = Date.now().toString();
  const dataset = "Browser synthetic verification " + token;
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Reviewer ID" })
    .fill("browser-test-reviewer");
  await page.getByRole("button", { name: "Scenarios", exact: true }).click();
  await page
    .getByRole("button", { name: "+ New dataset", exact: true })
    .click();
  await page.getByRole("textbox", { name: "Name", exact: true }).fill(dataset);
  await page
    .getByRole("button", { name: "Create dataset", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Dataset", exact: true }),
  ).toContainText(dataset);
  await page
    .getByRole("button", { name: "Import scenarios", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Import content", exact: true })
    .fill(
      JSON.stringify([
        {
          inputs: {
            learning_goal: "Explain a graph",
            planned_activity: "Describe a distance-time graph",
            student_note: "I want to compare two parts.",
          },
          tags: ["normal"],
          synthetic_origin:
            "BROWSER VERIFICATION: manually constructed synthetic case",
        },
      ]),
    );
  await page
    .getByRole("button", { name: "Preview import", exact: true })
    .click();
  await expect(page.getByText("1 valid · 0 invalid")).toBeVisible();
  await page
    .getByRole("button", { name: "Commit validated rows", exact: true })
    .click();
  await page.getByRole("button").filter({ hasText: "Explain a graph" }).click();
  await expect(
    page.getByRole("heading", { name: "Review exact version 1" }),
  ).toBeVisible();
  await page
    .getByText("Edit manually · creates a new unapproved version", {
      exact: true,
    })
    .click();
  await page
    .getByRole("textbox", { name: "Candidate model inputs", exact: true })
    .fill(
      JSON.stringify({
        learning_goal: "Explain two sections of a graph",
        planned_activity: "Describe a distance-time graph",
        student_note: "I want to compare two parts.",
      }),
    );
  await page
    .getByRole("button", { name: "Save new version", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Review exact version 2" }),
  ).toBeVisible();
  await expect(
    page.getByText("Parent → candidate · highlighted changes"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Approve exact version", exact: true })
    .click();
  await expect(page.getByText("Decision saved for version 2.")).toBeVisible();
  await page
    .getByRole("button", { name: "Question bank", exact: true })
    .click();
  await page
    .getByRole("textbox", {
      name: "Question and applicability (JSON)",
      exact: true,
    })
    .fill(
      JSON.stringify({
        text: "What would you like to explore about the graph?",
        tags: ["graph"],
        conditions: {},
        priority: 100,
        origin: "BROWSER VERIFICATION: unvalidated human suggestion",
      }),
    );
  await page
    .getByRole("button", { name: "Create question for review", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Approve question version", exact: true })
    .click();
  await page.getByRole("button", { name: "Snapshots", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Snapshot name", exact: true })
    .fill("Browser dataset " + token);
  await page
    .getByRole("button", { name: "Freeze approved versions", exact: true })
    .click();
  await expect(
    page.getByText("Immutable snapshot created from approved latest versions."),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Snapshot type", exact: true })
    .selectOption("bank");
  await page
    .getByRole("textbox", { name: "Snapshot name", exact: true })
    .fill("Browser bank " + token);
  await page
    .getByRole("button", { name: "Freeze approved versions", exact: true })
    .click();
  await expect(
    page.getByRole("cell").filter({ hasText: "Browser bank " + token }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Experiment name", exact: true })
    .fill("Browser deterministic experiment " + token);
  await page
    .getByRole("combobox", { name: "Dataset snapshot", exact: true })
    .selectOption({ label: "Browser dataset " + token });
  await page
    .getByRole("combobox", { name: "Question-bank snapshot", exact: true })
    .selectOption({ label: "Default question bank · Browser bank " + token + " · Approved only" });
  await expect(
    page.getByRole("combobox", { name: "AI control", exact: true }),
  ).toHaveValue("No");
  await page
    .getByRole("button", { name: "Queue experiment →", exact: true })
    .click();
  await expect(
    page.getByText("completed", { exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Evaluation", exact: true })
    .click();
  const ownExperiment = await page.getByRole('combobox', {name:'Experiment to evaluate',exact:true}).locator('option').filter({hasText:'Browser deterministic experiment '+token}).getAttribute('value');
  await page.getByRole('combobox', {name:'Experiment to evaluate',exact:true}).selectOption(ownExperiment!);
  await expect(
    page.getByRole("checkbox", {
      name: "Hide model and method identities",
      exact: true,
    }),
  ).toBeChecked();
  await expect(page.locator("blockquote")).toContainText(
    "What would you like to explore about the graph?",
  );
  for (const criterion of ["usefulness", "correctness"]) {
    await page
      .getByRole("combobox", { name: criterion + " judgment", exact: true })
      .selectOption("Yes");
    await page
      .getByRole("textbox", { name: criterion + " explanation", exact: true })
      .fill("Open, relevant and supported by the recorded learning activity.");
    await page
      .getByRole("textbox", { name: criterion + " context", exact: true })
      .fill("The scenario goal explicitly concerns a graph.");
  }
  await page
    .getByRole("button", { name: "Save human evaluation", exact: true })
    .click();
  await expect(
    page.getByText("Human judgment saved separately from AI judgments."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Metrics", exact: true }).click();
  const experimentSelect = page.getByRole("combobox", {
    name: "Experiment",
    exact: true,
  });
  const names = await experimentSelect.locator("option").allTextContents();
  await experimentSelect.selectOption({
    label: names.find((v) => v.includes(token))!,
  });
  await expect(
    page.getByText("End-to-end acceptance", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("100.0%").first()).toBeVisible();
  await expect(
    page.getByText("Not available: human reference labels required", {
      exact: true,
    }),
  ).toHaveCount(2);
  await expect(
    page.getByText("1 planned · 1 attempted · 0 not attempted"),
  ).toBeVisible();
  await page.screenshot({
    path: "../docs/browser-metrics.png",
    fullPage: true,
  });
  expect(failures).toEqual([]);
});

test("provider availability is visibly distinct from research results", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Protocol & records", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Local model discovery" }),
  ).toBeVisible();
  await expect(
    page.getByText("Only installed models verified as local are offered.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Refresh installed models", exact: true }),
  ).toBeVisible();
  const text = await page.locator("body").innerText();
  expect(text.includes("Ollama offline") || /\d+ local models/.test(text)).toBe(
    true,
  );
});
