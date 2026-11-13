"use client";

import { createContext, useCallback, useContext, useEffect, useSyncExternalStore, type ReactNode } from "react";

/** Colour themes; the tokens for each live in app/globals.css under `[data-theme="<id>"]`. */
export const THEMES = [
  { id: "blue", label: "Blue", dark: false },
  { id: "white", label: "Light", dark: false },
  { id: "green", label: "Green", dark: false },
  { id: "sepia", label: "Sepia", dark: false },
  { id: "violet", label: "Violet", dark: false },
  { id: "midnight", label: "Midnight", dark: true },
  { id: "black", label: "Black", dark: true },
  { id: "forest", label: "Forest", dark: true },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];
export type ThemePref = "system" | ThemeId;

const KEY = "ks-theme";
const LAST_LIGHT = "ks-theme-light";
const LAST_DARK = "ks-theme-dark";
const EVENT = "ks-theme-change";
const DARK_IDS = THEMES.filter((t) => t.dark).map((t) => t.id);
// Before named themes existed the choices were light and dark.
const LEGACY: Record<string, ThemeId> = { light: "blue", dark: "midnight" };

/** Runs before paint (next/script beforeInteractive) so the saved theme never flashes in. */
export const themeBootScript = `(function(){try{var t=localStorage.getItem('${KEY}')||'system';t=${JSON.stringify(LEGACY)}[t]||t;if(t==='system')t=matchMedia('(prefers-color-scheme: dark)').matches?'midnight':'blue';var e=document.documentElement;e.classList.toggle('dark',${JSON.stringify(DARK_IDS)}.indexOf(t)>=0);e.setAttribute('data-theme',t)}catch(e){}})()`;

const isThemeId = (v: string | null): v is ThemeId => THEMES.some((t) => t.id === v);

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function read(): ThemePref {
  const v = stored(KEY);
  const id = (v && LEGACY[v]) || v;
  return isThemeId(id) ? id : "system";
}

const prefersDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const resolve = (t: ThemePref): ThemeId => (t === "system" ? (prefersDark() ? "midnight" : "blue") : t);
const isDark = (id: ThemeId) => (DARK_IDS as readonly string[]).includes(id);

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
  const id = resolve(t);
  const el = document.documentElement;
  el.classList.toggle("dark", isDark(id));
  el.setAttribute("data-theme", id);
}

type ThemeContext = {
  /** The saved choice, which may be "system". */
  theme: ThemePref;
  /** Whether the theme on screen is a dark one. */
  dark: boolean;
  setTheme: (t: ThemePref) => void;
  /** Switch between the light and the dark theme used most recently. */
  toggleDark: () => void;
};

const Ctx = createContext<ThemeContext>({ theme: "system", dark: false, setTheme: () => {}, toggleDark: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  // localStorage is the source of truth; the server always renders "system".
  const theme = useSyncExternalStore(subscribe, read, () => "system" as ThemePref);
  const dark = useSyncExternalStore(
    subscribe,
    () => isDark(resolve(read())),
    () => false,
  );

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
      if (t !== "system") localStorage.setItem(isDark(t) ? LAST_DARK : LAST_LIGHT, t);
    } catch {
      /* storage unavailable: still apply for this page view */
    }
    apply(t);
    window.dispatchEvent(new Event(EVENT));
  }, []);

  const toggleDark = useCallback(() => {
    const toDark = !isDark(resolve(read()));
    const last = stored(toDark ? LAST_DARK : LAST_LIGHT);
    setTheme(isThemeId(last) && isDark(last) === toDark ? last : toDark ? "midnight" : "blue");
  }, [setTheme]);

  return <Ctx.Provider value={{ theme, dark, setTheme, toggleDark }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
