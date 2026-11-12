import { expect, type Page } from "@playwright/test";

export const PASSWORD = "correct-horse-battery-staple";

export function uniqueEmail(prefix = "reader") {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}@example.com`;
}

export async function register(page: Page, email = uniqueEmail(), name = "Nino") {
  await page.goto("/register");
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/app$/);
  return email;
}

export const SAMPLE_DOC = `# Notes on the Caucasus

## The Beekeepers of Racha

Beekeepers in Racha produce chestnut honey in the Caucasus mountains near Tbilisi. The honey harvest happens in late summer.

## Mountain Shepherds

Shepherds move their flocks to Kazbek pastures each June. Tbilisi markets sell cheese from the Caucasus.
`;

/** Upload a Markdown file through the UI and wait until the library marks it ready. */
export async function uploadSample(page: Page, name = "caucasus-notes.md", body = SAMPLE_DOC) {
  await page.goto("/app/library");
  await page.getByRole("button", { name: "Upload" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Upload to your library" });
  await dialog.getByTestId("file-input").setInputFiles({ name, mimeType: "text/markdown", buffer: Buffer.from(body) });
  await dialog.getByRole("button", { name: /^Upload file/ }).click();
  await expect(dialog.getByText("Uploaded. Processing has started.")).toBeVisible();
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("table").getByText("Ready").filter({ visible: true }).first()).toBeVisible({ timeout: 45_000 });
}
