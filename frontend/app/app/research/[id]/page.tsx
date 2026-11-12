"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileText, Play, Trash2, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { Button, ButtonLink } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Select, Textarea } from "@/components/ui/field";
import { Badge, ErrorState, LoadingRows, Notice, PageHeader, Section, UsageBar } from "@/components/ui/misc";
import { EMPTY_FILTERS, FilterBar } from "@/features/ask/filters";
import { qk, useUsage } from "@/features/auth/session";
import { RunTimeline, STOP_REASON, TERMINAL, phaseLabel, useRunEvents } from "@/features/research/run-progress";
import { ApiError, del, get, newIdempotencyKey, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber, relativeTime } from "@/lib/format";
import type { Estimate, Filters, ResearchProject, ResearchRun } from "@/types/api";

const STATUS_TONE: Record<string, "ok" | "warn" | "danger" | "action" | "neutral"> = {
  completed: "ok",
  incomplete: "warn",
  failed: "danger",
  cancelled: "neutral",
  running: "action",
  queued: "neutral",
};

function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const usage = useUsage();
  const project = useQuery({
    queryKey: ["research-project", id],
    queryFn: () => get<ResearchProject & { runs: ResearchRun[] }>(`/research/projects/${id}`),
    refetchInterval: (q) => (q.state.data?.runs.some((r) => !TERMINAL.includes(r.status)) ? 5000 : false),
  });
  const runs = project.data?.runs ?? [];
  const selectedId = params.get("run") || runs[0]?.id;

  const [depth, setDepth] = useState<"quick" | "standard" | "thorough">("standard");
  const [question, setQuestion] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [starting, setStarting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const estimate = useQuery({ queryKey: ["research-estimate", depth], queryFn: () => post<Estimate>(`/research/estimate?depth=${depth}`) });

  const reports = usage.data?.usage.research_reports;
  const concurrent = usage.data?.usage.concurrent_research;
  const reportsLeft = reports ? reports.limit - reports.used - reports.reserved : 1;

  async function start() {
    setStarting(true);
    try {
      const run = await post<ResearchRun>(
        `/research/projects/${id}/runs`,
        { depth, filters, question: question?.trim() && question.trim() !== project.data?.question ? question.trim() : null },
        { headers: { "idempotency-key": newIdempotencyKey() } },
      );
      await qc.invalidateQueries({ queryKey: ["research-project", id] });
      qc.invalidateQueries({ queryKey: qk.usage });
      router.replace(`/app/research/${id}?run=${run.id}`, { scroll: false });
      toast("Research started. You can leave this page; progress is saved.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't start research.");
    } finally {
      setStarting(false);
    }
  }

  if (project.isError) return <ErrorState error={project.error} onRetry={() => project.refetch()} />;
  if (!project.data) return <LoadingRows rows={6} />;
  const p = project.data;
  const archived = p.status === "archived";
  const busy = runs.some((r) => !TERMINAL.includes(r.status));

  return (
    <>
      <PageHeader
        back={
          <Link href="/app/research" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
            <ArrowLeft className="size-4" /> Deep research
          </Link>
        }
        title={p.title}
        description={p.question}
        actions={
          <Button variant="ghost" onClick={() => setDeleting(true)} aria-label="Delete project" disabled={busy}>
            <Trash2 className="size-4" />
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          {selectedId ? <RunPanel key={selectedId} runId={selectedId} /> : <Section><p className="text-sm text-muted">No runs yet. Start one to produce a cited report.</p></Section>}
        </div>

        <div className="space-y-6">
          <Section title="Start a run">
            {archived ? (
              <Notice tone="warn">This project is archived. Restore it from the projects list to run new research.</Notice>
            ) : (
              <div className="space-y-4">
                <Field label="Question" hint="Defaults to the project question.">
                  {(f) => <Textarea {...f} rows={3} value={question ?? p.question} onChange={(e) => setQuestion(e.target.value)} maxLength={2000} />}
                </Field>
                <Field label="Depth">
                  {(f) => (
                    <Select {...f} value={depth} onChange={(e) => setDepth(e.target.value as typeof depth)}>
                      <option value="quick">Quick: fewer steps</option>
                      <option value="standard">Standard</option>
                      <option value="thorough">Thorough: uses your plan&apos;s full step budget</option>
                    </Select>
                  )}
                </Field>
                <div>
                  <div className="mb-1.5 text-sm font-medium">Sources</div>
                  <FilterBar value={filters} onChange={setFilters} />
                </div>
                {estimate.data && (
                  <dl className="grid grid-cols-2 gap-2 rounded-lg bg-surface-2 px-3 py-2.5 text-[0.82rem]">
                    <div>
                      <dt className="text-muted">Research steps</dt>
                      <dd className="tabular-nums">up to {estimate.data.research_steps}</dd>
                    </div>
                    <div>
                      <dt className="text-muted">AI calls</dt>
                      <dd className="tabular-nums">about {estimate.data.model_calls}</dd>
                    </div>
                    <div className="col-span-2 text-faint">{estimate.data.explanation || "Estimates, not guarantees. Hard limits stop the run safely if reached."}</div>
                  </dl>
                )}
                <Button className="w-full" onClick={start} loading={starting} disabled={reportsLeft <= 0 || (concurrent ? concurrent.used + concurrent.reserved >= concurrent.limit : false)}>
                  <Play className="size-4" /> Start research
                </Button>
                {reportsLeft <= 0 && (
                  <p className="text-[0.82rem] text-warn">
                    You&apos;ve used every research report this period.{" "}
                    <Link href="/app/billing" className="underline">
                      See plans
                    </Link>
                  </p>
                )}
                {concurrent && concurrent.used + concurrent.reserved >= concurrent.limit && reportsLeft > 0 && (
                  <p className="text-[0.82rem] text-muted">
                    {concurrent.limit === 1 ? "A research run is already in progress." : `You already have ${concurrent.limit} runs in progress.`} Wait for it to finish or cancel it.
                  </p>
                )}
              </div>
            )}
          </Section>

          {runs.length > 0 && (
            <Section title="Runs">
              <ul className="-mx-2 -my-1 space-y-0.5">
                {runs.map((r) => (
                  <li key={r.id}>
                    <Link
                      href={`/app/research/${id}?run=${r.id}`}
                      scroll={false}
                      className={cn("flex items-center justify-between gap-2 rounded-md px-2 py-2 text-sm", r.id === selectedId ? "bg-action-soft" : "hover:bg-surface-2")}
                    >
                      <span className="min-w-0">
                        <span className="block truncate">{r.depth[0]!.toUpperCase() + r.depth.slice(1)} run</span>
                        <span className="text-[0.76rem] text-faint">{relativeTime(r.created_at)}</span>
                      </span>
                      <Badge tone={STATUS_TONE[r.status] || "neutral"}>{r.status}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this project?"
        description="All runs and reports in this project are deleted. Saved findings stay."
        confirmLabel="Delete project"
        danger
        onConfirm={async () => {
          await del(`/research/projects/${id}`);
          qc.invalidateQueries({ queryKey: ["research-projects"] });
          router.push("/app/research");
        }}
      />
    </>
  );
}

function RunPanel({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["research-run", runId],
    queryFn: () => get<ResearchRun>(`/research/runs/${runId}`),
    refetchInterval: (q) => (q.state.data && !TERMINAL.includes(q.state.data.status) ? 4000 : false),
  });
  const { events, mode } = useRunEvents(run.data);
  const [cancelling, setCancelling] = useState(false);

  if (run.isError) return <ErrorState error={run.error} />;
  if (!run.data) return <LoadingRows rows={5} />;
  const r = run.data;
  const finished = TERMINAL.includes(r.status);
  const calls = Number(r.budget.max_ai_calls) || 0;

  async function cancel() {
    setCancelling(true);
    try {
      await post(`/research/runs/${runId}/cancel`);
      toast("Cancelling. The run stops at the next safe checkpoint.");
      qc.invalidateQueries({ queryKey: ["research-run", runId] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't cancel.");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          {phaseLabel(r.phase)}
          <Badge tone={STATUS_TONE[r.status] || "neutral"}>{r.status}</Badge>
        </span>
      }
      description={r.question}
      actions={
        !finished ? (
          <Button size="sm" variant="secondary" onClick={cancel} loading={cancelling} disabled={r.cancel_requested}>
            <XCircle className="size-4" /> {r.cancel_requested ? "Cancelling" : "Cancel"}
          </Button>
        ) : r.report_id ? (
          <ButtonLink size="sm" href={`/app/reports/${r.report_id}`}>
            <FileText className="size-4" /> Open report
          </ButtonLink>
        ) : null
      }
    >
      {r.status === "incomplete" && (
        <Notice tone="warn" className="mb-4">
          This run stopped early because {STOP_REASON[r.stop_reason || ""] || r.stop_reason || "a limit was reached"}. The report is marked incomplete and only contains
          what the evidence supported.
        </Notice>
      )}
      {r.status === "failed" && r.error && (
        <Notice tone="danger" className="mb-4">
          {r.error.message}
        </Notice>
      )}

      <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_220px]">
        <div>
          <RunTimeline run={r} events={events} />
          {!finished && <p className="mt-1 text-[0.76rem] text-faint">{mode === "poll" ? "Updating every few seconds." : "Live updates."} Safe to leave this page.</p>}
        </div>
        <dl className="space-y-3 text-sm">
          <div>
            <dt className="text-muted">Passages gathered</dt>
            <dd className="text-lg font-semibold tabular-nums">{formatNumber(r.sources_found)}</dd>
          </div>
          <div>
            <dt className="mb-1 text-muted">AI calls used</dt>
            <dd>
              <span className="tabular-nums">
                {r.usage.calls} of {calls}
              </span>
              <div className="mt-1">
                <UsageBar used={r.usage.calls} limit={calls} label="AI calls used" />
              </div>
            </dd>
          </div>
          {r.conflicts > 0 && (
            <div>
              <dt className="text-muted">Conflicts found</dt>
              <dd className="tabular-nums">{r.conflicts}</dd>
            </div>
          )}
        </dl>
      </div>

      {r.plan?.subquestions?.length ? (
        <div className="mt-5 border-t border-rule pt-4">
          <h3 className="mb-2 text-sm font-medium">Sub-questions</h3>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-ink-2">
            {r.plan.subquestions.map((s) => (
              <li key={s.id}>{s.question}</li>
            ))}
          </ol>
        </div>
      ) : null}
      {r.gaps.length > 0 && (
        <div className="mt-4 border-t border-rule pt-4">
          <h3 className="mb-2 text-sm font-medium">Evidence gaps</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            {r.gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}

export default function ResearchProjectPage() {
  return (
    <Suspense fallback={<LoadingRows rows={6} />}>
      <ProjectView />
    </Suspense>
  );
}
