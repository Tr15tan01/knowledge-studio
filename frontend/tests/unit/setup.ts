import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  document.cookie.split(";").forEach((c) => {
    document.cookie = `${c.split("=")[0]!.trim()}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  });
  vi.restoreAllMocks();
});

// jsdom lacks these browser APIs used by Radix/React Flow.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    value: (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {}, onchange: null, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }),
  });
}
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver ||= RO as unknown as typeof ResizeObserver;
