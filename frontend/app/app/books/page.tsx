"use client";

import { Suspense } from "react";
import { LoadingRows } from "@/components/ui/misc";
import { SourcesList } from "@/features/library/sources-list";

export default function Page() {
  return (
    <Suspense fallback={<LoadingRows rows={6} />}>
      <SourcesList kind="book" />
    </Suspense>
  );
}
