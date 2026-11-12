import type { ReactNode } from "react";
import { AppShell } from "@/components/app-shell";
import { RequireSession } from "@/features/auth/session";
import { AccountBanner } from "@/features/account/account-banner";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <RequireSession>
      <AppShell>
        <AccountBanner />
        {children}
      </AppShell>
    </RequireSession>
  );
}
