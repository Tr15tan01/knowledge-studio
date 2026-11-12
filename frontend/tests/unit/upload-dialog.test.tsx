import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { qk } from "@/features/auth/session";
import { UploadDialog } from "@/features/library/upload-dialog";
import { MIB } from "@/lib/format";

function setup() {
  const qc = new QueryClient();
  const m = (used: number, limit: number) => ({ used, reserved: 0, limit, period_start: null, resets_at: "2026-11-01T00:00:00Z" });
  qc.setQueryData(qk.usage, {
    entitlements: { features: [] },
    usage: { uploads: m(1, 5), questions: m(0, 30), research_reports: m(0, 1) },
    storage: { used_bytes: 0, reserved_bytes: 0, limit_bytes: 500 * MIB, available_bytes: 500 * MIB, max_file_size_bytes: 25 * MIB },
  });
  render(
    <QueryClientProvider client={qc}>
      <UploadDialog open onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
  return screen.getByTestId("file-input") as HTMLInputElement;
}

describe("UploadDialog pre-validation", () => {
  it("rejects unsupported formats and oversized files before uploading", () => {
    const input = setup();
    const exe = new File(["MZ"], "setup.exe");
    const big = new File(["x"], "big.pdf");
    Object.defineProperty(big, "size", { value: 30 * MIB });
    const ok = new File(["hello"], "notes.md");
    fireEvent.change(input, { target: { files: [exe, big, ok] } });
    expect(screen.getByText(/Unsupported type/)).toBeInTheDocument();
    expect(screen.getByText(/30 MiB is over your plan's 25 MiB per-file limit/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload file/ })).toBeEnabled();
    expect(screen.getByText("4 of 5")).toBeInTheDocument();
  });
});
