"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Badge, EmptyState, ErrorState, LoadingRows, Meta, Notice, PageHeader, Section, Tabs } from "@/components/ui/misc";
import { hasFeature, useUsage } from "@/features/auth/session";
import { ApiError, get, patch, post } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { ResearchProject } from "@/types/api";

export default function ResearchPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const usage = useUsage();
  const [status, setStatus] = useState<"active" | "archived">("active");
  const projects = useQuery({ queryKey: ["research-projects", status], queryFn: () => get<{ items: ResearchProject[]; limit: number }>("/research/projects", { status }) });
  const active = useQuery({ queryKey: ["research-projects", "active"], queryFn: () => get<{ items: ResearchProject[]; limit: number }>("/research/projects", { status: "active" }) });
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const limit = active.data?.limit ?? 0;
  const activeCount = active.data?.items.length ?? 0;
  const atLimit = active.data ? activeCount >= limit : false;
  const enabled = hasFeature(usage.data, "deep_research");
  const reports = usage.data?.usage.research_reports;

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const p = await post<ResearchProject>("/research/projects", { title: title.trim(), question: question.trim() });
      qc.invalidateQueries({ queryKey: ["research-projects"] });
      router.push(`/app/research/${p.id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't create the project.");
      setBusy(false);
    }
  }

  async function setProjectStatus(p: ResearchProject, s: "active" | "archived") {
    try {
      await patch(`/research/projects/${p.id}`, { status: s });
      qc.invalidateQueries({ queryKey: ["research-projects"] });
      toast(s === "archived" ? "Project archived." : "Project restored.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't update the project.");
    }
  }

  return (
    <>
      <PageHeader
        title="Deep research"
        description="Give a research question. Knowledge Studio plans sub-questions, gathers evidence across your library, checks for gaps and conflicts, and writes a report where every claim is cited."
      />
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0">
          <Tabs
            value={status}
            onChange={setStatus}
            items={[
              { value: "active", label: "Active", count: activeCount },
              { value: "archived", label: "Archived" },
            ]}
          />
          <div className="mt-4">
            {projects.isError ? (
              <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
            ) : projects.isLoading ? (
              <LoadingRows rows={4} />
            ) : !projects.data?.items.length ? (
              <EmptyState icon={<Sparkles className="size-6" />} title={status === "active" ? "No research projects yet" : "Nothing archived"}>
                {status === "active" ? "Start with a question you'd otherwise answer by reading several books side by side." : "Archived projects keep their reports and free a project slot."}
              </EmptyState>
            ) : (
              <ul className="divide-y divide-rule">
                {projects.data.items.map((p) => (
                  <li key={p.id} className="flex items-start gap-3 py-4">
                    <Link href={`/app/research/${p.id}`} className="group min-w-0 flex-1">
                      <span className="block font-medium group-hover:underline">{p.title}</span>
                      <span className="mt-0.5 line-clamp-2 block text-sm text-ink-2">{p.question}</span>
                      <Meta className="mt-1" items={[`${p.run_count} ${p.run_count === 1 ? "run" : "runs"}`, `Updated ${relativeTime(p.updated_at)}`]} />
                    </Link>
                    {status === "active" ? (
                      <Button size="sm" variant="ghost" onClick={() => setProjectStatus(p, "archived")} aria-label={`Archive ${p.title}`}>
                        <Archive className="size-4" />
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" onClick={() => setProjectStatus(p, "active")} disabled={atLimit}>
                        <ArchiveRestore className="size-4" /> Restore
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <Section title="New research project">
            {!enabled ? (
              <Notice tone="highlight">
                Deep research isn&apos;t included in your plan.{" "}
                <Link href="/app/billing" className="font-medium underline">
                  Compare plans
                </Link>
              </Notice>
            ) : atLimit ? (
              <Notice tone="warn">
                You have {activeCount} of {limit} active {limit === 1 ? "project" : "projects"}. Archive one to start another, or{" "}
                <Link href="/app/billing" className="font-medium underline">
                  upgrade
                </Link>
                .
              </Notice>
            ) : (
              <form onSubmit={create} className="space-y-4">
                <Field label="Title">{(p) => <Input {...p} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} placeholder="Monastic economies in medieval Georgia" />}</Field>
                <Field label="Research question" hint="Be specific about what you want compared, explained or traced.">
                  {(p) => <Textarea {...p} value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={2000} rows={4} />}
                </Field>
                {err && <p className="text-sm text-danger">{err}</p>}
                <Button type="submit" className="w-full" loading={busy} disabled={title.trim().length < 2 || question.trim().length < 5}>
                  Create project
                </Button>
              </form>
            )}
          </Section>
          {reports && (
            <div className="px-1 text-sm text-muted">
              <Badge tone={reports.used >= reports.limit ? "warn" : "neutral"}>
                {Math.max(0, reports.limit - reports.used - reports.reserved)} of {reports.limit} reports left
              </Badge>{" "}
              this period. Projects: {activeCount} of {limit} active.
            </div>
          )}
        </div>
      </div>
    </>
  );
}
