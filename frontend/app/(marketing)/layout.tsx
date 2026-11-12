import type { ReactNode } from "react";
import { SiteFooter, SiteHeader } from "@/components/marketing/site-chrome";

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <SiteHeader />
      <main id="main">{children}</main>
      <SiteFooter />
    </>
  );
}
