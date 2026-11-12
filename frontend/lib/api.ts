/**
 * Typed client for the Knowledge Studio API.
 *
 * - Same-origin requests only (`/api/v1/*` is proxied by Next.js), so the HttpOnly session
 *   cookie is sent automatically and never touched by JavaScript.
 * - Every state-changing request echoes the CSRF token from the readable `ks_csrf` cookie.
 * - Errors are normalised into `ApiError` with the backend's stable `code`.
 */

export const API_BASE = "/api/v1";
const CSRF_COOKIE = "ks_csrf";

export type ApiErrorBody = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string | null;
  retryable?: boolean;
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  requestId?: string | null;
  retryable: boolean;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || "Request failed.");
    this.status = status;
    this.code = body.code || "http_error";
    this.details = body.details || {};
    this.requestId = body.request_id;
    this.retryable = Boolean(body.retryable);
  }

  get isQuota() {
    return this.status === 402 || this.code === "quota_exceeded" || this.code.startsWith("quota_");
  }
  get needsReauth() {
    return this.code === "reauth_required";
  }
}

export function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return m ? decodeURIComponent(m.slice(name.length + 1)) : "";
}

export function csrfHeader(): Record<string, string> {
  const t = readCookie(CSRF_COOKIE);
  return t ? { "x-csrf-token": t } : {};
}

type Opts = Omit<RequestInit, "body"> & {
  json?: unknown;
  body?: BodyInit | null;
  query?: Record<string, string | number | boolean | null | undefined | string[]>;
};

export function buildUrl(path: string, query?: Opts["query"]): string {
  const url = path.startsWith("http") || path.startsWith("/api") ? path : `${API_BASE}${path}`;
  if (!query) return url;
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `${url}?${qs}` : url;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: ApiErrorBody = { code: "http_error", message: `Request failed (${res.status}).` };
  try {
    const data = await res.json();
    if (data?.error) body = data.error;
  } catch {
    if (res.status === 502 || res.status === 504) {
      body = { code: "api_unreachable", message: "The Knowledge Studio API isn't reachable. Check that it's running.", retryable: true };
    }
  }
  return new ApiError(res.status, body);
}

export async function apiFetch(path: string, opts: Opts = {}): Promise<Response> {
  const { json, query, headers, ...rest } = opts;
  const method = (rest.method || (json !== undefined ? "POST" : "GET")).toUpperCase();
  const h: Record<string, string> = { accept: "application/json", ...(headers as Record<string, string>) };
  if (method !== "GET" && method !== "HEAD") Object.assign(h, csrfHeader());
  let body = rest.body;
  if (json !== undefined) {
    h["content-type"] = "application/json";
    body = JSON.stringify(json);
  }
  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), { ...rest, method, headers: h, body, credentials: "same-origin", cache: "no-store" });
  } catch {
    throw new ApiError(0, { code: "network_error", message: "Network error. Check your connection and try again.", retryable: true });
  }
  if (!res.ok) throw await parseError(res);
  return res;
}

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const res = await apiFetch(path, opts);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const get = <T>(path: string, query?: Opts["query"]) => api<T>(path, { query });
export const post = <T>(path: string, json?: unknown, opts: Opts = {}) => api<T>(path, { ...opts, method: "POST", json: json ?? {} });
export const patch = <T>(path: string, json: unknown) => api<T>(path, { method: "PATCH", json });
export const put = <T>(path: string, json: unknown) => api<T>(path, { method: "PUT", json });
export const del = <T>(path: string, json?: unknown) => api<T>(path, { method: "DELETE", json });

/** Parse a `text/event-stream` response body into typed events. */
export async function* readSSE<T = Record<string, unknown>>(res: Response, signal?: AbortSignal): AsyncGenerator<T & { type: string }> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      if (signal?.aborted) break;
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (!data.length) continue;
        try {
          const parsed = JSON.parse(data.join("\n"));
          yield { type: event, ...parsed };
        } catch {
          /* ignore malformed frames */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Upload a file as a streamed raw body with a hard client-side size check (server enforces it too). */
export function uploadFile(
  file: File,
  params: { kind: "auto" | "book" | "document"; title?: string; collectionId?: string; idempotencyKey: string },
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<{ file: { id: string; title: string }; job_id: string | null; replayed: boolean }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", buildUrl("/files/upload", { kind: params.kind, title: params.title, collection_id: params.collectionId }));
    xhr.withCredentials = true;
    xhr.setRequestHeader("x-filename", encodeURIComponent(file.name));
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.setRequestHeader("idempotency-key", params.idempotencyKey);
    xhr.setRequestHeader("accept", "application/json");
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) xhr.setRequestHeader("x-csrf-token", csrf);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onerror = () => reject(new ApiError(0, { code: "network_error", message: "Upload interrupted. Check your connection and retry." }));
    xhr.onabort = () => reject(new ApiError(0, { code: "aborted", message: "Upload cancelled." }));
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as never);
      else {
        const err = (data as { error?: ApiErrorBody } | null)?.error;
        reject(new ApiError(xhr.status, err || { code: "upload_failed", message: `Upload failed (${xhr.status}).` }));
      }
    };
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(file);
  });
}

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
