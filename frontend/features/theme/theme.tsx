"use client";

import { createContext, useCallback, useContext, useEffect, useSyncExternalStore, type ReactNode } from "react";

export type ThemePref = "system" | "light" | "dark";
const KEY = "ks-theme";
const EVENT = "ks-theme-change";

/** Runs before paint (next/script beforeInteractive) so there is no light/dark flash. */
export const themeBootScript = `(function(){try{var t=localStorage.getItem('${KEY}')||'system';var d=t==='dark'||(t==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d)}catch(e){}})()`;

function read(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function subscribe(cb: () => void) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  window.addEventListener("storage", cb);
  window.addEventListener(EVENT, cb);
  mq.addEventListener("change", cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener(EVENT, cb);
    mq.removeEventListener("change", cb);
  };
}

function apply(t: ThemePref) {
  const dark = t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

const Ctx = createContext<{ theme: ThemePref; setTheme: (t: ThemePref) => void }>({ theme: "system", setTheme: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  // localStorage is the source of truth; the server always renders "system".
  const theme = useSyncExternalStore(subscribe, read, () => "system" as ThemePref);

  useEffect(() => {
    apply(theme);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: ThemePref) => {
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* storage unavailable: still apply for this page view */
    }
    apply(t);
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return <Ctx.Provider value={{ theme, setTheme }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
