"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Library, MoreHorizontal, RotateCw, Search, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button, IconButton } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/menu";
import { EmptyState, ErrorState, LoadingRows, Meta, PageHeader, Pagination, Tabs } from "@/components/ui/misc";
import { qk } from "@/features/auth/session";
import { FileStatus, isProcessing } from "@/features/library/status";
import { UploadDialog } from "@/features/library/upload-dialog";
import { useDebounced, useUrlState } from "@/hooks/use-url-state";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import type { Collection, FileItem, LibraryStats, Paged } from "@/types/api";

const PAGE_SIZE = 20;

function LibraryView() {
  const qc = useQueryClient();
  const router = useRouter();
  const [f, setF] = useUrlState({ status: "all", kind: "all", sort: "newest", q: "", collection: "", page: "1" });
  const [search, setSearch] = useState(f.q);
  const q = useDebounced(search, 300);
  useEffect(() => {
    if (q !== f.q) setF({ q, page: "1" });
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  const [uploadOpen, setUploadOpen] = useState(false);
  // Selection belongs to one filtered view; changing filters starts a fresh selection.
  const viewKey = `${f.status}|${f.kind}|${f.q}|${f.collection}|${f.page}`;
  const [selection, setSelection] = useState<{ key: string; ids: Set<string> }>({ key: viewKey, ids: new Set() });
  const selected = selection.key === viewKey ? selection.ids : new Set<string>();
  const setSelected = (next: Set<string> | ((cur: Set<string>) => Set<string>)) =>
    setSelection((s) => ({ key: viewKey, ids: typeof next === "function" ? next(s.key === viewKey ? s.ids : new Set()) : next }));
  const [toDelete, setToDelete] = useState<FileItem[] | null>(null);
  const [newCollection, setNewCollection] = useState(false);

  const page = Number(f.page) || 1;
  const files = useQuery({
    queryKey: ["files", f],
    queryFn: () =>
      get<Paged<FileItem>>("/files", { page, page_size: PAGE_SIZE, status: f.status, kind: f.kind, sort: f.sort, q: f.q || undefined, collection_id: f.collection || undefined }),
    refetchInterval: (query) => (query.state.data?.items.some(isProcessing) ? 3000 : false),
    placeholderData: (prev) => prev,
  });
  const stats = useQuery({ queryKey: ["library-stats"], queryFn: () => get<LibraryStats>("/library/stats") });
  const collections = useQuery({ queryKey: ["collections"], queryFn: () => get<{ items: Collection[] }>("/collections") });


  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["library-stats"] });
    qc.invalidateQueries({ queryKey: qk.usage });
    qc.invalidateQueries({ queryKey: ["collections"] });
  };

  const retry = useMutation({
    mutationFn: (id: string) => post(`/files/${id}/retry`),
    onSuccess: () => {
      toast("Processing restarted.");
      refresh();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Retry failed."),
  });

  async function addToCollection(collectionId: string, ids: string[], action: "add" | "remove" = "add") {
    try {
      await post(`/collections/${collectionId}/files`, { file_ids: ids, action });
      toast(action === "add" ? `Added ${ids.length} to collection.` : "Removed from collection.");
      setSelected(new Set());
      refresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't update the collection.");
    }
  }

  const items = files.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((i) => selected.has(i.id));
  const s = stats.data;
  const activeCollection = collections.data?.items.find((c) => c.id === f.collection);

  return (
    <>
      <PageHeader
        title={activeCollection ? activeCollection.name : "Library"}
        description={activeCollection?.description || "Every file you've uploaded. Books and documents are listed separately in their own views."}
        actions={
          <Button onClick={() => setUploadOpen(true)}>
            <Upload className="size-4" /> Upload
          </Button>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[200px_minmax(0,1fr)]">
        <aside className="space-y-1 text-sm" aria-label="Collections">
          <button
            onClick={() => setF({ collection: "", page: "1" })}
            className={cn("flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left", !f.collection ? "bg-surface-2 font-medium" : "text-ink-2 hover:bg-surface-2")}
          >
            All files <span className="tabular-nums text-faint">{s?.files_uploaded ?? ""}</span>
          </button>
          <div className="px-2.5 pb-1 pt-4 text-[0.76rem] font-medium text-faint">Collections</div>
          {collections.data?.items.map((c) => (
            <button
              key={c.id}
              onClick={() => setF({ collection: c.id, page: "1" })}
              className={cn("flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left", f.collection === c.id ? "bg-surface-2 font-medium" : "text-ink-2 hover:bg-surface-2")}
            >
              <span className="truncate">{c.name}</span>
              <span className="tabular-nums text-faint">{c.file_count}</span>
            </button>
          ))}
          {collections.data?.items.length === 0 && <p className="px-2.5 text-[0.82rem] text-muted">Group related files to filter questions and research.</p>}
          <button onClick={() => setNewCollection(true)} className="mt-1 flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-action hover:bg-action-soft">
            <FolderPlus className="size-4" /> New collection
          </button>
          {activeCollection && <CollectionActions collection={activeCollection} onChanged={refresh} onDeleted={() => setF({ collection: "" })} />}
        </aside>

        <div className="min-w-0">
          <Tabs
            value={f.status as "all"}
            onChange={(v) => setF({ status: v, page: "1" })}
            items={[
              { value: "all", label: "All" },
              { value: "processing", label: "Processing", count: s?.files_processing },
              { value: "indexed", label: "Ready", count: s?.files_indexed },
              { value: "attention", label: "Needs attention", count: s?.files_need_attention },
            ]}
          />
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <div className="relative min-w-52 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search titles and filenames" className="pl-9" aria-label="Search files" />
            </div>
            <Select value={f.kind} onChange={(e) => setF({ kind: e.target.value, page: "1" })} className="w-auto" aria-label="Type">
              <option value="all">Books and documents</option>
              <option value="book">Books</option>
              <option value="document">Documents</option>
            </Select>
            <Select value={f.sort} onChange={(e) => setF({ sort: e.target.value })} className="w-auto" aria-label="Sort">
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="title">Title</option>
              <option value="size">Largest first</option>
            </Select>
          </div>

          {selected.size > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-[10px] bg-action-soft px-3.5 py-2 text-sm">
              <span className="font-medium">{selected.size} selected</span>
              <span className="flex-1" />
              {collections.data?.items.length ? (
                <Menu
                  trigger={
                    <Button size="sm" variant="secondary">
                      Add to collection
                    </Button>
                  }
                >
                  {collections.data.items.map((c) => (
                    <MenuItem key={c.id} onSelect={() => addToCollection(c.id, [...selected])}>
                      {c.name}
                    </MenuItem>
                  ))}
                </Menu>
              ) : null}
              {activeCollection && (
                <Button size="sm" variant="secondary" onClick={() => addToCollection(activeCollection.id, [...selected], "remove")}>
                  Remove from collection
                </Button>
              )}
              <Button size="sm" variant="danger" onClick={() => setToDelete(items.filter((i) => selected.has(i.id)))}>
                <Trash2 className="size-3.5" /> Delete
              </Button>
            </div>
          )}

          <div className="mt-4">
            {files.isError ? (
              <ErrorState error={files.error} onRetry={() => files.refetch()} />
            ) : files.isLoading ? (
              <LoadingRows rows={6} />
            ) : items.length === 0 ? (
              <EmptyState
                icon={<Library className="size-6" />}
                title={f.q || f.status !== "all" || f.kind !== "all" || f.collection ? "No files match these filters" : "Your library is empty"}
                action={
                  !f.q && f.status === "all" ? (
                    <Button onClick={() => setUploadOpen(true)}>
                      <Upload className="size-4" /> Upload files
                    </Button>
                  ) : (
                    <Button variant="secondary" onClick={() => (setSearch(""), setF({ status: "all", kind: "all", q: "", collection: "", page: "1" }))}>
                      Clear filters
                    </Button>
                  )
                }
              >
                {f.collection ? "Select files in All files and add them to this collection." : "Upload books and documents to search and question them."}
              </EmptyState>
            ) : (
              <div className="overflow-hidden rounded-[var(--radius-panel)] border border-rule bg-surface">
                <table className="w-full table-fixed text-sm">
                  <thead className="border-b border-rule bg-surface-2/60 text-left text-[0.8rem] text-muted">
                    <tr>
                      <th className="w-11 py-2.5 pl-4">
                        <input
                          type="checkbox"
                          aria-label="Select all on this page"
                          className="size-4 accent-[var(--action)]"
                          checked={allSelected}
                          onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((i) => i.id)))}
                        />
                      </th>
                      <th className="py-2.5 font-medium">Title</th>
                      <th className="hidden w-48 py-2.5 font-medium md:table-cell">Status</th>
                      <th className="hidden w-24 py-2.5 pr-2 text-right font-medium sm:table-cell">Size</th>
                      <th className="w-12" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-rule">
                    {items.map((it) => (
                      <tr key={it.id} className={cn("align-middle", selected.has(it.id) && "bg-action-soft/40")}>
                        <td className="py-3 pl-4">
                          <input
                            type="checkbox"
                            className="size-4 accent-[var(--action)]"
                            aria-label={`Select ${it.title || it.filename}`}
                            checked={selected.has(it.id)}
                            onChange={() =>
                              setSelected((cur) => {
                                const n = new Set(cur);
                                if (n.has(it.id)) n.delete(it.id);
                                else n.add(it.id);
                                return n;
                              })
                            }
                          />
                        </td>
                        <td className="py-3 pr-3">
                          <Link href={`/app/library/${it.id}`} className="block truncate font-medium hover:underline">
                            {it.title || it.filename}
                          </Link>
                          <Meta
                            items={[
                              it.kind === "book" ? "Book" : it.kind === "document" ? "Document" : "Detecting type",
                              it.file_type.toUpperCase(),
                              it.chunk_count ? `${it.chunk_count} passages` : null,
                              relativeTime(it.created_at),
                            ]}
                          />
                          <div className="mt-1 md:hidden">
                            <FileStatus file={it} />
                          </div>
                          {it.processing_error?.message && <p className="mt-1 text-[0.8rem] text-danger">{it.processing_error.message}</p>}
                        </td>
                        <td className="hidden py-3 md:table-cell">
                          <FileStatus file={it} />
                        </td>
                        <td className="hidden py-3 pr-2 text-right tabular-nums text-muted sm:table-cell">{it.size_display}</td>
                        <td className="py-3 pr-2 text-right">
                          <Menu
                            trigger={
                              <IconButton label={`Actions for ${it.title || it.filename}`}>
                                <MoreHorizontal className="size-4" />
                              </IconButton>
                            }
                          >
                            <MenuItem onSelect={() => router.push(`/app/library/${it.id}`)}>Details</MenuItem>
                            {it.processing_status === "indexed" && <MenuItem onSelect={() => router.push(`/app/ask?file=${it.id}`)}>Ask about this file</MenuItem>}
                            {it.original_available && <MenuItem onSelect={() => window.open(`/api/v1/files/${it.id}/download`, "_self")}>Download original</MenuItem>}
                            {it.processing_status === "failed" && (
                              <MenuItem onSelect={() => retry.mutate(it.id)}>
                                <RotateCw className="size-4" /> Retry processing
                              </MenuItem>
                            )}
                            <MenuSeparator />
                            <MenuItem danger onSelect={() => setToDelete([it])}>
                              <Trash2 className="size-4" /> Delete
                            </MenuItem>
                          </Menu>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {files.data && <Pagination page={page} pageSize={PAGE_SIZE} total={files.data.total} onPage={(p) => setF({ page: String(p) })} />}
          </div>
        </div>
      </div>

      <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} collectionId={f.collection || undefined} />
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => !v && setToDelete(null)}
        title={toDelete && toDelete.length > 1 ? `Delete ${toDelete.length} files?` : "Delete this file?"}
        description="The original file, its passages, embeddings and graph links are removed. Reports that cited it keep their text. This can't be undone."
        confirmLabel="Delete"
        danger
        onConfirm={async () => {
          const results = await Promise.allSettled((toDelete || []).map((it) => del(`/files/${it.id}`)));
          const failed = results.filter((r) => r.status === "rejected").length;
          if (failed) toast.error(`${failed} file${failed > 1 ? "s" : ""} couldn't be deleted.`);
          else toast("Deleting. Storage is released once removal is confirmed.");
          setSelected(new Set());
          refresh();
        }}
      >
        {toDelete && toDelete.length <= 5 && (
          <ul className="list-disc pl-5 text-sm text-ink-2">
            {toDelete.map((t) => (
              <li key={t.id}>{t.title || t.filename}</li>
            ))}
          </ul>
        )}
      </ConfirmDialog>
      {newCollection && <CollectionDialog onOpenChange={setNewCollection} onSaved={refresh} />}
    </>
  );
}

