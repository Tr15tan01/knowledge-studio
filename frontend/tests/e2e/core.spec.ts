import { expect, test } from "@playwright/test";
import { PASSWORD, register, uniqueEmail, uploadSample } from "./helpers";

test("register, sign out and sign back in", async ({ page }) => {
  const email = await register(page);
  await expect(page.getByRole("heading", { name: "Welcome back, Nino" })).toBeVisible();
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in/);

  await page.goto("/app/library");
  await expect(page).toHaveURL(/\/sign-in\?next=%2Fapp%2Flibrary/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("wrong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator("form").getByRole("alert")).toHaveText("Invalid email or password.");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/app\/library$/);
});

test("upload, ask with citations, open the source passage", async ({ page }) => {
  await register(page);
  await uploadSample(page);

  // Counts are server-derived: one file, one document, storage in binary units.
  await page.goto("/app");
  await expect(page.getByText("Files uploaded").locator("..")).toContainText("1");
  await page.goto("/app/billing");
  await expect(page.getByTestId("storage-used")).toHaveText(/^Used: \d+(\.\d+)? (B|KiB) of 500 MiB$/);

  await page.goto("/app/ask");
  await page.getByLabel("Your question").fill("Where do beekeepers produce chestnut honey?");
  await page.keyboard.press("Enter");
  const answer = page.locator("article").last();
  await expect(answer.locator(".cite-chip").first()).toBeVisible({ timeout: 30_000 });
  await expect(answer).toContainText("chestnut honey");
  await expect(page).toHaveURL(/\/app\/ask\?c=/);

  await answer.locator(".cite-chip").first().click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.locator(".mark")).toContainText("Beekeepers in Racha produce chestnut honey");
  await sheet.getByRole("link", { name: /Open source details/ }).click();
  await expect(page).toHaveURL(/\/app\/library\/[a-f0-9]{24}$/);
  await expect(page.getByText("Sections")).toBeVisible();
});

test("an unrelated question says the library lacks evidence", async ({ page }) => {
  await register(page);
  await uploadSample(page);
  await page.goto("/app/ask");
  await page.getByLabel("Your question").fill("Quantum chromodynamics gluon confinement?");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/doesn.t contain enough evidence|couldn.t find anything/)).toBeVisible({ timeout: 30_000 });
});

test("deep research produces a cited report, then the report quota blocks a second run", async ({ page, request }) => {
  await register(page);
  await uploadSample(page);
  await page.goto("/app/research");
  await page.getByLabel("Title").fill("Life in the Caucasus");
  await page.getByLabel("Research question").fill("What do the sources say about honey and shepherds?");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/app\/research\/[a-f0-9]{24}$/);
  await page.getByRole("button", { name: "Start research" }).click();

  // Progress comes from persisted worker events.
  await expect(page.getByText(/Planned \d+ sub-questions/)).toBeVisible({ timeout: 45_000 });
  await page.getByRole("link", { name: "Open report" }).click({ timeout: 45_000 });
  await expect(page.getByRole("heading", { name: "Executive summary" })).toBeVisible();
  await expect(page.locator(".cite-chip").first()).toBeVisible();
  await expect(page.getByText("Hypothesis, not directly supported").first()).toBeVisible();
  await page.locator(".cite-chip").first().click();
  await expect(page.getByRole("dialog").locator(".mark")).toBeVisible();
  await page.keyboard.press("Escape");

  // Free plan: one report per period. The UI blocks it and so does the API.
  await page.getByRole("link", { name: "Research project" }).click();
  await expect(page.getByRole("button", { name: "Start research" })).toBeDisabled();
  await expect(page.getByText("You've used every research report this period.")).toBeVisible();
  const projectId = page.url().split("/").pop()!.split("?")[0];
  const csrf = (await page.context().cookies()).find((c) => c.name === "ks_csrf")!.value;
  const res = await page.request.post(`/api/v1/research/projects/${projectId}/runs`, { data: {}, headers: { "x-csrf-token": csrf, origin: new URL(page.url()).origin } });
  expect(res.status()).toBe(402);
  expect((await res.json()).error.code).toMatch(/quota/);
  void request;
});

test("users can't reach the admin area or another user's files", async ({ page, browser }) => {
  await register(page);
  await uploadSample(page);
  await page.getByRole("table").getByRole("link").first().click();
  const fileUrl = page.url();
  const fileId = fileUrl.split("/").pop()!;

  // Admin pages redirect ordinary users; admin APIs refuse them.
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/app$/);
  expect((await page.request.get("/api/v1/admin/overview")).status()).toBe(403);

  // A second account can't read the first account's file, even with its ID.
  const other = await browser.newPage();
  await register(other, uniqueEmail("other"));
  const r = await other.request.get(`/api/v1/files/${fileId}`);
  expect(r.status()).toBe(404);
  expect((await other.request.get(`/api/v1/files/${fileId}/download`)).status()).toBe(404);
  await other.close();
});

test("public pricing comes from the plan catalog", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page.getByRole("columnheader", { name: /Free/ })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: /Premium/ })).toContainText("$7.99");
  await page.getByRole("radio", { name: "Yearly" }).click();
  await expect(page.getByRole("columnheader", { name: /Premium/ })).toContainText("$79");
  await expect(page.getByText("Payments aren't enabled on this server yet.")).toBeVisible();
});
