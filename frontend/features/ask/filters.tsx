"use client";

import { useQuery } from "@tanstack/react-query";
import { Lock, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { Badge } from "@/components/ui/misc";
import { hasFeature, useUsage } from "@/features/auth/session";
import { get } from "@/lib/api";
import type { Collection, FileItem, Filters, Paged } from "@/types/api";

export const EMPTY_FILTERS: Filters = {
  source_ids: [],
  file_ids: [],
  collection_id: null,
  source_type: null,
  language: null,
  author: null,
  date_from: null,
  date_to: null,
};

export function activeFilterCount(f: Filters) {
  return f.file_ids.length + f.source_ids.length + (f.collection_id ? 1 : 0) + (f.source_type ? 1 : 0) + (f.language ? 1 : 0) + (f.author ? 1 : 0) + (f.date_from || f.date_to ? 1 : 0);
}

export function useIndexedFiles() {
  return useQuery({ queryKey: ["files", "indexed-all"], queryFn: () => get<Paged<FileItem>>("/files", { status: "indexed", page_size: 100, sort: "title" }), staleTime: 30_000 });
}

/** Chips summarising the current scope, with a dialog to change it. */
export function FilterBar({ value, onChange, disabled }: { value: Filters; onChange: (f: Filters) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const files = useIndexedFiles();
  const collections = useQuery({ queryKey: ["collections"], queryFn: () => get<{ items: Collection[] }>("/collections") });
  const name = (id: string) => files.data?.items.find((f) => f.id === id)?.title || "File";
  const chips: { label: string; clear: () => void }[] = [];
  value.file_ids.forEach((id) => chips.push({ label: name(id), clear: () => onChange({ ...value, file_ids: value.file_ids.filter((x) => x !== id) }) }));
  if (value.collection_id)
    chips.push({ label: `Collection: ${collections.data?.items.find((c) => c.id === value.collection_id)?.name || "…"}`, clear: () => onChange({ ...value, collection_id: null }) });
  if (value.source_type) chips.push({ label: value.source_type === "book" ? "Books only" : "Documents only", clear: () => onChange({ ...value, source_type: null }) });
  if (value.author) chips.push({ label: `Author: ${value.author}`, clear: () => onChange({ ...value, author: null }) });
  if (value.language) chips.push({ label: `Language: ${value.language}`, clear: () => onChange({ ...value, language: null }) });
  if (value.date_from || value.date_to) chips.push({ label: `Published ${value.date_from || "…"}–${value.date_to || "…"}`, clear: () => onChange({ ...value, date_from: null, date_to: null }) });

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)} disabled={disabled}>
        <SlidersHorizontal className="size-3.5" />
        {chips.length ? "Scope" : "Whole library"}
      </Button>
      {chips.map((c) => (
        <span key={c.label} className="inline-flex max-w-64 items-center gap-1 rounded-full bg-surface-2 py-0.5 pl-2.5 pr-1 text-[0.8rem] text-ink-2">
          <span className="truncate">{c.label}</span>
          <button onClick={c.clear} className="rounded-full p-0.5 hover:bg-rule" aria-label={`Remove filter ${c.label}`} disabled={disabled}>
            <X className="size-3" />
          </button>
        </span>
      ))}
      <FilterDialog open={open} onOpenChange={setOpen} value={value} onChange={onChange} files={files.data?.items ?? []} collections={collections.data?.items ?? []} />
    </div>
  );
}

