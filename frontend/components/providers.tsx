"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Toaster } from "sonner";
import { ApiError } from "@/lib/api";
import { ThemeProvider } from "@/features/theme/theme";

const AUTH_LOST = "ks:auth-lost";

/** Query-cache callbacks live outside React; they signal session loss with a DOM event. */
function onError(error: unknown) {
  if (error instanceof ApiError && error.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_LOST));
}

/** Sends people back to sign-in when their session expires mid-use, and drops cached data. */
function AuthLostRedirect() {
  const router = useRouter();
  const qc = useQueryClient();
  useEffect(() => {
    const handler = () => {
      const p = window.location.pathname;
      if (!p.startsWith("/app") && !p.startsWith("/admin")) return;
      qc.clear();
      router.replace(`/sign-in?next=${encodeURIComponent(p + window.location.search)}&reason=expired`);
    };
    window.addEventListener(AUTH_LOST, handler);
    return () => window.removeEventListener(AUTH_LOST, handler);
  }, [router, qc]);
  return null;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError }),
        mutationCache: new MutationCache({ onError }),
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            refetchOnWindowFocus: false,
            retry: (count, err) => {
              if (err instanceof ApiError && err.status > 0 && err.status < 500) return false;
              return count < 2;
            },
          },
        },
      }),
  );
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <AuthLostRedirect />
        {children}
        <Toaster
          position="bottom-right"
          toastOptions={{
            classNames: {
              toast: "!bg-surface !text-ink !border !border-rule !shadow-[var(--shadow-pop)] !font-sans",
              description: "!text-muted",
            },
          }}
        />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
