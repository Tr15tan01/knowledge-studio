import type { NextConfig } from "next";

/**
 * The browser only ever talks to this origin. `/api/*` is proxied to the FastAPI backend so
 * session cookies stay first-party (HttpOnly, SameSite=Lax) and no API URL or secret is exposed.
 */
const API_INTERNAL_URL = (process.env.API_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const isDev = process.env.NODE_ENV !== "production";

const paddle = ["https://cdn.paddle.com", "https://*.paddle.com"];

const csp = [
  "default-src 'self'",
  // Next.js injects inline bootstrap scripts; dev mode additionally needs eval for fast refresh.
  `script-src 'self' 'unsafe-inline' ${isDev ? "'unsafe-eval'" : ""} ${paddle.join(" ")}`,
  `style-src 'self' 'unsafe-inline' ${paddle.join(" ")}`,
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${paddle.join(" ")}${isDev ? " ws: http://localhost:* http://127.0.0.1:*" : ""}`,
  `frame-src ${paddle.join(" ")}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Next clamps proxied request bodies (10 MB by default). Allow the platform's hard upload ceiling
  // (UPLOAD_HARD_MAX_BYTES, 200 MiB) plus headroom. In production, route /api/* to FastAPI at the
  // reverse proxy instead so large uploads never pass through Node (see docs/deployment.md).
  experimental: { proxyClientMaxBodySize: (process.env.UPLOAD_PROXY_MAX_BODY || "210mb") as `${number}mb` },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_INTERNAL_URL}/api/:path*` },
      { source: "/health/:path*", destination: `${API_INTERNAL_URL}/health/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