function FilterDialog({
  open,
  onOpenChange,
  value,
  onChange,
  files,
  collections,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  value: Filters;
  onChange: (f: Filters) => void;
  files: FileItem[];
  collections: Collection[];
}) {
  const { data: usage } = useUsage();
  const advanced = hasFeature(usage, "advanced_filters");
  const [draft, setDraft] = useState<Filters>(value);
  const [fileQ, setFileQ] = useState("");
  const shown = files.filter((f) => (f.title || f.filename).toLowerCase().includes(fileQ.toLowerCase())).slice(0, 50);

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (v) setDraft(value);
        onOpenChange(v);
      }}
      title="Choose what to search"
      description="Answers only use passages from the sources you select."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={() => setDraft(EMPTY_FILTERS)}>
            Reset to whole library
          </Button>
          <Button
            onClick={() => {
              onChange(draft);
              onOpenChange(false);
            }}
          >
            Apply scope
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Collection">
            {(p) => (
              <Select {...p} value={draft.collection_id || ""} onChange={(e) => setDraft({ ...draft, collection_id: e.target.value || null })}>
                <option value="">Any collection</option>
                {collections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Type">
            {(p) => (
              <Select {...p} value={draft.source_type || ""} onChange={(e) => setDraft({ ...draft, source_type: (e.target.value || null) as Filters["source_type"] })}>
                <option value="">Books and documents</option>
                <option value="book">Books only</option>
                <option value="document">Documents only</option>
              </Select>
            )}
          </Field>
        </div>

        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <span className="text-sm font-medium">Specific files</span>
            <span className="text-[0.8rem] text-muted">{draft.file_ids.length ? `${draft.file_ids.length} selected` : "None selected means all"}</span>
          </div>
          <Input value={fileQ} onChange={(e) => setFileQ(e.target.value)} placeholder="Filter files" aria-label="Filter files" />
          <ul className="mt-2 max-h-52 divide-y divide-rule overflow-y-auto rounded-lg border border-rule">
            {shown.length === 0 && <li className="px-3 py-3 text-sm text-muted">No indexed files match.</li>}
            {shown.map((f) => (
              <li key={f.id}>
                <label className="flex cursor-pointer items-center gap-2.5 px-3 py-2 text-sm hover:bg-surface-2">
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--action)]"
                    checked={draft.file_ids.includes(f.id)}
                    onChange={(e) =>
                      setDraft({ ...draft, file_ids: e.target.checked ? [...draft.file_ids, f.id].slice(0, 50) : draft.file_ids.filter((x) => x !== f.id) })
                    }
                  />
                  <span className="min-w-0 flex-1 truncate">{f.title || f.filename}</span>
                  <span className="text-[0.78rem] text-faint">{f.kind === "book" ? "Book" : "Document"}</span>
                </label>
              </li>
            ))}
          </ul>
        </div>

        <fieldset disabled={!advanced} className="rounded-lg border border-rule p-4">
          <legend className="flex items-center gap-2 px-1 text-sm font-medium">
            Advanced filters
            {!advanced && (
              <Badge tone="highlight">
                <Lock className="size-3" /> Premium
              </Badge>
            )}
          </legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Author">{(p) => <Input {...p} value={draft.author || ""} maxLength={120} onChange={(e) => setDraft({ ...draft, author: e.target.value || null })} />}</Field>
            <Field label="Language code" hint="For example en or ka">
              {(p) => <Input {...p} value={draft.language || ""} maxLength={10} onChange={(e) => setDraft({ ...draft, language: e.target.value || null })} />}
            </Field>
            <Field label="Published from">{(p) => <Input {...p} value={draft.date_from || ""} placeholder="1990" maxLength={20} onChange={(e) => setDraft({ ...draft, date_from: e.target.value || null })} />}</Field>
            <Field label="Published to">{(p) => <Input {...p} value={draft.date_to || ""} placeholder="2024" maxLength={20} onChange={(e) => setDraft({ ...draft, date_to: e.target.value || null })} />}</Field>
          </div>
          {!advanced && (
            <p className="mt-3 text-[0.82rem] text-muted">
              Filtering by author, language and date is part of Premium.{" "}
              <Link href="/app/billing" className="text-action underline">
                Compare plans
              </Link>
            </p>
          )}
        </fieldset>
      </div>
    </Dialog>
  );
}
