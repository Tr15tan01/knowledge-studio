"use client";

import "@xyflow/react/dist/style.css";

import { useQuery } from "@tanstack/react-query";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { Expand, Info, Lock, Network, RotateCcw, Search, X } from "lucide-react";
import Link from "next/link";
import { memo, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button, ButtonLink, IconButton } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { Badge, EmptyState, ErrorState, LoadingRows, Notice } from "@/components/ui/misc";
import { Spinner } from "@/components/ui/spinner";
import { hasFeature, useUsage } from "@/features/auth/session";
import { CitationProvider, SourceList } from "@/features/citations/citations";
import { layout, typeColor, type P } from "@/features/graph/layout";
import { useIndexedFiles } from "@/features/ask/filters";
import { useDebounced } from "@/hooks/use-url-state";
import { ApiError, get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";
import type { EntityDetail, GraphMeta, GraphNode, Paged, RelationshipDetail, Subgraph } from "@/types/api";

type NodeData = { entity: GraphNode; selected: boolean; dim: boolean };

const EntityNode = memo(function EntityNode({ data }: NodeProps<Node<NodeData>>) {
  const e = data.entity;
  const size = Math.min(1.25, 0.9 + Math.log10(1 + e.mention_count) * 0.25);
  return (
    <div
      className={cn(
        "flex max-w-[190px] items-center gap-1.5 rounded-full border bg-surface px-2.5 py-1 shadow-sm transition-opacity",
        data.selected ? "border-action ring-2 ring-action/30" : "border-rule-strong",
        data.dim && "opacity-35",
      )}
      style={{ fontSize: `${0.78 * size}rem` }}
      title={`${e.name} (${titleCase(e.type)}, ${e.mention_count} mentions)`}
    >
      <Handle type="target" position={Position.Top} className="!size-1 !min-h-0 !min-w-0 !border-0 !bg-transparent" />
      <span className="size-2.5 shrink-0 rounded-full" style={{ background: typeColor(e.type) }} />
      <span className="truncate font-medium text-ink">{e.name}</span>
      <Handle type="source" position={Position.Bottom} className="!size-1 !min-h-0 !min-w-0 !border-0 !bg-transparent" />
    </div>
  );
});

const nodeTypes = { entity: EntityNode };
const NO_LAYERS: { g: Subgraph; replace: boolean }[] = [];

function GraphView() {
  const usage = useUsage();
  const advanced = hasFeature(usage.data, "advanced_knowledge_graph");
  const basic = hasFeature(usage.data, "basic_knowledge_graph");
  const meta = useQuery({ queryKey: ["graph-meta"], queryFn: () => get<GraphMeta>("/graph/meta"), enabled: basic });
  const files = useIndexedFiles();
  const flow = useReactFlow();

  const [types, setTypes] = useState<string[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [expanding, setExpanding] = useState(false);
  const [selected, setSelected] = useState<{ kind: "entity" | "rel"; id: string } | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const search = useQuery({ queryKey: ["graph-search", dq], queryFn: () => get<Paged<GraphNode>>("/graph/entities", { q: dq, page_size: 8 }), enabled: dq.length >= 2 });

  // The overview is the base layer; each expansion adds a layer. Changing scope starts over.
  const scopeKey = `${types.join(",")}|${sourceId}`;
  const overview = useQuery({
    queryKey: ["graph-overview", scopeKey],
    queryFn: () => get<Subgraph>("/graph/overview", { limit: 40, type: types.length ? types : undefined, source_id: sourceId || undefined }),
    enabled: basic,
  });
  const [layerState, setLayerState] = useState<{ scope: string; layers: { g: Subgraph; replace: boolean }[] }>({ scope: scopeKey, layers: [] });
  const layers = useMemo(() => (layerState.scope === scopeKey ? layerState.layers : NO_LAYERS), [layerState, scopeKey]);

  const graph = useMemo(() => {
    let nodes = new Map<string, GraphNode>();
    let edges = new Map<string, Subgraph["edges"][number]>();
    let positions = new Map<string, P>();
    let truncated = false;
    const apply = (g: Subgraph, replace: boolean) => {
      if (replace) {
        nodes = new Map();
        edges = new Map();
        positions = new Map();
      }
      g.nodes.forEach((n) => nodes.set(n.id, n));
      g.edges.forEach((e) => nodes.has(e.source) && nodes.has(e.target) && edges.set(e.id, e));
      positions = layout([...nodes.values()].map((n) => ({ id: n.id, weight: n.mention_count })), [...edges.values()], positions);
      truncated = g.truncated;
    };
    const lastReplace = layers.findLastIndex((l) => l.replace);
    if (lastReplace < 0 && overview.data) apply(overview.data, true);
    layers.slice(Math.max(0, lastReplace)).forEach((l) => apply(l.g, l.replace));
    return { nodes, edges, positions, truncated };
  }, [overview.data, layers]);
  const { nodes, edges, positions, truncated } = graph;

  useEffect(() => {
    const t = setTimeout(() => flow.fitView({ duration: 400, padding: 0.18, maxZoom: 1.4 }), 60);
    return () => clearTimeout(t);
  }, [graph, flow]);

  function resetView() {
    setLayerState({ scope: scopeKey, layers: [] });
    setSelected(null);
    setFocus(null);
    overview.refetch();
  }

  async function expand(id: string, depth = 1, replace = false) {
    setExpanding(true);
    try {
      const g = await get<Subgraph>(`/graph/neighborhood/${id}`, { depth, limit: 60, type: types.length ? types : undefined, source_id: sourceId || undefined });
      setLayerState((s) => ({ scope: scopeKey, layers: replace ? [{ g, replace: true }] : [...(s.scope === scopeKey ? s.layers : []), { g, replace: false }] }));
      setFocus(id);
      setSelected({ kind: "entity", id });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't expand this node.");
    } finally {
      setExpanding(false);
    }
  }
  const loading = overview.isFetching || expanding;
  const error = overview.error;

  const neighbours = useMemo(() => {
    if (!focus) return null;
    const s = new Set([focus]);
    edges.forEach((e) => {
      if (e.source === focus) s.add(e.target);
      if (e.target === focus) s.add(e.source);
    });
    return s;
  }, [focus, edges]);

  const rfNodes: Node<NodeData>[] = useMemo(
    () =>
      [...nodes.values()].map((n) => ({
        id: n.id,
        type: "entity",
        position: positions.get(n.id) || { x: 0, y: 0 },
        data: { entity: n, selected: selected?.kind === "entity" && selected.id === n.id, dim: !!neighbours && !neighbours.has(n.id) },
      })),
    [nodes, positions, selected, neighbours],
  );
  const rfEdges: Edge[] = useMemo(
    () =>
      [...edges.values()].map((e) => {
        const active = selected?.kind === "rel" && selected.id === e.id;
        const dim = !!neighbours && !(neighbours.has(e.source) && neighbours.has(e.target));
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.type.replace(/_/g, " "),
          labelStyle: { fontSize: 10, fill: "var(--muted)" },
          labelBgStyle: { fill: "var(--paper)" },
          style: { stroke: active ? "var(--action)" : "var(--rule-strong)", strokeWidth: active ? 2.2 : 1.2 + Math.min(2, e.evidence_count * 0.3), opacity: dim ? 0.25 : 1 },
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: active ? "var(--action)" : "var(--rule-strong)" },
        };
      }),
    [edges, selected, neighbours],
  );

  if (usage.data && !basic) {
    return (
      <EmptyState icon={<Lock className="size-6" />} title="The knowledge graph isn't in your plan" action={<ButtonLink href="/app/billing">Compare plans</ButtonLink>} />
    );
  }

  const presentTypes = Object.entries(meta.data?.entities_by_type || {}).sort((a, b) => b[1] - a[1]);
  const totalEntities = presentTypes.reduce((n, [, c]) => n + c, 0);
  const pending = (meta.data?.files_by_graph_status?.extracting || 0) + (meta.data?.files_by_graph_status?.none || 0);

  return (
    <div className="-mx-4 -my-6 flex h-[calc(100dvh-3.5rem)] flex-col sm:-mx-8 lg:-mx-10 lg:-my-9 lg:h-dvh">
      <header className="flex flex-wrap items-center gap-2 border-b border-rule bg-surface px-4 py-3 sm:px-6">
        <h1 className="mr-2 text-lg font-semibold">Knowledge graph</h1>
        <div className="relative w-full max-w-64 sm:w-auto">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an entity" className="h-9 pl-9" aria-label="Find an entity" />
          {dq.length >= 2 && search.data && (
            <ul className="absolute left-0 top-10 z-20 w-72 rounded-lg border border-rule bg-surface p-1 shadow-[var(--shadow-pop)]">
              {search.data.items.length === 0 && <li className="px-3 py-2 text-sm text-muted">No entities match.</li>}
              {search.data.items.map((e) => (
                <li key={e.id}>
                  <button
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-surface-2"
                    onClick={() => {
                      setQ("");
                      expand(e.id, 1, true);
                    }}
                  >
                    <span className="size-2.5 rounded-full" style={{ background: typeColor(e.type) }} />
                    <span className="flex-1 truncate">{e.name}</span>
                    <span className="text-[0.76rem] text-faint">{e.mention_count}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <Select
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
          disabled={!advanced}
          className="h-9 w-auto max-w-56"
          aria-label="Limit to a book or document"
          title={advanced ? undefined : "Filtering by source is part of Premium"}
        >
          <option value="">{advanced ? "All sources" : "All sources (Premium filters)"}</option>
          {files.data?.items
            .filter((f) => f.source_id)
            .map((f) => (
              <option key={f.id} value={f.source_id!}>
                {f.title || f.filename}
              </option>
            ))}
        </Select>
        <span className="flex-1" />
        {loading && <Spinner className="size-4 text-muted" label="Loading graph" />}
        <Button size="sm" variant="ghost" onClick={resetView}>
          <RotateCcw className="size-4" /> Reset view
        </Button>
      </header>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-rule bg-surface px-4 py-2 sm:px-6">
        {presentTypes.map(([t, c]) => {
          const on = types.includes(t);
          return (
            <button
              key={t}
              onClick={() => setTypes((cur) => (on ? cur.filter((x) => x !== t) : [...cur, t]))}
              aria-pressed={on}
              className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[0.8rem]", on ? "border-action bg-action-soft text-action" : "border-rule text-ink-2 hover:border-rule-strong")}
            >
              <span className="size-2 rounded-full" style={{ background: typeColor(t) }} />
              {titleCase(t)} <span className="tabular-nums text-faint">{c}</span>
            </button>
          );
        })}
        {types.length > 0 && (
          <button onClick={() => setTypes([])} className="text-[0.8rem] text-muted hover:text-ink">
            Clear types
          </button>
        )}
      </div>

      <div className="relative flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1 bg-paper">
          {error ? (
            <div className="p-6">
              <ErrorState error={error} onRetry={resetView} />
            </div>
          ) : meta.data && totalEntities === 0 ? (
            <EmptyState icon={<Network className="size-6" />} title={pending ? "Building your graph" : "No entities yet"}>
              {pending
                ? `Concepts and relationships are being extracted from ${pending} file${pending > 1 ? "s" : ""}. This runs in the background after indexing.`
                : "Entities are extracted from your files after they're indexed. Upload a book or document to start."}
            </EmptyState>
          ) : (
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              nodeTypes={nodeTypes}
              onNodeClick={(_, n) => {
                setSelected({ kind: "entity", id: n.id });
                setFocus(n.id);
              }}
              onNodeDoubleClick={(_, n) => expand(n.id)}
              onEdgeClick={(_, e) => setSelected({ kind: "rel", id: e.id })}
              onPaneClick={() => {
                setSelected(null);
                setFocus(null);
              }}
              nodesConnectable={false}
              minZoom={0.15}
              maxZoom={2.5}
              proOptions={{ hideAttribution: false }}
              fitView
            >
              <Background gap={28} size={1} color="var(--rule)" />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
          <div className="pointer-events-none absolute bottom-3 left-1/2 w-[min(560px,calc(100%-2rem))] -translate-x-1/2 space-y-2">
            {truncated && (
              <Notice tone="warn" className="pointer-events-auto shadow-sm">
                Showing a limited part of the graph to keep it readable. Search or expand a node to explore further.
              </Notice>
            )}
            {meta.data?.disclaimer && totalEntities > 0 && (
              <p className="pointer-events-auto mx-auto w-fit rounded-full bg-surface/90 px-3 py-1 text-center text-[0.76rem] text-muted shadow-sm">
                <Info className="mr-1 inline size-3.5" />
                AI-extracted from your sources, not verified facts. Double-click a node to expand it.
              </p>
            )}
          </div>
        </div>

        {selected && (
          <aside className="absolute inset-y-0 right-0 z-10 w-full max-w-sm overflow-y-auto border-l border-rule bg-surface shadow-[var(--shadow-pop)] sm:static sm:shadow-none">
            {selected.kind === "entity" ? (
              <EntityPanel id={selected.id} onClose={() => setSelected(null)} onExpand={(id, d) => expand(id, d)} advanced={advanced} onSelectRel={(id) => setSelected({ kind: "rel", id })} />
            ) : (
              <RelPanel id={selected.id} onClose={() => setSelected(null)} onSelectEntity={(id) => (setSelected({ kind: "entity", id }), setFocus(id))} />
            )}
          </aside>
        )}
      </div>
    </div>
  );
}

function PanelHeader({ title, sub, onClose }: { title: string; sub?: React.ReactNode; onClose: () => void }) {
  return (
    <div className="sticky top-0 flex items-start justify-between gap-3 border-b border-rule bg-surface px-4 py-3">
      <div className="min-w-0">
        <h2 className="font-semibold leading-snug">{title}</h2>
        {sub && <div className="mt-0.5 text-[0.82rem] text-muted">{sub}</div>}
      </div>
      <IconButton label="Close details" onClick={onClose}>
        <X className="size-4" />
      </IconButton>
    </div>
  );
}

function EntityPanel({
  id,
  onClose,
  onExpand,
  advanced,
  onSelectRel,
}: {
  id: string;
  onClose: () => void;
  onExpand: (id: string, depth: number) => void;
  advanced: boolean;
  onSelectRel: (id: string) => void;
}) {
  const q = useQuery({ queryKey: ["entity", id], queryFn: () => get<EntityDetail>(`/graph/entities/${id}`) });
  if (q.isError) return <div className="p-4"><ErrorState error={q.error} compact /></div>;
  if (!q.data) return <div className="p-4"><LoadingRows rows={4} /></div>;
  const e = q.data;
  return (
    <div>
      <PanelHeader
        title={e.name}
        onClose={onClose}
        sub={
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rounded-full" style={{ background: typeColor(e.type) }} />
            {titleCase(e.type)}, {e.mention_count} mentions in {e.source_count} {e.source_count === 1 ? "source" : "sources"}
          </span>
        }
      />
      <div className="space-y-5 px-4 py-4">
        {e.description && <p className="text-sm text-ink-2">{e.description}</p>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => onExpand(e.id, 1)}>
            <Expand className="size-3.5" /> Show neighbours
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onExpand(e.id, 2)} disabled={!advanced} title={advanced ? undefined : "Two-hop exploration is part of Premium"}>
            {!advanced && <Lock className="size-3.5" />} Two hops
          </Button>
        </div>
        {e.relationships.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-[0.8rem] font-medium text-muted">Relationships</h3>
            <ul className="space-y-0.5">
              {e.relationships.map((r) => (
                <li key={r.id}>
                  <button onClick={() => onSelectRel(r.id)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-2">
                    <span className="text-faint">{r.direction === "out" ? "→" : "←"}</span>
                    <span className="text-muted">{r.type.replace(/_/g, " ")}</span>
                    <span className="flex-1 truncate font-medium">{r.other.name}</span>
                    <span className="text-[0.76rem] tabular-nums text-faint">{r.evidence_count}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <SourceList
          title="Mentioned in"
          cites={e.mentions.map((m, i) => ({ key: `M${i}`, chunk_id: m.chunk_id, source_title: m.source_title, citation_label: m.citation_label, section_path: m.section_path }))}
        />
        {e.sources.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-[0.8rem] font-medium text-muted">Sources</h3>
            <ul className="space-y-1 text-sm">
              {e.sources.map((s) => (
                <li key={s.id}>
                  <Badge>{s.kind === "book" ? "Book" : "Document"}</Badge> {s.title}
                </li>
              ))}
            </ul>
          </div>
        )}
        <Link href={`/app/ask`} className="block text-sm text-action hover:underline">
          Ask about {e.name}
        </Link>
      </div>
    </div>
  );
}

function RelPanel({ id, onClose, onSelectEntity }: { id: string; onClose: () => void; onSelectEntity: (id: string) => void }) {
  const q = useQuery({ queryKey: ["relationship", id], queryFn: () => get<RelationshipDetail>(`/graph/relationships/${id}`) });
  if (q.isError) return <div className="p-4"><ErrorState error={q.error} compact /></div>;
  if (!q.data) return <div className="p-4"><LoadingRows rows={4} /></div>;
  const r = q.data;
  return (
    <div>
      <PanelHeader title={`${r.source_entity?.name ?? "?"} ${r.type.replace(/_/g, " ")} ${r.target_entity?.name ?? "?"}`} onClose={onClose} sub="Relationship" />
      <div className="space-y-5 px-4 py-4">
        <Notice tone="warn">{r.disclaimer}</Notice>
        <div className="flex flex-wrap gap-2 text-sm">
          {r.source_entity && (
            <button className="rounded-full border border-rule px-2.5 py-0.5 hover:bg-surface-2" onClick={() => onSelectEntity(r.source_entity!.id)}>
              {r.source_entity.name}
            </button>
          )}
          <span className="self-center text-muted">{r.type.replace(/_/g, " ")}</span>
          {r.target_entity && (
            <button className="rounded-full border border-rule px-2.5 py-0.5 hover:bg-surface-2" onClick={() => onSelectEntity(r.target_entity!.id)}>
              {r.target_entity.name}
            </button>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-muted">Supporting passages</dt>
            <dd className="tabular-nums">{r.evidence_count}</dd>
          </div>
          <div>
            <dt className="text-muted">Model confidence</dt>
            <dd className="tabular-nums">{r.confidence !== null ? `${Math.round(r.confidence * 100)}% (estimate)` : "Not given"}</dd>
          </div>
        </dl>
        {r.descriptions.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            {r.descriptions.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        )}
        <SourceList
          title="Evidence"
          cites={r.evidence.map((m, i) => ({ key: `R${i}`, chunk_id: m.chunk_id, source_title: m.source_title, citation_label: m.citation_label, section_path: m.section_path }))}
        />
      </div>
    </div>
  );
}

export default function GraphPage() {
  return (
    <CitationProvider>
      <ReactFlowProvider>
        <GraphView />
      </ReactFlowProvider>
    </CitationProvider>
  );
}
