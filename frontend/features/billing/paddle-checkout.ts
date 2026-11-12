"use client";

import { initializePaddle, type Paddle } from "@paddle/paddle-js";

let instance: Promise<Paddle | undefined> | null = null;
let key = "";

/**
 * Loads Paddle.js with the public client-side token returned by the API. The browser only opens
 * a checkout for a transaction the server created from a mapped price; it can't set the amount.
 */
export function getPaddle(token: string, environment: "sandbox" | "production", onEvent?: (name: string) => void) {
  const k = `${token}:${environment}`;
  if (!instance || key !== k) {
    key = k;
    instance = initializePaddle({
      token,
      environment,
      eventCallback: (e) => e.name && onEvent?.(e.name),
    });
  }
  return instance;
}
