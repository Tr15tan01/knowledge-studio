"use client";

import { useEffect, useRef, useState } from "react";

/** Shown while the library is searched and read, before the first words of an answer arrive. */
export function Thinking({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-1 text-[0.95rem]" role="status">
      <span className="thinking-orb" aria-hidden />
      <span className="thinking-label font-medium">{label}</span>
    </div>
  );
}

const MIN_CHARS_PER_SECOND = 70;

function prefersReducedMotion() {
  return typeof window !== "undefined" && (window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.classList.contains("reduce-motion"));
}

/**
 * Reveals `target` a few characters at a time, so text that arrives from the model in
 * uneven bursts reads as steady writing. The pace follows the backlog: it never falls far
 * behind, and with `finishing` it catches up within a moment. `done` is true once
 * everything received so far is on screen.
 */
export function useTypewriter(target: string, finishing: boolean): { text: string; done: boolean } {
  const [count, setCount] = useState(0);
  const [instant] = useState(prefersReducedMotion);
  const latest = useRef({ length: target.length, finishing });
  useEffect(() => {
    latest.current = { length: target.length, finishing };
  }, [target, finishing]);

  useEffect(() => {
    if (instant) return;
    let frame = 0;
    let shown = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const { length, finishing: hurry } = latest.current;
      if (shown > length) shown = length; // a new, shorter text replaced the old one
      const backlog = length - shown;
      if (backlog > 0) {
        const rate = Math.max(MIN_CHARS_PER_SECOND, backlog * (hurry ? 7 : 2.2));
        shown = Math.min(length, shown + rate * dt);
        setCount(Math.floor(shown));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [instant]);

  const n = instant ? target.length : Math.min(count, target.length);
  return { text: target.slice(0, n), done: n >= target.length };
}
