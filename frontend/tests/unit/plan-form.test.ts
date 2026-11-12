import { describe, expect, it } from "vitest";
import { blankDraft, draftToPayload, entitlementDiff, toMinor } from "@/features/admin/plan-form";
import { GIB, MIB } from "@/lib/format";

describe("plan editor helpers", () => {
  it("converts display prices to minor units", () => {
    expect(toMinor("7.99")).toBe(799);
    expect(toMinor("79")).toBe(7900);
    expect(toMinor("")).toBeNull();
  });

  it("reports entitlement differences in readable units", () => {
    const a = blankDraft().entitlements;
    const b = { ...a, storage_limit_bytes: 5 * GIB, question_limit_per_period: 100, features: { ...a.features, pdf_export: true } };
    const diff = entitlementDiff(a, b);
    expect(diff).toContainEqual({ label: "Total storage", from: "500 MiB", to: "5 GiB" });
    expect(diff).toContainEqual({ label: "AI questions per period", from: "30", to: "100" });
    expect(diff).toContainEqual({ label: "PDF export", from: "Off", to: "On" });
    expect(entitlementDiff(a, a)).toEqual([]);
  });

  it("builds a payload with integer bytes and trimmed highlights", () => {
    const d = { ...blankDraft(), name: " Student ", monthly: "4.99", annual: "", highlights: "One\n\n Two \n" };
    d.entitlements.max_file_size_bytes = 25 * MIB;
    const p = draftToPayload(d);
    expect(p.name).toBe("Student");
    expect(p.pricing).toEqual({ currency: "USD", monthly_display_price_minor: 499, annual_display_price_minor: null });
    expect(p.marketing.highlights).toEqual(["One", "Two"]);
    expect(Number.isInteger(p.entitlements.storage_limit_bytes)).toBe(true);
  });
});
