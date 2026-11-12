import { describe, expect, it } from "vitest";
import { GIB, MIB, formatBytes, formatMoneyMinor, parseBytes, percent } from "@/lib/format";

describe("binary byte formatting", () => {
  it("uses binary units consistently", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1 KiB");
    expect(formatBytes(MIB)).toBe("1 MiB");
    expect(formatBytes(500 * MIB)).toBe("500 MiB");
    expect(formatBytes(5 * GIB)).toBe("5 GiB");
    expect(formatBytes(1_073_741_824)).toBe("1 GiB");
    expect(formatBytes(2562)).toBe("2.5 KiB");
    expect(formatBytes(124 * MIB + 300 * 1024)).toBe("124 MiB");
  });

  it("never uses decimal (MB/GB) labels", () => {
    for (const n of [1000, 1_000_000, 1_000_000_000, 123_456_789]) expect(formatBytes(n)).not.toMatch(/\b(MB|GB|KB)\b/);
  });

  it("handles missing values", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
  });

  it("parses binary quantities into integer bytes", () => {
    expect(parseBytes("25 MiB")).toBe(25 * MIB);
    expect(parseBytes("5GiB")).toBe(5 * GIB);
    expect(parseBytes("1.5 gib")).toBe(Math.round(1.5 * GIB));
    expect(parseBytes("1048576")).toBe(MIB);
    expect(parseBytes("25 MB")).toBeNull();
  });
});

describe("percent", () => {
  it("clamps and handles zero limits", () => {
    expect(percent(50, 100)).toBe(50);
    expect(percent(150, 100)).toBe(100);
    expect(percent(0, 0)).toBe(0);
    expect(percent(1, 0)).toBe(100);
  });
});

describe("money", () => {
  it("formats minor units", () => {
    expect(formatMoneyMinor(799)).toBe("$7.99");
    expect(formatMoneyMinor(7900)).toBe("$79");
    expect(formatMoneyMinor(0)).toBe("$0");
  });
});
