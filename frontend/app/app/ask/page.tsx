"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowUp, BookmarkPlus, Copy, Download, History, MessageSquarePlus, MoreHorizontal, Pencil, Square, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { toast } from "sonner";
import { Button, IconButton } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/field";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/menu";
import { Badge, ErrorState, LoadingRows, Notice } from "@/components/ui/misc";
import { Spinner } from "@/components/ui/spinner";
import { EMPTY_FILTERS, FilterBar } from "@/features/ask/filters";
import { qk, useUsage } from "@/features/auth/session";
import { CitationProvider, CitedText, SourceList, citeRefs } from "@/features/citations/citations";
import { useDebounced } from "@/hooks/use-url-state";
import { ApiError, apiFetch, del, get, newIdempotencyKey, patch, post, readSSE } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import type { Conversation, ConversationSummary, Estimate, Filters, Hit, Message, Paged } from "@/types/api";

type Pending = {
  question: string;
  text: string;
  sources: Hit[];
  degraded: string[];
  estimate: Estimate | null;
  error: { code: string; message: string; retryable: boolean; fallback: { label: string; items: Hit[] } | null } | null;
};

const DEGRADED: Record<string, string> = {
  vector_unavailable: "Semantic search was unavailable, so this answer used keyword matches only.",
  keyword_unavailable: "Keyword search was unavailable, so this answer used semantic matches only.",
};

