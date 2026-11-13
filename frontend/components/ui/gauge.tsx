import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

const SWEEP = 0.75; // the arc covers 270 degrees, open at the bottom

/**
 * Radial progress gauge. `value` is a fraction from 0 to 1. `finishing` pulses the full arc
 * for the moment after the last byte is sent, while the server is still responding.
 */
export function Gauge({
  value,
  size = 44,
  stroke = 4,
  label,
  finishing,
  className,
  children,
}: {
  value: number;
  size?: number;
  stroke?: number;
  label: string;
  finishing?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  const v = Math.max(0, Math.min(1, value));
  const pct = Math.round(v * 100);
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const arc = c * SWEEP;
  return (
    <div
      className={cn("relative grid shrink-0 place-items-center", finishing && "gauge-finishing", className)}
      style={{ width: size, height: size }}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="absolute inset-0 rotate-[135deg]" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${arc} ${c}`} />
        <circle
          className="gauge-arc"
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--action)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${arc} ${c}`}
          strokeDashoffset={arc * (1 - v)}
        />
      </svg>
      <span className="relative tabular-nums">{children ?? `${pct}%`}</span>
    </div>
  );
}
