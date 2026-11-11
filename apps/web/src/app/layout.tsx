import type { Metadata } from "next";
import { Suspense } from "react";

import "./globals.css";
import "./console.css";
import { HeaderShell, UserBar } from "./user-bar";

export const metadata: Metadata = {
  title: "IncidentOps",
  description: "Policy-governed AI incident investigation runtime (lab prototype)",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font -- app router root layout */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"
        />
      </head>
      <body>
        <Suspense fallback={<HeaderShell />}>
          <UserBar />
        </Suspense>
        {children}
      </body>
    </html>
  );
}