function AskView() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const convId = params.get("c");
  const usage = useUsage();
  const [filters, setFilters] = useState<Filters>(() => (params.get("file") ? { ...EMPTY_FILTERS, file_ids: [params.get("file")!] } : EMPTY_FILTERS));
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  const conv = useQuery({ queryKey: ["conversation", convId], queryFn: () => get<Conversation>(`/conversations/${convId}`), enabled: !!convId });
  // When a different conversation loads, adopt its saved scope (React's "adjust state while rendering" pattern).
  const [scopedConv, setScopedConv] = useState<string | null>(null);
  if (conv.data && conv.data.id !== scopedConv) {
    setScopedConv(conv.data.id);
    if (conv.data.filters) setFilters({ ...EMPTY_FILTERS, ...conv.data.filters });
  }

  // Pre-flight estimate so expensive questions are explained before they run.
  const dq = useDebounced(question.trim(), 700);
  const estimate = useQuery({
    queryKey: ["ask-estimate", dq, filters.file_ids.length + filters.source_ids.length],
    queryFn: () => post<Estimate>("/ask/estimate", { question: dq, filters }),
    enabled: dq.length >= 8 && !streaming,
    staleTime: 60_000,
  });

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [pending?.text, conv.data?.messages.length]);

  const q = usage.data?.usage.questions;
  const questionsLeft = q ? q.limit - q.used - q.reserved : null;

  async function ask(text: string) {
    const body = text.trim();
    if (!body || streaming) return;
    setQuestion("");
    setStreaming(true);
    setPending({ question: body, text: "", sources: [], degraded: [], estimate: null, error: null });
    const ctrl = new AbortController();
    abort.current = ctrl;
    let newConv: string | null = null;
    try {
      const res = await apiFetch("/ask", {
        method: "POST",
        json: { question: body, conversation_id: convId, filters },
        headers: { accept: "text/event-stream", "idempotency-key": newIdempotencyKey() },
        signal: ctrl.signal,
      });
      for await (const ev of readSSE<Record<string, unknown>>(res, ctrl.signal)) {
        if (ev.type === "conversation") newConv = ev.id as string;
        else if (ev.type === "estimate") setPending((p) => p && { ...p, estimate: ev as unknown as Estimate });
        else if (ev.type === "sources") setPending((p) => p && { ...p, sources: ev.items as Hit[], degraded: (ev.degraded as string[]) || [] });
        else if (ev.type === "delta") setPending((p) => p && { ...p, text: p.text + (ev.text as string) });
        else if (ev.type === "error") setPending((p) => p && { ...p, error: ev as unknown as Pending["error"] });
        else if (ev.type === "done") setPending(null);
      }
    } catch (e) {
      if (ctrl.signal.aborted) {
        setPending((p) => p && { ...p, error: { code: "stopped", message: "You stopped this answer.", retryable: true, fallback: null } });
      } else {
        const err = e instanceof ApiError ? e : null;
        setPending((p) => p && { ...p, error: { code: err?.code || "error", message: err?.message || "The answer couldn't be generated.", retryable: true, fallback: null } });
      }
    } finally {
      setStreaming(false);
      abort.current = null;
      qc.invalidateQueries({ queryKey: qk.usage });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      const target = newConv || convId;
      if (target) {
        await qc.invalidateQueries({ queryKey: ["conversation", target] });
        if (target !== convId) router.replace(`/app/ask?c=${target}`, { scroll: false });
      }
      setPending((p) => (p?.error ? p : null));
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    ask(question);
  }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      ask(question);
    }
  }
  function newChat() {
    setPending(null);
    setFilters(EMPTY_FILTERS);
    router.push("/app/ask");
    textarea.current?.focus();
  }

  const messages = conv.data?.messages ?? [];
  const empty = !convId && !pending;

  return (
    <CitationProvider>
      <div className="-mx-4 -my-6 flex h-[calc(100dvh-3.5rem)] sm:-mx-8 lg:-mx-10 lg:-my-9 lg:h-dvh">
        <aside className="hidden w-64 shrink-0 flex-col border-r border-rule bg-surface xl:flex">
          <ConversationList activeId={convId} onNew={newChat} />
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between gap-3 border-b border-rule px-4 py-3 sm:px-8">
            <div className="min-w-0">
              <h1 className="truncate text-lg font-semibold">{conv.data?.title || "Ask your library"}</h1>
            </div>
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" className="xl:hidden" onClick={() => setHistoryOpen(true)}>
                <History className="size-4" /> History
              </Button>
              {convId && (
                <a href={`/api/v1/conversations/${convId}/export.md`} className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-control)] px-3 text-sm text-ink-2 hover:bg-surface-2">
                  <Download className="size-4" /> Export
                </a>
              )}
              <Button size="sm" variant="secondary" onClick={newChat}>
                <MessageSquarePlus className="size-4" /> New
              </Button>
            </div>
          </header>

          <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
            <div className="mx-auto max-w-3xl space-y-8">
              {empty && <Intro onPick={(t) => ask(t)} />}
              {conv.isLoading && <LoadingRows rows={4} />}
              {conv.isError && <ErrorState error={conv.error} />}
              {messages.map((m) => (m.role === "user" ? <UserBubble key={m.id} text={m.content} /> : <AssistantMessage key={m.id} m={m} onRetry={(t) => ask(t)} prevQuestion={prevUser(messages, m)} />))}
              {pending && (
                <>
                  <UserBubble text={pending.question} />
                  <PendingAnswer p={pending} streaming={streaming} onRetry={() => ask(pending.question)} />
                </>
              )}
              <div ref={bottom} />
            </div>
          </div>

          <div className="border-t border-rule bg-paper px-4 pb-4 pt-3 sm:px-8">
            <form onSubmit={onSubmit} className="mx-auto max-w-3xl">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <FilterBar value={filters} onChange={setFilters} disabled={streaming} />
                {questionsLeft !== null && (
                  <span className={cn("text-[0.8rem] tabular-nums", questionsLeft <= 3 ? "text-warn" : "text-faint")}>
                    {Math.max(0, questionsLeft)} of {q!.limit} questions left
                  </span>
                )}
              </div>
              {estimate.data?.expensive && estimate.data.explanation && !streaming && (
                <Notice tone="highlight" className="mb-2">
                  {estimate.data.explanation}
                  {estimate.data.question_credits > 1 && ` It counts as ${estimate.data.question_credits} questions.`}
                </Notice>
              )}
              <div className="flex items-end gap-2 rounded-[14px] border border-rule-strong bg-surface p-2 focus-within:border-action focus-within:ring-2 focus-within:ring-action/20">
                <textarea
                  ref={textarea}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={onKey}
                  rows={1}
                  maxLength={4000}
                  placeholder={convId ? "Ask a follow-up" : "Ask a question about your books and documents"}
                  aria-label="Your question"
                  className="max-h-48 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-[0.98rem] outline-none placeholder:text-faint [field-sizing:content]"
                />
                {streaming ? (
                  <Button type="button" variant="secondary" onClick={() => abort.current?.abort()} aria-label="Stop generating">
                    <Square className="size-3.5 fill-current" /> Stop
                  </Button>
                ) : (
                  <Button type="submit" disabled={question.trim().length < 2 || questionsLeft === 0} aria-label="Ask">
                    <ArrowUp className="size-4" />
                  </Button>
                )}
              </div>
              {questionsLeft === 0 && (
                <p className="mt-2 text-[0.82rem] text-warn">
                  You&apos;ve used every question this period. Library search still works.{" "}
                  <Link href="/app/billing" className="underline">
                    See plans
                  </Link>
                </p>
              )}
            </form>
          </div>
        </div>
      </div>
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen} title="Conversations" side="right">
        <ConversationList activeId={convId} onNew={() => (setHistoryOpen(false), newChat())} onNavigate={() => setHistoryOpen(false)} />
      </Dialog>
    </CitationProvider>
  );
}

