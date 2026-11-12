"use client";

import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { PlansResponse } from "@/types/api";

/** Reads the live plan catalog so marketing copy never drifts from configured quotas. */
export function FreePlanLine() {
  const { data } = useQuery({ queryKey: ["plans"], queryFn: () => get<PlansResponse>("/plans"), staleTime: 300_000 });
  const free = data?.plans.find((p) => p.is_default) ?? data?.plans.find((p) => !p.monthly_price_minor);
  if (!free) return <p className="mt-4 h-5 text-sm text-muted" />;
  return (
    <p className="mt-4 text-sm text-muted">
      {free.name} plan: {formatNumber(free.limits.uploads_per_period)} uploads and {formatNumber(free.limits.questions_per_period)} questions a month,{" "}
      {free.limits.storage_display} of storage. No card required.
    </p>
  );
}
