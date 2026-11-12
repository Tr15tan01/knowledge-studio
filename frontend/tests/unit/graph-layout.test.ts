import { describe, expect, it } from "vitest";
import { layout } from "@/features/graph/layout";

describe("graph layout", () => {
  const nodes = Array.from({ length: 30 }, (_, i) => ({ id: `n${i}` }));
  const edges = Array.from({ length: 25 }, (_, i) => ({ source: `n${i % 30}`, target: `n${(i * 7 + 3) % 30}` }));

  it("is deterministic", () => {
    const a = layout(nodes, edges);
    const b = layout(nodes, edges);
    expect([...a.entries()]).toEqual([...b.entries()]);
  });

  it("keeps existing positions fixed when expanding", () => {
    const first = layout(nodes.slice(0, 10), edges.filter((e) => +e.source.slice(1) < 10 && +e.target.slice(1) < 10));
    const second = layout(nodes, edges, first);
    for (const [id, p] of first) expect(second.get(id)).toEqual(p);
  });

  it("separates nodes and stays bounded", () => {
    const p = [...layout(nodes, edges).values()];
    let min = Infinity;
    for (let i = 0; i < p.length; i++) for (let j = i + 1; j < p.length; j++) min = Math.min(min, Math.hypot(p[i]!.x - p[j]!.x, p[i]!.y - p[j]!.y));
    expect(min).toBeGreaterThan(40);
    const span = Math.max(...p.map((q) => q.x)) - Math.min(...p.map((q) => q.x));
    expect(span).toBeLessThan(5000);
  });
});
