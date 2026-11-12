import { describe, expect, it, vi } from "vitest";
import { ApiError, api, buildUrl, readSSE } from "@/lib/api";

function mockFetch(status: number, body: unknown, headers: Record<string, string> = { "content-type": "application/json" }) {
  const fn = vi.fn(async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("api client", () => {
  it("sends the CSRF token on state-changing requests only", async () => {
    document.cookie = "ks_csrf=tok123; path=/";
    const fetch = mockFetch(200, { ok: true });
    await api("/things", { method: "POST", json: { a: 1 } });
    await api("/things");
    const [, postInit] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    const [, getInit] = fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect((postInit.headers as Record<string, string>)["x-csrf-token"]).toBe("tok123");
    expect((getInit.headers as Record<string, string>)["x-csrf-token"]).toBeUndefined();
    expect(postInit.credentials).toBe("same-origin");
  });

  it("normalises backend errors into ApiError with code and request id", async () => {
    mockFetch(402, { error: { code: "quota_exceeded", message: "You've used every question.", request_id: "r1", details: { metric: "questions" } } });
    const err = (await api("/ask", { method: "POST", json: {} }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("quota_exceeded");
    expect(err.isQuota).toBe(true);
    expect(err.requestId).toBe("r1");
    expect(err.message).toBe("You've used every question.");
  });

  it("recognises reauthentication requests", async () => {
    mockFetch(403, { error: { code: "reauth_required", message: "Confirm your password." } });
    const err = (await api("/admin/plans", { method: "POST", json: {} }).catch((e) => e)) as ApiError;
    expect(err.needsReauth).toBe(true);
  });

  it("builds query strings and skips empty values", () => {
    expect(buildUrl("/files", { page: 2, q: "", status: undefined, type: ["a", "b"] })).toBe("/api/v1/files?page=2&type=a&type=b");
  });
});

describe("readSSE", () => {
  it("parses events across chunk boundaries", async () => {
    const enc = new TextEncoder();
    const chunks = ['event: delta\ndata: {"text":"Hel', 'lo"}\n\nevent: done\ndata: {"message":{"id":"m1"}}\n\n'];
    const stream = new ReadableStream({
      start(c) {
        chunks.forEach((x) => c.enqueue(enc.encode(x)));
        c.close();
      },
    });
    const out = [];
    for await (const ev of readSSE(new Response(stream))) out.push(ev);
    expect(out).toEqual([
      { type: "delta", text: "Hello" },
      { type: "done", message: { id: "m1" } },
    ]);
  });
});
