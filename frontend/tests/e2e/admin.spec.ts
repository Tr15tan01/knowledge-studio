import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { PASSWORD, register, uniqueEmail, uploadSample } from "./helpers";

/** Promote an account with the real bootstrap CLI (no admin registration endpoint exists). */
function promote(email: string, role: "ADMIN" | "SUPER_ADMIN") {
  const env = { ...process.env, ...JSON.parse(process.env.E2E_BACKEND_ENV || "{}") };
  const out = execFileSync(process.env.E2E_PYTHON!, ["-m", "app.cli", "create-admin", "--email", email, "--role", role, "--allow-unverified", "--yes", "--reason", "e2e"], {
    cwd: process.env.E2E_BACKEND_DIR,
    env,
    encoding: "utf8",
  });
  expect(out).toContain(`Promoted account to ${role}`);
  expect(out).not.toMatch(/password|token|secret/i);
}

async function confirmPasswordIfAsked(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Confirm it's you" });
  if (await dialog.isVisible().catch(() => false)) {
    await dialog.getByLabel("Password").fill(PASSWORD);
    await dialog.getByRole("button", { name: "Confirm" }).click();
  }
}

test("a super admin changes a plan quota in the UI and users see it immediately", async ({ page, browser }) => {
  const adminEmail = await register(page, uniqueEmail("admin"), "Admin");
  // Before promotion the admin area is closed.
  expect((await page.request.get("/api/v1/admin/plans")).status()).toBe(403);
  promote(adminEmail, "SUPER_ADMIN");

  await page.goto("/admin/plans");
  await expect(page.getByRole("heading", { name: "Plans & pricing" })).toBeVisible();
  await page.getByRole("link", { name: "Free", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Free" })).toBeVisible();

  await page.getByLabel("AI questions per period").fill("42");
  await page.getByRole("button", { name: "Review and publish" }).click();
  const review = page.getByRole("dialog", { name: "Review changes" });
  await expect(review.getByRole("row", { name: /AI questions per period 30 42/ })).toBeVisible();
  await review.getByLabel("Apply to").selectOption("all_subscribers");
  await review.getByLabel("Change note").fill("Raise free question allowance");
  await review.getByRole("button", { name: "Publish version 2" }).click();
  await confirmPasswordIfAsked(page);
  await expect(page.getByText("Published version 2.")).toBeVisible();

  // A brand-new user is on the default plan and sees the new allowance from the server.
  const user = await browser.newPage();
  await register(user, uniqueEmail("member"));
  await user.goto("/app/billing");
  await expect(user.getByText("AI questions", { exact: true }).locator("..")).toContainText("0 of 42");
  await expect(user.getByRole("columnheader", { name: /Free/ })).toBeVisible();
  // An ordinary user still can't open admin pages or APIs.
  expect((await user.request.get("/api/v1/admin/users")).status()).toBe(403);
  await user.close();

  // The change is in the immutable audit log.
  await page.goto("/admin/audit?action=plan.version_published");
  await expect(page.getByRole("cell", { name: "plan.version_published" }).first()).toBeVisible();
  await expect(page.getByText("Raise free question allowance").first()).toBeVisible();
});

test("ordinary admins can view plans but not change them; secrets are never shown", async ({ page }) => {
  const email = await register(page, uniqueEmail("ops"), "Ops");
  promote(email, "ADMIN");
  await page.goto("/admin/plans");
  await expect(page.getByText("requires a super administrator")).toBeVisible();
  await expect(page.getByRole("link", { name: "New plan" })).toHaveCount(0);
  const csrf = (await page.context().cookies()).find((c) => c.name === "ks_csrf")!.value;
  const res = await page.request.post("/api/v1/admin/plans/reorder", { data: { order: ["x"] }, headers: { "x-csrf-token": csrf, origin: new URL(page.url()).origin } });
  expect(res.status()).toBe(403);

  await page.goto("/admin/system");
  await expect(page.getByRole("heading", { name: "Configuration" })).toBeVisible();
  const cfg = await (await page.request.get("/api/v1/admin/config")).text();
  expect(cfg).not.toContain("e2e-only-session-secret");
  expect(cfg).not.toContain("e2e-not-used");
});

test("a super admin chooses where files are stored, and originals aren't kept after indexing", async ({ page }) => {
  const email = await register(page, uniqueEmail("owner"), "Owner");
  promote(email, "SUPER_ADMIN");

  await page.goto("/admin/settings");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: /Server disk/ })).toBeChecked();
  await expect(page.getByRole("button", { name: "Save settings" })).toBeDisabled();
  await page.getByRole("radio", { name: /MongoDB/ }).check();
  await page.getByLabel("Passages per answer").fill("6");
  await page.getByLabel("Reason").fill("No shared disk between servers");
  await page.getByRole("button", { name: "Save settings" }).click();
  await confirmPasswordIfAsked(page);
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("radio", { name: /MongoDB/ })).toBeChecked();
  await expect(page.getByLabel("Passages per answer")).toHaveValue("6");

  // An upload goes through MongoDB, is indexed, and its original is gone afterwards.
  await uploadSample(page);
  const files = await (await page.request.get("/api/v1/files")).json();
  expect(files.items[0].original_available).toBe(false);
  expect((await page.request.get(`/api/v1/files/${files.items[0].id}/download`)).status()).toBe(404);
  await page.goto(`/app/library/${files.items[0].id}`);
  await expect(page.getByText("isn't kept after indexing")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download" })).toHaveCount(0);
  const held = await (await page.request.get("/api/v1/admin/settings")).json();
  expect(held.files_held).toEqual([]);

  // The change is audited, and the defaults can be restored.
  await page.goto("/admin/audit?action=admin.settings_changed");
  await expect(page.getByText("No shared disk between servers").first()).toBeVisible();
  await page.goto("/admin/settings");
  await page.getByRole("button", { name: "Use defaults" }).click();
  await page.getByLabel("Reason").fill("Back to defaults");
  await page.getByRole("button", { name: "Save settings" }).click();
  await confirmPasswordIfAsked(page);
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await expect(page.getByRole("radio", { name: /Server disk/ })).toBeChecked();
});
