import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UsageMeters } from "@/features/account/usage-meters";
import { MIB } from "@/lib/format";
import type { UsageResponse } from "@/types/api";

const meter = (used: number, limit: number, reserved = 0) => ({ used, reserved, limit, period_start: "2026-10-01T00:00:00+00:00", resets_at: "2026-11-01T00:00:00+00:00" });

function usage(over: Partial<UsageResponse["storage"]> = {}): UsageResponse {
  return {
    entitlements: {} as UsageResponse["entitlements"],
    usage: {
      uploads: meter(3, 5),
      questions: meter(29, 30, 1),
      research_reports: meter(1, 1),
      storage_bytes: meter(124 * MIB, 500 * MIB),
      concurrent_research: meter(0, 1),
      compute_daily: meter(0, 400),
      compute_monthly: meter(0, 3000),
    },
    storage: {
      used_bytes: 124 * MIB,
      reserved_bytes: 0,
      limit_bytes: 500 * MIB,
      available_bytes: 376 * MIB,
      max_file_size_bytes: 25 * MIB,
      percent_used: 24.8,
      warning_level: null,
      over_limit: false,
      display: { used: "124 MiB", limit: "500 MiB", available: "376 MiB", max_file_size: "25 MiB" },
      breakdown: { stored_bytes: 0, book_bytes: 100 * MIB, document_bytes: 24 * MIB, stored_files: 3, uploading_files: 0, uploading_bytes: 0, deleting_files: 0, failed_uploads: 0 },
      unit_system: "binary",
      ...over,
    },
  };
}

describe("UsageMeters", () => {
  it("shows server-calculated storage in binary units", () => {
    render(<UsageMeters usage={usage()} />);
    expect(screen.getByTestId("storage-used")).toHaveTextContent("Used: 124 MiB of 500 MiB");
    expect(screen.getByText(/376 MiB remaining/)).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Storage used" })).toHaveAttribute("aria-valuenow", String(124 * MIB));
  });

  it("counts in-progress reservations against what's left", () => {
    render(<UsageMeters usage={usage()} />);
    expect(screen.getByText("1 in progress, 0 left")).toBeInTheDocument();
    expect(screen.getByText("2 left")).toBeInTheDocument();
  });
});