/** Mounted only while open, so its fields always start from the current collection. */
function CollectionDialog({ onOpenChange, onSaved, existing }: { onOpenChange: (v: boolean) => void; onSaved: () => void; existing?: Collection }) {
  const [name, setName] = useState(existing?.name || "");
  const [description, setDescription] = useState(existing?.description || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function save() {
    setBusy(true);
    setErr(null);
    try {
      if (existing) await patch(`/collections/${existing.id}`, { name, description });
      else await post("/collections", { name, description });
      onSaved();
      onOpenChange(false);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title={existing ? "Edit collection" : "New collection"}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!name.trim()}>
            {existing ? "Save collection" : "Create collection"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">{(p) => <Input {...p} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoFocus />}</Field>
        <Field label="Description" optional>
          {(p) => <Textarea {...p} value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} rows={3} />}
        </Field>
        {err && <p className="text-sm text-danger">{err}</p>}
      </div>
    </Dialog>
  );
}

function CollectionActions({ collection, onChanged, onDeleted }: { collection: Collection; onChanged: () => void; onDeleted: () => void }) {
  const [edit, setEdit] = useState(false);
  const [remove, setRemove] = useState(false);
  return (
    <div className="mt-4 border-t border-rule pt-3">
      <button className="block w-full rounded-md px-2.5 py-1.5 text-left text-ink-2 hover:bg-surface-2" onClick={() => setEdit(true)}>
        Edit collection
      </button>
      <button className="block w-full rounded-md px-2.5 py-1.5 text-left text-danger hover:bg-danger-soft" onClick={() => setRemove(true)}>
        Delete collection
      </button>
      {edit && <CollectionDialog onOpenChange={setEdit} onSaved={onChanged} existing={collection} />}
      <ConfirmDialog
        open={remove}
        onOpenChange={setRemove}
        title={`Delete “${collection.name}”?`}
        description="Only the collection is removed. Its files stay in your library."
        confirmLabel="Delete collection"
        danger
        onConfirm={async () => {
          await del(`/collections/${collection.id}`);
          onDeleted();
          onChanged();
        }}
      />
    </div>
  );
}

export default function LibraryPage() {
  return (
    <Suspense fallback={<LoadingRows rows={6} />}>
      <LibraryView />
    </Suspense>
  );
}
