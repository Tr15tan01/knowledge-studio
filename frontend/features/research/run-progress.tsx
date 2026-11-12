"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, CircleAlert, CircleDashed, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { ResearchRun, RunEvent } from "@/types/api";

export const TERMINAL = ["completed", "incomplete", "failed", "cancelled"];

const PHASE_LABEL: Record<string, string> = {
  queued: "Waiting for a worker",
  planning: "Planning sub-questions",
  retrieving: "Gathering evidence",
  critiquing: "Checking coverage and conflicts",
  follow_up: "Following up on gaps",
  synthesizing: "Writing the report",
  validating: "Validating citations",
  completed: "Report ready",
  incomplete: "Stopped early",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const STOP_REASON: Record<string, string> = {
  ai_calls: "it reached the maximum number of AI calls for your plan",
  time: "it reached the time limit for one research run",
  retrieval_iterations: "it reached the retrieval limit for one run",
  no_evidence: "your library had no relevant evidence",
  synthesis_unavailable: "the AI provider couldn't write the final synthesis",
  compute_budget: "your compute budget for the period was used up",
  cancelled: "it was cancelled",
};

export function phaseLabel(p: string) {
  return PHASE_LABEL[p] || p.replace(/_/g, " ");
}

function describe(e: RunEvent): string | null {
  const d = e.data as Record<string, unknown>;
  switch (e.type) {
    case "phase":
      if (d.phase === "retrieving" && d.subquestion) return `Searching: ${d.subquestion}`;
      if (d.phase === "follow_up" && d.query) return `Follow-up search: ${d.query}`;
      if (d.phase === "synthesizing" && d.evidence !== undefined) return `Writing from ${d.evidence} passages`;
      return phaseLabel(String(d.phase));
    case "plan":
      return `Planned ${(d.subquestions as unknown[])?.length ?? 0} sub-questions`;
    case "sources":
      return `Found ${d.count} passages (${d.total_sources} unique so far)`;
    case "critique":
      return `Coverage ${d.coverage}; ${(d.gaps as unknown[])?.length ?? 0} gaps, ${d.conflicts} conflicts`;
    case "completed":
      return d.status === "completed" ? "Report saved" : "Partial report saved";
    case "warning":
      return String(d.message || "Warning");
    default:
      return null;
  }
}

/**
 * Live progress from persisted job events. Uses SSE (which resumes from Last-Event-ID after a
 * reconnect) and falls back to polling if the stream keeps failing. Nothing here is simulated:
 * every line is an event the worker actually recorded.
 *
 * Mount one instance per run (key the parent by run id) so state never leaks between runs.
 */
export function useRunEvents(run: ResearchRun | undefined) {
  const runId = run?.id;
  const finished = run ? TERMINAL.includes(run.status) : false;
  const qc = useQueryClient();
  const initial = useQuery({
    queryKey: ["run-events", runId],
    queryFn: () => get<{ events: RunEvent[] }>(`/research/runs/${runId}/events`),
    enabled: !!runId,
    staleTime: Infinity,
  });
  const [live, setLive] = useState<RunEvent[]>([]);
  const [mode, setMode] = useState<"stream" | "poll">("stream");
  const lastSeq = useRef(0);

  const base = initial.data?.events ?? [];
  const baseLast = base.at(-1)?.seq ?? 0;
  const events = [...base, ...live.filter((e) => e.seq > baseLast)];

  useEffect(() => {
    if (!runId || finished || !initial.data) return;
    lastSeq.current = Math.max(lastSeq.current, initial.data.events.at(-1)?.seq ?? 0);
    let closed = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    const push = (list: RunEvent[]) => {
      const fresh = list.filter((e) => e.seq > lastSeq.current);
      if (!fresh.length) return;
      lastSeq.current = fresh.at(-1)!.seq;
      setLive((cur) => [...cur, ...fresh]);
      qc.invalidateQueries({ queryKey: ["research-run", runId] });
    };
    const poll = async () => {
      if (closed) return;
      try {
        const r = await get<{ events: RunEvent[]; status: string }>(`/research/runs/${runId}/events`, { after: lastSeq.current });
        push(r.events);
        if (TERMINAL.includes(r.status)) {
          qc.invalidateQueries({ queryKey: ["research-run", runId] });
          qc.invalidateQueries({ queryKey: ["usage"] });
          return;
        }
      } catch {
        /* keep polling */
      }
      pollTimer = setTimeout(poll, 2500);
    };

    let failures = 0;
    const es = new EventSource(`/api/v1/research/runs/${runId}/stream?after=${lastSeq.current}`);
    const onEvent = (msg: MessageEvent) => {
      try {
        push([JSON.parse(msg.data) as RunEvent]);
        failures = 0;
      } catch {
        /* ignore malformed frames */
      }
    };
    for (const t of ["phase", "plan", "sources", "critique", "completed", "warning", "message"]) es.addEventListener(t, onEvent as EventListener);
    es.addEventListener("end", () => {
      es.close();
      qc.invalidateQueries({ queryKey: ["research-run", runId] });
      qc.invalidateQueries({ queryKey: ["usage"] });
    });
    es.onerror = () => {
      failures += 1;
      if (failures >= 3 && !closed) {
        es.close();
        setMode("poll");
        void poll();
      }
    };
    return () => {
      closed = true;
      es.close();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [runId, finished, initial.data, qc]);

  return { events, mode, loading: initial.isLoading };
}

export function RunTimeline({ run, events }: { run: ResearchRun; events: RunEvent[] }) {
  const finished = TERMINAL.includes(run.status);
  const lines = events.map((e) => ({ e, text: describe(e) })).filter((x) => x.text);
  return (
    <ol className="relative space-y-0">
      {lines.map(({ e, text }, i) => {
        const last = i === lines.length - 1;
        const running = last && !finished;
        return (
          <li key={e.seq} className="relative flex gap-3 pb-3.5">
            {!last && <span className="absolute left-[9px] top-6 h-[calc(100%-14px)] w-px bg-rule" aria-hidden />}
            <span className="relative z-10 mt-0.5 shrink-0 bg-surface">
              {running ? (
                <Loader2 className="size-[19px] animate-spin text-action" />
              ) : e.type === "completed" ? (
                <CheckCircle2 className="size-[19px] text-ok" />
              ) : e.type === "warning" ? (
                <CircleAlert className="size-[19px] text-warn" />
              ) : (
                <Circle className="size-[19px] text-rule-strong" />
              )}
            </span>
            <div className="min-w-0">
              <p className={cn("text-sm", running ? "font-medium text-ink" : "text-ink-2")}>{text}</p>
              <p className="text-[0.74rem] tabular-nums text-faint">{new Date(e.at).toLocaleTimeString()}</p>
            </div>
          </li>
        );
      })}
      {!lines.length && (
        <li className="flex items-center gap-3 text-sm text-muted">
          <CircleDashed className="size-[19px]" /> {phaseLabel(run.phase)}
        </li>
      )}
    </ol>
  );
}
