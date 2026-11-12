/**
 * Display formatting. Storage always uses binary units (1 MiB = 1,048,576 bytes),
 * matching the backend's source of truth (integer bytes).
 */

export const KIB = 1024;
export const MIB = 1024 * 1024;
export const GIB = 1024 * 1024 * 1024;

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB"] as const;

export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "—";
  const n = Math.max(0, Math.trunc(bytes));
  if (n < KIB) return `${n} B`;
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  const rounded = v >= 100 || Number.isInteger(v) ? Math.round(v).toString() : v.toFixed(digits).replace(/\.0+$/, "");
  return `${rounded} ${UNITS[i]}`;
}

/** Parse "25 MiB" / "5 GiB" / "1048576" into integer bytes (binary units only). */
export function parseBytes(input: string): number | null {
  const m = input.trim().match(/^(\d+(?:\.\d+)?)\s*(b|kib|mib|gib|tib)?$/i);
  if (!m) return null;
  const n = Number(m[1]);
  const unit = (m[2] || "b").toLowerCase();
  const mult = { b: 1, kib: KIB, mib: MIB, gib: GIB, tib: GIB * 1024 }[unit] ?? 1;
  return Math.round(n * mult);
}

export function percent(used: number, limit: number): number {
  if (!limit || limit <= 0) return used > 0 ? 100 : 0;
  return Math.min(100, Math.max(0, (used / limit) * 100));
}

export function formatMoneyMinor(minor: number | null | undefined, currency = "USD"): string {
  if (minor === null || minor === undefined) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: minor % 100 === 0 ? 0 : 2 }).format(minor / 100);
}

export function formatUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: Math.max(digits, 4) }).format(v);
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

export function formatDate(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat(undefined, opts).format(d);
}

export function formatDateTime(iso: string | null | undefined): string {
  return formatDate(iso, { dateStyle: "medium", timeStyle: "short" });
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.round((t - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const abs = Math.abs(s);
  if (abs < 60) return rtf.format(s, "second");
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(s / 86400), "day");
  return formatDate(iso);
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
