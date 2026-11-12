"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileUp, Upload, XCircle } from "lucide-react";
import Link from "next/link";
import { useRef, useState, type DragEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Select } from "@/components/ui/field";
import { Notice, UsageBar } from "@/components/ui/misc";
import { qk, useUsage } from "@/features/auth/session";
import { ApiError, newIdempotencyKey, uploadFile } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";

export const ACCEPTED_EXT = ["pdf", "txt", "md", "markdown", "json", "docx", "html", "htm", "csv"];
const ACCEPT_ATTR = ACCEPTED_EXT.map((e) => `.${e}`).join(",");

type Item = {
  key: string;
  file: File;
  state: "ready" | "uploading" | "done" | "error";
  progress: number;
  error?: string;
  upgrade?: boolean;
  fileId?: string;
};

function ext(name: string) {
  return name.split(".").pop()?.toLowerCase() || "";
}

export function UploadDialog({ open, onOpenChange, collectionId }: { open: boolean; onOpenChange: (v: boolean) => void; collectionId?: string }) {
  const qc = useQueryClient();
  const { data: usage } = useUsage();
  const input = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [kind, setKind] = useState<"auto" | "book" | "document">("auto");
  const [drag, setDrag] = useState(false);
  const busy = items.some((i) => i.state === "uploading");

  const maxFile = usage?.storage.max_file_size_bytes ?? 0;
  const available = usage?.storage.available_bytes ?? 0;
  const up = usage?.usage.uploads;
  const uploadsLeft = up ? Math.max(0, up.limit - up.used - up.reserved) : 0;

  function add(files: FileList | File[]) {
    const next: Item[] = [];
    for (const f of Array.from(files)) {
      // Client-side checks are a convenience only; the server enforces every limit.
      let error: string | undefined;
      if (!ACCEPTED_EXT.includes(ext(f.name))) error = "Unsupported type. Use PDF, TXT, Markdown, JSON, DOCX, HTML or CSV.";
      else if (f.size === 0) error = "This file is empty.";
      else if (maxFile && f.size > maxFile) error = `${formatBytes(f.size)} is over your plan's ${formatBytes(maxFile)} per-file limit.`;
      next.push({ key: newIdempotencyKey(), file: f, state: error ? "error" : "ready", progress: 0, error, upgrade: Boolean(error && maxFile && f.size > maxFile) });
    }
    setItems((cur) => [...cur.filter((c) => c.state !== "done"), ...next]);
  }

  function update(key: string, patch: Partial<Item>) {
    setItems((cur) => cur.map((c) => (c.key === key ? { ...c, ...patch } : c)));
  }

  async function start() {
    for (const it of items.filter((i) => i.state === "ready")) {
      update(it.key, { state: "uploading", progress: 0 });
      try {
        const res = await uploadFile(it.file, { kind, collectionId, idempotencyKey: it.key }, (p) => update(it.key, { progress: p }));
        update(it.key, { state: "done", progress: 1, fileId: res.file.id });
      } catch (e) {
        const err = e instanceof ApiError ? e : null;
        update(it.key, { state: "error", error: err?.message || "Upload failed.", upgrade: Boolean(err && (err.isQuota || err.status === 413)) });
      }
      qc.invalidateQueries({ queryKey: qk.usage });
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["library-stats"] });
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    if (e.dataTransfer.files?.length) add(e.dataTransfer.files);
  }

  const ready = items.filter((i) => i.state === "ready");
  const readyBytes = ready.reduce((n, i) => n + i.file.size, 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !busy && onOpenChange(v)}
      title="Upload to your library"
      description="Files are processed in the background. You can keep working while they're indexed."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            {items.some((i) => i.state === "done") ? "Done" : "Cancel"}
          </Button>
          <Button onClick={start} disabled={!ready.length || busy} loading={busy}>
            <Upload className="size-4" />
            Upload {ready.length > 1 ? `${ready.length} files` : "file"}
          </Button>
        </>
      }
    >
      {usage && (
        <div className="mb-4 grid gap-3 rounded-[10px] bg-surface-2 px-4 py-3 text-sm sm:grid-cols-3">
          <div>
            <div className="text-muted">Storage left</div>
            <div className="font-medium tabular-nums">{formatBytes(available)}</div>
          </div>
          <div>
            <div className="text-muted">Largest file</div>
            <div className="font-medium tabular-nums">{formatBytes(maxFile)}</div>
          </div>
          <div>
            <div className="text-muted">Uploads left</div>
            <div className="font-medium tabular-nums">
              {uploadsLeft} of {up?.limit}
            </div>
          </div>
          <div className="sm:col-span-3">
            <UsageBar used={usage.storage.used_bytes + readyBytes} limit={usage.storage.limit_bytes} label="Storage after upload" />
          </div>
        </div>
      )}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center rounded-[12px] border-2 border-dashed px-6 py-8 text-center transition-colors",
          drag ? "border-action bg-action-soft" : "border-rule-strong",
        )}
      >
        <FileUp className="size-7 text-muted" />
        <p className="mt-2 font-medium">Drop files here</p>
        <p className="text-sm text-muted">PDF, DOCX, TXT, Markdown, HTML, JSON or CSV</p>
        <Button variant="secondary" size="sm" className="mt-4" onClick={() => input.current?.click()}>
          Choose files
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT_ATTR}
          className="sr-only"
          data-testid="file-input"
          onChange={(e) => {
            if (e.target.files) add(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label htmlFor="upload-kind" className="text-sm text-muted">
          Treat as
        </label>
        <Select id="upload-kind" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className="w-auto min-w-44">
          <option value="auto">Detect automatically</option>
          <option value="book">Book</option>
          <option value="document">Document</option>
        </Select>
      </div>

      {items.length > 0 && (
        <ul className="mt-4 divide-y divide-rule rounded-[10px] border border-rule">
          {items.map((it) => (
            <li key={it.key} className="flex items-center gap-3 px-3.5 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-sm font-medium">{it.file.name}</span>
                  <span className="shrink-0 text-xs tabular-nums text-muted">{formatBytes(it.file.size)}</span>
                </div>
                {it.state === "uploading" && (
                  <div className="mt-1.5">
                    <UsageBar used={Math.round(it.progress * 100)} limit={100} label={`Uploading ${it.file.name}`} />
                  </div>
                )}
                {it.state === "done" && <p className="mt-0.5 text-xs text-ok">Uploaded. Processing has started.</p>}
                {it.state === "error" && (
                  <p className="mt-0.5 text-xs text-danger">
                    {it.error}{" "}
                    {it.upgrade && (
                      <Link href="/app/billing" className="font-medium underline">
                        See plans
                      </Link>
                    )}
                  </p>
                )}
              </div>
              {it.state === "done" ? (
                <CheckCircle2 className="size-4.5 shrink-0 text-ok" aria-label="Uploaded" />
              ) : it.state === "error" ? (
                <XCircle className="size-4.5 shrink-0 text-danger" aria-label="Failed" />
              ) : it.state === "ready" ? (
                <button className="text-xs text-muted hover:text-ink" onClick={() => setItems((c) => c.filter((x) => x.key !== it.key))}>
                  Remove
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {uploadsLeft === 0 && usage && (
        <Notice tone="warn" className="mt-4">
          You&apos;ve used every upload in this period. Uploads reset on {new Date(up!.resets_at!).toLocaleDateString()}.{" "}
          <Link href="/app/billing" className="font-medium underline">
            Compare plans
          </Link>
        </Notice>
      )}
    </Dialog>
  );
}
