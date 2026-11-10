"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { REFRESH_INTERVAL_MS } from "@/lib/refresh";

/**
 * Re-fetches the server-rendered page while `active`, pausing when the tab is
 * hidden. Client state such as typed input survives a refresh.
 */
export function AutoRefresh({
  active,
  intervalMs = REFRESH_INTERVAL_MS,
}: {
  active: boolean;
  intervalMs?: number;
}) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, intervalMs);
    return () => clearInterval(timer);
  }, [active, intervalMs, router]);
  if (!active) return null;
  return (
    <p className="subtitle small-text live" role="status">
      <span className="live-dot" aria-hidden="true" />
      Live · refreshing every {Math.round(intervalMs / 1000)} s
    </p>
  );
}
