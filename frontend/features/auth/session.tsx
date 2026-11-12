"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { get, post } from "@/lib/api";
import type { AuthConfig, Role, SessionResponse, UsageResponse, User } from "@/types/api";
import { Spinner } from "@/components/ui/spinner";

export const qk = {
  session: ["session"] as const,
  authConfig: ["auth-config"] as const,
  usage: ["usage"] as const,
};

export function useSession() {
  return useQuery({ queryKey: qk.session, queryFn: () => get<SessionResponse>("/auth/session"), staleTime: 60_000 });
}

export function useUser(): User | undefined {
  return useSession().data?.user;
}

export function useAuthConfig() {
  return useQuery({ queryKey: qk.authConfig, queryFn: () => get<AuthConfig>("/auth/config"), staleTime: 300_000 });
}

/** Authoritative server-side usage, entitlements and storage for the current account. */
export function useUsage() {
  return useQuery({ queryKey: qk.usage, queryFn: () => get<UsageResponse>("/me/usage"), staleTime: 10_000 });
}

export function hasFeature(usage: UsageResponse | undefined, key: string): boolean {
  return Boolean(usage?.entitlements.features.find((f) => f.key === key)?.enabled);
}

const RANK: Record<Role, number> = { USER: 0, ADMIN: 1, SUPER_ADMIN: 2 };
export function hasRole(user: User | undefined, role: Role): boolean {
  return Boolean(user && RANK[user.role] >= RANK[role]);
}

export function useSignOut() {
  const qc = useQueryClient();
  const router = useRouter();
  return async () => {
    try {
      await post("/auth/logout");
    } finally {
      qc.clear();
      router.replace("/sign-in");
    }
  };
}

/**
 * Client-side gate for the app shell. It only decides what to render — every API call is
 * authorized independently by the backend.
 */
export function RequireSession({ children, role }: { children: ReactNode; role?: Role }) {
  const { data, isLoading, isError } = useSession();
  const router = useRouter();
  const allowed = data?.authenticated && (!role || hasRole(data.user, role));

  useEffect(() => {
    if (isLoading) return;
    if (data && !data.authenticated) {
      router.replace(`/sign-in?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    } else if (data?.authenticated && role && !hasRole(data.user, role)) {
      router.replace("/app");
    }
  }, [data, isLoading, role, router]);

  if (isError) {
    return (
      <div className="grid min-h-dvh place-items-center p-6 text-center">
        <div>
          <p className="font-medium">Knowledge Studio can&apos;t reach its API.</p>
          <p className="mt-1 text-sm text-muted">Check that the backend is running, then reload the page.</p>
        </div>
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="grid min-h-dvh place-items-center text-muted">
        <Spinner className="size-6" label="Loading" />
      </div>
    );
  }
  return <>{children}</>;
}
