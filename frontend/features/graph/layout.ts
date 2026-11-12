/**
 * Deterministic force-directed layout for small subgraphs (the API caps them at 150 nodes).
 * Existing positions are kept so expanding a neighbourhood doesn't reshuffle the view.
 */
export type P = { x: number; y: number };

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967295;
}

export function layout(
  nodes: { id: string; weight?: number }[],
  edges: { source: string; target: string }[],
  fixed: Map<string, P> = new Map(),
  opts: { iterations?: number; spacing?: number; anchor?: string } = {},
): Map<string, P> {
  const spacing = opts.spacing ?? 170;
  const iterations = opts.iterations ?? 260;
  const pos = new Map<string, P>();
  const n = nodes.length;
  nodes.forEach((node, i) => {
    const f = fixed.get(node.id);
    if (f) return pos.set(node.id, { ...f });
    // Seed new nodes near an already-placed neighbour, otherwise on a golden-angle spiral.
    const nb = edges.find((e) => (e.source === node.id && fixed.has(e.target)) || (e.target === node.id && fixed.has(e.source)));
    const base = nb ? fixed.get(nb.source === node.id ? nb.target : nb.source)! : { x: 0, y: 0 };
    const a = i * 2.39996 + hash(node.id) * 0.5;
    const r = nb ? spacing * (0.8 + hash(node.id + "r") * 0.6) : spacing * 0.55 * Math.sqrt(i + 1);
    pos.set(node.id, { x: base.x + Math.cos(a) * r, y: base.y + Math.sin(a) * r });
  });
  if (n <= 1) return pos;

  const ids = nodes.map((x) => x.id);
  const movable = new Set(ids.filter((id) => !fixed.has(id)));
  if (!movable.size) return pos;
  const k = spacing;
  let temp = spacing * 0.6;
  for (let it = 0; it < iterations; it++) {
    const disp = new Map<string, P>(ids.map((id) => [id, { x: 0, y: 0 }]));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = pos.get(ids[i]!)!;
        const b = pos.get(ids[j]!)!;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d = Math.hypot(dx, dy);
        if (d < 0.01) {
          dx = hash(ids[i]! + ids[j]!) - 0.5;
          dy = 0.5 - hash(ids[j]! + ids[i]!);
          d = 0.5;
        }
        if (d > k * 4) continue; // bounded repulsion keeps components from drifting apart
        const f = (k * k) / d;
        const da = disp.get(ids[i]!)!;
        const db = disp.get(ids[j]!)!;
        da.x += (dx / d) * f;
        da.y += (dy / d) * f;
        db.x -= (dx / d) * f;
        db.y -= (dy / d) * f;
      }
    }
    for (const e of edges) {
      const a = pos.get(e.source);
      const b = pos.get(e.target);
      if (!a || !b) continue;
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d = Math.max(0.01, Math.hypot(dx, dy));
      const f = (d * d) / k;
      const da = disp.get(e.source)!;
      const db = disp.get(e.target)!;
      da.x -= (dx / d) * f;
      da.y -= (dy / d) * f;
      db.x += (dx / d) * f;
      db.y += (dy / d) * f;
    }
    for (const id of movable) {
      const d = disp.get(id)!;
      const p = pos.get(id)!;
      // Gentle pull to the centre keeps disconnected components on screen.
      d.x -= p.x * 0.12;
      d.y -= p.y * 0.12;
      const len = Math.max(0.01, Math.hypot(d.x, d.y));
      p.x += (d.x / len) * Math.min(len, temp);
      p.y += (d.y / len) * Math.min(len, temp);
    }
    temp *= 0.985;
  }
  return pos;
}

export const TYPE_COLORS: Record<string, string> = {
  concept: "#3b4cca",
  person: "#b4467e",
  organization: "#1f7a4f",
  location: "#b86b00",
  event: "#6a4fc4",
  book: "#1b1f3b",
  technique: "#0e7c86",
  product: "#8a6d00",
  scientific_term: "#c0392b",
  other: "#6f7790",
};

export const typeColor = (t: string) => TYPE_COLORS[t] || TYPE_COLORS.other!;
