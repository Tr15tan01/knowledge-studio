import { expect, test } from "@playwright/test";
import { register } from "./helpers";

test("mobile navigation opens, navigates and closes", async ({ page }) => {
  await register(page);
  await page.getByRole("button", { name: "Open navigation" }).click();
  const nav = page.getByRole("dialog", { name: "Navigation" });
  await nav.getByRole("link", { name: "Ask AI" }).click();
  await expect(page).toHaveURL(/\/app\/ask$/);
  await expect(nav).toBeHidden();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("landing page has no horizontal scroll on phones", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
});
