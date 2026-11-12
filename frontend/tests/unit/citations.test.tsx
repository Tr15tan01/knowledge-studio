import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { CitationProvider, CitedText, type CiteRef } from "@/features/citations/citations";

const cites: CiteRef[] = [
  { key: "S1", chunk_id: "c1", source_title: "A Field Guide", citation_label: "p. 42" },
  { key: "S2", chunk_id: "c2", source_title: "Markets", citation_label: "p. 117" },
];

function wrap(ui: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <CitationProvider>{ui}</CitationProvider>
    </QueryClientProvider>,
  );
}

describe("CitedText", () => {
  it("turns validated markers into numbered citation chips", () => {
    wrap(<CitedText text="Honey went to market [S1] and was sold [S1, S2]." cites={cites} />);
    const chips = screen.getAllByRole("button", { name: /Source \d/ });
    expect(chips.map((c) => c.textContent)).toEqual(["1", "1", "2"]);
    expect(chips[0]).toHaveAccessibleName("Source 1: A Field Guide, p. 42");
  });

  it("drops markers that the backend did not validate", () => {
    wrap(<CitedText text="Claim [S9] and another [S1]." cites={cites} />);
    expect(screen.getAllByRole("button", { name: /Source/ })).toHaveLength(1);
    expect(screen.queryByText(/S9/)).toBeNull();
  });

  it("does not render raw HTML or external links from model output", () => {
    const { container } = wrap(<CitedText text={'Click <img src=x onerror=alert(1)> [here](https://evil.example) now.'} cites={cites} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("here");
  });
});