function prevUser(ms: Message[], m: Message): string | undefined {
  const i = ms.indexOf(m);
  for (let j = i - 1; j >= 0; j--) if (ms[j]!.role === "user") return ms[j]!.content;
  return undefined;
}

function Intro({ onPick }: { onPick: (q: string) => void }) {
  const examples = ["Summarize the main argument of my most recent book", "Where do my sources disagree about this topic?", "Which chapters discuss methodology?"];
  return (
    <div className="pt-[8vh]">
      <h2 className="text-[1.7rem] font-semibold tracking-[-0.02em]">What do you want to find out?</h2>
      <p className="mt-2 max-w-xl text-muted">
        Answers come only from your library. Every claim carries a numbered citation; open it to read the <span className="mark">exact passage</span> it came from.
      </p>
      <div className="mt-6 flex flex-col items-start gap-2">
        {examples.map((e) => (
          <button key={e} onClick={() => onPick(e)} className="rounded-full border border-rule bg-surface px-3.5 py-1.5 text-left text-sm text-ink-2 hover:border-rule-strong hover:text-ink">
            {e}
          </button>
        ))}
      </div>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-[16px] rounded-br-[5px] bg-ink px-4 py-2.5 text-[0.97rem] text-paper">{text}</div>
    </div>
  );
}

