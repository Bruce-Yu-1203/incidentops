"use client";

import { useEffect, useState } from "react";

import { remaining } from "@/lib/countdown";

/** Live time-to-expiry. The server still enforces the TTL; this is display only. */
export function Countdown({ expiresAt }: { expiresAt: string }) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);
  if (now === null) {
    return <time dateTime={expiresAt}>{new Date(expiresAt).toLocaleTimeString()}</time>;
  }
  const left = remaining(expiresAt, now);
  return (
    <time
      dateTime={expiresAt}
      className={
        left.expired
          ? "countdown countdown-expired"
          : left.seconds < 120
            ? "countdown countdown-soon"
            : "countdown"
      }
      title={new Date(expiresAt).toLocaleString()}
    >
      {left.expired ? "expired" : `${left.label} left`}
    </time>
  );
}
