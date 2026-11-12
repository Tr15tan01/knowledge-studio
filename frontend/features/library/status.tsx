import { AlertCircle, CheckCircle2, Clock, Loader2, RotateCw } from "lucide-react";
import { Badge } from "@/components/ui/misc";
import type { FileItem, JobProgress } from "@/types/api";

const PHASES: Record<string, string> = {
  queued: "Waiting",
  extracting: "Reading text",
  parsing: "Reading text",
  chunking: "Splitting into passages",
  embedding: "Indexing",
  indexing: "Indexing",
  persisting: "Saving",
  completed: "Done",
};

export function progressLabel(p: JobProgress | null | undefined): string | null {
  if (!p?.phase || p.phase === "completed") return null;
  const label = PHASES[p.phase] || p.phase.replace(/_/g, " ");
  if (p.total_steps && p.completed_steps !== undefined) return `${label} (${p.completed_steps} of ${p.total_steps})`;
  return label;
}

export function FileStatus({ file }: { file: Pick<FileItem, "processing_status" | "progress" | "processing_error"> }) {
  switch (file.processing_status) {
    case "indexed":
      return (
        <Badge tone="ok">
          <CheckCircle2 className="size-3.5" /> Ready
        </Badge>
      );
    case "queued":
    case "uploading":
      return (
        <Badge tone="neutral">
          <Clock className="size-3.5" /> Queued
        </Badge>
      );
    case "processing":
      return (
        <Badge tone="action">
          <Loader2 className="size-3.5 animate-spin" /> {progressLabel(file.progress) || "Processing"}
        </Badge>
      );
    case "retrying":
      return (
        <Badge tone="warn">
          <RotateCw className="size-3.5" /> Retrying
        </Badge>
      );
    case "failed":
    case "rejected":
      return (
        <Badge tone="danger">
          <AlertCircle className="size-3.5" /> Needs attention
        </Badge>
      );
    default:
      return <Badge>{file.processing_status}</Badge>;
  }
}

export const isProcessing = (f: Pick<FileItem, "processing_status">) => ["uploading", "queued", "processing", "retrying"].includes(f.processing_status);
