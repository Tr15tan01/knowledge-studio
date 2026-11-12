import "@fontsource-variable/bricolage-grotesque/standard.css";
import "@fontsource-variable/literata/standard.css";
import "@fontsource-variable/literata/standard-italic.css";
import "@fontsource-variable/noto-sans-georgian/index.css";
import "@fontsource-variable/noto-serif-georgian/index.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import Script from "next/script";
import type { ReactNode } from "react";
import { Providers } from "@/components/providers";
import { themeBootScript } from "@/features/theme/theme";

export const metadata: Metadata = {
  title: { default: "Knowledge Studio", template: "%s · Knowledge Studio" },
  description: "Upload your books and documents, ask questions with page-level citations, and research across your whole library.",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f6f9" },
    { media: "(prefers-color-scheme: dark)", color: "#12152a" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh">
        {/* Applies the saved theme before first paint to avoid a light/dark flash. */}
        <Script id="theme-boot" strategy="beforeInteractive">
          {themeBootScript}
        </Script>
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:shadow-[var(--shadow-pop)]">
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
