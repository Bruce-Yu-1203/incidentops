"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

const ITEMS = [
  { href: "/", label: "Inbox", match: (p: string) => p === "/" || p.startsWith("/incidents") || p.startsWith("/actions") || p.startsWith("/investigations") },
  { href: "/history", label: "History", match: (p: string) => p.startsWith("/history") },
  { href: "/metrics", label: "Metrics", match: (p: string) => p.startsWith("/metrics") },
  { href: "/reports", label: "Reports", match: (p: string) => p.startsWith("/reports") },
  { href: "/benchmarks", label: "Benchmarks", match: (p: string) => p.startsWith("/benchmarks") },
];

function ActiveNav() {
  const pathname = usePathname() ?? "/";
  return (
    <nav className="app-nav" aria-label="Main">
      {ITEMS.map((item) => (
        <Link key={item.href} href={item.href} aria-current={item.match(pathname) ? "page" : undefined}>
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

function StaticNav() {
  return (
    <nav className="app-nav" aria-label="Main">
      {ITEMS.map((item) => (
        <Link key={item.href} href={item.href}>
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

/** The pathname is request data, so the active marker streams in after prerendering. */
export function AppNav() {
  return (
    <Suspense fallback={<StaticNav />}>
      <ActiveNav />
    </Suspense>
  );
}
