"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

/** Keep simple filter state in the URL so views are shareable and survive refresh. */
export function useUrlState<T extends Record<string, string>>(defaults: T) {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const state = Object.fromEntries(Object.entries(defaults).map(([k, v]) => [k, params.get(k) ?? v])) as T;
  const set = useCallback(
    (patch: Partial<T>) => {
      const sp = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined || v === "" || v === defaults[k]) sp.delete(k);
        else sp.set(k, String(v));
      }
      const qs = sp.toString();
      router.replace(qs ? `${path}?${qs}` : path, { scroll: false });
    },
    // `defaults` is a literal at each call site; params/path/router capture everything that changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [params, path, router],
  );
  return [state, set] as const;
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