function AssistantMessage({ m, onRetry, prevQuestion }: { m: Message; onRetry: (q: string) => void; prevQuestion?: string }) {
  const cites = citeRefs(m.citations);
  const [saving, setSaving] = useState(false);
  if (m.status === "failed" || m.error) {
    return (
      <div>
        <ErrorState error={new ApiError(0, { code: m.error?.code || "failed", message: m.error?.message || "This answer failed." })} onRetry={prevQuestion ? () => onRetry(prevQuestion) : undefined} />
        <p className="mt-1.5 text-[0.8rem] text-faint">{m.operation?.charged ? "This attempt counted toward your questions." : "This attempt didn't count toward your questions."}</p>
      </div>
    );
  }
  async function saveFinding() {
    setSaving(true);
    try {
      await post("/findings", { text: m.content.replace(/\s*\[(?:S\d+(?:\s*,\s*S\d+)*)\]/g, ""), message_id: m.id, chunk_ids: m.citations.slice(0, 20).map((c) => c.chunk_id) });
      toast("Saved to findings.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <article className="group">
      {m.insufficient_evidence && (
        <Notice tone="warn" className="mb-3" icon={<AlertTriangle className="size-4" />}>
          Your library doesn&apos;t contain enough evidence to answer this confidently.
        </Notice>
      )}
      <CitedText text={m.content} cites={cites} />
      {m.retrieval?.degraded?.map((d) => (
        <Notice key={d} tone="warn" className="mt-3">
          {DEGRADED[d] || d}
        </Notice>
      ))}
      <SourceList cites={cites} />
      <div className="mt-3 flex items-center gap-1 text-muted">
        <IconButton label="Copy answer" onClick={() => navigator.clipboard.writeText(m.content).then(() => toast("Copied."))}>
          <Copy className="size-4" />
        </IconButton>
        {m.citations.length > 0 && (
          <IconButton label="Save to findings" onClick={saveFinding} disabled={saving}>
            <BookmarkPlus className="size-4" />
          </IconButton>
        )}
        {m.operation?.model && <span className="ml-2 text-[0.76rem] text-faint">Generated by {m.operation.model}</span>}
      </div>
    </article>
  );
}

function PendingAnswer({ p, streaming, onRetry }: { p: Pending; streaming: boolean; onRetry: () => void }) {
  // While streaming, markers aren't validated yet: they are hidden until the final message arrives.
  const shown = p.text.replace(/\s*\[(?:S\d+(?:\s*,\s*S\d+)*)\]/g, "");
  return (
    <article aria-live="polite" aria-busy={streaming}>
      {p.estimate?.expensive && p.estimate.explanation && (
        <Notice tone="highlight" className="mb-3">
          {p.estimate.explanation}
        </Notice>
      )}
      {streaming && !p.text && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner className="size-4" />
          {p.sources.length ? `Reading ${p.sources.length} passages` : "Searching your library"}
        </div>
      )}
      {p.text && <CitedText text={shown} cites={[]} />}
      {p.error && (
        <div className="mt-3 space-y-3">
          <ErrorState error={new ApiError(0, { code: p.error.code, message: p.error.message })} onRetry={p.error.retryable ? onRetry : undefined} />
          {p.error.code.startsWith("quota") && (
            <Link href="/app/billing" className="text-sm text-action underline">
              See usage and plans
            </Link>
          )}
          {p.error.fallback?.items.length ? (
            <div className="rounded-[var(--radius-panel)] border border-rule bg-surface p-4">
              <div className="mb-2 flex items-center gap-2">
                <Badge tone="warn">Not an AI answer</Badge>
                <span className="text-sm font-medium">{p.error.fallback.label}</span>
              </div>
              <SourceList
                title="Matching passages"
                cites={p.error.fallback.items.map((h, i) => ({ key: `K${i}`, chunk_id: h.chunk_id, source_title: h.source_title, citation_label: h.citation_label, section_path: h.section_path }))}
              />
            </div>
          ) : null}
        </div>
      )}
    </article>
  );
}

function ConversationList({ activeId, onNew, onNavigate }: { activeId: string | null; onNew: () => void; onNavigate?: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const list = useQuery({ queryKey: ["conversations"], queryFn: () => get<Paged<ConversationSummary>>("/conversations", { page_size: 50 }) });
  const [renaming, setRenaming] = useState<ConversationSummary | null>(null);
  const [title, setTitle] = useState("");
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 py-3">
        <span className="text-sm font-medium">Conversations</span>
        <IconButton label="New conversation" onClick={onNew}>
          <MessageSquarePlus className="size-4" />
        </IconButton>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {list.isLoading && <LoadingRows rows={5} />}
        {list.data?.items.length === 0 && <p className="px-2 text-sm text-muted">Your questions will appear here.</p>}
        {list.data?.items.map((c) => (
          <div key={c.id} className={cn("group flex items-center rounded-lg", activeId === c.id ? "bg-action-soft" : "hover:bg-surface-2")}>
            <Link href={`/app/ask?c=${c.id}`} onClick={onNavigate} className="min-w-0 flex-1 px-2.5 py-2">
              <span className={cn("block truncate text-sm", activeId === c.id && "font-medium text-action")}>{c.title}</span>
              <span className="text-[0.76rem] text-faint">{relativeTime(c.updated_at)}</span>
            </Link>
            <Menu
              trigger={
                <button className="mr-1 rounded p-1 text-faint opacity-0 hover:text-ink group-hover:opacity-100 focus:opacity-100" aria-label={`Options for ${c.title}`}>
                  <MoreHorizontal className="size-4" />
                </button>
              }
            >
              <MenuItem onSelect={() => (setTitle(c.title), setRenaming(c))}>
                <Pencil className="size-4" /> Rename
              </MenuItem>
              <MenuItem onSelect={() => window.open(`/api/v1/conversations/${c.id}/export.md`, "_self")}>
                <Download className="size-4" /> Export Markdown
              </MenuItem>
              <MenuSeparator />
              <MenuItem danger onSelect={() => setDeleting(c)}>
                <Trash2 className="size-4" /> Delete
              </MenuItem>
            </Menu>
          </div>
        ))}
      </div>
      <Dialog
        open={!!renaming}
        onOpenChange={(v) => !v && setRenaming(null)}
        title="Rename conversation"
        size="sm"
        footer={
          <Button
            onClick={async () => {
              await patch(`/conversations/${renaming!.id}`, { title });
              qc.invalidateQueries({ queryKey: ["conversations"] });
              qc.invalidateQueries({ queryKey: ["conversation", renaming!.id] });
              setRenaming(null);
            }}
            disabled={!title.trim()}
          >
            Save name
          </Button>
        }
      >
        <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} aria-label="Conversation name" autoFocus />
      </Dialog>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(v) => !v && setDeleting(null)}
        title="Delete this conversation?"
        description="Its questions and answers are removed. Saved findings stay."
        confirmLabel="Delete conversation"
        danger
        onConfirm={async () => {
          await del(`/conversations/${deleting!.id}`);
          qc.invalidateQueries({ queryKey: ["conversations"] });
          if (deleting!.id === activeId) router.push("/app/ask");
        }}
      />
    </div>
  );
}

export default function AskPage() {
  return (
    <Suspense fallback={<LoadingRows rows={4} />}>
      <AskView />
    </Suspense>
  );
}
